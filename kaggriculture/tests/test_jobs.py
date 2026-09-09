"""Issue #42: an interrupted run resumes instead of restarting.

The executor is injected everywhere here. That is not only for speed: `arena/parallel.py`
is the process-isolation layer, and a store that can only be tested through it could not
be trusted to be the reason a run survived.
"""
import json
import sqlite3

import pytest

from arena.jobs import (INFRASTRUCTURE, JobStore, execute, id_of_row, job_id, plan,
                        single_provenance)


def row(opponent='a', seed=0, seat=0, score=1., candidate_hash='cand', **extra):
    return {'candidate': 'us', 'opponent': opponent, 'seed': seed, 'seat': seat,
            'backend': 'fast', 'candidate_hash': candidate_hash,
            'opponent_hash': opponent + '-hash', 'score': score, 'money': 10.,
            'opponent_money': 5., 'margin': 5., 'failures': [], 'opponent_failures': [],
            'wall_seconds': .1, 'environment': {'engine': '1.32.7'}, **extra}


def spec_of(r, split='dev'):
    return {**{k: r[k] for k in ('candidate', 'opponent', 'seed', 'seat', 'backend',
                                 'candidate_hash', 'opponent_hash')},
            'split': split, 'job_id': id_of_row(r, split)}


def specs(opponents=('a',), seeds=range(1), split='dev'):
    """The same identity `plan()` builds, with the hashes `row()` reports back."""
    return [spec_of(row(opponent=o, seed=seed, seat=seat), split)
            for seed in seeds for o in opponents for seat in (0, 1)]


def emit(job):
    return row(opponent=job['opponent'], seed=job['seed'], seat=job['seat'])


def store(tmp_path, name='jobs.sqlite3'):
    return JobStore(tmp_path / name, provenance={'git_commit': 'abc', 'git_dirty': 0})


def test_plan_hashes_each_agent_once_and_covers_both_seats():
    jobs = plan('random', ['starter', 'pass'], [7, 8], split='dev')
    assert len(jobs) == 8
    assert {j['seat'] for j in jobs} == {0, 1}
    assert len({j['job_id'] for j in jobs}) == 8
    assert len({j['candidate_hash'] for j in jobs}) == 1
    assert all(j['job_id'] == job_id(j) for j in jobs)


def test_identity_covers_the_agent_hashes_not_the_paths():
    base = spec_of(row())
    assert job_id(base) == job_id(dict(base))
    # An edited, uncommitted candidate keeps its path and its commit, and must not be
    # allowed to reuse a result produced by the previous bytes.
    assert job_id({**base, 'candidate_hash': 'edited'}) != job_id(base)
    assert job_id({**base, 'seat': 1}) != job_id(base)
    assert job_id({**base, 'split': 'validation'}) != job_id(base)
    with pytest.raises(ValueError, match='candidate_hash'):
        job_id({**base, 'candidate_hash': None})


def test_the_same_game_recorded_twice_is_stored_once(tmp_path):
    with store(tmp_path) as jobs:
        first = jobs.record(row(), 'dev')
        second = jobs.record(row(), 'dev')
        assert first == second
        assert len(jobs.rows()) == 1
        assert jobs.completed() == {first}


def test_an_interrupted_run_resumes_without_repeating_or_losing_a_game(tmp_path):
    jobs = specs(('a', 'b'), range(50))
    assert len(jobs) == 200
    played = []

    def flaky(payload, workers, stop_after):
        for index, job in enumerate(payload):
            if index == stop_after:
                raise OSError('worker host went away')
            played.append((job['opponent'], job['seed'], job['seat']))
            yield emit(job)

    with store(tmp_path) as book:
        # Two interruptions, then a clean pass. Nothing is replayed and nothing is lost.
        rows = execute(jobs, book, 'dev',
                       lambda p, w: flaky(p, w, 60 if len(played) == 0 else
                                          40 if len(played) == 60 else None),
                       workers=2, attempts=4)
    assert len(rows) == 200
    assert len(played) == 200, 'a finished game was replayed'
    assert len(set(played)) == 200, 'a game was played twice'


def test_a_second_process_picks_up_exactly_what_the_first_left(tmp_path):
    jobs = specs(('a',), range(20))
    half = jobs[:20]
    with store(tmp_path) as first:
        for job in half:
            first.record(emit(job), 'dev')
    with store(tmp_path) as second:
        pending = second.pending(jobs)
        assert len(pending) == 20
        assert {j['job_id'] for j in pending}.isdisjoint({j['job_id'] for j in half})


