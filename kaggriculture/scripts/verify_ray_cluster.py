"""Prove exact cross-node determinism and the remote deadline barrier.

This deliberately refuses a one-node cluster.  A local Ray smoke test is useful for the
transport implementation, but it is not evidence for issue #43's release gates.
"""
import argparse
import atexit
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from arena.batch import make_batch, run_batch  # noqa: E402
from arena.jobs import git_provenance, plan  # noqa: E402
from arena.ray_transport import DEFAULT_CPUS_PER_WORKER, connect  # noqa: E402

NONDETERMINISTIC = {'runtime_ms', 'wall_seconds', 'hostname', 'git_commit', 'git_dirty',
                    'execution_resources'}
MINIMUM_SEED_PAIRS = 200


class CrashGate:
    """State survives the disposable worker that the recovery probe kills."""
    def __init__(self):
        self.seen = set()
        self.calls = 0

    def first(self, batch_id):
        self.calls += 1
        if batch_id in self.seen:
            return False
        self.seen.add(batch_id)
        return True

    def count(self):
        return self.calls


def crash_once(batch, workers, timeout, gate):
    import ray
    if ray.get(gate.first.remote(batch['batch_id'])):
        os._exit(86)
    result = run_batch(batch, workers=workers, timeout=timeout)
    result['ray_node_id'] = str(ray.get_runtime_context().get_node_id())
    return result


def verify_worker_recovery(mapper, batch, workers):
    """Kill and recover one worker pinned to every live Ray node."""
    strategy = mapper.ray.util.scheduling_strategies.NodeAffinitySchedulingStrategy
    recovered_nodes = []
    for node in [item for item in mapper.ray.nodes() if item.get('Alive')]:
        gate = mapper.ray.remote(num_cpus=0)(CrashGate).remote()
        recovery_task = mapper.ray.remote(num_cpus=workers, max_retries=0,
                                          retry_exceptions=False)(
            lambda work, count, timeout: crash_once(work, count, timeout, gate))
        pinned_task = recovery_task.options(scheduling_strategy=strategy(
            node['NodeID'], soft=False))
        original_task, mapper._task = mapper._task, pinned_task
        try:
            recovered = list(mapper([batch], workers))
        finally:
            mapper._task = original_task
        calls = mapper.ray.get(gate.count.remote())
        if len(recovered) != 1 or calls != 2:
            raise SystemExit(f'Killed worker on node {node["NodeID"]} did not recover once')
        if recovered[0].get('ray_node_id') != node['NodeID']:
            raise SystemExit(f'Recovery escaped node affinity for {node["NodeID"]}')
        recovered_nodes.append({'node_id': node['NodeID'],
                                'hostname': recovered[0]['hostname'],
                                'attempts': calls})
    return recovered_nodes


def exact_result(row):
    relevant = {key: value for key, value in row.items() if key not in NONDETERMINISTIC}
    return json.dumps(relevant, sort_keys=True, separators=(',', ':'), allow_nan=False)


def money_bits(row):
    """Canonical IEEE-754 binary64 bytes; signed zero and every finite bit must match."""
    try:
        return b''.join(struct.pack('!d', row[field])
                        for field in ('money', 'opponent_money'))
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise SystemExit(f'Invalid money value in job {row.get("job_id")}: {exc}') from exc


def digest(chunks):
    value = hashlib.sha256()
    for chunk in chunks:
        if isinstance(chunk, str):
            chunk = chunk.encode('utf-8')
        value.update(len(chunk).to_bytes(8, 'big'))
        value.update(chunk)
    return value.hexdigest()


