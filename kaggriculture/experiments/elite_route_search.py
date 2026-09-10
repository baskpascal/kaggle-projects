"""Fit the first elite replay branch on fresh games, optimizing ladder score.

The elite streams share an exact opening through step 144, then split into only a
handful of distinct continuations.  A probe deliberately stops at that boundary and
reports the public shop pair through its captured exception.  Every continuation is
then played in exactly those same worlds.  This makes the route label a win/loss result
instead of the original replay's final cash, which is confounded by its later shops and
opponent.
"""
import argparse
import base64
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import zlib

from arena.batch import batched_runner, host_contribution, worker_budget
from arena.league import run_league
from arena.ray_transport import DEFAULT_CPUS_PER_WORKER, require_cluster
from arena.seeds import parse_seeds, validate_seeds
from experiments.elite_route_train import (action_digest, build, select_training_tapes,
                                            shop_histories, train)


PROBE_TEMPLATE = '''import base64, json, zlib
ACTIONS = json.loads(zlib.decompress(base64.b85decode({blob!r})))
def agent(observation, configuration=None):
    turn = observation.get('step')
    if turn == 144:
        shops = observation.get('town', {{}}).get('unlocked_shops', ())[:2]
        raise RuntimeError('SHOPPAIR:' + '|'.join(shops))
    action = ACTIONS[turn]
    return {{'farmer': action.get('farmer', ['PASS']),
            'hands': action.get('hands', []), 'market': action.get('market', [])}}
'''


def continuations(tapes, step=216):
    """One deterministic representative for every distinct next action prefix."""
    groups = defaultdict(list)
    for index, tape in enumerate(tapes):
        groups[action_digest(tape['actions'], step)].append(index)
    return [max(members, key=lambda i: (tapes[i]['money'], tapes[i]['sha256']))
            for _, members in sorted(groups.items())]


