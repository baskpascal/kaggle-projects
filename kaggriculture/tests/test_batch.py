"""Batching is what a second machine will distribute, so it must be invisible in the rows.

The reason the batch exists at all is that distributing one *match* per remote task would
undo the isolation: Ray reuses workers between tasks, and one process per game is the only
thing that undoes what a bundle does at import time. So the batch is the unit of
distribution and the match stays the unit of isolation -- and every guarantee that held for
a direct run has to still hold one level down.
"""
import os
import threading

import pytest

from arena.batch import (adaptive_batch_size, batched_runner, make_batch, partition,
                         run_batch, worker_budget)
from arena.parallel import matches

JOB = dict(candidate='pass', opponent='pass', backend='fast', telemetry_enabled=False)

HANGS_FOREVER = '''
import signal, time

def agent(observation, configuration=None):
    signal.signal(signal.SIGALRM, signal.SIG_IGN)
    signal.setitimer(signal.ITIMER_REAL, 0)
    while True:
        time.sleep(.05)
'''


def jobs(count, first=1800):
    return [dict(JOB, seed=first + index, seat=index % 2) for index in range(count)]


def test_partition_covers_every_job_once_and_in_order():
    work = jobs(7)
    batches = partition(work, 3)
    assert [len(batch) for batch in batches] == [3, 3, 1]
    assert [job for batch in batches for job in batch] == work
    assert partition([], 4) == []
    with pytest.raises(ValueError, match='batch size must be positive'):
        partition(work, 0)


def test_adaptive_batches_keep_at_least_eight_waves_per_slot():
    assert adaptive_batch_size(8000, 14) == 32
    assert adaptive_batch_size(32, 14) == 1
    assert adaptive_batch_size(0, 14) == 1
    with pytest.raises(ValueError):
        adaptive_batch_size(10, 0)


def test_the_worker_budget_leaves_the_host_usable_and_never_returns_zero():
    assert worker_budget(cpus_free=2, cpus=16) == 14
    assert worker_budget(cpus_free=0, cpus=16) == 16
    # A run that makes the machine unusable gets killed by a human, which is worse than slow.
    assert worker_budget(cpus_free=4, cpus=2) == 1
    assert worker_budget(cpus_free=1, cpus=1) == 1
    with pytest.raises(ValueError, match='cannot be negative'):
        worker_budget(cpus_free=-1)


def test_batching_does_not_change_a_single_row():
    """The whole point: an aggregate must not depend on how the work was cut up."""
    work = jobs(4)
    direct = [(row['seed'], row['seat'], row['score'], row['money'], row['opponent_money'])
              for row in matches(work, workers=2)]
    for size in (1, 3, 10):
        batched = [(row['seed'], row['seat'], row['score'], row['money'], row['opponent_money'])
                   for row in batched_runner(size=size)(work, 2)]
        assert batched == direct, size


def test_an_envelope_carries_where_and_how_long():
    envelope = run_batch(jobs(2), workers=2)
    assert envelope['games'] == 2 and len(envelope['rows']) == 2
    assert envelope['wall_seconds'] > 0 and envelope['pid'] == os.getpid()
    assert envelope['hostname']
    assert envelope['job_ids'] == [row['job_id'] for row in envelope['rows']]
    assert all(row['batch_id'] == envelope['batch_id'] for row in envelope['rows'])


def test_a_transport_that_loses_a_batch_is_refused():
    """A remote transport that drops work must not look like a completed run."""
    def lossy(batches, workers):
        for batch in batches[:-1]:
            yield run_batch(batch, workers=workers)
    with pytest.raises(OSError, match='omitted'):
        list(batched_runner(size=2, map_batches=lossy)(jobs(4), 2))


def test_a_batch_that_finishes_second_is_handed_over_first(tmp_path):
    """Holding batch 2 until batch 1 lands is how a dying driver loses finished work.

    The rows leave the runner in arrival order on purpose; `arena.jobs.execute` records
    each one by its own `job_id` and puts the plan's order back afterwards, which is what
    `test_the_report_is_identical_whatever_order_the_games_arrive_in` pins.
    """
    work = jobs(4)

    def reversed_transport(batches, workers):
        yield run_batch(batches[1], workers=workers)
        yield run_batch(batches[0], workers=workers)

    seen = []
    runner = batched_runner(size=2, map_batches=reversed_transport, on_batch=seen.append)
    rows = list(runner(work, 2))
    assert [row['seed'] for row in rows] == [job['seed'] for job in work[2:] + work[:2]]
    assert len(seen) == 2, 'every envelope is handed over as it arrives'
    assert {row['seed'] for row in rows} == {job['seed'] for job in work}


