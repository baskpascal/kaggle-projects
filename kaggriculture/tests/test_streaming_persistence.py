"""Finished work must reach the store before the run is over, not when order allows.

Both levels of the executor used to put results back into plan order before handing them
on: `arena.parallel.matches` held rows until the next index arrived, and
`arena.batch.batched_runner` held envelopes until the next batch did. A game or a batch
that finished early therefore sat in the driver's RAM -- 116-134 KB a row -- until the slow
one ahead of it landed, and a driver that died in that window replayed work it had already
computed. These tests pin the fix and, just as importantly, pin that the *report* did not
move: arrival order is now free, and the plan's order is reimposed where the evidence is
materialised.
"""
import json
import time

import pytest

from arena.batch import batched_runner, make_batch, partition, run_batch
from arena.jobs import JobStore, execute, plan
from arena.parallel import matches, stream

SPLIT = 'dev'


def row_for(job, **over):
    return {**{field: job[field] for field in
               ('candidate', 'opponent', 'seed', 'seat', 'backend')},
            'candidate_hash': job['candidate_hash'], 'opponent_hash': job['opponent_hash'],
            'score': .5, 'money': 100., 'opponent_money': 100., 'margin': 0.,
            'environment': {}, 'configuration': {'seed': job['seed']}, 'steps': 719,
            'failures': [], 'opponent_failures': [], 'runtime_ms': [1], 'audit': {},
            'sales': {}, 'unsold_items': 0, 'wall_seconds': .01, **over}


def work(n=6):
    """`n` seeds against one opponent, which is 2n jobs: every seed is played on both seats."""
    return plan('pass', ['starter'], list(range(1000, 1000 + n)), split=SPLIT)


def key(record):
    return record['seed'], record['seat']


# --- the store sees a game when it finishes -------------------------------------------

def test_a_late_first_game_does_not_hold_back_the_ones_behind_it(tmp_path):
    """The failure this issue names: everything after a slow game stuck in RAM."""
    jobs = work(4)
    recorded = []

    def runner(given, workers):
        given = list(given)
        for job in given[1:]:              # the first game never finishes in time
            yield row_for(job)
        yield row_for(given[0])

    with JobStore(tmp_path / 'run.sqlite3', provenance={}) as store:
        def watch(row):
            recorded.append((key(row), len(store.completed())))
        execute(jobs, store, SPLIT, runner, workers=2, on_row=watch)
    # Every game was already durable when the next one arrived, so the count grows by one
    # per row instead of jumping at the end.
    assert [count for _, count in recorded] == list(range(1, len(jobs) + 1))
    assert [identity for identity, _ in recorded] == \
        [key(job) for job in jobs[1:]] + [key(jobs[0])]


def test_a_driver_that_dies_mid_run_keeps_what_had_already_landed(tmp_path):
    jobs = work(4)
    store_path = tmp_path / 'run.sqlite3'

    def dies_after_two(given, workers):
        given = list(given)
        yield row_for(given[-1])           # arrives out of plan order on purpose
        yield row_for(given[-2])
        raise OSError('driver went away')

    with JobStore(store_path, provenance={}) as store:
        with pytest.raises(RuntimeError, match='Infrastructure faults exhausted'):
            execute(jobs, store, SPLIT, dies_after_two, workers=2, attempts=1)

    with JobStore(store_path, provenance={}) as store:
        assert len(store.completed()) == 2, 'the two finished games survived the crash'
        replayed = []

        def finishes(given, workers):
            for job in given:
                replayed.append(job['seed'])
                yield row_for(job)

        rows = execute(jobs, store, SPLIT, finishes, workers=2)
    assert sorted(replayed) == sorted(job['seed'] for job in jobs[:-2]), \
        'the recorded games are not played again'
    assert [key(row) for row in rows] == [key(job) for job in jobs]


def test_the_peak_of_unsaved_work_is_the_window_in_flight(tmp_path):
    """Nothing computed is held back, so 'unsaved' never exceeds what is being handed over."""
    jobs = work(8)
    unsaved = []
    with JobStore(tmp_path / 'run.sqlite3', provenance={}) as store:
        def runner(given, workers):
            for offset, job in enumerate(reversed(list(given))):
                unsaved.append(offset + 1 - len(store.completed()))
                yield row_for(job)
        execute(jobs, store, SPLIT, runner, workers=4)
    assert max(unsaved) <= 1


