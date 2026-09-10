"""Extract the strategic decisions of elite streams and where our planner disagrees.

The trajectory corpus is the wrong dataset for the question that matters now: our
artifact beats every pinned public agent and still sits far below the paid band, so
imitating more trajectories cannot raise resolution. What separates a ~2500 agent from a
~2950 one is a small number of capital decisions, and the ~700 turns where both sides are
merely walking, harvesting or repeating execution carry no information about them.

This module keeps only the decisive events - the first land purchase, each expansion, the
first animal of a species, a species-mix change, a structure build, a hiring burst, a newly
revealed shop, an unusually large sale and the start of terminal liquidation - together
with the observable state immediately before each one. It then asks our own planner what it
would emit at that same state.

The planner is invoked on the recorded observation for that turn and nothing else, so no
future information from the replay can reach it. `observation_digest` records exactly what
was fed in, and a decision whose digest does not match its recorded turn is refused rather
than reported.
"""
import argparse
import csv
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import zipfile

from agent.planner import policy
from agent.params import DEFAULTS
from experiments.top_panel import admissible, load_leaderboard

SCHEMA = 1
ANIMAL_KINDS = ('COW', 'SHEEP', 'GOOSE')
STRUCTURES = ('COOP', 'PASTURE')


def observation_digest(observation):
    payload = json.dumps(observation, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def canonical_turn(observation, configuration):
    """The clock the engine agrees on. `step` is absent for seat 1 in recorded dumps
    (forum topic 737545), so day/hour is the authority and `step` only has to agree."""
    turns = configuration.get('turnsPerDay', 24)
    turn = observation['day'] * turns + observation['hour']
    supplied = observation.get('step')
    if supplied is not None and supplied != turn:
        raise ValueError(f'step {supplied} disagrees with day/hour clock {turn}')
    return turn


def _tiles(observation):
    me = observation['farms'][observation['player']]
    return [tile for row in me['tiles'] for tile in row]


def summarize_state(observation, configuration):
    """The observable features a capital decision is actually taken against."""
    me = observation['farms'][observation['player']]
    them = observation['farms'][1 - observation['player']]
    tiles = _tiles(observation)
    private = observation['private']
    animals = Counter(t['animal'] for t in tiles
                      if isinstance(t, dict) and 'animal' in t)
    structures = Counter(t.get('kind') for t in tiles
                         if isinstance(t, dict) and t.get('kind') in STRUCTURES)
    shed = dict(private['shed'])
    carried = Counter()
    for inventory in private['inventories']:
        carried.update(inventory)
    return {
        'turn': canonical_turn(observation, configuration),
        'day': observation['day'],
        'hour': observation['hour'],
        'cash': me['money'],
        'hands': len(me['hands']),
        'hires_today': me['hires_today'],
        'known_shops': list(observation['town']['unlocked_shops']),
        'market_prices': dict(observation['market']['prices']),
        'market_inventory': dict(observation['market']['inventory']),
        'land_owned': sum(t != 'LOCKED' for t in tiles),
        'land_used': sum(t is not None and t != 'LOCKED' for t in tiles),
        'animals': {k: animals.get(k, 0) for k in ANIMAL_KINDS},
        'structures': {k: structures.get(k, 0) for k in STRUCTURES},
        'shed': shed,
        'carried': dict(carried),
        'seeds': dict(private['seeds']),
        'opponent_public': {'cash': them['money'], 'hands': len(them['hands']),
                            'land_used': sum(t is not None and t != 'LOCKED'
                                             for row in them['tiles'] for t in row)},
    }


def strategic_events(steps, seat, configuration, *, hire_burst=3, large_sale_units=25):
    """Decisive events in one recorded stream, each with the state that preceded it."""
    events = []
    seen_animal, seen_shops, land_index = set(), None, 0
    last_day, hires_in_day = None, 0
    terminal_seen = False
    total_turns = len(steps)
    for turn, frame in enumerate(steps):
        record = frame[seat]
        observation, action = record['observation'], record.get('action')
        if not isinstance(action, dict):
            continue
        state = summarize_state(observation, configuration)
        if state['day'] != last_day:
            last_day, hires_in_day = state['day'], 0
        shops = tuple(observation['town']['unlocked_shops'])
        if seen_shops is not None and shops != seen_shops:
            events.append(('SHOP_REVEAL', {'revealed': [s for s in shops
                                                        if s not in seen_shops]}, state))
        seen_shops = shops

        unit_actions = [action.get('farmer') or []] + list(action.get('hands') or [])
        for verb in unit_actions:
            if verb and str(verb[0]).startswith('BUILD_'):
                events.append(('BUILD', {'kind': str(verb[0])[6:]}, state))

        for order in action.get('market') or []:
            if not order:
                continue
            kind = order[0]
            if kind == 'BUY_LAND':
                land_index += 1
                events.append(('BUY_LAND', {'expansion_index': land_index}, state))
            elif kind == 'BUY_ANIMAL':
                species, count = order[1], (order[2] if len(order) > 2 else 1)
                first = species not in seen_animal
                seen_animal.add(species)
                events.append(('BUY_ANIMAL',
                               {'species': species, 'count': count,
                                'first_of_species': first,
                                'mix_switch': first and len(seen_animal) > 1}, state))
            elif kind == 'HIRE':
                hires_in_day += 1
                if hires_in_day == hire_burst:
                    events.append(('HIRE_BURST', {'hires_today': hires_in_day}, state))
            elif kind == 'SELL' and len(order) > 2 and (order[2] or 0) >= large_sale_units:
                terminal = turn >= total_turns - configuration.get('turnsPerDay', 24)
                label = 'TERMINAL_LIQUIDATION' if terminal else 'LARGE_SALE'
                if label == 'TERMINAL_LIQUIDATION':
                    if terminal_seen:
                        continue
                    terminal_seen = True
                events.append((label, {'item': order[1], 'count': order[2]}, state))
    return events


def executed_milestones(steps, seat):
    """Macro milestones read from state transitions, never from issued orders.

    A market order is a request. The engine refuses it when the cash is short or the asset
    is capped, and a refused BUY_LAND looks exactly like a bought one in the action stream.
    Counting requests made a team that attempts expansion seven times and succeeds twice
    look adaptive, and made its first expansion look like turn 2 instead of 151. Quadrants,
    animals on tiles and structures are the ledger the engine actually keeps.
    """
    milestones, previous = {}, None
    for turn, frame in enumerate(steps):
        farm = frame[seat]['observation']['farms'][seat]
        tiles = [tile for row in farm['tiles'] for tile in row]
        current = {
            'quadrants': len(farm.get('unlocked_quadrants') or []),
            'hands': len(farm.get('hands') or []),
        }
        for name in ANIMAL_KINDS:
            current['animal_' + name] = sum(
                isinstance(tile, dict) and tile.get('animal') == name for tile in tiles)
        for kind in STRUCTURES:
            current['structure_' + kind] = sum(
                isinstance(tile, dict) and tile.get('kind') == kind for tile in tiles)
        if previous is not None:
            if current['quadrants'] > previous['quadrants']:
                index = current['quadrants'] - 1
                milestones.setdefault('land%d' % index, turn)
            for name in ANIMAL_KINDS:
                key = 'animal_' + name
                if current[key] > 0 and previous[key] == 0:
                    milestones.setdefault('first_' + name, turn)
            for kind in STRUCTURES:
                key = 'structure_' + kind
                if current[key] > previous[key]:
                    milestones.setdefault('build_' + kind, turn)
        previous = current
    return milestones


def planner_intent(observation, configuration, parameters=None):
    """What our planner emits at exactly this recorded state, and nothing else."""
    action = policy(observation, configuration, parameters)
    macro = {'buy_land': 0, 'buy_animal': {}, 'hire': 0, 'build': [], 'sell_units': 0}
    for order in action.get('market') or []:
        if not order:
            continue
        if order[0] == 'BUY_LAND':
            macro['buy_land'] += 1
        elif order[0] == 'BUY_ANIMAL':
            macro['buy_animal'][order[1]] = (macro['buy_animal'].get(order[1], 0)
                                             + (order[2] if len(order) > 2 else 1))
        elif order[0] == 'HIRE':
            macro['hire'] += 1
        elif order[0] == 'SELL' and len(order) > 2:
            macro['sell_units'] += order[2] or 0
    for verb in [action.get('farmer') or []] + list(action.get('hands') or []):
        if verb and str(verb[0]).startswith('BUILD_'):
            macro['build'].append(str(verb[0])[6:])
    return macro


def agrees(kind, detail, macro):
    """Whether our planner takes the same macro decision the elite took here."""
    if kind == 'BUY_LAND':
        return macro['buy_land'] > 0
    if kind == 'BUY_ANIMAL':
        return macro['buy_animal'].get(detail['species'], 0) > 0
    if kind == 'HIRE_BURST':
        return macro['hire'] > 0
    if kind == 'BUILD':
        return detail['kind'] in macro['build']
    if kind in ('LARGE_SALE', 'TERMINAL_LIQUIDATION'):
        return macro['sell_units'] > 0
    return None            # SHOP_REVEAL is context, not a decision to agree with


def collect(archive, rows, *, parameters=None, limit=None):
    decisions, failures = [], []
    with zipfile.ZipFile(archive) as source:
        for row in rows[:limit] if limit else rows:
            episode = json.loads(source.read(f"{row['episode']}.json"))
            configuration = dict(episode['configuration'])
            seat = int(row['seat'])
            for kind, detail, state in strategic_events(episode['steps'], seat,
                                                        configuration):
                frame = episode['steps'][state['turn']][seat]
                observation = frame['observation']
                if canonical_turn(observation, configuration) != state['turn']:
                    failures.append({'episode': row['episode'], 'turn': state['turn'],
                                     'reason': 'recorded clock disagrees with its index'})
                    continue
                try:
                    macro = planner_intent(observation, configuration, parameters)
                except Exception as error:                      # noqa: BLE001
                    failures.append({'episode': row['episode'], 'turn': state['turn'],
                                     'reason': f'{type(error).__name__}: {error}'})
                    continue
                decisions.append({
                    'episode': row['episode'], 'seat': seat, 'team': row['team'],
                    'rating': row['rating'], 'rank': row['rank'],
                    'final_money': row['money'], 'opponent_money': row['opponent_money'],
                    'event': kind, 'detail': detail, 'state': state,
                    'planner_macro': macro, 'agrees': agrees(kind, detail, macro),
                    'observation_digest': observation_digest(observation),
                })
    return decisions, failures


def rank_divergences(decisions):
    """Disagreements grouped by event, ordered by how much of the field they cover."""
    grouped = defaultdict(lambda: {'events': 0, 'disagreements': 0, 'teams': set(),
                                   'turns': [], 'cash': []})
    for row in decisions:
        if row['agrees'] is None:
            continue
        key = row['event']
        if key == 'BUY_LAND':
            key = f"BUY_LAND#{row['detail']['expansion_index']}"
        elif key == 'BUY_ANIMAL' and row['detail']['first_of_species']:
            key = f"FIRST_{row['detail']['species']}"
        elif key == 'BUY_ANIMAL':
            continue
        bucket = grouped[key]
        bucket['events'] += 1
        bucket['teams'].add(row['team'])
        if not row['agrees']:
            bucket['disagreements'] += 1
            bucket['turns'].append(row['state']['turn'])
            bucket['cash'].append(row['state']['cash'])
    ranked = []
    for key, bucket in grouped.items():
        if not bucket['events']:
            continue
        turns = sorted(bucket['turns'])
        ranked.append({
            'decision': key,
            'events': bucket['events'],
            'disagreements': bucket['disagreements'],
            'disagreement_rate': bucket['disagreements'] / bucket['events'],
            'teams': len(bucket['teams']),
            'median_elite_turn': turns[len(turns) // 2] if turns else None,
            'median_cash_at_decision': (sorted(bucket['cash'])[len(bucket['cash']) // 2]
                                        if bucket['cash'] else None),
        })
    return sorted(ranked, key=lambda row: (-row['disagreements'], row['decision']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', required=True)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--leaderboard', required=True)
    parser.add_argument('--observed-at', required=True)
    parser.add_argument('--min-rating', type=float, default=2900.)
    parser.add_argument('--limit', type=int, help='smoke only: first N admitted streams')
    parser.add_argument('--economic-planner', action='store_true',
                        help='ask the planner with its allocation layer switched on')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    library = json.loads(Path(args.library).read_text(encoding='utf-8'))
    leaderboard = load_leaderboard(args.leaderboard)
    rows, refused = admissible(library['tapes'], leaderboard, args.min_rating)
    rows.sort(key=lambda row: (row['rank'], -row['margin']))
    parameters = {'economic_planner': True} if args.economic_planner else None
    decisions, failures = collect(args.archive, rows, parameters=parameters,
                                  limit=args.limit)
    payload = {
        'schema_version': SCHEMA,
        'library': args.library,
        'archive': args.archive,
        'snapshot': {'leaderboard': Path(args.leaderboard).name,
                     'observed_at': args.observed_at},
        'min_rating': args.min_rating,
        'planner_parameters': {**DEFAULTS, **(parameters or {})},
        'streams_admitted': len(rows),
        'streams_read': min(len(rows), args.limit) if args.limit else len(rows),
        'refused': refused,
        'decisions': len(decisions),
        'failures': failures,
        'ranked_divergences': rank_divergences(decisions),
        'rows': decisions,
    }
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in payload.items()
                      if k not in ('rows', 'planner_parameters')}, indent=2)[:3000])


if __name__ == '__main__':
    main()
