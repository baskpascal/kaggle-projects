"""Whether an expansion is converted into production, not merely bought earlier.

Buying land sooner can look elite and still hurt: an empty quadrant is capital spent on
tiles the executor cannot fill. So the timing of the purchase is reported beside what
happened to the land afterwards - how long it took to become productive, and what revenue
followed it - and never on its own.
"""
import argparse
import json
from pathlib import Path
import statistics

from experiments.elite_events import canonical_turn
from experiments.planner_milestones import parameterized_agent, play, replay_frames

import tempfile


def _productive(observation, seat):
    """Tiles carrying a crop, an animal or a structure: land actually put to work."""
    farm = observation['farms'][seat]
    return sum(isinstance(tile, dict) for row in farm['tiles'] for tile in row)


def _owned(observation, seat):
    farm = observation['farms'][seat]
    return sum(tile != 'LOCKED' for row in farm['tiles'] for tile in row)


def _cash(observation, seat):
    return observation['farms'][seat]['money']


def land_report(steps, seat, configuration, *, fill_horizon=200):
    """The first expansion, and what the farm did with it."""
    purchases = []
    for turn, frame in enumerate(steps):
        action = frame[seat].get('action')
        if not isinstance(action, dict):
            continue
        for order in action.get('market') or []:
            if order and order[0] == 'BUY_LAND':
                purchases.append(turn)
    if not purchases:
        return {'bought': False}
    first = purchases[0]
    # `turns[0]` is step 1, so the frame carrying the order is already the world in which
    # it resolves. The state the decision was taken against is the frame before it.
    before = steps[max(0, first - 1)][seat]['observation']
    # A BUY_LAND order does not resolve on the next callback, so reading the tile count
    # one turn later reports no new land at all and makes the fill look instantaneous.
    # Take the largest ownership seen in the day that follows the purchase instead.
    owned_before = _owned(before, seat)
    window = steps[first:min(first + 26, len(steps))]
    owned_after = max(_owned(frame[seat]['observation'], seat) for frame in window)
    if owned_after <= owned_before:
        return {'bought': True, 'purchases': len(purchases), 'turn_land1': first,
                'unresolved': True}
    target = _productive(before, seat) + (owned_after - owned_before)
    fill_turn = None
    for turn in range(first, min(first + fill_horizon, len(steps))):
        if _productive(steps[turn][seat]['observation'], seat) >= target:
            fill_turn = turn
            break

    def cash_at(turn):
        index = min(turn, len(steps) - 1)
        return _cash(steps[index][seat]['observation'], seat)

    return {
        'bought': True,
        'purchases': len(purchases),
        'turn_land1': first,
        'turn_land2': purchases[1] if len(purchases) > 1 else None,
        # Same frame rule as `before`: the pre-decision cash is the turn before the one
        # carrying the order, because that frame already reflects the resolved purchase.
        'cash_before_land1': cash_at(max(0, first - 1)),
        'cash_after_land1': cash_at(first),
        'productive_tiles_before': _productive(before, seat),
        'productive_tiles_after_50': _productive(
            steps[min(first + 50, len(steps) - 1)][seat]['observation'], seat),
        'owned_before': owned_before,
        'owned_after': owned_after,
        'time_to_fill_new_land': None if fill_turn is None else fill_turn - first,
        'cash_delta_50': cash_at(first + 50) - cash_at(first),
        'cash_delta_100': cash_at(first + 100) - cash_at(first),
        'terminal_cash': _cash(steps[-1][seat]['observation'], seat),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parameters', required=True)
    parser.add_argument('--opponent', required=True)
    parser.add_argument('--seeds', default='1000:1020')
    parser.add_argument('--seat', type=int, default=0)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    start, _, end = args.seeds.partition(':')
    seeds = list(range(int(start), int(end))) if end else [int(start)]
    parameters = json.loads(args.parameters)
    agent = parameterized_agent(parameters, tempfile.mkdtemp(prefix='land-diag-'))
    rows, failures = [], []
    for seed in seeds:
        try:
            replay = play(agent, args.opponent, seed, args.seat)
        except RuntimeError as error:
            failures.append({'seed': seed, 'reason': str(error)})
            continue
        steps = replay_frames(replay)
        configuration = replay.get('configuration') or {'turnsPerDay': 24}
        rows.append({'seed': seed, **land_report(steps, args.seat, configuration)})

    bought = [row for row in rows if row.get('bought')]
    def med(key):
        values = [row[key] for row in bought if row.get(key) is not None]
        return int(statistics.median(values)) if values else None
    summary = {
        'worlds': len(rows), 'worlds_that_expanded': len(bought),
        'median': {key: med(key) for key in (
            'turn_land1', 'turn_land2', 'cash_before_land1', 'cash_after_land1',
            'productive_tiles_before', 'productive_tiles_after_50',
            'time_to_fill_new_land', 'cash_delta_50', 'cash_delta_100',
            'terminal_cash')},
        'never_filled': sum(row.get('time_to_fill_new_land') is None for row in bought),
    }
    payload = {'schema_version': 1, 'parameters': parameters, 'opponent': args.opponent,
               'seeds': args.seeds, 'failures': failures, 'summary': summary,
               'rows': rows}
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
