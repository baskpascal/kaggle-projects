"""A run has to be one thing, and its evidence has to be about that thing.

The project could already run the right strategy under a combination of parameters that
was not the experiment the report named. These tests pin the two halves of the fix: the
spec is validated before anything is consumed and addressed by what it measured, and the
manifest refuses to read evidence from another run as if it belonged to this one.
"""
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from arena import runspec
from arena.jobs import JobStore
from arena.seeds import REGISTRY

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)
ENVIRONMENT = {'version': '1.32.7', 'interpreter_sha256': 'bc' * 32,
               'specification_sha256': 'ab' * 32}
REGISTRY_STATE = None


def registry_state():
    global REGISTRY_STATE
    if REGISTRY_STATE is None:
        from arena.seeds import validate_seeds
        REGISTRY_STATE = validate_seeds([1000], 'dev', path=REGISTRY)
    return REGISTRY_STATE


def spec(**over):
    seeds = over.pop('seeds', list(range(1000, 1010)))
    base = dict(baseline=('champion', 'aa' * 32), candidate=('challenger', 'bb' * 32),
                opponents={'starter': {'hash': 'cc' * 32, 'lineage': 'starter'},
                           'crop': {'hash': 'dd' * 32, 'lineage': 'crop'}},
                seeds=seeds, split='dev', registry=registry_state(),
                environment=ENVIRONMENT, min_blocks=2, attempts=3, now=NOW)
    return runspec.build(**{**base, **over})


# --- what the id is a function of -------------------------------------------------------

def test_the_same_inputs_give_the_same_run_id():
    assert spec()['run_id'] == spec()['run_id']


@pytest.mark.parametrize('change', [
    {'baseline': ('champion', 'ff' * 32)},
    {'candidate': ('challenger', 'ff' * 32)},
    {'opponents': {'starter': {'hash': 'cc' * 32, 'lineage': 'starter'}}},
    {'seeds': list(range(1000, 1020))},
    {'backend': 'official'},
    {'environment': {**ENVIRONMENT, 'specification_sha256': 'ff' * 32}},
    {'panel': {'revision': 'ee' * 32, 'observed_at': '2026-09-08'}},
    {'min_blocks': 5},
    {'cut': 2400.},
    {'max_rating_age_days': 7},
    {'deadline_seconds': 30.},
    {'attempts': 5},
    {'evidence_profile': 'audit'},
    {'gate': 'eval.standing.gate'},
])
def test_any_material_field_moves_the_run_id(change):
    assert spec(**change)['run_id'] != spec()['run_id']


@pytest.mark.parametrize('spread', [
    {'topology': 'ray', 'workers': 32},
    {'topology': 'local', 'workers': 4, 'batch_size': 64},
])
def test_how_the_work_was_spread_is_not_part_of_the_run(spread):
    """#43 measured batches as bit-exact across nodes, so a run resumed on the cluster is
    the same run. Folding worker counts into the id would refuse that for no reason."""
    assert spec(distribution=spread)['run_id'] == spec()['run_id']


def test_the_lineage_of_an_opponent_is_material():
    """The panel is what a standing is about; a relabelled lineage is a different panel."""
    moved = spec(opponents={'starter': {'hash': 'cc' * 32, 'lineage': 'other'},
                            'crop': {'hash': 'dd' * 32, 'lineage': 'crop'}})
    assert moved['run_id'] != spec()['run_id']


# --- validation happens before anything is consumed --------------------------------------

@pytest.mark.parametrize('change, expected', [
    ({'opponents': {}}, 'must name its opponent'),
    ({'opponents': {'starter': {'lineage': 'starter'}}}, 'carries no sha256'),
    ({'baseline': ('champion', 'short')}, 'carries no sha256'),
    ({'seeds': []}, 'nonempty list of unique seeds'),
    ({'split': 'holdout'}, 'is not one of'),
    ({'backend': 'guess'}, 'is not one of'),
    ({'environment': {}}, 'engine fingerprint'),
    ({'min_blocks': 1}, 'at least 2'),
    ({'min_blocks': 500}, 'under the 500'),
    ({'attempts': 0}, 'positive integer'),
    ({'deadline_seconds': -1}, 'positive number of seconds'),
    ({'gate': ''}, 'names the gate'),
    ({'evidence_profile': 'verbose'}, 'Evidence profile'),
])
def test_a_spec_that_cannot_be_run_is_refused_with_the_reason(change, expected):
    with pytest.raises(ValueError, match=expected):
        spec(**change)


