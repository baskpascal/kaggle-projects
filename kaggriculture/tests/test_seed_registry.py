"""Issue #21: the registry is the only authority on which seeds may be run."""
import json

import pytest

from arena.seeds import admit_run, load_registry, parse_seeds, validate_seeds


def write(path, entries, revision=1, holdout_start=9000000, schema=1):
    payload = {'schema_version': schema, 'revision': revision, 'holdout_start': holdout_start,
               'entries': entries, 'history': []}
    path.write_text(json.dumps(payload, indent=2) + '\n')
    return path


HOLDOUT = {'id': 'final-holdout', 'start': 9000000, 'end': None, 'classification': 'holdout',
           'provenance': ['reserved for the release decision']}


def registry(tmp_path, *entries, **kwargs):
    return write(tmp_path / 'registry.json', [*entries, dict(HOLDOUT)], **kwargs)


def entry(id, start, end, classification, provenance=('test',)):
    return {'id': id, 'start': start, 'end': end, 'classification': classification,
            'provenance': list(provenance)}


def test_the_shipped_registry_is_well_formed():
    data, digest = load_registry()
    assert data['schema_version'] == 1 and len(digest) == 64
    assert {e['classification'] for e in data['entries']} <= {'dev', 'seen', 'validation',
                                                              'diagnostic', 'holdout'}
    assert all(e['provenance'] for e in data['entries'])


def test_documented_history_keeps_its_classification():
    # 100100:100125 was reserved validation until the v004 paired comparison spent
    # it, and 100125:100165 until the v006 gate did; consuming a reservation reclassifies
    # exactly the seeds used and leaves the rest of the interval reserved, which is what
    # the rows around each boundary assert.
    for seed, split in [(1000, 'dev'), (1099, 'dev'), (100000, 'seen'), (100099, 'seen'),
                        (100100, 'seen'), (100124, 'seen'), (100125, 'seen'),
                        (100164, 'seen'), (100165, 'validation'), (400000, 'dev'),
                        (419999, 'dev'), (500099, 'seen'), (500100, 'validation'),
                        (314159, 'diagnostic')]:
        assert validate_seeds([seed], split)['split'] == split


def test_burned_evidence_cannot_be_relabelled_as_clean_validation():
    with pytest.raises(ValueError, match='is seen, not validation'):
        validate_seeds([100000], 'validation')


@pytest.mark.parametrize('split', ['dev', 'seen', 'validation', 'diagnostic'])
def test_the_holdout_is_rejected_for_every_ordinary_split(split):
    with pytest.raises(ValueError):
        validate_seeds([9000000], split)
    with pytest.raises(ValueError):
        validate_seeds([9999999], split)


def test_unregistered_seeds_fail_closed():
    with pytest.raises(ValueError, match='Unregistered seed'):
        validate_seeds([600000], 'dev')


def test_duplicates_and_bad_splits_are_refused():
    with pytest.raises(ValueError):
        validate_seeds([1000, 1000], 'dev')
    with pytest.raises(ValueError):
        validate_seeds([1000], 'holdout')
    with pytest.raises(ValueError):
        validate_seeds([], 'dev')
    with pytest.raises(ValueError):
        validate_seeds([-1], 'dev')


def test_parse_seeds_refuses_duplicate_or_negative_ranges():
    assert parse_seeds('1000:1003') == [1000, 1001, 1002]
    with pytest.raises(ValueError):
        parse_seeds('7,7')
    with pytest.raises(ValueError):
        parse_seeds('-3')


@pytest.mark.parametrize('entries', [
    [entry('a', 0, 100, 'dev'), entry('b', 50, 150, 'validation')],
    [entry('a', 0, 100, 'dev'), entry('a', 100, 200, 'validation')],
    [entry('a', 0, None, 'dev')],
    [entry('a', 100, 100, 'dev')],
    [entry('a', 0, 100, 'unknown')],
    [entry('a', 0, 100, 'dev', provenance=())],
])
def test_a_malformed_registry_is_never_loaded(tmp_path, entries):
    with pytest.raises(ValueError):
        load_registry(registry(tmp_path, *entries))


def test_the_holdout_boundary_cannot_be_moved(tmp_path):
    path = write(tmp_path / 'moved.json', [{**HOLDOUT, 'start': 8000000}], holdout_start=8000000)
    with pytest.raises(ValueError):
        load_registry(path)


def test_an_unsupported_schema_is_refused(tmp_path):
    with pytest.raises(ValueError):
        load_registry(registry(tmp_path, entry('a', 0, 100, 'dev'), schema=2))


def test_validation_seeds_are_burned_before_the_run(tmp_path):
    path = registry(tmp_path, entry('reserved', 0, 10, 'validation'))
    run = {'candidate': 'challenger', 'batch_members': ['hashA'], 'seeds': [2, 3]}
    metadata = admit_run([2, 3], 'validation', run, path)
    assert metadata['consumption_revision'] == 2

    # The same seeds are now spent evidence, whatever the outcome of that run.
    with pytest.raises(ValueError, match='is seen, not validation'):
        validate_seeds([2], 'validation', path)
    assert validate_seeds([2, 3], 'seen', path)['split'] == 'seen'
    assert validate_seeds([4], 'validation', path)['split'] == 'validation'

    data, _ = load_registry(path)
    assert data['revision'] == 2
    assert data['history'][-1]['seeds'] == [2, 3]
    assert data['history'][-1]['run'] == run
    assert data['history'][-1]['batch'] == metadata['batch']
    covered = sorted(s for e in data['entries'] if e['end'] is not None
                     for s in range(e['start'], e['end']))
    assert covered == list(range(10))