def test_only_infrastructure_faults_are_retried(tmp_path):
    jobs = specs()
    attempts = {'n': 0}

    def crashing(payload, workers, exc):
        attempts['n'] += 1
        raise exc
        yield  # pragma: no cover

    with store(tmp_path) as book:
        with pytest.raises(RuntimeError, match='Infrastructure faults exhausted'):
            execute(jobs, book, 'dev', lambda p, w: crashing(p, w, INFRASTRUCTURE[0]('pool broke')),
                    attempts=3)
        assert attempts['n'] == 3
        attempts['n'] = 0
        # An agent exception is a result, not a fault. Repeating it would make the
        # harness select agents that work if you try a few times.
        with pytest.raises(ZeroDivisionError):
            execute(jobs, book, 'dev', lambda p, w: crashing(p, w, ZeroDivisionError()), attempts=3)
        assert attempts['n'] == 1


def test_an_agent_failure_is_recorded_as_a_result_and_never_replayed(tmp_path):
    jobs = specs()
    calls = {'n': 0}

    def runner(payload, workers):
        calls['n'] += 1
        for job in payload:
            yield {**emit(job), 'score': 0., 'failures': ['agent raised']}

    with store(tmp_path) as book:
        execute(jobs, book, 'dev', runner)
        execute(jobs, book, 'dev', runner)
    assert calls['n'] == 1
    with store(tmp_path) as book:
        with book._session() as connection:
            recorded = list(connection.execute('SELECT failed, outcome FROM jobs'))
    assert [dict(r) for r in recorded] == [{'failed': 1, 'outcome': 'loss'}] * 2


def test_a_mixed_result_set_is_refused_by_the_aggregator():
    assert single_provenance([row(), row(seed=1)]) is True
    with pytest.raises(ValueError, match='Mixed candidate hashes'):
        single_provenance([row(), row(seed=1, candidate_hash='other')])
    with pytest.raises(ValueError, match='Mixed opponent hashes'):
        single_provenance([row(), {**row(seed=1), 'opponent_hash': 'rebuilt'}])
    with pytest.raises(ValueError, match='engine fingerprint'):
        single_provenance([row(), {**row(seed=1), 'environment': {'engine': '1.33.0'}}])


def test_execute_hard_fails_a_result_outside_the_admitted_plan(tmp_path):
    jobs = specs()

    def wrong_artifact(payload, workers):
        for job in payload:
            yield row(opponent=job['opponent'], seed=job['seed'], seat=job['seat'],
                      candidate_hash='different')

    with store(tmp_path) as book:
        with pytest.raises(ValueError, match='admitted plan'):
            execute(jobs, book, 'dev', wrong_artifact, attempts=3)
        assert book.rows() == []


def test_provenance_columns_travel_with_every_row(tmp_path):
    with store(tmp_path) as jobs:
        jobs.record(row(), 'dev')
        with jobs._session() as connection:
            record = dict(connection.execute(
                'SELECT hostname, git_commit, git_dirty, split, outcome, row FROM jobs').fetchone())
    assert record['git_commit'] == 'abc' and record['git_dirty'] == 0
    assert record['hostname'] and record['split'] == 'dev' and record['outcome'] == 'win'
    assert json.loads(record['row'])['margin'] == 5.


def test_the_store_survives_a_transient_filesystem_fault(tmp_path, monkeypatch):
    # The checkout is on a 9p mount where SQLite reports a transient disk I/O error
    # under parallel load. Losing a finished game to it would defeat the point.
    jobs = store(tmp_path)
    real, calls = sqlite3.connect, {'n': 0}

    def flaky_connect(*args, **kwargs):
        calls['n'] += 1
        if calls['n'] == 1:
            raise sqlite3.OperationalError('disk I/O error')
        return real(*args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', flaky_connect)
    monkeypatch.setattr('arena.jobs.SQLITE_BACKOFF', 0.)
    assert jobs.record(row(), 'dev')
    monkeypatch.setattr(sqlite3, 'connect',
                        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError('no such column')))
    with pytest.raises(sqlite3.OperationalError, match='no such column'):
        jobs.record(row(seed=9), 'dev')
    jobs.close()


def test_an_empty_plan_and_a_zero_attempt_budget_raise(tmp_path):
    with store(tmp_path) as jobs:
        assert execute([], jobs, 'dev', lambda p, w: iter(())) == []
        with pytest.raises(ValueError, match='attempts must be positive'):
            execute(specs(), jobs, 'dev', lambda p, w: iter(()), attempts=0)


