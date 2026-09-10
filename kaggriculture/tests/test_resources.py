"""Concurrent worktrees must share one physical match-worker budget."""
import multiprocessing
import os
import time

import pytest

from arena.resources import capacity, resource_lease


def _contender(root, current, peak, completed):
    with resource_lease(1, cpu_budget=2, memory_budget_mb=1024,
                        memory_per_worker_mb=256, root=root):
        with current.get_lock():
            current.value += 1
            peak.value = max(peak.value, current.value)
        time.sleep(.15)
        with current.get_lock():
            current.value -= 1
        with completed.get_lock():
            completed.value += 1


def _crash_with_lease(root):
    with resource_lease(1, cpu_budget=1, memory_budget_mb=512,
                        memory_per_worker_mb=256, root=root):
        os._exit(17)


def test_three_processes_never_cross_the_global_limit_and_all_progress(tmp_path):
    context = multiprocessing.get_context('fork')
    current, peak, completed = (context.Value('i', 0), context.Value('i', 0),
                                context.Value('i', 0))
    processes = [context.Process(target=_contender,
                                 args=(str(tmp_path), current, peak, completed))
                 for _ in range(3)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(5)

    assert all(process.exitcode == 0 for process in processes)
    assert completed.value == 3
    assert peak.value == 2


def test_kernel_releases_resources_after_holder_crashes(tmp_path):
    context = multiprocessing.get_context('fork')
    process = context.Process(target=_crash_with_lease, args=(str(tmp_path),))
    process.start()
    process.join(5)
    assert process.exitcode == 17

    started = time.monotonic()
    with resource_lease(1, cpu_budget=1, memory_budget_mb=512,
                        memory_per_worker_mb=256, root=tmp_path):
        pass
    assert time.monotonic() - started < 1


def test_request_is_adjusted_to_cpu_and_memory_capacity(tmp_path):
    with pytest.warns(RuntimeWarning, match='adjusted to 2'):
        with resource_lease(8, cpu_budget=3, memory_budget_mb=1100,
                            memory_per_worker_mb=300, root=tmp_path) as plan:
            assert plan['requested_workers'] == 8
            assert plan['granted_workers'] == 2
            assert plan['reserved_memory_mb'] == 1024


def test_memory_smaller_than_one_rounded_worker_is_rejected():
    with pytest.raises(ValueError, match='cannot fit one'):
        capacity(cpu_budget=4, memory_budget_mb=300, memory_per_worker_mb=300)
    with pytest.raises(ValueError, match='CPU budget must be a positive integer'):
        capacity(cpu_budget=0)
