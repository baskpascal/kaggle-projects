"""The batch is the unit of distribution; the match stays the unit of isolation.

Ray reuses its worker processes between tasks, so one match per Ray task would quietly
undo the guarantee `arena/parallel.py` exists to provide: a third-party bundle runs code at
import time, `arena/agents.py` can notice a rebind but not undo it, and the undo is the
process boundary. `@ray.remote(max_calls=1)` would restore isolation by killing the worker
every task, but Ray's worker startup is heavier than the `spawn` we just removed, so it
would be slower than doing nothing.

So Ray -- when a second machine exists -- distributes **batches**, and inside each batch
the local forkserver pool still gives every game its own process. This module is that
batch layer, and it runs today on one machine with no Ray installed. The only seam a
transport has to replace is `map_batches`, which is why it is an argument rather than an
import.

Nothing here weakens the deadline. The barrier from issue #44 lives in the parent of each
match, so it holds one level down: `run_batch` called from a worker, a thread, or a Ray
task still kills a game that will not end. That matters because `signal.setitimer` only
works on the main thread of a POSIX process, and a driver running off the main thread would
silently degrade the in-process timer to the measure-afterwards fallback. Each match runs
in the main thread of its own child, so the timer is armed where it works, and the kill does
not depend on it either way.
"""
import hashlib
import json
import os
import socket
import time
from pathlib import Path

from .jobs import git_provenance
from .parallel import matches

# One or two cores left to the machine by default: an evaluation run that makes the host
# unusable gets interrupted by a human, and an interrupted run is worse than a slower one.
LEAVE_FREE = 1
BATCH_SIZE = 32
TRANSPORT_FIELDS = ('candidate', 'opponent', 'seed', 'seat', 'backend')


def worker_budget(cpus_free=LEAVE_FREE, cpus=None):
    """Workers to use on this host, never fewer than one."""
    if cpus_free < 0:
        raise ValueError('cpus_free cannot be negative')
    if cpus is None:
        cpus = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else os.cpu_count()
    return max(1, (cpus or 1) - cpus_free)


def partition(jobs, size=BATCH_SIZE):
    """Contiguous batches, small enough that the slowest one cannot stall the queue.

    Small batches are what keeps a dynamic queue balanced without pre-splitting the work per
    machine, which is the failure mode of a static partition: the fast node idles while the
    slow one finishes its half.
    """
    jobs = list(jobs)
    if size < 1:
        raise ValueError('batch size must be positive')
    return [jobs[start:start + size] for start in range(0, len(jobs), size)]


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def host_cpu_times(path=Path('/proc/stat')):
    """Linux host CPU counters as (total, idle); unavailable platforms return None."""
    try:
        fields = path.read_text().splitlines()[0].split()
        if fields[0] != 'cpu':
            return None
        values = [int(value) for value in fields[1:]]
    except (OSError, ValueError, IndexError):
        return None
    return sum(values), values[3] + (values[4] if len(values) > 4 else 0)


def host_cpu_utilization(start, finish):
    if start is None or finish is None or finish[0] <= start[0]:
        return None
    return 1 - (finish[1] - start[1]) / (finish[0] - start[0])


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * fraction
    low, high = int(position), min(len(values) - 1, int(position) + 1)
    weight = position - low
    return values[low] * (1 - weight) + values[high] * weight


def transport_job_id(spec):
    """Use the durable ID when present, or a deterministic smoke-test identity."""
    return spec.get('job_id') or _digest({field: spec.get(field) for field in TRANSPORT_FIELDS})


def make_batch(specs, index=0):
    specs = [dict(spec) for spec in specs]
    job_ids = [transport_job_id(spec) for spec in specs]
    return {'batch_id': _digest({'index': index, 'job_ids': job_ids}),
            'job_ids': job_ids, 'specs': specs}


def adaptive_batch_size(total_jobs, available_slots, *, target_waves=8,
                        minimum=1, maximum=BATCH_SIZE):
    """Keep at least ``target_waves`` of work queued per cluster slot."""
    if total_jobs < 0 or available_slots < 1 or target_waves < 1:
        raise ValueError('jobs must be nonnegative and slots/target_waves positive')
    if minimum < 1 or maximum < minimum:
        raise ValueError('invalid batch-size limits')
    if total_jobs == 0:
        return minimum
    target_batches = target_waves * available_slots
    size = (total_jobs + target_batches - 1) // target_batches
    return min(maximum, max(minimum, size))


def _check_row(spec, row, job_id, batch_id, hostname, provenance):
    for field in TRANSPORT_FIELDS:
        if row.get(field) != spec.get(field):
            raise ValueError(f'{field} mismatch in batch {batch_id}, job {job_id}')
    for field in ('candidate_hash', 'opponent_hash'):
        expected = spec.get(field)
        if expected is not None and row.get(field) != expected:
            raise ValueError(f'{field} mismatch in batch {batch_id}, job {job_id}')
    return {**row, 'job_id': job_id, 'batch_id': batch_id, 'hostname': hostname,
            'git_commit': provenance.get('git_commit'),
            'git_dirty': provenance.get('git_dirty')}


def _identity(record):
    """The tuple that says which game a spec or a finished row is, without using position."""
    return tuple(record.get(field) for field in TRANSPORT_FIELDS)


