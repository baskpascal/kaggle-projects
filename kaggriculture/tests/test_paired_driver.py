import json

import pytest

from arena import league, paired
from arena.agents import agent_hash
from arena.seeds import load_registry


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / 'seeds.json'
    path.write_text(json.dumps(dict(schema_version=1, revision=1, holdout_start=9000000,
        history=[], entries=[
            dict(id='dev', start=1000, end=1100, classification='dev', provenance=['test']),
            dict(id='validation', start=100100, end=100200, classification='validation', provenance=['test']),
            dict(id='holdout', start=9000000, end=None, classification='holdout', provenance=['test'])])))
    return path


@pytest.fixture
def fake_engine(monkeypatch, registry):
    calls = []
    # A run spec records the engine fingerprint it was planned under and refuses an empty
    # one, so the stub names itself rather than claiming there is no engine.
    monkeypatch.setattr(paired, 'fingerprint', lambda: {'version': 'stub'})
    def matches(jobs, workers):
        calls.append(jobs)
        for job in jobs:
            if job['seed'] >= 100100:
                state, _ = load_registry(registry)
                assert state['revision'] == 2, 'both legs share one consumption'
                assert len(state['history'][0]['run']['batch_members']) == 2
            yield dict(job, candidate_hash=agent_hash(job['candidate']),
                opponent_hash=agent_hash(job['opponent']), environment={'version': 'stub'},
                configuration={'seed': job['seed']}, score=.5, margin=0,
                money=100, opponent_money=100, failures=[], opponent_failures=[],
                unsold_items=0, runtime_ms=[1], audit={}, sales={}, steps=719, wall_seconds=.01)
    monkeypatch.setattr(league, 'stream', matches)
    return calls


def run(tmp_path, registry, **kwargs):
    return paired.run_pair('pass', 'starter', ['random'], range(100100, 100200),
                           tmp_path / 'run', registry_path=registry, split='validation', **kwargs)


def test_both_100_block_legs_share_one_admission_and_emit_evidence(tmp_path, registry, fake_engine):
    result, gate = run(tmp_path, registry)
    state, _ = load_registry(registry)
    assert len(fake_engine) == 2
    assert len(fake_engine[0]) == len(fake_engine[1]) == 200
    assert state['revision'] == 2 and len(state['history']) == 1
    assert result['seed_blocks'] == 100
    assert gate['verdict'] == 'NO SUBMIT'
    assert result['snapshot']['ratings_sha256']
    for name in ('plan.json', 'spec.json', 'manifest.json', 'comparison.json', 'gate.json',
                 'report.md', 'baseline/matches.jsonl', 'candidate/matches.jsonl'):
        assert (tmp_path / 'run' / name).is_file()


def test_one_spec_drives_the_run_and_the_manifest_binds_its_evidence(tmp_path, registry,
                                                                     fake_engine):
    """Issue #46's 'done when': every number reconstructs to the spec that produced it."""
    from arena import runspec
    comparison, gate = run(tmp_path, registry)
    spec = runspec.load(tmp_path / 'run' / 'spec.json')
    manifest = json.loads((tmp_path / 'run' / 'manifest.json').read_text())
    assert comparison['run_id'] == gate['run_id'] == spec['run_id']
    assert manifest['run_id'] == spec['run_id']
    assert manifest['verdict'] == 'BOUND' and manifest['refusals'] == []
    # The job stores of both legs are part of the evidence, so a number can be traced back
    # to the individual games it came from.
    assert {'spec', 'comparison', 'gate', 'baseline_jobs', 'candidate_jobs'} \
        <= set(manifest['evidence'])
    assert spec['seeds']['split'] == 'validation'
    assert spec['agents']['candidate']['spec'] == 'starter'


def test_how_the_run_was_spread_does_not_change_which_run_it_was(tmp_path, registry,
                                                                 fake_engine):
    """#43 measured batches as bit-exact across nodes; the run id must agree."""
    from arena import runspec
    # `dev` seeds, because the point is two runs of the same experiment and validation
    # seeds are burned by the first one on purpose.
    for name, spread in (('local', None),
                         ('cluster', {'topology': 'ray', 'workers': 64, 'cpus_per_worker': 2})):
        paired.run_pair('pass', 'starter', ['random'], range(1000, 1100), tmp_path / name,
                        registry_path=registry, split='dev', distribution=spread)
    first = runspec.load(tmp_path / 'local' / 'spec.json')
    second = runspec.load(tmp_path / 'cluster' / 'spec.json')
    assert first['run_id'] == second['run_id']
    assert first['distribution'] != second['distribution']


def test_a_run_store_refuses_to_serve_games_to_a_different_run(tmp_path, registry,
                                                               fake_engine):
    """The concrete hole: job ids cannot see a deadline, a panel or a threshold, so two
    specs differing only there would have resumed each other's games."""
    from arena.jobs import JobStore
    run(tmp_path, registry)
    store = tmp_path / 'run' / 'baseline.jobs.sqlite3'
    assert store.is_file()
    with pytest.raises(ValueError, match='Resume reuses only the games of its own run'):
        JobStore(store, provenance={}, run_id='f' * 64)


@pytest.mark.parametrize('problem', ['used_output', 'insufficient_seeds', 'invalid_agent', 'holdout'])
def test_prechecks_do_not_consume_seeds(tmp_path, registry, fake_engine, problem):
    before = registry.read_bytes()
    args = dict(baseline='pass', candidate='starter', opponents=['random'],
                seeds=list(range(100100, 100200)), output=tmp_path / 'run',
                split='validation', registry_path=registry)
    if problem == 'used_output':
        args['output'].mkdir()
    elif problem == 'insufficient_seeds':
        args['seeds'] = args['seeds'][:75]
    elif problem == 'invalid_agent':
        args['candidate'] = 'missing.py'
    else:
        args['seeds'] = list(range(9000000, 9000100))
    with pytest.raises((ValueError, FileExistsError)):
        paired.run_pair(**args)
    assert registry.read_bytes() == before
    assert not fake_engine


def test_partial_run_keeps_validation_burned_and_emits_no_verdict(tmp_path, registry, fake_engine, monkeypatch):
    original = league.stream
    def interrupted(jobs, workers):
        yield next(iter(original(jobs, workers)))
        raise RuntimeError('worker interrupted')
    monkeypatch.setattr(league, 'stream', interrupted)
    with pytest.raises(RuntimeError, match='interrupted'):
        run(tmp_path, registry)
    assert load_registry(registry)[0]['revision'] == 2
    assert (tmp_path / 'run/failure.json').is_file()
    assert not (tmp_path / 'run/gate.json').exists()


def test_artifact_drift_is_detected_before_second_leg(tmp_path, registry, fake_engine, monkeypatch):
    original = paired.check_spec
    def check(spec):
        return 'changed' if fake_engine and spec == 'starter' else original(spec)
    monkeypatch.setattr(paired, 'check_spec', check)
    with pytest.raises(ValueError, match='changed after'):
        run(tmp_path, registry)
    assert len(fake_engine) == 1
    assert not (tmp_path / 'run/gate.json').exists()


def test_strong_only_uses_frozen_ratings_and_rejects_empty_panel(tmp_path, registry, fake_engine):
    with pytest.raises(ValueError, match='No current strong'):
        run(tmp_path, registry, strong_only=True, ratings={})
    assert not fake_engine
    rating = dict(rating=2500, observed_at=paired.date.today().isoformat(), kind='direct')
    result, _ = run(tmp_path, registry, strong_only=True, ratings={'random': rating})
    assert result['strong']['games'] == 200
    assert result['execution']['strong_only'] is True
