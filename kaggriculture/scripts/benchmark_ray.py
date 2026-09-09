"""Compare a private Ray cluster with the fastest per-node forkserver baseline."""
import argparse
import atexit
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from arena.batch import batched_runner, make_batch, percentile  # noqa: E402
from arena.jobs import git_provenance, plan  # noqa: E402
from arena.ray_transport import DEFAULT_CPUS_PER_WORKER, connect  # noqa: E402


def write_report(path, report):
    """Checkpoint atomically so a late cluster failure preserves earlier evidence."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.tmp')
    temporary.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    temporary.replace(target)


def metrics(rows, seconds, batches=(), *, cpu_utilization=None):
    match_seconds = [row['wall_seconds'] for row in rows]
    batches = list(batches)
    if batches:
        weighted = [(batch.get('cpu_utilization'), batch['wall_seconds']) for batch in batches
                    if batch.get('cpu_utilization') is not None]
        cpu_utilization = (None if not weighted else
                           sum(value * weight for value, weight in weighted) /
                           sum(weight for _, weight in weighted))
    return {'seconds': seconds, 'jobs_per_second': len(rows) / seconds,
            'p50_match_seconds': percentile(match_seconds, .5),
            'p95_match_seconds': percentile(match_seconds, .95),
            'batch_tail_seconds': max((batch['wall_seconds'] for batch in batches), default=None),
            'cpu_utilization': cpu_utilization}


def game_result(row):
    return json.dumps({key: value for key, value in row.items()
                       if key not in {'runtime_ms', 'wall_seconds', 'hostname', 'git_commit',
                                      'git_dirty', 'job_id', 'batch_id'}},
                      sort_keys=True, separators=(',', ':'), allow_nan=False)


def load_verification(path, *, candidate, opponent, environments, current_git):
    try:
        raw = Path(path).read_bytes()
        proof = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f'Cannot read Ray verification artifact: {exc}') from exc
    required = ('paired_seats', 'bit_exact', 'money_binary64_exact',
                'deadline_passed_on_every_node', 'worker_recovery_passed')
    if proof.get('schema_version', 0) < 3 or any(proof.get(field) is not True
                                                 for field in required):
        raise SystemExit('Ray verification artifact has not passed every release gate')
    if proof.get('candidate') != candidate or proof.get('opponent') != opponent:
        raise SystemExit('Ray verification artifacts do not match benchmark agents')
    hostnames = sorted({item.get('hostname') for item in environments})
    if sorted(proof.get('hostnames', [])) != hostnames:
        raise SystemExit('Ray verification hostnames do not match the current cluster')
    if proof.get('seed_pairs', 0) < 200 or proof.get('jobs_per_node') != 2 * proof['seed_pairs']:
        raise SystemExit('Ray verification does not cover 200 seeds in both seats')
    verified_commit = proof.get('driver_git', {}).get('git_commit')
    current_commit = current_git.get('git_commit')
    # git_provenance serializes the SQLite-friendly clean flag as integer zero.
    # JSON booleans from older proof fixtures remain valid because False == 0.
    if (not verified_commit or not current_commit or verified_commit != current_commit
            or proof.get('driver_git', {}).get('git_dirty') != 0
            or current_git.get('git_dirty') != 0):
        raise SystemExit('Ray verification git commit does not match this benchmark')
    verified_nodes = {item.get('hostname'): item for item in proof.get('nodes', [])}
    current_nodes = {item.get('hostname'): item for item in environments}
    if set(verified_nodes) != set(hostnames) or set(current_nodes) != set(hostnames):
        raise SystemExit('Ray environment evidence does not cover every current hostname')
    for hostname in hostnames:
        before, now = verified_nodes[hostname], current_nodes[hostname]
        for field in ('python', 'environment', 'artifacts'):
            if before.get(field) != now.get(field):
                raise SystemExit(f'Ray {field} changed on {hostname} after verification')
    determinism = proof.get('determinism_nodes', [])
    recovery = proof.get('worker_recovery_nodes', [])
    if ({item.get('hostname') for item in determinism} != set(hostnames)
            or {item.get('hostname') for item in recovery} != set(hostnames)
            or any(item.get('attempts') != 2 for item in recovery)):
        raise SystemExit('Ray verification does not cover every current hostname')
    result_digests = {item.get('relevant_result_sha256') for item in determinism}
    money_digests = {item.get('money_binary64_sha256') for item in determinism}
    if len(result_digests) != 1 or None in result_digests \
            or len(money_digests) != 1 or None in money_digests:
        raise SystemExit('Ray verification node digests are incomplete or divergent')
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
            'git_commit': verified_commit, 'hostnames': hostnames,
            'artifacts': current_nodes[hostnames[0]]['artifacts'],
            'jobs_per_node': proof.get('jobs_per_node')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--address', default='auto')
    parser.add_argument('--jobs', default='32,256,1024,8000')
    parser.add_argument('--candidate', default='versions/v004/main.py')
    parser.add_argument('--opponent', default='opponents/public/thomas_t95/main.py')
    parser.add_argument('--seed', type=int, default=1700)
    parser.add_argument('--local-workers', type=int,
                        help='optional per-node cap; default uses every CPU each node advertises')
    parser.add_argument('--cpus-per-worker', type=int, default=DEFAULT_CPUS_PER_WORKER)
    parser.add_argument('--batch-size', type=int,
                        help='omit to keep at least eight waves queued per cluster slot')
    parser.add_argument('--evidence-profile', choices=['score', 'audit', 'full'], default='score')
    parser.add_argument('--minimum-representative-speedup', type=float, default=1.10,
                        help='required speedup for the 8000-job workload (default: 1.10)')
    parser.add_argument('--verification',
                        help='schema-3 output from verify_ray_cluster.py; required for 8000 jobs')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.minimum_representative_speedup <= 1:
        parser.error('--minimum-representative-speedup must be greater than 1')
    try:
        counts = [int(value) for value in args.jobs.split(',')]
    except ValueError:
        parser.error('--jobs must be a comma-separated list of integers')
    if not counts or any(count < 1 for count in counts):
        parser.error('--jobs values must be positive')

    mapper = connect(args.address, cpus_per_worker=args.cpus_per_worker)
    atexit.register(mapper.ray.shutdown)
    report = {'schema_version': 4, 'status': 'running',
              'local_workers_cap': args.local_workers,
              'cluster_slots': mapper.available_slots,
              'node_slots': mapper.node_slots,
              'cpus_per_worker': args.cpus_per_worker,
              'evidence_profile': args.evidence_profile,
              'cluster_hostnames': None, 'verification': None, 'workloads': []}
    write_report(args.output, report)
    baseline_node_id = None
    for count in counts:
        seeds = range(args.seed, args.seed + (count + 1) // 2)
        jobs = plan(args.candidate, [args.opponent], seeds, split='diagnostic',
                    evidence_profile=args.evidence_profile)[:count]

        whole = make_batch(jobs)
        environments = mapper.verify_cluster([whole])
        hostnames = sorted({item['hostname'] for item in environments})
        if report['cluster_hostnames'] is None:
            report['cluster_hostnames'] = hostnames
            if 8000 in counts and len(hostnames) < 2:
                raise SystemExit('The representative benchmark requires two distinct hostnames')
            if 8000 in counts and not args.verification:
                raise SystemExit('The representative benchmark requires --verification first')
            if args.verification:
                report['verification'] = load_verification(
                    args.verification, candidate=args.candidate, opponent=args.opponent,
                    environments=environments, current_git=git_provenance(ROOT))
        elif hostnames != report['cluster_hostnames']:
            raise SystemExit('Ray cluster membership changed during the benchmark')
        # The smaller workloads establish the fastest host. Repeating all 8000 jobs on
        # every slower host adds no evidence and dominated the previous benchmark time.
        if count == 8000 and baseline_node_id is not None:
            local_runs = [mapper.local_baseline_on_node(
                whole, baseline_node_id, maximum_workers=args.local_workers)]
            baseline_strategy = 'selected-fastest-from-prior-workloads'
        else:
            local_runs = mapper.local_baseline_on_every_node(
                whole, maximum_workers=args.local_workers)
            baseline_strategy = 'all-live-nodes'
        local_nodes = []
        for node, envelope, workers in local_runs:
            local_nodes.append({'node_id': node['NodeID'],
                                'hostname': envelope['hostname'], 'workers': workers,
                                **metrics(envelope['rows'], envelope['wall_seconds'], [envelope])})
        fastest = min(local_nodes, key=lambda item: item['seconds'])
        if count != 8000:
            baseline_node_id = fastest['node_id']

        envelopes = []
        runner = batched_runner(size=args.batch_size, map_batches=mapper,
                                on_batch=envelopes.append,
                                available_slots=mapper.available_slots)
        started = time.monotonic()
        distributed = list(runner(jobs, args.cpus_per_worker))
        distributed_seconds = time.monotonic() - started
        fastest_rows = min(local_runs, key=lambda item: item[1]['wall_seconds'])[1]['rows']
        if [game_result(row) for row in fastest_rows] != [game_result(row) for row in distributed]:
            raise SystemExit(f'Distributed results differ for the {count}-job workload')

        ray_metrics = metrics(distributed, distributed_seconds, envelopes)
        speedup = fastest['seconds'] / distributed_seconds
        capacity_ratio = ((mapper.available_slots * args.cpus_per_worker) /
                          fastest['workers'])
        report['workloads'].append({'jobs': count, 'local_nodes': local_nodes,
            'baseline_strategy': baseline_strategy,
            'fastest_local_hostname': fastest['hostname'],
            'fastest_local_workers': fastest['workers'], 'direct': {
                key: value for key, value in fastest.items()
                if key not in {'node_id', 'hostname', 'workers'}},
            'ray': ray_metrics, 'speedup': speedup,
            'parallel_efficiency': speedup / capacity_ratio,
            'batch_size': len(envelopes[0]['rows']) if envelopes else None,
            'batches': len(envelopes)})
        write_report(args.output, report)
        print(json.dumps(report['workloads'][-1], indent=2), flush=True)
    representative = next((row for row in report['workloads'] if row['jobs'] == 8000), None)
    report['throughput_gate'] = {
        'representative_jobs': 8000,
        'minimum_speedup': args.minimum_representative_speedup,
        'observed_speedup': None if representative is None else representative['speedup'],
        'passed': (None if representative is None else
                   representative['speedup'] >= args.minimum_representative_speedup),
    }
    report['benchmark_valid'] = (report['verification'] is not None and
                                 (representative is None or
                                  report['throughput_gate']['passed'] is True))
    report['status'] = 'complete'
    write_report(args.output, report)
    if representative is not None and not report['throughput_gate']['passed']:
        raise SystemExit('Distributed 8000-job throughput did not beat the fastest local '
                         f'node by {args.minimum_representative_speedup:.2f}x')
    mapper.ray.shutdown()


if __name__ == '__main__':
    main()