def test_admission_requires_provenance_and_leaves_no_lock(tmp_path):
    path = registry(tmp_path, entry('reserved', 0, 10, 'validation'))
    with pytest.raises(ValueError, match='provenance'):
        admit_run([1], 'validation', None, path)
    with pytest.raises(ValueError):
        admit_run([1, 9000000], 'validation',
                  {'batch_members': ['hashA'], 'seeds': [1, 9000000]}, path)
    assert not (tmp_path / 'registry.json.lock').exists()
    assert load_registry(path)[0]['revision'] == 1


def test_non_validation_splits_are_checked_but_not_consumed(tmp_path):
    path = registry(tmp_path, entry('dev', 0, 10, 'dev'))
    admit_run([1], 'dev', {'run': 'x'}, path)  # non-validation needs no batch
    assert load_registry(path)[0]['revision'] == 1
    assert validate_seeds([1], 'dev', path)['split'] == 'dev'


def test_a_ray_worker_cannot_admit_or_consume_seeds(tmp_path, monkeypatch):
    path = registry(tmp_path, entry('reserved', 0, 10, 'validation'))
    before = path.read_bytes()
    monkeypatch.setenv('ARENA_ROLE', 'ray-worker')
    with pytest.raises(PermissionError, match='cannot admit'):
        admit_run([2], 'validation', {'batch_members': ['hashA'], 'seeds': [2]}, path)
    assert path.read_bytes() == before
    assert not (path.parent / (path.name + '.lock')).exists()


def test_metadata_pins_the_registry_version_for_the_report():
    metadata = validate_seeds([1000], 'dev')
    assert metadata['registry_schema'] == 1
    assert metadata['registry_sha256'] == load_registry()[1]
    assert metadata['competitive'] is True
    assert validate_seeds([314159], 'diagnostic')['competitive'] is False


def paired(tmp_path, members=('hashA', 'hashB'), seeds=(2, 3)):
    path = registry(tmp_path, entry('reserved', 0, 10, 'validation'))
    provenance = {'batch_members': list(members), 'opponent_hashes': {'starter': 'o1'},
                  'backend': 'fast', 'seeds': list(seeds)}
    return path, provenance


def test_both_legs_of_a_declared_batch_may_run_the_same_seeds(tmp_path):
    # A paired comparison is two runs over identical seeds. The first burns
    # them; without batch identity the second leg could never run at all.
    path, provenance = paired(tmp_path)
    first = admit_run([2, 3], 'validation', {**provenance, 'candidate': 'baseline'}, path)
    second = admit_run([2, 3], 'validation', {**provenance, 'candidate': 'candidate'}, path)
    assert (first['batch_leg'], second['batch_leg']) == (1, 2)
    assert first['batch'] == second['batch']
    assert load_registry(path)[0]['revision'] == 2, 'the batch burns its seeds exactly once'
    assert validate_seeds([2, 3], 'seen', path)['split'] == 'seen'


def test_the_batch_identity_ignores_which_leg_runs_first(tmp_path):
    path, provenance = paired(tmp_path)
    forwards = admit_run([2, 3], 'validation', {**provenance, 'candidate': 'baseline'}, path)
    reversed_members = {**provenance, 'batch_members': ['hashB', 'hashA']}
    assert admit_run([2, 3], 'validation', {**reversed_members, 'candidate': 'x'},
                     path)['batch'] == forwards['batch']


def test_an_undeclared_candidate_cannot_join_a_burned_batch(tmp_path):
    path, provenance = paired(tmp_path)
    admit_run([2, 3], 'validation', {**provenance, 'candidate': 'baseline'}, path)
    late = {**provenance, 'batch_members': ['hashA', 'hashB', 'hashC']}
    with pytest.raises(ValueError, match='different batch'):
        admit_run([2, 3], 'validation', {**late, 'candidate': 'latecomer'}, path)


def test_a_batch_cannot_be_widened_after_its_first_leg(tmp_path):
    path, provenance = paired(tmp_path)
    admit_run([2, 3], 'validation', {**provenance, 'candidate': 'baseline'}, path)
    wider = {**provenance, 'seeds': [2, 3]}
    with pytest.raises(ValueError, match='cannot be widened'):
        admit_run([2, 3, 4], 'validation', {**wider, 'candidate': 'baseline'}, path)


def test_validation_requires_declaring_the_batch_up_front(tmp_path):
    path = registry(tmp_path, entry('reserved', 0, 10, 'validation'))
    for provenance in ({'candidate': 'x'}, {'candidate': 'x', 'batch_members': []},
                       {'candidate': 'x', 'batch_members': ['hashA', '']}):
        with pytest.raises(ValueError, match='declare every candidate'):
            admit_run([2], 'validation', provenance, path)
    assert load_registry(path)[0]['revision'] == 1
