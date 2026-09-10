"""Why a bought tile is still idle, turn by turn, for the whole game.

The expansion pays late, and the previous attempts to say why were all wrong in the same
way: a window shorter than the effect, or a threshold that tracked the size of the farm
rather than the decision. So this module reads the complete trace to turn 719 and refuses
to summarise a horizon it did not cover.

The categories are derived from what actually gates a PLANT in `agent/planner.py`: the
planner only plants before `plant_until_hour`; it needs seed of the crop in the private
store; seed is bought with cash; the tile has to be reached by a unit, and there are at most
`max_hands` of them plus the farmer; and a tile the planner simply did not select stays
empty while units idle. Each idle tile is attributed to the first of those that binds.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

from experiments.planner_milestones import parameterized_agent, play, replay_frames

import tempfile

TURNS = 719
REASONS = ('OUTSIDE_PLANTING_HOURS', 'NO_SEED_IN_STORE', 'NO_CASH_FOR_SEED',
           'NO_IDLE_UNIT', 'NOT_SELECTED_BY_PLANNER')


def idle_tiles(observation, seat):
    farm = observation['farms'][seat]
    return sum(tile is None for row in farm['tiles'] for tile in row)


def idle_units(observation, action, seat):
    """Units whose verb does nothing this turn: the planner had capacity to spare."""
    verbs = [action.get('farmer') or ['PASS']] + list(action.get('hands') or [])
    return sum(1 for verb in verbs if verb and verb[0] == 'PASS')


def attribute(observation, action, configuration, seat, *, plant_until_hour=18,
              cheapest_seed=None):
    """The first binding constraint on planting one more tile, or None if none binds."""
    if idle_tiles(observation, seat) == 0:
        return None
    if observation['hour'] >= plant_until_hour:
        return 'OUTSIDE_PLANTING_HOURS'
    seeds = observation['private']['seeds']
    if not any(count > 0 for count in seeds.values()):
        money = observation['farms'][seat]['money']
        if cheapest_seed is not None and money < cheapest_seed:
            return 'NO_CASH_FOR_SEED'
        return 'NO_SEED_IN_STORE'
    if idle_units(observation, action, seat) == 0:
        return 'NO_IDLE_UNIT'
    return 'NOT_SELECTED_BY_PLANNER'


def trace(steps, seat, configuration, *, plant_until_hour=18, cheapest_seed=None):
    from agent.economy import CROPS
    if cheapest_seed is None:
        cheapest_seed = min(cost for cost, *_ in CROPS.values())
    reasons, covered = Counter(), 0
    occupancy = []
    for frame in steps:
        record = frame[seat]
        action = record.get('action')
        if not isinstance(action, dict):
            continue
        covered += 1
        reason = attribute(record['observation'], action, configuration, seat,
                           plant_until_hour=plant_until_hour,
                           cheapest_seed=cheapest_seed)
        if reason:
            reasons[reason] += 1
        farm = record['observation']['farms'][seat]
        occupancy.append((sum(isinstance(t, dict) for r in farm['tiles'] for t in r),
                          sum(t != 'LOCKED' for r in farm['tiles'] for t in r)))
    if covered < TURNS:
        raise ValueError(f'trace covers {covered} turns, not the full {TURNS}: a window '
                         'shorter than the effect is how the last four readings went wrong')
    return reasons, occupancy


def fill_times(occupancy, land_turn):
    """Turns from the purchase to each occupancy fraction of the land then owned."""
    if land_turn is None or land_turn >= len(occupancy):
        return {}
    owned = occupancy[min(land_turn + 25, len(occupancy) - 1)][1]
    result = {}
    for fraction in (0.5, 0.8, 1.0):
        target = owned * fraction
        hit = next((turn for turn in range(land_turn, len(occupancy))
                    if occupancy[turn][0] >= target), None)
        result['turns_to_%d_percent' % int(fraction * 100)] = (
            None if hit is None else hit - land_turn)
    return result


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
    agent = parameterized_agent(parameters, tempfile.mkdtemp(prefix='expansion-'))
    totals, rows, failures = Counter(), [], []
    for seed in seeds:
        try:
            replay = play(agent, args.opponent, seed, args.seat)
        except RuntimeError as error:
            failures.append({'seed': seed, 'reason': str(error)})
            continue
        steps = replay_frames(replay)
        configuration = replay.get('configuration') or {'turnsPerDay': 24}
        reasons, occupancy = trace(steps, args.seat, configuration,
                                   plant_until_hour=parameters.get('plant_until_hour', 18))
        land_turn = next((turn for turn, frame in enumerate(steps)
                          if any(order and order[0] == 'BUY_LAND'
                                 for order in (frame[args.seat].get('action') or {})
                                 .get('market') or [])), None)
        totals.update(reasons)
        rows.append({'seed': seed, 'land_turn': land_turn, 'reasons': dict(reasons),
                     **fill_times(occupancy, land_turn)})
    idle_total = sum(totals.values())
    payload = {'schema_version': 1, 'parameters': parameters, 'seeds': args.seeds,
               'worlds': len(rows), 'failures': failures,
               'idle_turns_total': idle_total,
               'share': {key: round(totals[key] / idle_total, 4) for key in REASONS
                         if totals.get(key)},
               'counts': dict(totals), 'rows': rows}
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in payload.items() if k != 'rows'}, indent=2))


if __name__ == '__main__':
    main()
