"""Compress a strong economy into the few numbers a macro genome would have to carry.

Elite episodes are data, not implementations. What we need from them is not their actions but
the shape of the economy those actions produced: how much land, how big a herd of which
species, how many crops, how many workers, and when each of those arrived. Everything is read
from engine state - tiles, quadrants, hands, cash - because a requested order is not an
executed one and this project has been wrong about that repeatedly.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import zipfile

from experiments.elite_events import executed_milestones
from experiments.top_panel import admissible, load_leaderboard

PHASES = (0, 96, 192, 288, 384, 480, 576, 719)
CROP_KINDS = ('WHEAT', 'CARROT', 'TOMATO', 'STRAWBERRY', 'MELON')
ANIMAL_KINDS = ('COW', 'SHEEP', 'GOOSE')


def snapshot(observation, seat):
    farm = observation['farms'][seat]
    tiles = [tile for row in farm['tiles'] for tile in row]
    crops = Counter(t['crop'] for t in tiles if isinstance(t, dict) and 'crop' in t)
    animals = Counter(t['animal'] for t in tiles if isinstance(t, dict) and 'animal' in t)
    structures = Counter(t.get('kind') for t in tiles
                         if isinstance(t, dict) and t.get('kind') in ('PASTURE', 'COOP'))
    return {
        'cash': farm['money'],
        'quadrants': len(farm.get('unlocked_quadrants') or []),
        'hands': len(farm.get('hands') or []),
        'owned': sum(t != 'LOCKED' for t in tiles),
        'free': sum(t is None for t in tiles),
        'crops': {k: crops.get(k, 0) for k in CROP_KINDS},
        'crops_total': sum(crops.values()),
        'animals': {k: animals.get(k, 0) for k in ANIMAL_KINDS},
        'structures': {k: structures.get(k, 0) for k in ('PASTURE', 'COOP')},
    }


def fingerprint(steps, seat):
    profile = {str(turn): snapshot(steps[min(turn, len(steps) - 1)][seat]['observation'], seat)
               for turn in PHASES}
    return {'phases': profile, 'milestones': executed_milestones(steps, seat),
            'terminal_cash': profile[str(PHASES[-1])]['cash']}


def genome(record):
    """The dimensions a macro plan would actually have to specify."""
    phases = record['phases']
    peak = max(phases.values(), key=lambda p: sum(p['animals'].values()))
    late = phases['576']
    mid = phases['288']
    milestones = record['milestones']
    return {
        'land1': milestones.get('land1'), 'land2': milestones.get('land2'),
        'quadrants': late['quadrants'],
        'cows': peak['animals']['COW'], 'sheep': peak['animals']['SHEEP'],
        'geese': peak['animals']['GOOSE'],
        'pasture': late['structures']['PASTURE'], 'coop': late['structures']['COOP'],
        'hands_mid': mid['hands'], 'hands_late': late['hands'],
        'crops_mid': mid['crops_total'], 'crops_late': late['crops_total'],
        'crop_mix_mid': max(mid['crops'], key=mid['crops'].get) if mid['crops_total'] else None,
        'first_cow': milestones.get('first_COW'), 'first_sheep': milestones.get('first_SHEEP'),
        'first_goose': milestones.get('first_GOOSE'),
        'cash_192': phases['192']['cash'], 'cash_384': phases['384']['cash'],
        'terminal_cash': record['terminal_cash'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', required=True)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--leaderboard', required=True)
    parser.add_argument('--min-rating', type=float, default=2950.)
    parser.add_argument('--per-team', type=int, default=6)
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

    records, failures = [], []
    with zipfile.ZipFile(args.archive) as source:
        for row in chosen:
            try:
                episode = json.loads(source.read(f"{row['episode']}.json"))
                record = fingerprint(episode['steps'], int(row['seat']))
            except Exception as error:                               # noqa: BLE001
                failures.append({'episode': row['episode'],
                                 'reason': f'{type(error).__name__}: {error}'})
                continue
            records.append({'episode': row['episode'], 'team': row['team'],
                            'rank': row['rank'], 'rating': row['rating'],
                            'money': row['money'], **record, 'genome': genome(record)})

    by_team = {}
    for record in records:
        by_team.setdefault(record['team'], []).append(record)
    profiles = []
    for team, batch in sorted(by_team.items(), key=lambda kv: kv[1][0]['rank']):
        keys = batch[0]['genome']
        summary = {}
        for key in keys:
            values = [r['genome'][key] for r in batch if isinstance(r['genome'][key], (int, float))]
            summary[key] = int(statistics.median(values)) if values else batch[0]['genome'][key]
        profiles.append({'team': team, 'rank': batch[0]['rank'], 'rating': batch[0]['rating'],
                         'worlds': len(batch), 'genome': summary})
    payload = {'schema_version': 1, 'min_rating': args.min_rating,
               'teams': len(by_team), 'worlds': len(records), 'refused': refused,
               'failures': failures, 'profiles': profiles, 'rows': records}
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps({'teams': len(by_team), 'worlds': len(records),
                      'failures': len(failures)}, indent=2))


if __name__ == '__main__':
    main()