def make_agents(tapes, representatives, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    opening = tapes[0]['actions'][:145]
    blob = base64.b85encode(zlib.compress(json.dumps(
        opening, separators=(',', ':')).encode(), 9)).decode()
    probe = directory / 'probe.py'
    probe.write_text(PROBE_TEMPLATE.format(blob=blob), encoding='utf-8')
    paths = {}
    for index in representatives:
        path = directory / f'route-{index}.py'
        build([tapes[index]], {}, path, f'forced elite continuation {index}', default=0)
        paths[index] = path
    return probe, paths


def probe_shops(row):
    failures = row.get('failures', ())
    if len(failures) != 1 or failures[0].get('step') != 144:
        raise ValueError('probe did not stop exactly once at step 144')
    marker = failures[0].get('message', '')
    if not marker.startswith('SHOPPAIR:'):
        raise ValueError('probe result has no shop-pair marker')
    shops = marker.removeprefix('SHOPPAIR:').split('|')
    if len(shops) != 2 or not all(shops):
        raise ValueError(f'invalid shop pair {shops!r}')
    return tuple(shops)


def choose_routes(probe_rows, route_rows, representatives, minimum_worlds=8):
    worlds = {(row['opponent'], row['seed'], row['seat']): probe_shops(row)
              for row in probe_rows}
    cells = defaultdict(lambda: defaultdict(list))
    overall = defaultdict(list)
    for index in representatives:
        for row in route_rows[index]:
            key = (row['opponent'], row['seed'], row['seat'])
            cells[worlds[key]][index].append(row)
            overall[index].append(row)

    def metric(rows):
        return (statistics.mean(row['score'] for row in rows),
                statistics.mean(row['margin'] for row in rows))

    fallback = max(representatives, key=lambda index: (*metric(overall[index]), -index))
    table, report = {}, {}
    for shops, plans in sorted(cells.items()):
        games = len(next(iter(plans.values())))
        complete = all(len(plans.get(index, ())) == games for index in representatives)
        if not complete:
            raise ValueError(f'unpaired route results for {shops}')
        best = (max(representatives, key=lambda index: (*metric(plans[index]), -index))
                if games >= minimum_worlds else fallback)
        key = '|'.join(shops)
        table[key] = best
        report[key] = {
            'worlds': games, 'selected': best,
            'selection': 'cell' if games >= minimum_worlds else 'global_fallback',
            'routes': {str(index): {'score_rate': metric(plans[index])[0],
                                    'mean_margin': metric(plans[index])[1]}
                       for index in representatives},
        }
    return table, fallback, report


def fit_routes(routes, default, table):
    fitted = json.loads(json.dumps(routes))
    fitted['144'][str(default)] = dict(table)
    return fitted


def run(args):
    validate_seeds(args.seeds, args.split)
    library = json.loads(Path(args.library).read_text())
    tapes = select_training_tapes(library, args.team)
    histories = shop_histories(args.archive, tapes)
    routes, coverage = train(tapes, histories)
    representatives = continuations(tapes)
    output = Path(args.output)
    if output.exists() and not args.resume:
        raise ValueError(f'{output} already exists; pass --resume to reuse durable runs')
    output.mkdir(parents=True, exist_ok=True)
    # Results are intentionally excluded from Ray's working-dir package.  Executable
    # artifacts live in a separate included directory so both machines hash and run the
    # same files while bulky reports stay on the head.
    probe, paths = make_agents(tapes, representatives, args.agents_dir)

    mapper, nodes = require_cluster(args.ray_address, cpus_per_worker=args.cpus_per_worker)
    runner = batched_runner(map_batches=mapper, available_slots=mapper.available_slots,
                            minimum_batch_size=args.cpus_per_worker)
    common = dict(opponents=args.opponents, seeds=args.seeds, workers=args.workers,
                  backend='fast', split=args.split, resume=args.resume,
                  evidence_profile='score', runner=runner)
    probe_rows, _, _ = run_league(str(probe), output=output / 'probe', **common)
    route_rows = {}
    for number, index in enumerate(representatives, 1):
        print(f'route {number}/{len(representatives)}: tape {index}', flush=True)
        rows, _, _ = run_league(str(paths[index]), output=output / f'route-{index}', **common)
        if any(row['failures'] or row['opponent_failures'] for row in rows):
            raise ValueError(f'route {index} had callback failures')
        route_rows[index] = rows
    table, fallback, cells = choose_routes(probe_rows, route_rows, representatives,
                                           args.minimum_worlds)
    fitted = fit_routes(routes, fallback, table)
    artifact_hash, built_default = build(
        tapes, fitted, args.agent, f'fresh-game score fit from {args.team}', default=fallback)
    report = {
        'schema_version': 1, 'team': args.team, 'seeds': args.seeds,
        'opponents': args.opponents, 'representatives': representatives,
        'fallback': fallback, 'built_default': built_default,
        'minimum_worlds': args.minimum_worlds, 'cells': cells,
        'coverage': coverage, 'artifact_sha256': artifact_hash,
        'hosts': host_contribution([*probe_rows, *(row for rows in route_rows.values()
                                                   for row in rows)]),
        'nodes': nodes,
        'limitation': 'Development fit only; requires disjoint evaluation and release gate.',
    }
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    mapper.ray.shutdown()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--library', required=True)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--team', default='Mengfei Li')
    parser.add_argument('--opponents', required=True)
    parser.add_argument('--seeds', required=True)
    parser.add_argument('--split', default='dev')
    parser.add_argument('--output', required=True)
    parser.add_argument('--agent', required=True)
    parser.add_argument('--agents-dir', default='experiments/generated/elite-route-search')
    parser.add_argument('--workers', type=int, default=worker_budget())
    parser.add_argument('--cpus-per-worker', type=int, default=DEFAULT_CPUS_PER_WORKER)
    parser.add_argument('--ray-address', default='auto')
    parser.add_argument('--minimum-worlds', type=int, default=8)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    args.opponents = [value for value in args.opponents.split(',') if value]
    args.seeds = parse_seeds(args.seeds)
    report = run(args)
    print(json.dumps({key: report[key] for key in
                      ('representatives', 'fallback', 'artifact_sha256', 'hosts')}, indent=2))


if __name__ == '__main__':
    main()
