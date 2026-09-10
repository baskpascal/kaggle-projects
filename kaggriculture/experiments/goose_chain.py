"""The minimal economic chain around the goose, read from replays without playing a game.

A coop is a prerequisite, not a strategy. Built early while the bird is still acquired at
turn 242 it spends capital and earns nothing for a hundred and eighty turns, so ablating it
alone would reject a regime for a cost its own chain never got to repay. This extracts the
whole chain instead - structure, acquisition, feed, first production, first realised sale and
the turn the investment is repaid - so the smallest mechanism capable of a return can be
identified before anything is grafted.

Every step is read from state the engine keeps, never from issued orders: a refused purchase
is indistinguishable from a granted one in the action stream.
"""
import argparse
import json
from pathlib import Path
import statistics
import zipfile

COOP_COST = 65.
GOOSE_PRODUCT = 'EGG'


def _farm(frame, seat):
    observation = frame[seat]['observation']
    return observation['farms'][seat], observation


def _held(observation, item):
    private = observation['private']
    return private['shed'].get(item, 0) + sum(
        inventory.get(item, 0) for inventory in private['inventories'])


def chain(steps, seat):
    """Turns at which each link of the goose chain first becomes true."""
    result = {'coop_built': None, 'goose_present': None, 'first_egg_held': None,
              'first_egg_sold': None, 'eggs_sold_total': 0, 'egg_revenue': 0.,
              'payback_turn': None, 'geese_peak': 0}
    previous_structures = 0
    spend = 0.
    for turn, frame in enumerate(steps):
        farm, observation = _farm(frame, seat)
        tiles = [tile for row in farm['tiles'] for tile in row]
        coops = sum(isinstance(tile, dict) and tile.get('kind') == 'COOP'
                    for tile in tiles)
        geese = sum(isinstance(tile, dict) and tile.get('animal') == 'GOOSE'
                    for tile in tiles)
        result['geese_peak'] = max(result['geese_peak'], geese)
        if coops > previous_structures and result['coop_built'] is None:
            result['coop_built'] = turn
            spend += COOP_COST
        previous_structures = max(previous_structures, coops)
        if geese and result['goose_present'] is None:
            result['goose_present'] = turn
        if result['first_egg_held'] is None and _held(observation, GOOSE_PRODUCT):
            result['first_egg_held'] = turn
        action = frame[seat].get('action')
        if isinstance(action, dict):
            for order in action.get('market') or []:
                if not order:
                    continue
                if order[0] == 'BUY_ANIMAL' and order[1] == 'GOOSE':
                    # Priced from the market the turn it was requested; the executed
                    # count is what `goose_present` confirms.
                    spend += observation['market']['prices'].get('GOOSE', 0.) * (
                        order[2] if len(order) > 2 else 1)
                elif order[0] == 'SELL' and order[1] == GOOSE_PRODUCT:
                    units = order[2] if len(order) > 2 else 1
                    before = _held(observation, GOOSE_PRODUCT)
                    settled = min(units, before)
                    if settled <= 0:
                        continue
                    if result['first_egg_sold'] is None:
                        result['first_egg_sold'] = turn
                    result['eggs_sold_total'] += settled
                    result['egg_revenue'] += settled * observation['market'][
                        'prices'].get(GOOSE_PRODUCT, 0.)
                    if result['payback_turn'] is None and result['egg_revenue'] >= spend:
                        result['payback_turn'] = turn
    result['invested'] = round(spend, 1)
    result['egg_revenue'] = round(result['egg_revenue'], 1)
    return result


def summarise(records):
    keys = ('coop_built', 'goose_present', 'first_egg_held', 'first_egg_sold',
            'payback_turn')
    out = {}
    for key in keys:
        values = [record[key] for record in records if record[key] is not None]
        out[key] = {'worlds': len(values), 'of': len(records),
                    'median': int(statistics.median(values)) if values else None}
    for key in ('eggs_sold_total', 'egg_revenue', 'invested', 'geese_peak'):
        values = [record[key] for record in records]
        out[key] = {'median': round(statistics.median(values), 1) if values else None}
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', help='adaptive_evidence output naming the episodes')
    parser.add_argument('--archive')
    parser.add_argument('--replay', help='a lab replay to read instead of an archive')
    parser.add_argument('--seat', type=int, default=0)
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    records = []
    if args.replay:
        replay = json.loads(Path(args.replay).read_text(encoding='utf-8'))
        steps = [[{'observation': observation, 'action': action}
                  for observation, action in zip(turn['observations'], turn['actions'])]
                 for turn in replay['turns']]
        records.append({'source': args.replay, **chain(steps, args.seat)})
    else:
        evidence = json.loads(Path(args.evidence).read_text(encoding='utf-8'))
        with zipfile.ZipFile(args.archive) as source:
            for row in evidence['rows'][:args.limit]:
                episode = json.loads(source.read(f"{row['episode']}.json"))
                records.append({'source': row['episode'],
                                **chain(episode['steps'], int(row['seat']))})
    payload = {'schema_version': 1, 'worlds': len(records),
               'summary': summarise(records), 'rows': records}
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps(payload['summary'], indent=2))


if __name__ == '__main__':
    main()
