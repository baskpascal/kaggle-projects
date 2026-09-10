"""Does one elite team's macro timing move with observable state, in its own worlds?

Variation across the field is not adaptation: two teams with different fixed schedules
produce a large aggregate spread while neither reacts to anything. Only variation inside one
team's own episodes, tracking features that team could see, is evidence of a live policy.

So this reads a single team's admissible streams straight from the dump - no games are
played - and for every macro milestone records the state the team actually observed at
several points before it, not one frame. `P(event occurs)` is kept apart from
`turn | occurred`, because coding an absent goose as turn 719 manufactures dispersion.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics
import zipfile

from experiments.elite_events import (canonical_turn, executed_milestones,
                                      strategic_events, summarize_state)
from experiments.top_panel import admissible, load_leaderboard

LOOKBACKS = (40, 20, 10, 1)
# Conditioning on state at `t - 40` is circular for a decision whose turn is what we are
# trying to explain: an early decision has an early lookback, where no shop has unlocked
# yet. Fixed turns are the same clock for every world, so they can carry an explanation.
FIXED_POINTS = (24, 48, 72, 96)
FEATURES = ('cash', 'known_shops', 'land_used', 'animals', 'structures', 'hands')


def milestones_with_context(steps, seat, configuration):
    """First occurrence of each *executed* macro decision, with the state seen before it."""
    first = {}
    for key, turn in sorted(executed_milestones(steps, seat).items(),
                            key=lambda item: item[1]):
        context = {}
        state = summarize_state(steps[turn][seat]['observation'], configuration)
        for back in LOOKBACKS:
            index = turn - back
            if index < 0:
                continue
            earlier = summarize_state(steps[index][seat]['observation'], configuration)
            context['t-%d' % back] = {k: earlier[k] for k in FEATURES}
        first[key] = {'turn': turn, 'context': context,
                      'at_decision': {k: state[k] for k in FEATURES}}
    return first


def fixed_point_state(steps, seat, configuration):
    """The world as the team saw it at the same absolute turns in every episode."""
    result = {}
    for turn in FIXED_POINTS:
        if turn >= len(steps):
            continue
        state = summarize_state(steps[turn][seat]['observation'], configuration)
        result['t%d' % turn] = {k: state[k] for k in FEATURES}
    return result


def read_streams(archive, rows):
    with zipfile.ZipFile(archive) as source:
        for row in rows:
            episode = json.loads(source.read(f"{row['episode']}.json"))
            yield row, episode


def summarise(records):
    """Per decision: how often it happens, when, and how much its timing moves."""
    keys = sorted({key for record in records for key in record['milestones']})
    table = []
    for key in keys:
        present = [record for record in records if key in record['milestones']]
        turns = sorted(record['milestones'][key]['turn'] for record in present)
        spread = None
        if len(turns) >= 2:
            spread = {'median': turns[len(turns) // 2], 'min': turns[0], 'max': turns[-1],
                      'iqr': turns[(3 * len(turns)) // 4] - turns[len(turns) // 4],
                      'distinct': len(set(turns))}
        table.append({'decision': key,
                      'occurrence_rate': round(len(present) / len(records), 3),
                      'worlds_with_event': len(present), 'worlds': len(records),
                      'turn_when_it_occurs': spread})
    return table


def shop_conditioning(records, decision, at='t24'):
    """Timing grouped by the shops known at the same absolute turn in every world."""
    groups = defaultdict(list)
    for record in records:
        entry = record['milestones'].get(decision)
        fixed = record.get('fixed', {}).get(at)
        if not entry or not fixed:
            continue
        shops = tuple(sorted(fixed['known_shops']))
        groups[shops].append(entry['turn'])
    return sorted(
        ({'known_shops': list(shops), 'worlds': len(turns),
          'median_turn': int(statistics.median(turns)),
          'min': min(turns), 'max': max(turns)}
         for shops, turns in groups.items() if len(turns) >= 2),
        key=lambda row: -row['worlds'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', required=True)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--leaderboard', required=True)
    parser.add_argument('--team', required=True)
    parser.add_argument('--min-rating', type=float, default=2900.)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    library = json.loads(Path(args.library).read_text(encoding='utf-8'))
    leaderboard = load_leaderboard(args.leaderboard)
    rows, _ = admissible(library['tapes'], leaderboard, args.min_rating)
    rows = [row for row in rows if row['team'] == args.team]
    rows.sort(key=lambda row: -row['margin'])
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        raise SystemExit(f'no admissible streams for {args.team!r}')

    records, failures = [], []
    for row, episode in read_streams(args.archive, rows):
        configuration = dict(episode['configuration'])
        seat = int(row['seat'])
        try:
            found = milestones_with_context(episode['steps'], seat, configuration)
        except Exception as error:                                   # noqa: BLE001
            failures.append({'episode': row['episode'],
                             'reason': f'{type(error).__name__}: {error}'})
            continue
        records.append({'episode': row['episode'], 'seed': row['seed'], 'seat': seat,
                        'money': row['money'], 'milestones': found,
                        'fixed': fixed_point_state(episode['steps'], seat, configuration)})

    payload = {'schema_version': 1, 'team': args.team, 'min_rating': args.min_rating,
               'worlds': len(records), 'failures': failures,
               'table': summarise(records),
               'land1_by_shops_t24': shop_conditioning(records, 'land1', 't24'),
               'land1_by_shops_t48': shop_conditioning(records, 'land1', 't48'),
               'land1_by_shops_t72': shop_conditioning(records, 'land1', 't72'),
               'rows': records}
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in payload.items() if k != 'rows'}, indent=2)[:3000])


if __name__ == '__main__':
    main()