def test_a_duplicate_or_substituted_batch_is_refused():
    work = jobs(4)

    def duplicate(batches, workers):
        result = run_batch(batches[0], workers=workers)
        yield result
        yield result

    with pytest.raises(ValueError, match='duplicate batch'):
        list(batched_runner(size=2, map_batches=duplicate)(work, 2))

    def substitute(batches, workers):
        result = run_batch(batches[0], workers=workers)
        result['job_ids'][0] = 'another-job'
        yield result

    with pytest.raises(OSError, match='expected jobs'):
        list(batched_runner(size=2, map_batches=substitute)(work, 2))


def test_worker_refuses_an_artifact_hash_that_differs_from_the_head():
    batch = make_batch([{**jobs(1)[0], 'candidate_hash': 'not-the-pass-agent'}])
    with pytest.raises(ValueError, match='candidate_hash mismatch'):
        run_batch(batch, workers=1)


def test_the_deadline_still_kills_a_hanging_game_inside_a_batch(tmp_path):
    path = tmp_path / 'hang.py'
    path.write_text(HANGS_FOREVER, encoding='utf-8')
    work = [dict(JOB, candidate=str(path), seed=1830, seat=0), dict(JOB, seed=1831, seat=0)]
    rows = list(batched_runner(size=2, timeout=4)(work, 2))
    assert rows[0]['timed_out'] is True and rows[0]['seed'] == 1830
    assert rows[1].get('timed_out') is None and rows[1]['steps'] > 0


def test_the_deadline_holds_when_the_driver_is_not_the_main_thread(tmp_path):
    """Issue #43's stated risk, checked rather than assumed.

    `signal.setitimer` only works on the main thread of a POSIX process, so a driver running
    off the main thread would silently degrade the in-process timer. It does not matter
    here: every match runs in the main thread of its own child, and the kill lives in the
    parent, so neither half of the barrier depends on who called `run_batch`.
    """
    path = tmp_path / 'hang.py'
    path.write_text(HANGS_FOREVER, encoding='utf-8')
    captured = {}

    def drive():
        captured['rows'] = list(batched_runner(size=1, timeout=4)(
            [dict(JOB, candidate=str(path), seed=1840, seat=0)], 1))

    thread = threading.Thread(target=drive)
    thread.start()
    thread.join(60)
    assert not thread.is_alive(), 'the hang was not killed from a non-main-thread driver'
    assert captured['rows'][0]['timed_out'] is True


def test_a_batched_run_reaches_the_store_like_any_other(tmp_path):
    """The runner seam is `arena.jobs.execute`'s, so resume keeps working through it."""
    from arena.jobs import JobStore, execute, plan
    work = plan('pass', ['pass'], [1850, 1851], backend='fast', split='dev')
    with JobStore(tmp_path / 'store.sqlite3') as store:
        rows = execute(work, store, 'dev', batched_runner(size=1), workers=2)
        assert len(rows) == 4
        assert store.pending(work) == []
        # Second call replays nothing: every job is already recorded.
        again = execute(work, store, 'dev', _refuse_to_run, workers=2)
        assert len(again) == 4


def _refuse_to_run(jobs, workers):
    raise AssertionError('a resumed run must not replay a finished job')
    yield  # pragma: no cover - generator marker


def test_host_contribution_rolls_up_batches_and_matches_per_machine():
    from arena.batch import host_contribution
    rows = ([{'hostname': 'DESKTOP-A', 'batch_id': 'b1'}] * 3 +
            [{'hostname': 'DESKTOP-A', 'batch_id': 'b2'}] * 2 +
            [{'hostname': 'DESKTOP-B', 'batch_id': 'b3'}] * 5)
    assert host_contribution(rows) == [
        {'hostname': 'DESKTOP-A', 'batches': 2, 'matches': 5, 'share': .5},
        {'hostname': 'DESKTOP-B', 'batches': 1, 'matches': 5, 'share': .5},
    ]


def test_host_contribution_makes_a_barely_used_node_visible():
    from arena.batch import host_contribution
    rows = ([{'hostname': 'DESKTOP-A', 'batch_id': f'b{i}'} for i in range(99)] +
            [{'hostname': 'DESKTOP-B', 'batch_id': 'z'}])
    idle = [host for host in host_contribution(rows) if host['share'] < .05]
    assert [host['hostname'] for host in idle] == ['DESKTOP-B']


def test_host_contribution_is_empty_without_rows():
    from arena.batch import host_contribution
    assert host_contribution([]) == []