# --- the report does not move ----------------------------------------------------------

def test_the_report_is_identical_whatever_order_the_games_arrive_in(tmp_path):
    """Arrival order is free; the evidence is not allowed to notice.

    A report whose digest moves because one node was slow would make every downstream hash
    -- `plan_sha256`, the comparison's row digests, a RunSpec's identity -- a statement
    about the cluster instead of about the games.
    """
    jobs = work(6)
    orders = {'plan': list(jobs), 'reversed': list(reversed(jobs)),
              'interleaved': jobs[1::2] + jobs[::2]}
    produced = {}
    for name, sequence in orders.items():
        def runner(given, workers, sequence=sequence):
            for job in sequence:
                yield row_for(job)
        with JobStore(tmp_path / f'{name}.sqlite3', provenance={}) as store:
            rows = execute(jobs, store, SPLIT, runner, workers=2)
        assert [key(row) for row in rows] == [key(job) for job in jobs]
        produced[name] = json.dumps(rows, sort_keys=True, default=str)
    assert len(set(produced.values())) == 1, 'the report followed the arrival order'


def test_the_store_refuses_to_materialise_a_plan_it_does_not_hold(tmp_path):
    jobs = work(3)
    with JobStore(tmp_path / 'run.sqlite3', provenance={}) as store:
        store.record(row_for(jobs[0]), SPLIT)
        with pytest.raises(OSError, match='absent from the store'):
            store.rows_in_order(jobs)


# --- the batch layer ---------------------------------------------------------------------

def test_an_envelope_is_handed_over_the_moment_it_arrives():
    jobs = work(2)
    batches = [make_batch(specs, index) for index, specs in enumerate(partition(jobs, 2))]
    handed = []

    def out_of_order(parts, workers):
        yield run_batch(batches[1], workers=workers)
        yield run_batch(batches[0], workers=workers)

    runner = batched_runner(size=2, map_batches=out_of_order, on_batch=handed.append)
    rows = list(runner(jobs, 2))
    assert [envelope['batch_id'] for envelope in handed] == \
        [batches[1]['batch_id'], batches[0]['batch_id']]
    assert [row['job_id'] for row in rows] == batches[1]['job_ids'] + batches[0]['job_ids']


def test_a_batch_still_materialises_its_own_rows_in_job_order():
    """Inside the envelope the order is the plan's: it is what the validator checks."""
    jobs = work(2)
    envelope = run_batch(make_batch(jobs), workers=2)
    assert envelope['job_ids'] == [row['job_id'] for row in envelope['rows']]
    assert [key(row) for row in envelope['rows']] == [key(job) for job in jobs]


def test_a_batch_naming_one_game_twice_is_refused():
    """Correlating by contents instead of by position needs the contents to be unique."""
    duplicated = work(1) * 2
    with pytest.raises(ValueError, match='names the same game twice'):
        run_batch(make_batch(duplicated), workers=1)


# --- the match layer ----------------------------------------------------------------------

def test_the_ordered_contract_is_still_a_contract():
    """`experiments/` pairs jobs with rows positionally; that is allowed, and stated."""
    jobs = work(2)
    rows = list(matches(jobs, workers=3))
    assert [key(row) for row in rows] == [key(job) for job in jobs]


def test_streaming_plays_the_same_games_without_promising_an_order():
    jobs = work(2)
    rows = list(stream(jobs, workers=3))
    assert sorted(key(row) for row in rows) == sorted(key(job) for job in jobs)
    assert len(rows) == len(jobs)


def test_a_streamed_game_arrives_before_the_slow_ones_it_was_queued_behind():
    """With one worker per game, the short game must not wait for the long one."""
    jobs = work(1)
    started = time.monotonic()
    first = next(iter(stream(jobs, workers=2)))
    assert key(first) in {key(job) for job in jobs}
    assert time.monotonic() - started < 120
