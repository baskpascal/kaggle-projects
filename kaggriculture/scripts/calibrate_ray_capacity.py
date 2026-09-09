#!/usr/bin/env python3
"""Measure useful local worker counts on every Ray node with identical jobs."""
from __future__ import annotations

import argparse
import atexit
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from arena.batch import make_batch, percentile  # noqa: E402
from arena.jobs import plan  # noqa: E402
from arena.ray_transport import connect  # noqa: E402


NONDETERMINISTIC = {'runtime_ms', 'wall_seconds', 'hostname', 'git_commit', 'git_dirty',
                    'job_id', 'batch_id'}


def result_signature(rows):
    # Distributed batches arrive in completion order.  Compare the exact result
    # multiset so scheduler timing cannot look like a semantic disagreement.
    return sorted(json.dumps({key: value for key, value in row.items()
                              if key not in NONDETERMINISTIC},
                             sort_keys=True, separators=(',', ':'), allow_nan=False)
                  for row in rows)


def parse_worker_caps(raw):
    values = []
    for token in raw.split(','):
        token = token.strip().lower()
        if token == 'max':
            value = None
        else:
            try:
                value = int(token)
            except ValueError as exc:
                raise ValueError(f'invalid worker count: {token}') from exc
            if value < 1:
                raise ValueError('worker counts must be positive')
        if value not in values:
            values.append(value)
    if not values:
        raise ValueError('at least one worker count is required')
    return values


def summarize(envelope, *, node_id, workers):
    seconds = envelope['wall_seconds']
    match_seconds = [row['wall_seconds'] for row in envelope['rows']]
    return {'node_id': node_id, 'hostname': envelope['hostname'], 'workers': workers,
            'seconds': seconds, 'jobs_per_second': len(envelope['rows']) / seconds,
            'p50_match_seconds': percentile(match_seconds, .5),
            'p95_match_seconds': percentile(match_seconds, .95),
            'cpu_utilization': envelope.get('cpu_utilization')}


def recommendations(rows):
    hosts = sorted({row['hostname'] for row in rows})
    return [{**max((row for row in rows if row['hostname'] == hostname),
                   key=lambda row: row['jobs_per_second'])}
            for hostname in hosts]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--address', default='auto')
    parser.add_argument('--jobs', type=int, default=256)
    parser.add_argument('--workers', default='1,4,8,max')
    parser.add_argument('--candidate', default='versions/v004/main.py')
    parser.add_argument('--opponent', default='opponents/public/thomas_t95/main.py')
    parser.add_argument('--seed', type=int, default=2700)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    try:
        caps = parse_worker_caps(args.workers)
    except ValueError as exc:
        parser.error(str(exc))

    mapper = connect(args.address)
    atexit.register(mapper.ray.shutdown)
    seeds = range(args.seed, args.seed + (args.jobs + 1) // 2)
    jobs = plan(args.candidate, [args.opponent], seeds, split='diagnostic')[:args.jobs]
    batch = make_batch(jobs)
    environments = mapper.verify_cluster([batch])

    rows = []
    reference = None

    def write(status):
        # Written after every measurement, not only at the end. A sweep costs tens of
        # minutes per cap; losing all of it because the driver died on the last one is a
        # cost this file has already paid once.
        report = {'schema_version': 1, 'status': status, 'jobs': args.jobs,
                  'worker_caps': args.workers,
                  'candidate': args.candidate, 'opponent': args.opponent,
                  'hostnames': sorted({row['hostname'] for row in rows}),
                  'environments': environments, 'measurements': rows,
                  'recommendations': recommendations(rows) if rows else [],
                  'result_signature_stable': True}
        Path(args.output).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        return report

    write('running')
    for cap in caps:
        runs = mapper.local_baseline_on_every_node(batch, maximum_workers=cap)
        for node, envelope, workers in runs:
            signature = result_signature(envelope['rows'])
            if reference is None:
                reference = signature
            elif signature != reference:
                raise SystemExit('Capacity measured on results that disagree; refusing')
            row = summarize(envelope, node_id=node['NodeID'], workers=workers)
            rows.append(row)
            print(json.dumps(row), flush=True)
            write('running')

    report = write('complete')
    print(json.dumps({'recommendations': report['recommendations']}, indent=2))
    mapper.ray.shutdown()


if __name__ == '__main__':
    main()
