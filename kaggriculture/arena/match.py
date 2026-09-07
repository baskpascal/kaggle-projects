import argparse
from collections import Counter
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import signal
import time
from types import SimpleNamespace

from .agents import agent_hash, invoke, load_agent
from .engine import fingerprint, make_environment, official


@contextmanager
def deadline(seconds):
    def expired(signum, frame):
        raise TimeoutError('Local callback deadline exceeded')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def snapshot(farm, private, index):
    pos = farm['farmer'] if index == 0 else farm['hands'][index - 1]
    tile = farm['tiles'][pos[1]][pos[0]]
    return json.dumps([pos, tile, private['inventories'][index], private['seeds'], private['shed']], sort_keys=True)


class Audit:
    def __init__(self, module):
        self.module = module
        self.original = module._apply_unit_action
        self.original_commit = module._commit_unit
        self.original_drop = module._drop_inventories_to_shed
        self.counts = [Counter(), Counter()]
        self.sales = [{}, {}]
        self.privates = {}
        self.player = -1

    def commit(self, op, item, price, farm, private, market, *args, **kwargs):
        ok = self.original_commit(op, item, price, farm, private, market, *args, **kwargs)
        if ok and op == 'SELL':
            player = self.privates[id(private)]
            row = self.sales[player].setdefault(item, {'units': 0, 'revenue': 0})
            row['units'] += 1
            row['revenue'] += price
        return ok

    def drop(self, private, capacity):
        before = sum(private['shed'].values()) + sum(sum(i.values()) for i in private['inventories'])
        self.original_drop(private, capacity)
        lost = before - sum(private['shed'].values())
        self.counts[self.privates[id(private)]]['overflow_items'] += lost

    def apply(self, farm, private, idx, action, *args, **kwargs):
        if idx == 0:
            self.player += 1
            self.privates[id(private)] = self.player
        counters = self.counts[self.player]
        op = action[0] if isinstance(action, list) and action else 'MALFORMED'
        counters['unit_actions'] += 1
        counters['op_' + op] += 1
        before = snapshot(farm, private, idx) if op != 'PASS' else None
        total = sum(private['shed'].values()) + sum(sum(i.values()) for i in private['inventories'])
        self.original(farm, private, idx, action, *args, **kwargs)
        if op == 'DROP':
            after = sum(private['shed'].values()) + sum(sum(i.values()) for i in private['inventories'])
            counters['overflow_items'] += total - after
        if op != 'PASS' and before == snapshot(farm, private, idx):
            counters['no_effect_actions'] += 1
            counters['no_effect_' + op] += 1


def observations(state):
    shared = state[0].observation
    result = []
    for i in range(2):
        obs = dict(state[i].observation)
        for key in ('step', 'day', 'hour', 'farms', 'market', 'town'):
            obs[key] = shared[key]
        # Isolate callbacks from each other and the interpreter. No opponent private data.
        result.append(json.loads(json.dumps(obs)))
    return result


def run_match(candidate, opponent, seed, seat=0, backend='fast', configuration=None, replay=None):
    start = time.perf_counter()
    module = official()
    reference = make_environment(seed, configuration)
    env = reference if backend == 'official' else SimpleNamespace(
        configuration=copy.deepcopy(reference.configuration), info=copy.deepcopy(reference.info), done=False,
        state=copy.deepcopy(reference.state))
    cfg = dict(env.configuration)
    names = [candidate, opponent] if seat == 0 else [opponent, candidate]
    functions = [load_agent(name, seed * 2 + i) for i, name in enumerate(names)]
    timings, failures = [[], []], [[], []]
    audit = Audit(module)
    transcript = []
    module._apply_unit_action = audit.apply
    module._commit_unit = audit.commit
    module._drop_inventories_to_shed = audit.drop
    try:
        steps = 0
        while not env.done:
            obs = observations(env.state)
            actions = []
            for i, function in enumerate(functions):
                t = time.perf_counter()
                try:
                    with deadline(cfg['actTimeout']):
                        action = invoke(function, obs[i], copy.deepcopy(cfg))
                    if not isinstance(action, dict):
                        raise ValueError('Action must be an object')
                    # Validate JSON serialization even on the fast path.
                    json.dumps(action, allow_nan=False)
                except Exception as exc:
                    failures[i].append({'step': steps, 'kind': type(exc).__name__, 'message': str(exc)})
                    action = {'farmer': ['PASS'], 'hands': [], 'market': []}
                timings[i].append((time.perf_counter() - t) * 1000)
                actions.append(action)
            if any(failures):
                break
            audit.player = -1
            if backend == 'official':
                reference.step(actions)
            else:
                for state, action in zip(env.state, actions):
                    state.action = action
                env.state = module.interpreter(env.state, env)
                steps_next = steps + 1
                env.state[0].observation.step = steps_next
                env.done = all(s.status == 'DONE' for s in env.state)
            steps += 1
            if replay:
                transcript.append({'step': steps, 'actions': actions,
                                   'observations': observations(env.state)})
        money = [float(f['money']) for f in env.state[0].observation.farms]
    finally:
        module._apply_unit_action = audit.original
        module._commit_unit = audit.original_commit
        module._drop_inventories_to_shed = audit.original_drop
    other = 1 - seat
    if failures[seat] or failures[other]:
        score = .5 if failures[seat] and failures[other] else float(not failures[seat])
    else:
        score = 1. if money[seat] > money[other] else 0. if money[seat] < money[other] else .5
    leftover = [sum(p.observation.private['shed'].values()) +
                sum(sum(inv.values()) for inv in p.observation.private['inventories']) for p in env.state]
    result = {
        'candidate': candidate, 'opponent': opponent, 'candidate_hash': agent_hash(candidate),
        'opponent_hash': agent_hash(opponent), 'seed': seed, 'seat': seat,
        'score': score, 'money': money[seat], 'opponent_money': money[other],
        'margin': money[seat] - money[other], 'steps': steps, 'backend': backend,
        'configuration': {**cfg, 'seed': seed}, 'environment': fingerprint(),
        'failures': failures[seat], 'opponent_failures': failures[other],
        'audit': dict(audit.counts[seat]), 'opponent_audit': dict(audit.counts[other]),
        'sales': audit.sales[seat], 'opponent_sales': audit.sales[other],
        'runtime_ms': timings[seat], 'unsold_items': leftover[seat],
        'wall_seconds': time.perf_counter() - start,
    }
    if replay:
        path = Path(replay)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'format': 'kaggriculture-lab-v1', 'result': result, 'turns': transcript}))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--agent', '--candidate', dest='candidate', default='challenger')
    parser.add_argument('--opponent', default='champion')
    parser.add_argument('--seed', type=int, default=123)
    parser.add_argument('--seat', type=int, choices=[0, 1], default=0)
    parser.add_argument('--backend', choices=['fast', 'official'], default='fast')
    parser.add_argument('--replay')
    args = parser.parse_args()
    result = run_match(**vars(args))
    samples = result.pop('runtime_ms')
    result['runtime_max_ms'] = max(samples, default=0)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
