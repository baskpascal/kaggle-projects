"""Which decisions are adaptive, as opposed to merely variable across the elite field.

Aggregate spread across teams is not evidence of adaptation: one team that always expands at
turn 70 and another that always expands at turn 200 produce a large elite IQR while both are
fixed policies. This module therefore separates within-team variation from between-team
variation, and reports the probability that a decision happens at all separately from its
timing when it does - folding "never bought a goose" into turn 719 manufactures dispersion
that is not there.

State-explained variation is the third filter and is not computed here: it needs the
observable state captured at each milestone, which the extractor records only for the
candidates promoted out of this table.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics


def _spread(values):
    present = sorted(values)
    if len(present) < 2:
        return None
    lower = present[len(present) // 4]
    upper = present[(3 * len(present)) // 4]
    return {'n': len(present), 'median': present[len(present) // 2],
            'iqr': upper - lower, 'min': present[0], 'max': present[-1]}


def table(rows):
    keys = sorted({key for row in rows for side in ('v006_milestones', 'elite_milestones')
                   for key in row[side]})
    by_team = defaultdict(lambda: defaultdict(list))
    for row in rows:
        for key, turn in row['elite_milestones'].items():
            by_team[row['team']][key].append(turn)
    result = []
    for key in keys:
        elite_worlds = [row for row in rows if key in row['elite_milestones']]
        ours_worlds = [row for row in rows if key in row['v006_milestones']]
        # Within-team: the spread of one team's own episodes, pooled across teams that
        # contributed at least two. This is the only spread that can show live adaptation.
        within = []
        teams_with_support = 0
        for team, decisions in by_team.items():
            turns = decisions.get(key, [])
            if len(turns) < 2:
                continue
            teams_with_support += 1
            within.append(max(turns) - min(turns))
        between = _spread([statistics.median(decisions[key])
                           for decisions in by_team.values() if decisions.get(key)])
        result.append({
            'decision': key,
            'elite_occurrence_rate': round(len(elite_worlds) / len(rows), 3),
            'v006_occurrence_rate': round(len(ours_worlds) / len(rows), 3),
            'elite_turn_when_it_occurs': _spread(
                [row['elite_milestones'][key] for row in elite_worlds]),
            'v006_turn_when_it_occurs': _spread(
                [row['v006_milestones'][key] for row in ours_worlds]),
            'within_team_range_median': (int(statistics.median(within)) if within
                                         else None),
            'between_team_spread': between,
            'support': {'worlds': len(elite_worlds),
                        'teams': len({row['team'] for row in elite_worlds}),
                        'teams_with_two_or_more_episodes': teams_with_support},
        })
    return result


def rigidity_candidates(rows_table, *, min_teams=3):
    """Decisions where we are fixed, the elite moves inside a team, and support exists."""
    candidates = []
    for row in rows_table:
        ours = row['v006_turn_when_it_occurs']
        within = row['within_team_range_median']
        if within is None or row['support']['teams_with_two_or_more_episodes'] < min_teams:
            continue
        our_iqr = ours['iqr'] if ours else 0
        if within > max(2, our_iqr * 2):
            candidates.append({
                'decision': row['decision'],
                'within_team_range_median': within,
                'v006_iqr': our_iqr,
                'v006_occurrence_rate': row['v006_occurrence_rate'],
                'elite_occurrence_rate': row['elite_occurrence_rate'],
                'support': row['support'],
            })
    return sorted(candidates, key=lambda row: -row['within_team_range_median'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--divergences', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    payload = json.loads(Path(args.divergences).read_text(encoding='utf-8'))
    rows = payload['rows']
    built = table(rows)
    result = {'schema_version': 1, 'source': args.divergences, 'worlds': len(rows),
              'teams': len({row['team'] for row in rows}),
              'table': built, 'candidates': rigidity_candidates(built)}
    Path(args.output).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'table'}, indent=2)[:2000])


if __name__ == '__main__':
    main()
