"""A per-day economic timeline for each archived ladder world, extracted once.

`docs/ladder-cohort-v006.json` split the 2600-2800 band into worlds we lose by more than a
thousand coins and worlds decided by less. Comparing those two populations needs a table,
not a replay: 67 episodes of 32 MB each is far too much to re-read for every question, and
the questions are all about the same handful of daily quantities.

So this module makes one pass over the archive and writes a compact timeline per world.
Parsing is not reimplemented - `elite_events.summarize_state` already produces the
observable state a capital decision is taken against, and `elite_events.canonical_turn`
already knows that `step` is absent for seat 1 in recorded dumps - this module only walks
the days and differences the state.

One rule governs how flows are derived. Market *orders* are requests, and a refused order
is indistinguishable from a granted one in the action stream, so nothing here counts an
order. Purchases, sales and production are read as **state transitions**: stock is shed
plus everything carried, a fall in stock alongside a rise in cash is a sale, land and
animals are counted from tiles. Only `PASS` is read from the action stream, because a pass
cannot be refused.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import zipfile

from experiments.elite_events import canonical_turn, summarize_state

ROOT = Path(__file__).resolve().parents[1]
TURNS = 719


def _stock(state):
    total = dict(state['shed'])
    for product, units in state['carried'].items():
        total[product] = total.get(product, 0) + units
    return total


def _crops(observation):
    me = observation['farms'][observation['player']]
    return sum(1 for row in me['tiles'] for tile in row
               if isinstance(tile, dict) and tile.get('kind') == 'PLANT')


def _passes(action):
    farmer = (action or {}).get('farmer') or []
    idle_hands = sum(1 for job in ((action or {}).get('hands') or []) if not job)
    return (1 if farmer and farmer[0] == 'PASS' else 0), idle_hands


def timeline(steps, seat, configuration):
    """One row per day, sampled at the day's first hour, with the day's flows attached."""
    days, previous = [], None
    passes = idle_hands = turns_in_day = 0
    for index in range(1, len(steps)):
        entry = steps[index][seat]
        observation = entry.get('observation') or {}
        if 'farms' not in observation:
            continue
        state = summarize_state(observation, configuration)
        farmer_pass, hands_idle = _passes(entry.get('action'))
        passes += farmer_pass
        idle_hands += hands_idle
        turns_in_day += 1
        if state['hour'] != 0:
            continue
        stock = _stock(state)
        row = {'day': state['day'], 'turn': state['turn'], 'cash': state['cash'],
               'hands': state['hands'], 'land_owned': state['land_owned'],
               'land_used': state['land_used'], 'crops': _crops(observation),
               'animals': dict(state['animals']),
               'herd': sum(state['animals'].values()),
               'structures': dict(state['structures']),
               'stock': stock, 'stock_units': sum(stock.values()),
               'seeds': dict(state['seeds']),
               'known_shops': len(state['known_shops']),
               'market_prices': dict(state['market_prices']),
               'market_inventory': dict(state['market_inventory']),
               'opponent_cash': state['opponent_public']['cash'],
               'opponent_land_used': state['opponent_public']['land_used'],
               'opponent_hands': state['opponent_public']['hands'],
               'passes': passes, 'idle_hands': idle_hands, 'turns': turns_in_day}
        if previous is not None:
            row['delta_cash'] = row['cash'] - previous['cash']
            row['delta_land'] = row['land_owned'] - previous['land_owned']
            row['delta_herd'] = row['herd'] - previous['herd']
            row['delta_crops'] = row['crops'] - previous['crops']
            products = set(stock) | set(previous['stock'])
            flows = {product: stock.get(product, 0) - previous['stock'].get(product, 0)
                     for product in products}
            row['stock_flow'] = {p: v for p, v in flows.items() if v}
            # A fall in stock with a rise in cash is the only sale evidence that cannot be
            # confused with a refused order.
            row['units_out'] = sum(-v for v in flows.values() if v < 0)
            row['units_in'] = sum(v for v in flows.values() if v > 0)
        else:
            row.update(delta_cash=0, delta_land=0, delta_herd=0, delta_crops=0,
                       stock_flow={}, units_out=0, units_in=0)
        days.append(row)
        previous = row
        passes = idle_hands = turns_in_day = 0
    return days


def extract(archive, worlds, *, limit=None, progress=None):
    """One timeline per world, keyed by episode id."""
    archive = Path(archive)
    chosen = worlds[:limit] if limit else worlds
    out = {}
    with zipfile.ZipFile(archive) as source:
        members = {int(Path(name).stem): name for name in source.namelist()
                   if name.endswith('.json')}
        for position, world in enumerate(chosen, start=1):
            identifier = world['episode_id']
            episode = json.loads(source.read(members[identifier]))
            steps = episode['steps']
            configuration = episode['configuration']
            seat = world['seat']
            rows = timeline(steps, seat, configuration)
            opening = steps[1][seat].get('observation') or {}
            out[identifier] = {
                'episode_id': identifier, 'seat': seat,
                'seed': (episode.get('info') or {}).get('seed'),
                'configuration_seed': configuration.get('seed'),
                'engine_version': episode.get('module_version'),
                'statuses': episode.get('statuses'),
                'opponent_team': world.get('opponent_team'),
                'opponent_rating': world.get('opponent_rating'),
                'ladder_margin': world.get('ladder_margin'),
                'ladder_our_money': world.get('ladder_our_money'),
                'ladder_opponent_money': world.get('ladder_opponent_money'),
                'opening_turn0': summarize_state(opening, configuration) if opening.get('farms') else None,
                'days': rows}
            if progress:
                progress(position, len(chosen), identifier)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', default='docs/ladder-cohort-v006.json')
    parser.add_argument('--archive', default='data/kaggle/ladder/replays-56125200.zip')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--output', required=True)
    arguments = parser.parse_args()

    worlds = json.loads(Path(arguments.cohort).read_text())['worlds']

    def progress(position, total, identifier):
        print(f'  timeline {position}/{total}  episode {identifier}',
              file=sys.stderr, flush=True)

    out = extract(arguments.archive, worlds, limit=arguments.limit, progress=progress)
    Path(arguments.output).parent.mkdir(parents=True, exist_ok=True)
    Path(arguments.output).write_text(json.dumps(out, default=str))
    print(f'{len(out)} timelines -> {arguments.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
