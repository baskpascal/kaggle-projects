"""Adaptive Option Selector v0: does choosing a macro option by state beat the best fixed one?

The engine is deterministic and public, so a macro option does not have to be learned from
reward - it can be simulated. This runs one important decision, three continuations per world,
and asks a single question: is there a state-conditioned rule that beats always picking the
option with the best overall win rate? If not, option-level adaptation dies here for the cost
of a few hundred games.

Fitness is win probability, not money: the final tournament is Bradley-Terry over wins, where
a win by one coin and a win by thirty thousand count the same.
"""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

AGENT = '''from agent.planner import policy

BASE = {base!r}
OPTION = {option!r}
SWITCH = {switch!r}


def agent(observation, configuration=None):
    turns = (configuration or {{}}).get("turnsPerDay", 24)
    step = observation.get("step")
    if step is None:
        step = observation["day"] * turns + observation["hour"]
    parameters = dict(BASE)
    if step >= SWITCH:
        parameters.update(OPTION)
    return policy(observation, configuration, parameters)
'''

# One decision, three continuations. Kept deliberately small: the point is to find out
# whether state-conditioned choice pays at all, not to search the option space.
OPTIONS = {
    'continue': {},
    'cow_heavy': {'animal_cap': 26, 'animal_type': 'COW'},
    'crop_heavy': {'animal_cap': 10, 'plant_until_hour': 22},
}


def build(directory, name, base, option, switch):
    path = Path(directory) / f'{name}.py'
    path.write_text(AGENT.format(base=base, option=option, switch=switch), encoding='utf-8')
    return str(path)


def play(agent, opponent, seed, seat):
    with tempfile.NamedTemporaryFile('r', suffix='.json', delete=False) as handle:
        path = handle.name
    result = subprocess.run(
        [sys.executable, '-m', 'arena.match', '--agent', agent, '--opponent', opponent,
         '--seed', str(seed), '--seat', str(seat), '--replay', path,
         '--evidence-profile', 'full'],
        cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-300:])
    replay = json.loads(Path(path).read_text(encoding='utf-8'))
    Path(path).unlink(missing_ok=True)
    return replay


def features(replay, seat, switch):
    """The state at the decision turn, from tiles and market rather than from orders."""
    frame = next((t for t in replay['turns'] if t['step'] >= switch), replay['turns'][-1])
    obs = frame['observations'][seat]
    me, them = obs['farms'][seat], obs['farms'][1 - seat]
    tiles = [z for row in me['tiles'] for z in row]
    other = [z for row in them['tiles'] for z in row]

    def count(source, key, value):
        return sum(isinstance(z, dict) and z.get(key) == value for z in source)

    return {
        'cash': me['money'],
        'cows': count(tiles, 'animal', 'COW'),
        'sheep': count(tiles, 'animal', 'SHEEP'),
        'crops': sum(isinstance(z, dict) and 'crop' in z for z in tiles),
        'free_tiles': sum(z is None for z in tiles),
        'opponent_cows': count(other, 'animal', 'COW'),
        'opponent_sheep': count(other, 'animal', 'SHEEP'),
        'opponent_crops': sum(isinstance(z, dict) and 'crop' in z for z in other),
        'opponent_cash': them['money'],
        'milk_price': obs['market']['prices'].get('MILK', 0),
        'wool_price': obs['market']['prices'].get('WOOL', 0),
        'strawberry_price': obs['market']['prices'].get('STRAWBERRY', 0),
        'shops': len(obs['town']['unlocked_shops']),
    }


def outcome(replay, seat):
    last = replay['turns'][-1]['observations']
    ours = last[seat]['farms'][seat]['money']
    theirs = last[1 - seat]['farms'][1 - seat]['money']
    return 1.0 if ours > theirs else (0.5 if ours == theirs else 0.0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opponent', default='versions/v006/main.py')
    parser.add_argument('--seeds', default='1000:1060')
    parser.add_argument('--switch', type=int, default=336)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    start, _, end = args.seeds.partition(':')
    seeds = list(range(int(start), int(end)))
    base = {'economic_planner': True, 'land_reservation': True}
    workdir = Path(tempfile.mkdtemp(prefix='option-selector-'))
    agents = {name: build(workdir, name, base, option, args.switch)
              for name, option in OPTIONS.items()}

    rows, failures = [], []
    for seed in seeds:
        for seat in (0, 1):
            record = {'seed': seed, 'seat': seat, 'results': {}}
            for name, path in agents.items():
                try:
                    replay = play(path, args.opponent, seed, seat)
                except RuntimeError as error:
                    failures.append({'seed': seed, 'seat': seat, 'option': name,
                                     'reason': str(error)})
                    continue
                record['results'][name] = outcome(replay, seat)
                if 'features' not in record:
                    record['features'] = features(replay, seat, args.switch)
            if len(record['results']) == len(agents):
                rows.append(record)

    fixed = {name: statistics.mean(r['results'][name] for r in rows) for name in OPTIONS}
    best_fixed = max(fixed, key=fixed.get)
    oracle = statistics.mean(max(r['results'].values()) for r in rows)
    payload = {'schema_version': 1, 'opponent': args.opponent, 'switch': args.switch,
               'worlds': len(rows), 'failures': failures,
               'fixed_win_rate': fixed, 'best_fixed': best_fixed,
               'oracle_win_rate': oracle, 'rows': rows}
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in payload.items() if k != 'rows'}, indent=2)[:1200])


if __name__ == '__main__':
    main()