def test_a_seed_the_registry_does_not_classify_is_refused():
    with pytest.raises(ValueError, match='Unregistered seed'):
        spec(seeds=[7_000_000, 7_000_001])


def test_a_declared_registry_revision_that_the_registry_denies_is_refused():
    """The spec records what it was told; the registry is what is asked."""
    built = spec()
    built['seeds']['registry_revision'] = built['seeds']['registry_revision'] + 1
    with pytest.raises(ValueError, match='registry_revision'):
        runspec.validate(built)


def test_a_paired_run_needs_no_panel_because_its_population_is_its_opponents():
    """A panel is required to *read* a run as an absolute standing, not to run one.

    `eval/standing.py` and `eval/dossier.py` are where issue #45 is enforced. Demanding a
    panel here would refuse a legitimate paired comparison, whose population is the
    `opponents` list the spec already carries.
    """
    assert spec(seeds=list(range(500100, 500110)), split='validation')['panel'] is None


def test_a_validation_run_with_a_panel_is_accepted():
    built = spec(seeds=list(range(500100, 500110)), split='validation',
                 panel={'revision': 'ee' * 32, 'observed_at': '2026-09-08'})
    assert built['panel']['revision'] == 'ee' * 32


def test_a_half_written_panel_reference_is_not_a_panel():
    with pytest.raises(ValueError, match='revision and an observation date'):
        spec(panel={'revision': 'ee' * 32})


# --- combining evidence from two runs -----------------------------------------------------

def test_two_specs_that_agree_are_one_run():
    assert runspec.same_run([spec(), spec()]) == spec()['run_id']


def test_two_specs_that_disagree_fail_explicitly_and_name_the_section():
    with pytest.raises(ValueError, match='they differ on seeds'):
        runspec.same_run([spec(), spec(seeds=list(range(1000, 1020)))])


def test_differences_names_every_section_that_moved():
    assert runspec.differences(spec(), spec(backend='official', min_blocks=5)) == \
        ['engine', 'metrics']


# --- a spec on disk ------------------------------------------------------------------------

def test_a_spec_that_was_edited_after_it_was_written_no_longer_loads(tmp_path):
    path = tmp_path / 'spec.json'
    built = spec()
    path.write_text(json.dumps(built))
    assert runspec.load(path)['run_id'] == built['run_id']
    built['metrics']['min_blocks'] = 3
    path.write_text(json.dumps(built))
    with pytest.raises(ValueError, match='edited after it was written'):
        runspec.load(path)


def test_render_names_the_run_and_says_what_is_outside_it():
    text = runspec.render(spec())
    assert text.startswith('RUN ')
    assert 'outside the run_id' in text


# --- the evidence manifest -------------------------------------------------------------------

def evidence(tmp_path, run_id, **extra):
    paths = {}
    for kind, body in {'comparison': {'run_id': run_id, 'verdict': 'x'},
                       'gate': {'run_id': run_id}, **extra}.items():
        path = tmp_path / f'{kind}.json'
        path.write_text(json.dumps(body))
        paths[kind] = path
    return paths


def test_a_manifest_over_this_run_s_own_evidence_is_bound(tmp_path):
    built = spec()
    result = runspec.manifest(built, evidence(tmp_path, built['run_id']), now=NOW)
    assert result['verdict'] == 'BOUND'
    assert result['refusals'] == [] and result['unbound'] == []
    assert set(result['evidence']) == {'comparison', 'gate'}
    assert all(piece['sha256'] for piece in result['evidence'].values())


