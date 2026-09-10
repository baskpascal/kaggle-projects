"""What the planner buys, what it already had, and what a tile was waiting for instead.

`NO_SEED_IN_STORE` at 18% of idle tile-turns and a reserve that removes forty BUY_PRODUCT
orders are two measured facts that sit next to each other. They are not evidence of a common
mechanism, and this module exists to keep them apart: it reports spend per SKU beside the
stock that already covered it, the endogenous production that would have arrived anyway, and
the tile-turns blocked for want of seed in the same interval.

Nothing here concludes. It measures the quantities a procurement ablation would have to move.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics
import tempfile

from experiments.expansion_telemetry import TURNS, attribute
from experiments.planner_milestones import parameterized_agent, play, replay_frames

# One animal yields one FERTILIZER per day, so a herd covers part of what we buy.
FERTILIZER_PER_ANIMAL_PER_DAY = 1


def _held(observation):
    private = observation['private']
    held = Counter(private['shed'])
    for inventory in private['inventories']:
        held.update(inventory)
    return held


def _animals(observation, seat):
    farm = observation['farms'][seat]
    return sum(isinstance(tile, dict) and 'animal' in tile
               for row in farm['tiles'] for tile in row)


def purchases(steps, seat, *, coverage_horizon=72):
    """Every purchase, with the stock and endogenous supply that already covered it."""
    rows = []
    for turn, frame in enumerate(steps):
        record = frame[seat]
        action = record.get('action')
        if not isinstance(action, dict):
            continue
        observation = record['observation']
        held = _held(observation)
        animals = _animals(observation, seat)
        for order in action.get('market') or []:
            if not order or order[0] not in ('BUY_PRODUCT', 'BUY_SEED'):
                continue
            item, count = order[1], (order[2] if len(order) > 2 else 1)
            end = min(turn + coverage_horizon, len(steps) - 1)
            future = _held(steps[end][seat]['observation'])
            endogenous = (animals * FERTILIZER_PER_ANIMAL_PER_DAY * (coverage_horizon // 24)
                          if item == 'FERTILIZER' else 0)
            rows.append({
                'turn': turn, 'kind': order[0], 'item': item, 'count': count,
                'stock_before': held.get(item, 0),
                'stock_after_horizon': future.get(item, 0),
                'endogenous_expected': endogenous,
                'held_at_least_as_much': held.get(item, 0) >= count,
                'covered_by_endogenous': endogenous >= count,
            })
    return rows


def seed_blocked(steps, seat, configuration, *, plant_until_hour=18):
    """Tile-turns idle specifically for want of seed, and where they fall in the game."""
    turns = []
    for turn, frame in enumerate(steps):
        record = frame[seat]
        action = record.get('action')
        if not isinstance(action, dict):
            continue
        if attribute(record['observation'], action, configuration, seat,
                     plant_until_hour=plant_until_hour) == 'NO_SEED_IN_STORE':
            turns.append(turn)
    return turns


def productive_tile_turns(steps, seat):
    """The integral of productive capacity, not its value at one arbitrary turn."""
    total = 0
    for frame in steps:
        farm = frame[seat]['observation']['farms'][seat]
        total += sum(isinstance(tile, dict) for row in farm['tiles'] for tile in row)
    return total


def report(steps, seat, configuration, *, plant_until_hour=18):
    if len(steps) < TURNS:
        raise ValueError(f'trace covers {len(steps)} turns, not the full {TURNS}')
    rows = purchases(steps, seat)
    blocked = seed_blocked(steps, seat, configuration,
                           plant_until_hour=plant_until_hour)
    by_item = defaultdict(lambda: {'orders': 0, 'units': 0, 'held_at_least_as_much': 0,
                                   'never_consumed': 0, 'covered_by_endogenous': 0})
    for row in rows:
        bucket = by_item[row['item']]
        bucket['orders'] += 1
        bucket['units'] += row['count']
        bucket['held_at_least_as_much'] += int(row['held_at_least_as_much'])
        bucket['never_consumed'] += int(
            row['stock_after_horizon'] >= row['stock_before'] + row['count'])
        bucket['covered_by_endogenous'] += int(row['covered_by_endogenous'])
    # `covered_by_stock` marks a top-up as a duplicate: the planner buys WHEAT to reach a
    # feed target, so holding four and ordering two trips it while nothing is wasted. The
    # checkable sense of redundant is that the units were never consumed - the store still
    # holds at least what it held plus what was bought, a horizon later.
    redundant = [row for row in rows
                 if row['stock_after_horizon'] >= row['stock_before'] + row['count']]
    blocked_during = [turn for turn in blocked
                      if any(abs(turn - row['turn']) <= 24 for row in redundant)]
    return {
        'purchase_orders': len(rows),
        'by_item': {k: dict(v) for k, v in sorted(by_item.items())},
        'redundant_orders': len(redundant),
        'redundant_units': sum(row['count'] for row in redundant),
        'seed_blocked_tile_turns': len(blocked),
        'seed_blocked_within_a_day_of_a_redundant_buy': len(blocked_during),
        'productive_tile_turns': productive_tile_turns(steps, seat),
        'terminal_cash': steps[-1][seat]['observation']['farms'][seat]['money'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parameters', required=True)
    parser.add_argument('--opponent', required=True)
    parser.add_argument('--seeds', default='1000:1010')
    parser.add_argument('--seat', type=int, default=0)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    start, _, end = args.seeds.partition(':')
    seeds = list(range(int(start), int(end))) if end else [int(start)]
    parameters = json.loads(args.parameters)
    agent = parameterized_agent(parameters, tempfile.mkdtemp(prefix='procurement-'))
    rows, failures = [], []
    for seed in seeds:
        try:
            replay = play(agent, args.opponent, seed, args.seat)
        except RuntimeError as error:
            failures.append({'seed': seed, 'reason': str(error)})
            continue
        steps = replay_frames(replay)
        configuration = replay.get('configuration') or {'turnsPerDay': 24}
        rows.append({'seed': seed, **report(
            steps, args.seat, configuration,
            plant_until_hour=parameters.get('plant_until_hour', 18))})

    def med(key):
        values = [row[key] for row in rows if isinstance(row.get(key), (int, float))]
        return int(statistics.median(values)) if values else None
    items = sorted({item for row in rows for item in row['by_item']})
    summary = {
        'worlds': len(rows),
        'median': {key: med(key) for key in (
            'purchase_orders', 'redundant_orders', 'redundant_units',
            'seed_blocked_tile_turns', 'seed_blocked_within_a_day_of_a_redundant_buy',
            'productive_tile_turns', 'terminal_cash')},
        'units_by_item_median': {
            item: int(statistics.median([row['by_item'].get(item, {}).get('units', 0)
                                         for row in rows])) for item in items},
        'orders_never_consumed_median': {
            item: int(statistics.median(
                [row['by_item'].get(item, {}).get('never_consumed', 0)
                 for row in rows])) for item in items},
    }
    payload = {'schema_version': 1, 'parameters': parameters, 'seeds': args.seeds,
               'failures': failures, 'summary': summary, 'rows': rows}
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