def assert_identical(results):
    baseline_node, baseline = results[0]
    baseline_rows = [exact_result(row) for row in baseline['rows']]
    baseline_money = [money_bits(row) for row in baseline['rows']]
    evidence = []
    for node, result in results:
        rows = [exact_result(row) for row in result['rows']]
        money = [money_bits(row) for row in result['rows']]
        evidence.append({'node_id': node['NodeID'], 'hostname': result['hostname'],
                         'jobs': len(rows), 'relevant_result_sha256': digest(rows),
                         'money_binary64_sha256': digest(money)})
        if len(rows) != len(baseline_rows):
            raise SystemExit(f'Node {node["NodeID"]} returned a different row count')
        for index, (expected, actual) in enumerate(zip(baseline_money, money)):
            if actual != expected:
                job_id = baseline['rows'][index]['job_id']
                raise SystemExit(f'Binary64 money mismatch at job {job_id}: '
                                 f'{baseline_node["NodeID"]} != {node["NodeID"]}')
        for index, (expected, actual) in enumerate(zip(baseline_rows, rows)):
            if actual != expected:
                job_id = baseline['rows'][index]['job_id']
                raise SystemExit(f'Bit-exact result mismatch at job {job_id}: '
                                 f'{baseline_node["NodeID"]} != {node["NodeID"]}')
    return evidence


def assert_paired_coverage(work, expected_pairs):
    coverage = {}
    for job in work:
        coverage.setdefault(job['seed'], []).append(job['seat'])
    if len(coverage) != expected_pairs or any(sorted(seats) != [0, 1]
                                               for seats in coverage.values()):
        raise SystemExit('Determinism proof must cover both seats once for every seed')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--address', default='auto')
    parser.add_argument('--candidate', default='versions/v004/main.py')
    parser.add_argument('--opponent', default='opponents/public/thomas_t95/main.py')
    parser.add_argument('--seed', type=int, default=200)
    parser.add_argument('--pairs', type=int, default=200,
                        help='seed pairs; each produces one match in each seat on every node')
    parser.add_argument('--cpus-per-worker', type=int, default=DEFAULT_CPUS_PER_WORKER)
    parser.add_argument('--deadline', type=float, default=4.)
    parser.add_argument('--deadline-overhead', type=float, default=15.)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.pairs < MINIMUM_SEED_PAIRS:
        parser.error(f'--pairs must be at least {MINIMUM_SEED_PAIRS} for release evidence')

    mapper = connect(args.address, cpus_per_worker=args.cpus_per_worker,
                     attempts=3, timeout=-1)
    atexit.register(mapper.ray.shutdown)
    work = plan(args.candidate, [args.opponent],
                range(args.seed, args.seed + args.pairs), split='diagnostic')
    assert_paired_coverage(work, args.pairs)
    batch = make_batch(work)
    environments = mapper.verify_cluster([batch])
    hostnames = {node['hostname'] for node in environments}
    if len(hostnames) < 2:
        raise SystemExit('Cross-node proof requires at least two distinct hostnames')
    results = mapper.run_on_every_node(batch)
    determinism_nodes = assert_identical(results)

    hang = plan('arena/probes/hang_agent.py', ['pass'], [args.seed - 1],
                split='diagnostic')[0]
    hang_results = mapper.run_on_every_node(make_batch([hang]), timeout=args.deadline)
    deadline_limit = args.deadline + args.deadline_overhead
    for node, result in hang_results:
        row = result['rows'][0]
        if row.get('timed_out') is not True or result['wall_seconds'] > deadline_limit:
            raise SystemExit(f'Deadline barrier failed on node {node["NodeID"]}')

    # Ray's hidden retry stays disabled. The mapper observes one real worker death and
    # explicitly resubmits the same batch; the actor keeps the probe's state outside the
    # process being killed so the second attempt can finish.
    recovery_nodes = verify_worker_recovery(mapper, make_batch([work[0]]),
                                            args.cpus_per_worker)

    report = {'schema_version': 3, 'nodes': environments, 'hostnames': sorted(hostnames),
              'driver_git': git_provenance(ROOT),
              'cpus_per_worker': args.cpus_per_worker,
              'jobs_per_node': len(work), 'candidate': args.candidate,
              'opponent': args.opponent, 'seed_start': args.seed,
              'seed_pairs': args.pairs, 'paired_seats': True, 'bit_exact': True,
              'money_binary64_exact': True,
              'determinism_nodes': determinism_nodes,
              'deadline_seconds': args.deadline,
              'deadline_limit_seconds': deadline_limit,
              'deadline_passed_on_every_node': True,
              'worker_recovery_passed': True,
              'worker_recovery_nodes': recovery_nodes}
    Path(args.output).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    mapper.ray.shutdown()


if __name__ == '__main__':
    main()