def run_batch(batch, workers=4, method=None, timeout=-1):
    """Play one batch here. This is the body a Ray task would wrap.

    Games are played unordered and correlated by their own contents, so a slow game does
    not hold the ones behind it inside the worker; the envelope is then materialised in job
    order, because that order is what `validate_batch_result` checks and what keeps a
    report's digest independent of how the games happened to finish.
    """
    batch = batch if isinstance(batch, dict) else make_batch(batch)
    specs, job_ids, batch_id = batch['specs'], batch['job_ids'], batch['batch_id']
    if len(specs) != len(job_ids):
        raise ValueError(f'Batch {batch_id} has inconsistent specs and job IDs')
    started, cpu_started = time.monotonic(), host_cpu_times()
    by_identity = {}
    for spec, identity in zip(specs, job_ids):
        key = _identity(spec)
        if key in by_identity:
            raise ValueError(f'Batch {batch_id} names the same game twice: {key}')
        by_identity[key] = (spec, identity)
    hostname = socket.gethostname()
    provenance = git_provenance()
    source = batch.get('source_git', {})
    provenance = {key: (provenance.get(key) if provenance.get(key) is not None
                        else source.get(key)) for key in ('git_commit', 'git_dirty')}
    checked = {}
    for row in matches(specs, workers=workers, method=method, timeout=timeout, ordered=False):
        found = by_identity.get(_identity(row))
        if found is None:
            raise ValueError(f'Batch {batch_id} played a game it never asked for: {_identity(row)}')
        spec, identity = found
        if identity in checked:
            raise ValueError(f'Batch {batch_id} produced job {identity} twice')
        checked[identity] = _check_row(spec, row, identity, batch_id, hostname, provenance)
    if len(checked) != len(specs):
        raise OSError(f'Batch {batch_id} returned {len(checked)} of {len(specs)} jobs')
    rows = [checked[identity] for identity in job_ids]
    elapsed, cpu_finished = time.monotonic() - started, host_cpu_times()
    match_seconds = [row['wall_seconds'] for row in rows if row.get('wall_seconds') is not None]
    return {'batch_id': batch_id, 'job_ids': job_ids, 'rows': rows,
            'hostname': hostname, 'pid': os.getpid(),
            'git_commit': provenance.get('git_commit'),
            'git_dirty': provenance.get('git_dirty'),
            'wall_seconds': elapsed, 'games': len(rows),
            'cpu_utilization': host_cpu_utilization(cpu_started, cpu_finished),
            'p50_match_seconds': percentile(match_seconds, .5),
            'p95_match_seconds': percentile(match_seconds, .95)}


def _local(batches, workers, method, timeout):
    """Run batches one after another on this host. The default transport."""
    for batch in batches:
        yield run_batch(batch, workers=workers, method=method, timeout=timeout)


def validate_batch_result(expected, result):
    """Refuse incomplete, duplicated, substituted, or malformed transport output."""
    if not isinstance(result, dict):
        raise ValueError('Batch transport must return a result envelope')
    batch_id = expected['batch_id']
    if result.get('batch_id') != batch_id:
        raise ValueError(f'Unexpected batch result {result.get("batch_id")}; expected {batch_id}')
    if result.get('job_ids') != expected['job_ids']:
        raise OSError(f'Batch {batch_id} returned an incomplete or reordered job list')
    rows = result.get('rows')
    if not isinstance(rows, list) or result.get('games') != len(expected['job_ids']) \
            or len(rows) != len(expected['job_ids']):
        raise OSError(f'Batch {batch_id} is incomplete')
    if [row.get('job_id') for row in rows] != expected['job_ids']:
        raise OSError(f'Batch {batch_id} rows do not cover the expected jobs')
    if not result.get('hostname'):
        raise ValueError(f'Batch {batch_id} has no worker hostname')
    if any(row.get('batch_id') != batch_id or row.get('hostname') != result['hostname']
           for row in rows):
        raise ValueError(f'Batch {batch_id} row provenance disagrees with its envelope')
    return result


def batched_runner(size=BATCH_SIZE, method=None, timeout=-1, map_batches=None, on_batch=None,
                   available_slots=None):
    """A `runner(jobs, workers)` for `arena.jobs.execute`, with batching in the middle.

    `map_batches(batches, workers)` yields one envelope per batch, in any order. The default
    runs them here; a Ray transport replaces exactly this function and nothing else.

    An envelope is passed on the moment it arrives, never held for an earlier batch. Holding
    it would mean that a cluster which finishes batch 2 first keeps batch 2 in the driver's
    memory until batch 1 lands, so a driver that dies in between loses work that was already
    computed and pays for it again on `--resume`. `arena.jobs.execute` records each row by
    its own `job_id` as it arrives and materialises the report in plan order afterwards, so
    nothing downstream needs arrival order to mean anything.
    """
    def runner(jobs, workers):
        jobs = list(jobs)
        chosen_size = (adaptive_batch_size(len(jobs), available_slots or workers)
                       if size is None else size)
        batches = [make_batch(specs, index) for index, specs in
                   enumerate(partition(jobs, chosen_size))]
        transport = map_batches or (lambda parts, count: _local(parts, count, method, timeout))
        expected = {batch['batch_id']: batch for batch in batches}
        received = set()
        for envelope in transport(batches, workers):
            batch_id = envelope.get('batch_id') if isinstance(envelope, dict) else None
            if batch_id not in expected:
                raise ValueError(f'Transport returned unknown batch: {batch_id}')
            if batch_id in received:
                raise ValueError(f'Transport returned duplicate batch: {batch_id}')
            received.add(batch_id)
            complete = validate_batch_result(expected[batch_id], envelope)
            if on_batch:
                on_batch(complete)
            yield from complete['rows']
        if len(received) != len(batches):
            missing = [batch['batch_id'] for batch in batches if batch['batch_id'] not in received]
            raise OSError(f'Transport omitted {len(missing)} batch(es): {", ".join(missing)}')
    return runner
