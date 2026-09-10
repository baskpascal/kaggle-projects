"""Small development-only screen of several agents against one opponent."""
import argparse
from collections import defaultdict
import json
from pathlib import Path

from arena.batch import batched_runner, host_contribution
from arena.parallel import matches
from arena.seeds import parse_seeds, validate_seeds


def report_labels(agents):
    paths = [Path(agent) for agent in agents]
    short = [path.parent.name if path.name == 'main.py' else path.stem for path in paths]
    duplicated = {name for name in short if short.count(name) > 1}
    labels = [f'{path.parent.parent.name}/{name}'
              if name in duplicated and path.name == 'main.py' else name
              for path, name in zip(paths, short)]
    if len(set(labels)) != len(labels):
        raise ValueError('Agent paths do not produce unique report labels')
    return labels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agents', nargs='+', required=True)
    parser.add_argument('--opponent')
    parser.add_argument('--opponents', help='comma-separated opponent paths')
    parser.add_argument('--seeds', required=True)
    parser.add_argument('--split', default='dev')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--distributed', action='store_true')
    parser.add_argument('--cpus-per-worker', type=int, default=5)
    parser.add_argument('--output', help='write the complete JSON report to this path')
    args = parser.parse_args()
    opponents = ([value for value in (args.opponents or '').split(',') if value]
                 if args.opponents else ([args.opponent] if args.opponent else []))
    if not opponents:
        parser.error('one of --opponent or --opponents is required')
    seeds = parse_seeds(args.seeds)
    validate_seeds(seeds, args.split)
    jobs = []
    for agent in args.agents:
        for opponent in opponents:
            for seed in seeds:
                for seat in (0, 1):
                    jobs.append({'candidate': agent, 'opponent': opponent,
                                 'seed': seed, 'seat': seat, 'backend': 'fast',
                                 'evidence_profile': 'score'})
    runner = matches
    mapper = None
    if args.distributed:
        from arena.ray_transport import require_cluster
        mapper, _ = require_cluster('auto', cpus_per_worker=args.cpus_per_worker)
        runner = batched_runner(size=None, map_batches=mapper,
                                available_slots=mapper.available_slots,
                                minimum_batch_size=args.cpus_per_worker)
    rows = defaultdict(list)
    all_rows = []
    for row in runner(jobs, args.workers):
        rows[row['candidate']].append(row)
        all_rows.append(row)
    # A sweep commonly has `family-a/tape-0/main.py` and
    # `family-b/tape-0/main.py`; the old parent-only label silently overwrote one
    # family in the JSON even though both had actually played.
    labels = report_labels(args.agents)
    report = {}
    for agent, label in zip(args.agents, labels):
        batch = rows[agent]
        report[label] = {
            'path': agent, 'games': len(batch),
            'score_rate': sum(row['score'] for row in batch) / len(batch),
            'mean_margin': sum(row['margin'] for row in batch) / len(batch),
            'failures': sum(bool(row['failures'] or row['opponent_failures']) for row in batch),
            'per_opponent': {
                opponent: {
                    'games': sum(row['opponent'] == opponent for row in batch),
                    'score_rate': sum(row['score'] for row in batch if row['opponent'] == opponent)
                                  / sum(row['opponent'] == opponent for row in batch),
                    'mean_margin': sum(row['margin'] for row in batch if row['opponent'] == opponent)
                                   / sum(row['opponent'] == opponent for row in batch),
                } for opponent in opponents},
        }
    payload = {'agents': report, 'hosts': host_contribution(all_rows)}
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    # Large tape sweeps stay readable on the terminal; the complete report is
    # retained above for selection and audit.
    leaders = sorted(report.items(), key=lambda item: (
        -item[1]['score_rate'], -item[1]['mean_margin']))[:20]
    print(json.dumps({'leaders': dict(leaders), 'agent_count': len(report),
                      'hosts': payload['hosts'], 'output': args.output}, indent=2))
    if mapper is not None:
        mapper.ray.shutdown()


if __name__ == '__main__':
    main()
