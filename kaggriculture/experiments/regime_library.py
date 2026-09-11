"""For each early shop configuration, which owned economy is the strongest?

`artifacts/regime_counterfactual_v006.json` settled the 2x2: forcing the sheep economy into
worlds without an early `YARN_STORE` drops the win rate from 0.296 to 0.185, and forcing the
coop economy into worlds that have one drops it from 0.769 to 0.077. The regimes are
genuinely context-dependent, and the mechanism is a demand sink - wool holds at a median of
241 on day 15 when the yarn store opened early and collapses to 1 in 37 of the 54 worlds
where it did not.

That makes the shop configuration the axis a regime library should be indexed on. Our own
episodes cannot supply that library: `v006` produces the same goods in every world, so they
only ever reveal the sink for wool and eggs. Teams that vary their production do reveal it,
and the public daily dumps hold hundreds of their episodes.

This module scans a dump and records, per seat, the first two shops unlocked at the routing
turn, the economy standing at the middle of the game, and what that seat scored. Grouping by
shop configuration and keeping the strong teams answers the question directly, from evidence
rather than from a search.

Nothing here trains or tunes. It reads recorded games and tabulates them, and the output is a
list of candidate regimes with the shop context each is observed to be strong in. Turning a
candidate into a claim needs the same forced counterfactual the yarn regime just went
through.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import statistics
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROUTE_STEP = 144
MIDGAME_STEP = 360
ANIMALS = ('COW', 'SHEEP', 'GOOSE')
STRUCTURES = ('PASTURE', 'COOP')


def _state(observation):
    farm = observation['farms'][observation['player']]
    tiles = [tile for row in farm['tiles'] for tile in row]
    animals = Counter(tile['animal'] for tile in tiles
                      if isinstance(tile, dict) and 'animal' in tile)
    structures = Counter(tile.get('kind') for tile in tiles
                         if isinstance(tile, dict) and tile.get('kind') in STRUCTURES)
    crops = Counter(tile.get('crop') for tile in tiles
                    if isinstance(tile, dict) and tile.get('kind') == 'PLANT')
    return {'cash': farm['money'], 'hands': len(farm['hands']),
            'animals': {kind: animals.get(kind, 0) for kind in ANIMALS},
            'structures': {kind: structures.get(kind, 0) for kind in STRUCTURES},
            'crops': dict(crops), 'crop_tiles': sum(crops.values()),
            'land_used': sum(1 for tile in tiles if tile is not None and tile != 'LOCKED'),
            'prices': dict(observation['market']['prices'])}


def read(reference):
    """One record per seat of one episode, or the reason the episode is unusable."""
    archive, member = reference
    try:
        with zipfile.ZipFile(archive) as source:
            episode = json.loads(source.read(member))
    except Exception as exc:                       # a truncated dump is not evidence
        return {'error': f'{type(exc).__name__}: {exc}'[:120], 'member': member}
    steps = episode.get('steps') or []
    if len(steps) <= MIDGAME_STEP:
        return {'error': 'too few steps', 'member': member}
    info = episode.get('info') or {}
    teams = info.get('TeamNames') or [None, None]
    rewards = episode.get('rewards') or [None, None]
    out = []
    for seat in (0, 1):
        route = steps[ROUTE_STEP][seat].get('observation') or {}
        mid = steps[MIDGAME_STEP][seat].get('observation') or {}
        if 'farms' not in route or 'farms' not in mid:
            continue
        shops = list(route['town']['unlocked_shops'])
        out.append({'episode_id': info.get('EpisodeId'), 'seat': seat,
                    'team': teams[seat], 'opponent_team': teams[1 - seat],
                    'reward': rewards[seat], 'opponent_reward': rewards[1 - seat],
                    'shops_at_route': shops[:2], 'shops_sorted': sorted(shops[:2]),
                    'midgame': _state(mid)})
    return {'seats': out}


def scan(archive, *, workers=6, limit=None, progress=None):
    with zipfile.ZipFile(archive) as source:
        members = [name for name in sorted(source.namelist()) if name.endswith('.json')]
    if limit:
        members = members[:limit]
    references = [(str(archive), member) for member in members]
    rows, failures = [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for position, result in enumerate(pool.map(read, references, chunksize=1), start=1):
            if 'error' in result:
                failures.append(result)
            else:
                rows.extend(result['seats'])
            if progress and position % 25 == 0:
                progress(position, len(references), len(rows))
    return rows, failures


def load_ratings(path):
    ratings = {}
    with open(path, encoding='utf-8-sig') as handle:
        for row in csv.DictReader(handle):
            ratings[row['TeamName']] = float(row['Score'])
    return ratings


def regime(state):
    """A compact name for the economy a seat is running at the middle of the game."""
    animals = state['animals']
    herd = sum(animals.values())
    dominant = max(animals, key=animals.get) if herd else None
    if herd == 0:
        return 'crops_only'
    share = animals[dominant] / herd
    if share < .5:
        return 'mixed_herd'
    return f'{dominant.lower()}_led'


def tabulate(rows, ratings, *, min_rating=2900., min_support=4):
    """Per shop configuration, which regimes the strong teams run and what they score."""
    strong = [row for row in rows
              if row['team'] in ratings and ratings[row['team']] >= min_rating
              and row['reward'] is not None and row['opponent_reward'] is not None]
    by_shop = defaultdict(lambda: defaultdict(list))
    for row in strong:
        key = tuple(row['shops_sorted'])
        by_shop[key][regime(row['midgame'])].append(row)
    out = []
    for shops, regimes in by_shop.items():
        total = sum(len(entries) for entries in regimes.values())
        if total < min_support:
            continue
        variants = []
        for name, entries in sorted(regimes.items(), key=lambda item: -len(item[1])):
            wins = sum(1 for entry in entries if entry['reward'] > entry['opponent_reward'])
            variants.append({
                'regime': name, 'n': len(entries),
                'win_rate': wins / len(entries),
                'reward_median': statistics.median(entry['reward'] for entry in entries),
                'teams': sorted({entry['team'] for entry in entries})[:6],
                'sheep_median': statistics.median(
                    entry['midgame']['animals']['SHEEP'] for entry in entries),
                'cow_median': statistics.median(
                    entry['midgame']['animals']['COW'] for entry in entries),
                'goose_median': statistics.median(
                    entry['midgame']['animals']['GOOSE'] for entry in entries),
                'crop_tiles_median': statistics.median(
                    entry['midgame']['crop_tiles'] for entry in entries)})
        out.append({'shops': list(shops), 'n': total, 'regimes': variants})
    out.sort(key=lambda entry: -entry['n'])
    return {'strong_seats': len(strong), 'min_rating': min_rating, 'configurations': out}


def by_shop_identity(rows, ratings, *, min_rating=2900.):
    """The coarser cut: one shop at a time, which regime the strong field runs beside it."""
    strong = [row for row in rows
              if row['team'] in ratings and ratings[row['team']] >= min_rating
              and row['reward'] is not None and row['opponent_reward'] is not None]
    shops = sorted({shop for row in strong for shop in row['shops_sorted']})
    out = []
    for shop in shops:
        present = [row for row in strong if shop in row['shops_sorted']]
        absent = [row for row in strong if shop not in row['shops_sorted']]
        if len(present) < 6 or len(absent) < 6:
            continue
        entry = {'shop': shop, 'present': len(present), 'absent': len(absent),
                 'regimes_present': Counter(regime(row['midgame']) for row in present).most_common(4),
                 'regimes_absent': Counter(regime(row['midgame']) for row in absent).most_common(4)}
        for animal in ANIMALS:
            entry[f'{animal.lower()}_present_median'] = statistics.median(
                row['midgame']['animals'][animal] for row in present)
            entry[f'{animal.lower()}_absent_median'] = statistics.median(
                row['midgame']['animals'][animal] for row in absent)
        out.append(entry)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', action='append', required=True,
                        help='a Kaggle daily episode dump; may be repeated')
    parser.add_argument('--leaderboard', required=True)
    parser.add_argument('--min-rating', type=float, default=2900.)
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--rows', help='also write the raw per-seat rows here')
    parser.add_argument('--output', required=True)
    arguments = parser.parse_args()

    def progress(position, total, kept):
        print(f'  {position}/{total} episodes, {kept} seats', file=sys.stderr, flush=True)

    rows, failures = [], []
    for archive in arguments.archive:
        got, bad = scan(archive, workers=arguments.workers, limit=arguments.limit,
                        progress=progress)
        rows.extend(got)
        failures.extend(bad)
    ratings = load_ratings(arguments.leaderboard)
    report = {'seats': len(rows), 'failures': len(failures),
              'by_configuration': tabulate(rows, ratings, min_rating=arguments.min_rating),
              'by_shop_identity': by_shop_identity(rows, ratings,
                                                   min_rating=arguments.min_rating)}
    Path(arguments.output).parent.mkdir(parents=True, exist_ok=True)
    Path(arguments.output).write_text(json.dumps(report, indent=1, default=str))
    if arguments.rows:
        Path(arguments.rows).write_text(json.dumps(rows, default=str))
    print(json.dumps({'seats': report['seats'], 'failures': report['failures'],
                      'strong_seats': report['by_configuration']['strong_seats'],
                      'by_shop_identity': report['by_shop_identity']},
                     indent=1, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