def test_a_ray_worker_cannot_open_the_authoritative_store(tmp_path, monkeypatch):
    monkeypatch.setenv('ARENA_ROLE', 'ray-worker')
    with pytest.raises(PermissionError, match='authoritative job store'):
        JobStore(tmp_path / 'worker-must-not-write.sqlite3')
    assert not (tmp_path / 'worker-must-not-write.sqlite3').exists()


def test_an_incomplete_runner_is_retried_as_infrastructure(tmp_path):
    calls = {'count': 0}

    def incomplete(payload, workers):
        calls['count'] += 1
        yield from ()

    with store(tmp_path) as jobs:
        with pytest.raises(RuntimeError, match='omitted 2 admitted job'):
            execute(specs(), jobs, 'dev', incomplete, attempts=2)
    assert calls['count'] == 2


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / 'seeds.json'
    path.write_text(json.dumps(dict(schema_version=1, revision=1, holdout_start=9000000,
        history=[], entries=[
            dict(id='dev', start=1000, end=1100, classification='dev', provenance=['test']),
            dict(id='validation', start=100100, end=100200, classification='validation',
                 provenance=['test']),
            dict(id='holdout', start=9000000, end=None, classification='holdout',
                 provenance=['test'])])))
    return path


def engine(monkeypatch, played, fail_after=None):
    """Stand in for arena.parallel.matches, optionally dying part-way through."""
    from arena import league
    from arena.agents import agent_hash

    def matches(jobs, workers):
        for job in jobs:
            if fail_after is not None and len(played) >= fail_after:
                raise OSError('worker host went away')
            played.append((job['candidate'], job['opponent'], job['seed'], job['seat']))
            yield dict(job, candidate_hash=agent_hash(job['candidate']),
                       opponent_hash=agent_hash(job['opponent']), environment={},
                       configuration={'seed': job['seed']}, score=.5, margin=0, money=100,
                       opponent_money=100, failures=[], opponent_failures=[], unsold_items=0,
                       runtime_ms=[1], audit={}, sales={}, steps=719, wall_seconds=.01)
    monkeypatch.setattr(league, 'stream', matches)


def test_a_resumed_validation_run_consumes_the_seed_registry_exactly_once(tmp_path, monkeypatch, registry):
    from arena import league
    from arena.seeds import load_registry
    seeds = list(range(100100, 100200))
    played = []
    engine(monkeypatch, played, fail_after=120)
    with pytest.raises(RuntimeError, match='Infrastructure faults exhausted'):
        league.run_league('pass', ['random'], seeds, workers=2, split='validation',
                          output=str(tmp_path / 'run'), registry_path=registry, attempts=1)
    state, _ = load_registry(registry)
    assert state['revision'] == 2, 'the head consumes the reserved seeds before the first game'
    assert len(played) == 120

    engine(monkeypatch, played)
    rows, _, _ = league.run_league('pass', ['random'], seeds, workers=2, split='validation',
                                   output=str(tmp_path / 'run'), registry_path=registry,
                                   resume=True)
    resumed, _ = load_registry(registry)
    # A resume re-declares the same batch; admit_run readmits it instead of burning the
    # reserved validation seeds a second time. Two checkouts must never both consume.
    assert resumed['revision'] == 2
    assert len(rows) == 200
    assert len(played) == 200 and len(set(played)) == 200


def test_a_second_run_over_an_existing_store_refuses_without_resume(tmp_path, monkeypatch, registry):
    from arena import league
    played = []
    engine(monkeypatch, played)
    league.run_league('pass', ['random'], list(range(1000, 1010)), workers=2, split='dev',
                      output=str(tmp_path / 'run'), registry_path=registry)
    assert league.store_path(tmp_path / 'run').exists()
    with pytest.raises(ValueError, match='pass resume to continue it'):
        league.run_league('pass', ['random'], list(range(1000, 1010)), workers=2, split='dev',
                          output=str(tmp_path / 'run2'), registry_path=registry,
                          store=league.store_path(tmp_path / 'run'))


def test_the_store_lives_beside_the_report_not_inside_it(tmp_path):
    from arena import league
    # write_report creates its directory with exist_ok=False, so a store inside it could
    # not outlive the interrupted run it exists to rescue.
    assert league.store_path(tmp_path / 'run') == tmp_path / 'run.jobs.sqlite3'
    assert league.store_path(None) is None
