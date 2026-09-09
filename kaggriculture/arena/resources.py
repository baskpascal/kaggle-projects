"""Host-wide CPU and memory leases shared by every checkout and worktree.

Process-local worker counts are unsafe when several experiments run at once: each one
sees the whole machine and starts a full pool.  Advisory file locks are a small local
scheduler with the property we need here: locks are shared across worktrees and the
kernel releases them when a process exits, is cancelled, or is killed.

One FIFO gate serialises acquisition.  A waiter keeps its place while current holders can
still release their token locks, so a stream of small experiments cannot starve a larger
one.  Ray workers use the same leases; this lets a local experiment and Ray tasks share a
host without oversubscribing it even though they belong to different schedulers.
"""
from contextlib import contextmanager
import fcntl
import math
import os
from pathlib import Path
import socket
import time
import warnings

CPU_FREE = 1
MEMORY_FRACTION = .75
MEMORY_PER_WORKER_MB = 512
MEMORY_TOKEN_MB = 256


def _positive_int(name, value):
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{name} must be a positive integer') from exc
    if value < 1:
        raise ValueError(f'{name} must be a positive integer')
    return value


def logical_cpus():
    return (len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity')
            else (os.cpu_count() or 1))


def total_memory_mb(path=Path('/proc/meminfo')):
    """Stable physical memory capacity, not the momentary free-memory reading."""
    try:
        for line in path.read_text().splitlines():
            if line.startswith('MemTotal:'):
                return max(1, int(line.split()[1]) // 1024)
    except (OSError, ValueError, IndexError):
        pass
    return 4096


def default_root():
    configured = os.environ.get('KAGGRICULTURE_RESOURCE_DIR')
    if configured:
        return Path(configured)
    # Configuration is machine state, deliberately outside every repository/worktree.
    return Path.home() / '.config' / 'kaggriculture' / 'resource-pool' / socket.gethostname()


def capacity(*, cpu_budget=None, memory_budget_mb=None, memory_per_worker_mb=None):
    def selected(explicit, environment, default):
        return explicit if explicit is not None else os.environ.get(environment, default)

    cpu_budget = _positive_int(
        'CPU budget', selected(cpu_budget, 'ARENA_CPU_BUDGET',
                               max(1, logical_cpus() - CPU_FREE)))
    memory_budget_mb = _positive_int(
        'memory budget', selected(memory_budget_mb, 'ARENA_MEMORY_BUDGET_MB',
                                  int(total_memory_mb() * MEMORY_FRACTION)))
    memory_per_worker_mb = _positive_int(
        'memory per worker', selected(memory_per_worker_mb, 'ARENA_MEMORY_PER_WORKER_MB',
                                      MEMORY_PER_WORKER_MB))
    memory_units = math.ceil(memory_per_worker_mb / MEMORY_TOKEN_MB)
    memory_workers = (memory_budget_mb // MEMORY_TOKEN_MB) // memory_units
    if memory_workers < 1:
        raise ValueError(f'Memory budget {memory_budget_mb} MiB cannot fit one '
                         f'{memory_per_worker_mb} MiB match worker')
    return {'cpu_budget': cpu_budget, 'memory_budget_mb': memory_budget_mb,
            'memory_per_worker_mb': memory_per_worker_mb,
            'worker_capacity': min(cpu_budget, memory_workers)}


def _open_tokens(directory, prefix, count):
    directory.mkdir(parents=True, exist_ok=True)
    return [open(directory / f'{prefix}-{index:05d}.lock', 'a+b') for index in range(count)]


def _try_lock(files, count):
    locked = []
    for handle in files:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked.append(handle)
            if len(locked) == count:
                return locked
        except BlockingIOError:
            continue
    for handle in locked:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return []


def _unlock(files):
    for handle in files:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _locked_count(files):
    """Approximate simultaneous reservations at this instant, including our own."""
    locked = 0
    for handle in files:
        probe = open(handle.name, 'a+b')
        try:
            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
        except BlockingIOError:
            locked += 1
        finally:
            probe.close()
    return locked


@contextmanager
def resource_lease(requested_workers, *, cpu_budget=None, memory_budget_mb=None,
                   memory_per_worker_mb=None, root=None, poll_seconds=.05):
    """Reserve match slots globally and yield the effective execution plan.

    Requests larger than host capacity are reduced rather than deadlocking forever.  All
    ordinary requests are granted atomically, so a pool never starts at a size that can
    only partially fit.  The gate is held while waiting, preserving queue order; holders
    do not need it to release their tokens.
    """
    requested = _positive_int('workers', requested_workers)
    limits = capacity(cpu_budget=cpu_budget, memory_budget_mb=memory_budget_mb,
                      memory_per_worker_mb=memory_per_worker_mb)
    granted = min(requested, limits['worker_capacity'])
    adjustment = None
    if granted != requested:
        adjustment = (f'requested {requested} workers, adjusted to {granted}: host limit is '
                      f'{limits["cpu_budget"]} CPUs and {limits["memory_budget_mb"]} MiB')
        warnings.warn(adjustment, RuntimeWarning, stacklevel=2)
    root = Path(root) if root is not None else default_root()
    root.mkdir(parents=True, exist_ok=True)
    gate = open(root / 'queue.lock', 'a+b')
    cpu_files = _open_tokens(root, 'cpu', limits['cpu_budget'])
    memory_units = math.ceil(limits['memory_per_worker_mb'] / MEMORY_TOKEN_MB)
    memory_tokens = limits['memory_budget_mb'] // MEMORY_TOKEN_MB
    memory_files = _open_tokens(root, 'memory', memory_tokens)
    cpu_locked = memory_locked = []
    started = time.monotonic()
    try:
        fcntl.flock(gate.fileno(), fcntl.LOCK_EX)
        while not cpu_locked:
            cpu_locked = _try_lock(cpu_files, granted)
            if cpu_locked:
                memory_locked = _try_lock(memory_files, granted * memory_units)
                if not memory_locked:
                    _unlock(cpu_locked)
                    cpu_locked = []
            if not cpu_locked:
                time.sleep(poll_seconds)
        queue_wait = time.monotonic() - started
        fcntl.flock(gate.fileno(), fcntl.LOCK_UN)
        plan = {**limits, 'requested_workers': requested, 'granted_workers': granted,
                'queue_wait_seconds': queue_wait,
                'global_workers_at_grant': _locked_count(cpu_files),
                'memory_token_mb': MEMORY_TOKEN_MB,
                'reserved_memory_mb': granted * memory_units * MEMORY_TOKEN_MB,
                'adjustment': adjustment}
        yield plan
    finally:
        # Unlocking is idempotent enough for partial acquisition and exception paths.
        _unlock(memory_locked)
        _unlock(cpu_locked)
        try:
            fcntl.flock(gate.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        for handle in (*cpu_files, *memory_files, gate):
            handle.close()
