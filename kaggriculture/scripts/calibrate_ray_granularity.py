#!/usr/bin/env python3
"""Choose Ray CPUs per batch task by measured two-host throughput."""
from __future__ import annotations

import argparse
import atexit
from collections import Counter
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from arena.batch import batched_runner  # noqa: E402
from arena.jobs import git_provenance, plan  # noqa: E402
from arena.ray_transport import connect  # noqa: E402
from scripts.calibrate_ray_capacity import result_signature  # noqa: E402


def parse_groups(raw):
    groups = []
    for token in raw.split(','):
        try:
            value = int(token.strip())
        except ValueError as exc:
            raise ValueError(f'invalid CPU group: {token}') from exc
        if value < 1:
            raise ValueError('CPU groups must be positive')
        if value not in groups:
            groups.append(value)
    if not groups:
        raise ValueError('at least one CPU group is required')
    return groups


def best_measurement(rows):
    return max(rows, key=lambda row: row['jobs_per_second'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--address', default='auto')
    parser.add_argument('--jobs', type=int, default=512)
    parser.add_argument('--cpu-groups', default='2,4')
    parser.add_argument('--candidate', default='versions/v004/main.py')
    parser.add_argument('--opponent', default='opponents/public/thomas_t95/main.py')
    parser.add_argument('--seed', type=int, default=2800)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    try:
        groups = parse_groups(args.cpu_groups)
    except ValueError as exc:
        parser.error(str(exc))

    seeds = range(args.seed, args.seed + (args.jobs + 1) // 2)
    jobs = plan(args.candidate, [args.opponent], seeds, split='diagnostic')[:args.jobs]
    for job in jobs:
        job['telemetry_enabled'] = False

    rows, reference, hostnames, mapper = [], None, None, None
    for cpus in groups:
        mapper = connect(args.address, cpus_per_worker=cpus)
        if not rows:
            atexit.register(mapper.ray.shutdown)
        envelopes = []
        runner = batched_runner(map_batches=mapper, on_batch=envelopes.append,
                                available_slots=mapper.available_slots)
        started = time.monotonic()
        results = list(runner(jobs, cpus))
        seconds = time.monotonic() - started
        signature = result_signature(results)
        if reference is None:
            reference = signature
            hostnames = sorted({node['hostname'] for node in mapper.nodes})
            if len(hostnames) < 2:
                raise SystemExit('Granularity calibration requires two distinct hostnames')
        elif signature != reference:
            raise SystemExit('Granularity measured on results that disagree; refusing')
        current = sorted({node['hostname'] for node in mapper.nodes})
        if current != hostnames:
            raise SystemExit('Ray cluster membership changed during calibration')
        jobs_by_host = Counter()
        batches_by_host = Counter()
        for envelope in envelopes:
            jobs_by_host[envelope['hostname']] += len(envelope['rows'])
            batches_by_host[envelope['hostname']] += 1
        row = {'cpus_per_worker': cpus, 'cluster_slots': mapper.available_slots,
               'node_slots': mapper.node_slots,
               'batch_size': len(envelopes[0]['rows']) if envelopes else None,
               'batches': len(envelopes), 'seconds': seconds,
               'jobs_per_second': len(results) / seconds,
               'jobs_by_hostname': dict(sorted(jobs_by_host.items())),
               'batches_by_hostname': dict(sorted(batches_by_host.items()))}
        rows.append(row)
        print(json.dumps(row), flush=True)

    report = {'schema_version': 1, 'jobs': args.jobs, 'cpu_groups': groups,
              'candidate': args.candidate, 'opponent': args.opponent,
              'hostnames': hostnames, 'git': git_provenance(ROOT),
              'measurements': rows, 'recommendation': best_measurement(rows),
              'result_signature_stable': True}
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'recommendation': report['recommendation']}, indent=2))
    assert mapper is not None
    mapper.ray.shutdown()


if __name__ == '__main__':
    main()
