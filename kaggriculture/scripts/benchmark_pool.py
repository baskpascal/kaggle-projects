"""Measure games per second on this host, so a distributed claim has a local baseline.

Issue #43 asks for the benchmark against the current `forkserver` pool rather than the
`spawn` one it replaced, and for the batch layer to be measured next to the direct pool:
batching is what a second machine would distribute, and if it costs throughput here it
would cost it there too.

    python scripts/benchmark_pool.py --games 32 --workers 1,2,4,8,14
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from arena.batch import (batched_runner, host_cpu_times, host_cpu_utilization,
                         percentile, worker_budget)  # noqa: E402
from arena.parallel import matches, start_method  # noqa: E402

NONDETERMINISTIC = {'runtime_ms', 'wall_seconds', 'hostname', 'git_commit', 'git_dirty',
                    'job_id', 'batch_id', 'execution_resources'}
OUTCOME_FIELDS = ('score', 'money', 'opponent_money', 'margin', 'steps',
                  'failures', 'opponent_failures')


def result_signature(rows):
    """Every game-relevant field, serialized exactly rather than float-tolerantly."""
    return [json.dumps({key: value for key, value in row.items()
                        if key not in NONDETERMINISTIC}, sort_keys=True,
                       separators=(',', ':'), allow_nan=False) for row in rows]


def run(jobs, workers, batch_size=None):
    started, cpu_started = time.monotonic(), host_cpu_times()
    batches = []
    if batch_size is None:
        rows = list(matches(jobs, workers=workers))
    else:
        rows = list(batched_runner(size=batch_size, on_batch=batches.append)(jobs, workers))
    elapsed = time.monotonic() - started
    utilization = host_cpu_utilization(cpu_started, host_cpu_times())
    match_seconds = [row['wall_seconds'] for row in rows]
    return rows, elapsed, {'p50_match_seconds': percentile(match_seconds, .5),
                           'p95_match_seconds': percentile(match_seconds, .95),
                           'batch_tail_seconds': max((b['wall_seconds'] for b in batches),
                                                     default=None),
                           'cpu_utilization': utilization}


def add_scaling(rows):
    """Compare each result with one worker in the same execution mode."""
    baselines = {row['mode']: row['seconds'] for row in rows if row['workers'] == 1}
    missing = sorted({row['mode'] for row in rows} - baselines.keys())
    if missing:
        raise ValueError(f'Missing one-worker baseline for: {", ".join(missing)}')
    for row in rows:
        speedup = baselines[row['mode']] / row['seconds']
        row['speedup_vs_one_worker'] = speedup
        row['parallel_efficiency'] = speedup / row['workers']


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--games', type=int, default=32)
    parser.add_argument('--workers', default='1,2,4,8')
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--profiles', default='score,full',
                        help='comma-separated evidence profiles to calibrate')
    parser.add_argument('--candidate', default='clock::versions/v004/main.py')
    parser.add_argument('--opponent', default='clock::opponents/public/thomas_t95/main.py')
    parser.add_argument('--seed', type=int, default=1700)
    parser.add_argument('--output')
    args = parser.parse_args()

    profiles = [value.strip() for value in args.profiles.split(',') if value.strip()]
    if not profiles or any(value not in ('score', 'audit', 'full') for value in profiles):
        parser.error('--profiles must contain score, audit, or full')
    report = {'host': os.uname().nodename if hasattr(os, 'uname') else '',
              'cpus': os.cpu_count(), 'start_method': start_method(),
              'default_workers': worker_budget(), 'games': args.games,
              'batch_size': args.batch_size, 'profiles': profiles, 'rows': []}
    references = {}
    outcome_reference = None
    for profile in profiles:
        jobs = [dict(candidate=args.candidate, opponent=args.opponent,
                     seed=args.seed + index, seat=index % 2, backend='fast',
                     evidence_profile=profile) for index in range(args.games)]
        for workers in [int(value) for value in args.workers.split(',')]:
            for label, size in (('direct', None), ('batched', args.batch_size)):
                rows, elapsed, metrics = run(jobs, workers, size)
                signature = result_signature(rows)
                if profile not in references:
                    references[profile] = signature
                elif signature != references[profile]:
                    raise SystemExit('Throughput measured on results that disagree; refusing')
                outcomes = [{key: row[key] for key in OUTCOME_FIELDS} for row in rows]
                if outcome_reference is None:
                    outcome_reference = outcomes
                elif outcomes != outcome_reference:
                    raise SystemExit('Evidence profiles changed game outcomes; refusing')
                entry = {'profile': profile, 'workers': workers, 'mode': label, 'seconds': elapsed,
                     'seconds_per_game': elapsed / len(rows),
                     'games_per_second': len(rows) / elapsed,
                     'mean_serialized_bytes': statistics.mean(
                         len(json.dumps(row, separators=(',', ':'))) for row in rows), **metrics}
                report['rows'].append(entry)
                print(f"{profile:5s} {label:8s} workers={workers:3d}  "
                      f"{entry['seconds_per_game']:.3f} s/game  "
                      f"{entry['games_per_second']:.2f} games/s", flush=True)
    for profile in profiles:
        add_scaling([row for row in report['rows'] if row['profile'] == profile])
    report['result_signature_stable'] = True
    report['median_games_per_second'] = statistics.median(r['games_per_second'] for r in report['rows'])
    report['recommended_by_profile'] = {
        profile: max((row for row in report['rows'] if row['profile'] == profile),
                     key=lambda row: row['games_per_second']) for profile in profiles}
    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'default_workers': report['default_workers'],
                      'recommended_by_profile': report['recommended_by_profile']}, indent=2))


if __name__ == '__main__':
    main()
