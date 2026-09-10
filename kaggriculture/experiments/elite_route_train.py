"""Train a compatible public-shop route trie from one elite team's replay streams.

Every transition is admitted only when the destination tape has the exact action prefix
already executed by the current tape. This is a behavioural clone of public replay data,
not a claim that an open-loop stream reconstructs the original live agent.
"""
import argparse
import base64
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import zipfile
import zlib

BOUNDARIES = tuple(range(144, 649, 72))
TURNS = 719


def action_digest(actions, end):
    return hashlib.sha256(json.dumps(
        actions[:end], sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def shop_histories(archive, tapes, boundaries=BOUNDARIES):
    result = {}
    with zipfile.ZipFile(archive) as source:
        for tape in tapes:
            episode = json.loads(source.read(f"{tape['episode']}.json"))
            seat = tape['seat']
            result[tape['sha256']] = {
                step: tuple(episode['steps'][step][seat]['observation']['town']['unlocked_shops'])
                for step in boundaries
            }
    return result


def select_training_tapes(library, team, common_prefix=144):
    tapes = [tape for tape in library['tapes'] if tape.get('team') == team]
    if not tapes:
        raise ValueError(f'no tapes for team {team!r}')
    groups = defaultdict(list)
    for tape in tapes:
        if len(tape.get('actions', ())) != TURNS:
            continue
        groups[action_digest(tape['actions'], common_prefix)].append(tape)
    if not groups:
        raise ValueError('no complete tapes')
    return max(groups.values(), key=lambda group: (len(group), max(t['money'] for t in group)))


def train(tapes, histories, boundaries=BOUNDARIES):
    """Return deterministic compatible transitions and their training coverage."""
    indices = {tape['sha256']: index for index, tape in enumerate(tapes)}
    prefixes = {step: [action_digest(tape['actions'], step) for tape in tapes]
                for step in boundaries}
    routes, coverage = {}, {}
    for position, step in enumerate(boundaries):
        next_step = boundaries[position + 1] if position + 1 < len(boundaries) else TURNS
        next_prefix = [action_digest(tape['actions'], next_step) for tape in tapes]
        groups = defaultdict(list)
        for index, prefix in enumerate(prefixes[step]):
            groups[prefix].append(index)
        table = {}
        observed = 0
        for members in groups.values():
            by_shops = defaultdict(list)
            for index in members:
                by_shops[histories[tapes[index]['sha256']][step]].append(index)
            for current in members:
                choices = {}
                for shops, candidates in by_shops.items():
                    # Follow the most-supported next action prefix. Final money breaks
                    # ties only within compatible, equally supported continuations.
                    support = Counter(next_prefix[index] for index in candidates)
                    best_prefix = max(support, key=lambda value: (
                        support[value], max(tapes[i]['money'] for i in candidates
                                            if next_prefix[i] == value), value))
                    target = max((i for i in candidates if next_prefix[i] == best_prefix),
                                 key=lambda i: (tapes[i]['money'], tapes[i]['sha256']))
                    if prefixes[step][target] != prefixes[step][current]:
                        raise AssertionError('incompatible route transition')
                    choices['|'.join(shops)] = target
                    observed += 1
                table[str(current)] = choices
        routes[str(step)] = table
        coverage[str(step)] = {
            'prefix_groups': len(groups),
            'shop_histories': len({history for tape in tapes
                                   for history in (histories[tape['sha256']][step],)}),
            'transitions': observed,
        }
    return routes, coverage


TEMPLATE = '''"""Elite compatible route trie. {provenance}"""
import base64
import json
import zlib

TAPES, ROUTES, DEFAULT = json.loads(zlib.decompress(base64.b85decode({blob!r})))
SESSIONS = {{}}


def _order(slot):
    if isinstance(slot, list) and slot:
        head = slot[0]
        if head in ('HIRE', 'BUY_LAND'):
            return slot[:]
        if head in ('BUY_SEED', 'BUY_PRODUCT', 'BUY_ANIMAL', 'SELL') and len(slot) > 2:
            try:
                if int(slot[2]) > 0:
                    return slot[:]
            except (TypeError, ValueError):
                pass
    return ['SELL', 'WHEAT', 0]


def agent(observation, configuration=None):
    turn = observation.get('step')
    if not isinstance(turn, int):
        turn = int(observation['day']) * 24 + int(observation['hour'])
    if turn < 0 or turn >= 719:
        return {{'farmer': ['PASS'], 'hands': [], 'market': []}}
    seat = int(observation['player'])
    state = SESSIONS.get(seat)
    if state is None or turn == 0 or turn < state[0]:
        state = [-1, DEFAULT]
        SESSIONS[seat] = state
    choices = ROUTES.get(str(turn), {{}}).get(str(state[1]), {{}})
    shops = '|'.join(observation.get('town', {{}}).get('unlocked_shops', ()))
    state[1] = choices.get(shops, state[1])
    state[0] = turn
    action = TAPES[state[1]][turn]
    farmer = action.get('farmer')
    return {{
        'farmer': farmer[:] if isinstance(farmer, list) and farmer else ['PASS'],
        'hands': [unit[:] if isinstance(unit, list) and unit else ['PASS']
                  for unit in action.get('hands', [])],
        'market': [_order(slot) for slot in action.get('market', [])[:10]],
    }}
'''


def build(tapes, routes, output, provenance, default=None):
    streams = [tape['actions'] for tape in tapes]
    if default is None:
        default = max(range(len(tapes)), key=lambda index: (tapes[index]['money'],
                                                            tapes[index]['sha256']))
    if type(default) is not int or default not in range(len(tapes)):
        raise ValueError('default must identify one training tape')
    blob = base64.b85encode(zlib.compress(json.dumps(
        [streams, routes, default], separators=(',', ':')).encode(), 9)).decode()
    source = TEMPLATE.format(blob=blob, provenance=provenance)
    compile(source, str(output), 'exec')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(source, encoding='utf-8')
    return hashlib.sha256(source.encode()).hexdigest(), default


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', required=True)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--team', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--default-sha256',
                        help='use this training tape as the initial route (full digest or unique prefix)')
    args = parser.parse_args()
    library = json.loads(Path(args.library).read_text())
    tapes = select_training_tapes(library, args.team)
    histories = shop_histories(args.archive, tapes)
    routes, coverage = train(tapes, histories)
    provenance = (f"{len(tapes)} public replay streams from {args.team}; compatible "
                  f"shop-history routing trained by {Path(__file__).name}")
    default = None
    if args.default_sha256:
        matches = [index for index, tape in enumerate(tapes)
                   if tape['sha256'].startswith(args.default_sha256)]
        if len(matches) != 1:
            raise ValueError('--default-sha256 must identify exactly one selected tape')
        default = matches[0]
    digest, default = build(tapes, routes, args.output, provenance, default=default)
    model = {
        'schema_version': 1, 'kind': 'elite_compatible_route_trie',
        'team': args.team, 'tapes': len(tapes), 'boundaries': list(BOUNDARIES),
        'coverage': coverage, 'default': default, 'artifact_sha256': digest,
        'training_tapes': [tape['sha256'] for tape in tapes],
        'source': args.library, 'archive': args.archive,
        'limitation': 'Public replay behavioural clone; validation must use fresh seeds.',
    }
    Path(args.model).parent.mkdir(parents=True, exist_ok=True)
    Path(args.model).write_text(json.dumps(model, indent=2) + '\n')
    print(json.dumps({key: model[key] for key in
                      ('team', 'tapes', 'boundaries', 'coverage', 'default',
                       'artifact_sha256')}, indent=2))


if __name__ == '__main__':
    main()
