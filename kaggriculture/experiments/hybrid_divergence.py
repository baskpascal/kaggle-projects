"""Where v006 decides one thing and a 2900+ agent decides another, in the same world.

v006 beats every pinned public artifact and loses every game to the live top, so the useful
question is no longer how to reproduce public play better. It is narrower: which decisions
does v006 take fixed, because it is replaying a public policy, where an elite varies with
the state?

For each admitted elite stream this replays that exact world - the episode's own seed, the
elite's seat, and the opponent replaying what it actually submitted - with v006 in the
elite's place, and reports the macro decisions where the two streams disagree, with the
observable state immediately before each. It does not score anything and it does not propose
a mechanism; it produces the table from which one decision is chosen.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict

from experiments.elite_events import strategic_events, summarize_state
from experiments.tape_agent import build as build_tape_agent
from experiments.top_panel import admissible, episode_streams, load_leaderboard

ROOT = Path(__file__).resolve().parents[1]
MACRO = ('BUY_LAND', 'BUY_ANIMAL', 'HIRE', 'BUY_SEED', 'BUY_PRODUCT', 'SELL')


def macro_profile(action):
    """The capital decisions in one action, ignoring movement and field work."""
    profile = Counter()
    for order in (action or {}).get('market') or []:
        if not order or order[0] not in MACRO:
            continue
        if order[0] == 'BUY_ANIMAL':
            profile['BUY_ANIMAL:' + str(order[1])] += (order[2] if len(order) > 2 else 1)
        elif order[0] == 'SELL':
            profile['SELL'] += (order[2] if len(order) > 2 else 1)
        else:
            profile[order[0]] += 1
    for verb in [(action or {}).get('farmer') or []] + list((action or {}).get('hands') or []):
        if verb and str(verb[0]).startswith('BUILD_'):
            profile['BUILD:' + str(verb[0])[6:]] += 1
    return profile


def divergences(ours, theirs, observations, configuration, *, limit=6):
    """The first `limit` turns whose macro decisions differ, with the state before."""
    found = []
    for turn in range(min(len(ours), len(theirs))):
        mine, other = macro_profile(ours[turn]), macro_profile(theirs[turn])
        if mine == other:
            continue
        keys = sorted(set(mine) | set(other))
        state = summarize_state(observations[max(0, turn - 1)], configuration)
        found.append({
            'turn': turn,
            'elite': {k: other[k] for k in keys if other[k]},
            'v006': {k: mine[k] for k in keys if mine[k]},
            'decision': sorted(k for k in keys if mine[k] != other[k]),
            'state': {k: state[k] for k in ('turn', 'cash', 'known_shops', 'land_used',
                                            'animals', 'structures', 'hands')},
        })
        if len(found) >= limit:
            break
    return found


def milestones(steps, seat, configuration):
    """First turn of each macro decision in one stream."""
    first = {}
    for kind, detail, state in strategic_events(steps, seat, configuration):
        key = None
        if kind == 'BUY_LAND':
            key = 'land%d' % detail['expansion_index']
        elif kind == 'BUY_ANIMAL' and detail['first_of_species']:
            key = 'first_' + detail['species']
        elif kind == 'BUILD':
            key = 'build_' + detail['kind']
        elif kind == 'HIRE_BURST':
            key = 'hire_burst'
        if key and key not in first:
            first[key] = state['turn']
    return first


def dispersion(values):
    """How much a decision moves across worlds. A fixed policy scores near zero."""
    present = sorted(v for v in values if v is not None)
    if len(present) < 2:
        return None
    lower = present[len(present) // 4]
    upper = present[(3 * len(present)) // 4]
    return {'worlds': len(present), 'median': present[len(present) // 2],
            'iqr': upper - lower, 'min': present[0], 'max': present[-1]}


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', required=True)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--leaderboard', required=True)
    parser.add_argument('--candidate', default='versions/v006/main.py')
    parser.add_argument('--min-rating', type=float, default=2900.)
    parser.add_argument('--per-team', type=int, default=2)
    parser.add_argument('--limit', type=int, help='smoke only: first N worlds')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    library = json.loads(Path(args.library).read_text(encoding='utf-8'))
    leaderboard = load_leaderboard(args.leaderboard)
    rows, refused = admissible(library['tapes'], leaderboard, args.min_rating)
    rows.sort(key=lambda row: (row['rank'], -row['margin']))
    seen, chosen = Counter(), []
    for row in rows:
        if seen[row['team']] >= args.per_team:
            continue
        seen[row['team']] += 1
        chosen.append(row)
    if args.limit:
        chosen = chosen[:args.limit]

    workdir = Path(tempfile.mkdtemp(prefix='hybrid-divergence-'))
    results, failures = [], []
    for row in chosen:
        try:
            seats = episode_streams(args.archive, row['episode'])
            seat = int(row['seat'])
            elite_actions = seats[seat]['actions']
            opponent_path = build_tape_agent(
                {'actions': seats[1 - seat]['actions']},
                workdir / f"opp-{row['episode']}-{1 - seat}.py",
                provenance=f"recorded opponent of episode {row['episode']}")
            replay = play(args.candidate, str(opponent_path), row['seed'], seat)
        except Exception as error:                                   # noqa: BLE001
            failures.append({'episode': row['episode'],
                             'reason': f'{type(error).__name__}: {error}'})
            continue
        ours = [turn['actions'][seat] for turn in replay['turns']]
        observations = [turn['observations'][seat] for turn in replay['turns']]
        configuration = replay.get('configuration') or {'turnsPerDay': 24}
        ours_steps = [[{'observation': observation, 'action': action}]
                      for observation, action in zip(observations, ours)]
        elite_steps = [[{'observation': observation, 'action': action}]
                       for observation, action in zip(observations, elite_actions)]
        results.append({
            'episode': row['episode'], 'team': row['team'], 'rank': row['rank'],
            'rating': row['rating'], 'seed': row['seed'], 'seat': seat,
            'divergences': divergences(ours, elite_actions, observations, configuration),
            'v006_milestones': milestones(ours_steps, 0, configuration),
            'elite_milestones': milestones(elite_steps, 0, configuration),
        })

    table = defaultdict(lambda: {'worlds': 0, 'first': 0, 'turns': []})
    for result in results:
        for index, row in enumerate(result['divergences']):
            for decision in row['decision']:
                bucket = table[decision.split(':')[0]]
                bucket['worlds'] += 1
                bucket['first'] += int(index == 0)
                bucket['turns'].append(row['turn'])
    summary = sorted(
        ({'decision': key, 'occurrences': value['worlds'],
          'was_first_divergence': value['first'],
          'median_turn': sorted(value['turns'])[len(value['turns']) // 2]}
         for key, value in table.items()),
        key=lambda row: -row['occurrences'])
    keys = sorted({key for result in results
                   for side in ('v006_milestones', 'elite_milestones')
                   for key in result[side]})
    variation = []
    for key in keys:
        ours_spread = dispersion([r['v006_milestones'].get(key) for r in results])
        theirs = dispersion([r['elite_milestones'].get(key) for r in results])
        variation.append({'decision': key, 'v006': ours_spread, 'elite': theirs,
                          'elite_more_variable': bool(
                              ours_spread and theirs
                              and theirs['iqr'] > max(1, ours_spread['iqr']))})
    payload = {'schema_version': 1, 'candidate': args.candidate,
               'decision_variation': variation,
               'min_rating': args.min_rating, 'worlds': len(results),
               'refused': refused, 'failures': failures,
               'decision_table': summary, 'rows': results}
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in payload.items() if k != 'rows'}, indent=2)[:2500])


if __name__ == '__main__':
    main()