def test_evidence_from_another_run_is_refused_by_name(tmp_path):
    built = spec()
    pieces = evidence(tmp_path, built['run_id'],
                      standing={'run_id': spec(backend='official')['run_id']})
    result = runspec.manifest(built, pieces, now=NOW)
    assert result['verdict'] == 'REFUSED'
    assert any('another experiment' in reason for reason in result['refusals'])


def test_evidence_that_predates_run_specs_is_unbound_not_assumed(tmp_path):
    built = spec()
    pieces = evidence(tmp_path, built['run_id'], standing={'games': 400})
    result = runspec.manifest(built, pieces, now=NOW)
    assert result['verdict'] == 'PARTIALLY BOUND'
    assert result['unbound'] == ['standing']
    assert result['refusals'] == []


def test_a_manifest_records_the_bytes_of_every_piece(tmp_path):
    built = spec()
    pieces = evidence(tmp_path, built['run_id'])
    result = runspec.manifest(built, pieces, now=NOW)
    for kind, path in pieces.items():
        import hashlib
        assert result['evidence'][kind]['sha256'] == \
            hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --- the job store belongs to one run ----------------------------------------------------------

def test_a_store_adopts_the_first_run_that_claims_it(tmp_path):
    path = tmp_path / 'run.sqlite3'
    with JobStore(path, provenance={}, run_id='a' * 64) as store:
        assert store.run_id == 'a' * 64
    with JobStore(path, provenance={}) as store:
        assert store.bound_run() == 'a' * 64


def test_a_store_never_changes_hands(tmp_path):
    """The hole this closes: two specs differing only in deadline or panel produce
    identical job ids, so resume would have served games from the other experiment."""
    path = tmp_path / 'run.sqlite3'
    with JobStore(path, provenance={}, run_id='a' * 64):
        pass
    with pytest.raises(ValueError, match='Resume reuses only the games of its own run'):
        JobStore(path, provenance={}, run_id='b' * 64)


def test_resuming_the_same_run_is_allowed(tmp_path):
    path = tmp_path / 'run.sqlite3'
    with JobStore(path, provenance={}, run_id='a' * 64):
        pass
    with JobStore(path, provenance={}, run_id='a' * 64) as store:
        assert store.run_id == 'a' * 64


def test_a_store_written_before_run_specs_existed_stays_resumable(tmp_path):
    path = tmp_path / 'run.sqlite3'
    with JobStore(path, provenance={}) as store:
        assert store.run_id is None
    with JobStore(path, provenance={}, run_id='a' * 64) as store:
        assert store.run_id == 'a' * 64


def test_a_run_id_has_to_be_a_run_id(tmp_path):
    with pytest.raises(ValueError, match='nonempty string'):
        JobStore(tmp_path / 'run.sqlite3', provenance={}, run_id='')


# --- what the release dossier does with a run id -------------------------------------------

def test_the_dossier_reports_the_declared_run_id_rather_than_a_rival_one():
    """PR #48 promised this: when #46 exists it becomes the authority and `run_spec`
    becomes an adapter. A reconstruction beside a real id would be a second opinion."""
    from eval.dossier import run_spec
    finalist = {'comparison': {'run_id': 'a' * 64, 'snapshot': {'candidate_hash': 'bb' * 32}}}
    assert run_spec(finalist)['sha256'] == 'a' * 64
    assert run_spec(finalist)['source'] == 'arena.runspec'


def test_a_report_written_before_run_specs_still_gets_a_reconstructed_spec():
    from eval.dossier import run_spec
    spec = run_spec({'comparison': {'snapshot': {'candidate_hash': 'bb' * 32}}})
    assert spec['source'] == 'reconstructed' and spec['run_id'] is None and spec['sha256']


def test_the_dossier_refuses_reports_that_name_different_runs():
    from eval.dossier import binding
    reasons = binding({'comparison': {'run_id': 'a' * 64,
                                      'snapshot': {'candidate_hash': 'bb' * 32}},
                       'standing': {'run_id': 'c' * 64, 'provenance': {'candidate_hash': 'dd' * 32}},
                       'preflight': {'sha256': 'dd' * 32}})
    assert any('the run they came from' in reason for reason in reasons)
