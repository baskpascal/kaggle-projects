import argparse
from collections import Counter
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import signal
import time
from types import SimpleNamespace

from .agents import agent_hash, invoke, load_agent, prepare
from .engine import fingerprint, make_environment, official
from .telemetry import EconomicTelemetry

EVIDENCE_PROFILES = ('score', 'audit', 'full')


def _profile(evidence_profile, telemetry_enabled):
    if evidence_profile is not None and evidence_profile not in EVIDENCE_PROFILES:
        raise ValueError(f'Unknown evidence profile {evidence_profile!r}')
    if telemetry_enabled is not None:
        legacy = 'full' if telemetry_enabled else 'audit'
        if evidence_profile is not None and evidence_profile != legacy:
            raise ValueError('telemetry_enabled conflicts with evidence_profile')
        return legacy
    return evidence_profile or 'full'


def _runtime_summary(samples):
    ordered = sorted(samples)
    def percentile(fraction):
        if not ordered:
            return 0.
        position = (len(ordered) - 1) * fraction
        low, high = int(position), min(len(ordered) - 1, int(position) + 1)
        weight = position - low
        return ordered[low] * (1 - weight) + ordered[high] * weight
    return {name: percentile(fraction) for name, fraction in
            [('p50', .5), ('p95', .95), ('p99', .99), ('max', 1.)]}


@contextmanager
def deadline(seconds):
    """Pre-empt a slow callback, when the callback lets us.

    This guard is armed with `signal.setitimer` in the interpreter that runs the bundle,
    so a bundle can disarm it with two lines and nothing here will fire. That is why it is
    only half of the barrier: `arena.parallel` holds a wall-clock deadline in the parent
    and kills the process group, and the caller below re-checks the elapsed time after the
    callback returns. Keep this timer anyway -- it costs nothing and it ends an honest slow
    agent's turn at the right moment instead of at the end of the game.
    """
    if not hasattr(signal, 'SIGALRM') or not hasattr(signal, 'setitimer'):
        # Windows has no SIGALRM/setitimer. We cannot safely pre-empt Python
        # code in-process there, but we can still reject callbacks that return
        # after the configured deadline instead of failing every match at step 0.
        started = time.perf_counter()
        yield
        if time.perf_counter() - started > seconds:
            raise TimeoutError('Local callback deadline exceeded')
        return
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
    # An agent may submit more hand actions than it has hands: the interpreter
    # resolves the position to None and silently no-ops. Mirroring that here is
    # what keeps the audit from crashing on a game the official engine plays.
    hands = farm['hands']
    if index and (index > len(hands) or index >= len(private['inventories'])):
        return None
    pos = farm['farmer'] if index == 0 else hands[index - 1]
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
        # The official framework hands `step` to BOTH players, always equal to
        # day * turnsPerDay + hour (verified in tests/test_clock.py against a
        # real episode). The fast backend only advances state[0], so seat 1
        # must read the shared value or it would silently see a stale clock.
        for key in ('step', 'day', 'hour', 'farms', 'market', 'town'):
            obs[key] = shared[key]
        # Isolate callbacks from each other and the interpreter. No opponent private data.
        result.append(json.loads(json.dumps(obs)))
    return result


