"""Issue #55: routine gates must not carry a full economic trace per game."""
import inspect
import json

import pytest

from arena import agents
from arena.jobs import job_id, plan
from arena.match import run_match
from eval.metrics import reconcile, summarize


@pytest.fixture(scope='module')
def profiles():
    return {name: run_match('pass', 'starter', 1977, seat=1, evidence_profile=name)
            for name in ('score', 'audit', 'full')}


def test_profiles_have_identical_outcomes_and_explicit_payloads(profiles):
    stable = ('score', 'money', 'opponent_money', 'margin', 'steps',
              'failures', 'opponent_failures')
    assert len({json.dumps({key: row[key] for key in stable}, sort_keys=True)
                for row in profiles.values()}) == 1
    assert profiles['score']['runtime_ms'] is None
    assert profiles['score']['audit'] == {} and profiles['score']['daily'] is None
    assert set(profiles['audit']['runtime_ms']) == {'p50', 'p95', 'p99', 'max'}
    assert profiles['audit']['audit'] and profiles['audit']['daily'] is None
    assert isinstance(profiles['full']['runtime_ms'], list) and profiles['full']['daily']


def test_score_row_is_at_least_eighty_percent_smaller_than_full(profiles):
    size = {name: len(json.dumps(row, separators=(',', ':')))
            for name, row in profiles.items()}
    assert size['score'] <= size['full'] * .2


def test_intentionally_compact_rows_do_not_report_missing_telemetry(profiles):
    assert reconcile(profiles['score']) == []
    summary = summarize([profiles['score']])
    assert summary['evidence_profiles'] == ['score']
    assert summary['daily'] is None and summary['runtime_ms']['max'] == 0.


def test_profile_is_part_of_job_and_run_identity():
    score = plan('pass', ['starter'], [1000], evidence_profile='score')[0]
    full = plan('pass', ['starter'], [1000], evidence_profile='full')[0]
    assert score['job_id'] != full['job_id']
    assert job_id(score) != job_id(full)


def test_callback_signature_is_not_inspected_per_turn(monkeypatch):
    calls = {'count': 0}
    original = inspect.signature

    def counted(function):
        calls['count'] += 1
        return original(function)

    monkeypatch.setattr(agents.inspect, 'signature', counted)
    run_match('pass', 'starter', 1978, evidence_profile='score')
    assert calls['count'] == 2


def test_replay_requires_the_full_profile(tmp_path):
    with pytest.raises(ValueError, match='full evidence profile'):
        run_match('pass', 'starter', 1979, evidence_profile='score',
                  replay=tmp_path / 'replay.json')
