"""When our planner reaches each capital milestone, in worlds we can replay.

Comparing our planner against an elite *at the elite's own recorded state* answers the
wrong question: that state already contains the decision the elite just took, so a planner
that correctly declines to buy a second cow it already owns is scored as disagreeing. The
comparison that means something is the one this module makes - run our planner from turn
zero and record the turn at which it first reaches each milestone, so the numbers line up
against the elite milestone table directly.
"""
import argparse
import json
from pathlib import Path
import subprocess
import statistics
import sys
import tempfile

from experiments.elite_events import strategic_events

ROOT = Path(__file__).resolve().parents[1]
MILESTONES = ('first_COW', 'first_SHEEP', 'first_GOOSE', 'land1', 'land2', 'land3',
              'hire_burst', 'build_PASTURE', 'build_COOP', 'liquidation')


def replay_frames(replay):
    """The lab replay, reshaped into the per-seat frames the extractor reads."""
    steps = []
    for turn in replay['turns']:
        actions = turn.get('actions') or []
        # Snapshot replays include the true step-zero engine state before either
        # agent has requested an action. Do not let zip silently discard that frame:
        # executed-state gates are indexed by engine step, not action count.
        steps.append([{'observation': observation,
                       'action': actions[seat] if seat < len(actions) else None}
                      for seat, observation in enumerate(turn['observations'])])
    return steps


def milestones(steps, seat, configuration):
    first = {}
    for kind, detail, state in strategic_events(steps, seat, configuration):
        key = None
        if kind == 'BUY_LAND':
            key = 'land%d' % detail['expansion_index']
        elif kind == 'BUY_ANIMAL' and detail['first_of_species']:
            key = 'first_' + detail['species']
        elif kind == 'HIRE_BURST':
            key = 'hire_burst'
        elif kind == 'BUILD':
            key = 'build_' + detail['kind']
        elif kind == 'TERMINAL_LIQUIDATION':
            key = 'liquidation'
        if key and key not in first:
            first[key] = state['turn']
    return first


AGENT_TEMPLATE = """from agent.planner import policy
PARAMETERS = {parameters!r}


def agent(observation, configuration=None):
    return policy(observation, configuration, PARAMETERS)
"""


def parameterized_agent(parameters, directory):
    """A real agent file, so the parameters travel through the same loader a
    submission would use instead of a side channel the arena does not model."""
    path = Path(directory) / 'planner_variant.py'
    path.write_text(AGENT_TEMPLATE.format(parameters=parameters), encoding='utf-8')
    return str(path)


def play(agent, opponent, seed, seat, parameters=None):
    with tempfile.NamedTemporaryFile('r', suffix='.json', delete=False) as handle:
        path = handle.name
    command = [sys.executable, '-m', 'arena.match', '--agent', agent,
               '--opponent', opponent, '--seed', str(seed), '--seat', str(seat),
               '--replay', path, '--evidence-profile', 'full']
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f'match failed for seed {seed}: {result.stderr[-400:]}')
    replay = json.loads(Path(path).read_text(encoding='utf-8'))
    Path(path).unlink(missing_ok=True)
    return replay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', default='challenger')
    parser.add_argument('--opponent', default='versions/v006/main.py')
    parser.add_argument('--seeds', default='1000:1010')
    parser.add_argument('--seat', type=int, default=0)
    parser.add_argument('--parameters', help='JSON overrides passed to the planner')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    start, _, end = args.seeds.partition(':')
    seeds = list(range(int(start), int(end))) if end else [int(start)]
    parameters = json.loads(args.parameters) if args.parameters else None
    workdir = tempfile.mkdtemp(prefix='planner-milestones-')
    agent = (parameterized_agent(parameters, workdir) if parameters else args.agent)
    rows, failures = [], []
    for seed in seeds:
        try:
            replay = play(agent, args.opponent, seed, args.seat)
        except RuntimeError as error:
            failures.append({'seed': seed, 'reason': str(error)})
            continue
        steps = replay_frames(replay)
        configuration = replay.get('configuration') or {'turnsPerDay': 24}
        rows.append({'seed': seed, **milestones(steps, args.seat, configuration)})

    summary = {}
    for key in MILESTONES:
        reached = [row[key] for row in rows if key in row]
        summary[key] = {
            'reached_in_worlds': len(reached), 'of': len(rows),
            'median_turn': int(statistics.median(reached)) if reached else None,
        }
    payload = {'schema_version': 1, 'agent': agent, 'opponent': args.opponent,
               'seeds': args.seeds, 'seat': args.seat, 'parameters': parameters,
               'worlds': len(rows), 'failures': failures,
               'milestones': summary, 'rows': rows}
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in payload.items() if k != 'rows'}, indent=2))


if __name__ == '__main__':
    main()