def run_match(candidate, opponent, seed, seat=0, backend='fast', configuration=None, replay=None,
              telemetry_enabled=None, replay_steps=None, evidence_profile=None,
              replay_inline=False):
    requested_profile = evidence_profile
    evidence_profile = _profile(evidence_profile, telemetry_enabled)
    # Before profiles existed, replay callers routinely disabled daily telemetry. Preserve
    # those jobs as full replay capture; new callers must ask for ``full`` explicitly.
    if replay and requested_profile is None and telemetry_enabled is False:
        evidence_profile = 'full'
    if (replay or replay_inline) and evidence_profile != 'full':
        raise ValueError('Replay capture requires the full evidence profile')
    if replay_steps is not None:
        replay_steps = list(replay_steps)
        if (not (replay or replay_inline) or not replay_steps
                or len(set(replay_steps)) != len(replay_steps)
                or any(type(step) is not int or step < 0 for step in replay_steps)):
            raise ValueError('Snapshot steps require a replay path and unique nonnegative integers')
    start = time.perf_counter()
    module = official()
    reference = make_environment(seed, configuration, verified=True)
    env = reference if backend == 'official' else SimpleNamespace(
        configuration=copy.deepcopy(reference.configuration), info=copy.deepcopy(reference.info), done=False,
        state=copy.deepcopy(reference.state))
    cfg = dict(env.configuration)
    if replay_steps is not None and max(replay_steps) >= cfg['episodeSteps']:
        raise ValueError('Snapshot step is outside the episode')
    names = [candidate, opponent] if seat == 0 else [opponent, candidate]
    # A bundle that mutates arena-visible state at import is refused here, so a
    # corrupted game never becomes evidence. Audit patching happens afterwards.
    functions = [prepare(load_agent(name, seed * 2 + i)) for i, name in enumerate(names)]
    hashes = [getattr(function, '__arena_sha256__', None) or agent_hash(name)
              for function, name in zip(functions, names)]
    timings, failures = [[], []], [[], []]
    audit = Audit(module) if evidence_profile in ('audit', 'full') else None
    transcript = []
    if replay_steps is not None and 0 in replay_steps:
        transcript.append({'step': 0, 'actions': [], 'observations': observations(env.state)})
    if audit:
        module._apply_unit_action = audit.apply
        module._commit_unit = audit.commit
        module._drop_inventories_to_shed = audit.drop
    telemetry = EconomicTelemetry(module) if evidence_profile == 'full' else None
    if telemetry:
        telemetry.turns_per_day = cfg['turnsPerDay']
        telemetry.shed_capacity = cfg['shedCapacity']
        telemetry.install()
    try:
        steps = 0
        while not env.done:
            obs = observations(env.state)
            if telemetry:
                telemetry.begin_turn(obs)
            actions = []
            for i, function in enumerate(functions):
                t = time.perf_counter()
                try:
                    with deadline(cfg['actTimeout']):
                        action = invoke(function, obs[i], copy.deepcopy(cfg))
                    # The timer above can be disarmed by the code it is timing, and a
                    # bundle that does so overran the deadline with nothing recorded at
                    # all. Measuring after the fact cannot pre-empt anything, but it does
                    # make the overrun a failure like any other, and it reads a clock
                    # rather than a handler, so disarming the timer no longer hides it.
                    if time.perf_counter() - t > cfg['actTimeout']:
                        raise TimeoutError('Callback returned past the deadline; the '
                                           'in-process timer did not fire')
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
            if audit:
                audit.player = -1
            before_audit = [c.copy() for c in audit.counts] if telemetry else None
            if backend == 'official':
                reference.step(actions)
            else:
                for state, action in zip(env.state, actions):
                    state.action = action
                env.state = module.interpreter(env.state, env)
                steps_next = steps + 1
                env.state[0].observation.step = steps_next
                env.done = all(s.status == 'DONE' for s in env.state)
            if telemetry:
                telemetry.finish_turn(env.state, before_audit, audit)
            steps += 1
            if (replay or replay_inline) and (replay_steps is None or steps in replay_steps):
                transcript.append({'step': steps, 'actions': actions,
                                   'observations': observations(env.state)})
        money = [float(f['money']) for f in env.state[0].observation.farms]
    finally:
        if telemetry:
            telemetry.restore()
        if audit:
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
        'candidate': candidate, 'opponent': opponent, 'candidate_hash': hashes[seat],
        'opponent_hash': hashes[other], 'seed': seed, 'seat': seat,
        'score': score, 'money': money[seat], 'opponent_money': money[other],
        'margin': money[seat] - money[other], 'steps': steps, 'backend': backend,
        'configuration': {**cfg, 'seed': seed},
        'environment': fingerprint(verified_module=module),
        'evidence_profile': evidence_profile,
        'failures': failures[seat], 'opponent_failures': failures[other],
        'audit': dict(audit.counts[seat]) if audit else {},
        'opponent_audit': dict(audit.counts[other]) if audit else {},
        'sales': audit.sales[seat] if audit else {},
        'opponent_sales': audit.sales[other] if audit else {},
        'telemetry_version': 1 if telemetry else None,
        'daily': telemetry.output(seat) if telemetry else None,
        'opponent_daily': telemetry.output(other) if telemetry else None,
        'runtime_ms': (timings[seat] if evidence_profile == 'full' else
                       _runtime_summary(timings[seat]) if evidence_profile == 'audit' else None),
        'unsold_items': leftover[seat] if audit else None,
        'wall_seconds': time.perf_counter() - start,
    }
    if replay_inline:
        result['replay_turns'] = transcript
    if replay:
        path = Path(replay)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {'format': 'kaggriculture-lab-v2', 'result': result, 'turns': transcript}
        if replay_steps is not None:
            payload.update(format='kaggriculture-lab-snapshots-v1', requested_steps=sorted(replay_steps))
        path.write_text(json.dumps(payload))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--agent', '--candidate', dest='candidate', default='challenger')
    parser.add_argument('--opponent', default='champion')
    parser.add_argument('--seed', type=int, default=123)
    parser.add_argument('--seat', type=int, choices=[0, 1], default=0)
    parser.add_argument('--backend', choices=['fast', 'official'], default='fast')
    parser.add_argument('--evidence-profile', choices=EVIDENCE_PROFILES, default='full')
    parser.add_argument('--replay')
    args = parser.parse_args()
    result = run_match(**vars(args))
    samples = result.pop('runtime_ms')
    result['runtime_max_ms'] = (samples.get('max', 0) if isinstance(samples, dict)
                                else max(samples or [], default=0))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
