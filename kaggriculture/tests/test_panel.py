"""The panel is evidence, so it has to be reconstructible, dated and refusable.

`eval/standing.py` was already asking the right question and being handed the answer to
"who is in which band" as a mapping typed beside the run. These tests pin the three
things that turns into: the snapshot is derived from the pinned artifacts rather than
declared, a revision is written once and can never be edited under its own name, and the
gate refuses an unusable panel instead of letting a correct win rate stand for the wrong
population.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from eval.panel import (BANDING_KINDS, MAX_PANEL_AGE_DAYS, MIN_LINEAGES_PER_BAND,
                        OBSERVATIONS, PANELS, admission, band_of, build, entries, gate,
                        lineages, load, load_bundles, ranks, render, save)
from eval.standing import standing

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def bundle(**over):
    base = {'id': 'a', 'sha256': 'aa' * 32, 'family': 'alpha', 'kernel': 'user/kernel',
            'source_url': 'https://kaggle.com/code/user/kernel',
            'engine': {'kaggle_environments': '1.32.7'}}
    return {**base, **over}


def observed(**over):
    base = {'kind': 'author_current', 'observed_at': '2026-09-08', 'rank': 5,
            'of': 8177, 'source': 'public leaderboard snapshot 2026-09-08'}
    return {**base, **over}


def inputs(names=('a', 'b'), *, bundles=None, seen=None, ratings=None):
    bundles = bundles or {name: bundle(id=name, family=name, sha256=name * 64)
                          for name in names}
    seen = seen or {name: observed(rank=index + 1) for index, name in enumerate(names)}
    ratings = ratings or {name: {'rating': 2500., 'kind': 'author_current',
                                 'observed_at': '2026-09-08'} for name in names}
    return {'opponents': seen, 'source': {'leaderboard': 'snapshot.csv'}}, bundles, ratings


def make(names=('a', 'b'), *, bundles=None, seen=None, ratings=None, now=NOW):
    observations, bundles, ratings = inputs(names, bundles=bundles, seen=seen, ratings=ratings)
    return build(observations, bundles=bundles, ratings=ratings, now=now)


# --- what a rank observation is allowed to conclude ------------------------------------

@pytest.mark.parametrize('kind', BANDING_KINDS)
def test_an_attributable_rank_places_an_opponent_in_its_band(kind):
    assert band_of(7, kind) == 'top10'
    assert band_of(45, kind) == 'rank30_100'


def test_an_upper_bound_rank_never_places_an_opponent_into_a_band():
    """The asymmetry docs/RATINGS_REFRESH.md draws for ratings, drawn again for ranks.

    A superseded artifact's team may be ranked third while the artifact itself is far
    worse. The bound can prove an agent is not top ten; it can never prove it is, and
    banding on it would quietly manufacture the coverage the gate exists to demand.
    """
    assert band_of(3, 'author_upper_bound') is None


@pytest.mark.parametrize('rank', [None, 0, 101, True, '5'])
def test_a_rank_that_is_not_a_position_bands_nothing(rank):
    assert band_of(rank, 'direct') is None


# --- admission ------------------------------------------------------------------------

def test_a_pinned_notebook_or_commit_is_admitted():
    assert admission(bundle())[0] == 'pinned_notebook'
    assert admission(bundle(kernel=None, repository='https://github.com/x/y',
                            commit='abc'))[0] == 'pinned_commit'


def test_an_artifact_with_no_checkable_source_is_refused():
    how, why = admission(bundle(kernel=None))
    assert how is None and 'came from' in why


def test_an_unverified_reconstruction_cannot_be_a_panel_opponent():
    """Issue #39 will generate tapes; this refuses them until an episode is reproduced."""
    how, why = admission(bundle(reconstruction={'episode': '12345'}))
    assert how is None
    assert 'replaying exactly the episode' in why


def test_a_reconstruction_that_reproduced_its_episode_is_admitted():
    reconstructed = bundle(
        sha256='aa' * 32, tape_sha256='bb' * 32,
        recorded={'seat': 1, 'money': 10, 'opponent_money': 9}, reconstruction={
            'episode': '12345', 'reproduction': {
                'dataset_revision': 'cc' * 32, 'episode_id': '12345', 'seat': 1,
                'engine_version': '1.32.7',
                'engine_fingerprint': {'version': '1.32.7'},
                'agent_sha256': 'aa' * 32, 'stream_sha256': 'bb' * 32,
                'expected': {'winner': 1, 'our_money': 10, 'opponent_money': 9},
                'actual': {'winner': 1, 'our_money': 10, 'opponent_money': 9},
                'verified_at': '2026-09-08'}})
    how, why = admission(reconstructed)
    assert (how, why) == ('episode_reconstruction', None)


@pytest.mark.parametrize('change,reason', [
    ({'dataset_revision': 'not-a-hash'}, 'dataset_revision'),
    ({'seat': 0}, 'seat does not match'),
    ({'agent_sha256': 'dd' * 32}, 'agent digest'),
    ({'actual': {'winner': 0, 'our_money': 9, 'opponent_money': 10}},
     'does not match'),
])
def test_a_false_or_incomplete_reproduction_is_refused(change, reason):
    proof = {'dataset_revision': 'cc' * 32, 'episode_id': '12345', 'seat': 1,
             'engine_version': '1.32.7', 'engine_fingerprint': {'version': '1.32.7'},
             'agent_sha256': 'aa' * 32, 'stream_sha256': 'bb' * 32,
             'expected': {'winner': 1, 'our_money': 10, 'opponent_money': 9},
             'actual': {'winner': 1, 'our_money': 10, 'opponent_money': 9},
             'verified_at': '2026-09-08'}
    proof.update(change)
    _, why = admission(bundle(sha256='aa' * 32, tape_sha256='bb' * 32,
                              recorded={'seat': 1, 'money': 10, 'opponent_money': 9},
                              reconstruction={
                                  'episode': '12345', 'reproduction': proof}))
    assert reason in why


def test_matching_but_forged_expected_and_actual_result_is_refused():
    forged = {'winner': 1, 'our_money': 999, 'opponent_money': 1}
    proof = {'dataset_revision': 'cc' * 32, 'episode_id': '12345', 'seat': 1,
             'engine_version': '1.32.7', 'engine_fingerprint': {'version': '1.32.7'},
             'agent_sha256': 'aa' * 32, 'stream_sha256': 'bb' * 32,
             'expected': forged, 'actual': forged, 'verified_at': '2026-09-08'}
    _, why = admission(bundle(sha256='aa' * 32, tape_sha256='bb' * 32,
                              recorded={'seat': 1, 'money': 10, 'opponent_money': 9},
                              reconstruction={'episode': '12345',
                                              'reproduction': proof}))
    assert 'recorded bundle result' in why


# --- the join, and the drift it refuses ------------------------------------------------

def test_an_opponent_with_no_pinned_bundle_is_refused_not_ignored():
    seen = {'a': observed(), 'ghost': observed()}
    snapshot = make(('a',), seen=seen, ratings={'a': {'rating': 2500., 'kind': 'author_current'}})
    assert 'ghost' in snapshot['refused']
    assert 'ghost' not in snapshot['opponents']


def test_a_rank_and_a_rating_that_disagree_about_kind_refuse_the_opponent():
    """Two files updated at different times is the drift a snapshot exists to make loud."""
    snapshot = make(('a',), seen={'a': observed(kind='author_current')},
                    ratings={'a': {'rating': 2500., 'kind': 'author_upper_bound'}})
    assert 'was updated without the other' in snapshot['refused']['a']


@pytest.mark.parametrize('broken, expected', [
    ({'kind': 'guess'}, 'not one of'),
    ({'observed_at': 'someday'}, 'parseable observed_at'),
    ({'source': '  '}, 'names no source'),
])
def test_an_observation_that_cannot_be_audited_is_refused(broken, expected):
    snapshot = make(('a',), seen={'a': observed(**broken)},
                    ratings={'a': {'rating': 2500., 'kind': broken.get('kind', 'author_current')}})
    assert expected in snapshot['refused']['a']


def test_the_record_carries_the_artifact_hash_and_lineage_from_the_bundle():
    snapshot = make(('a',))
    row = snapshot['opponents']['a']
    assert row['sha256'] == 'a' * 64 and row['lineage'] == 'a'
    assert row['engine'] == {'kaggle_environments': '1.32.7'}


# --- coverage and the gate --------------------------------------------------------------

def covered():
    """The panel #45 asks for: both decisive bands, two independent lineages each."""
    names = ('a', 'b', 'c', 'd')
    return make(names,
                bundles={name: bundle(id=name, family=name, sha256=name * 64)
                         for name in names},
                seen=dict(zip(names, (observed(rank=rank) for rank in (3, 7, 14, 22)))),
                ratings={name: {'rating': 2500., 'kind': 'author_current'} for name in names})


def test_a_fully_covered_panel_passes():
    snapshot = covered()
    assert snapshot['coverage']['top10']['lineage_count'] == 2
    assert snapshot['coverage']['rank10_30']['lineage_count'] == 2
    assert snapshot['gate'] == {'verdict': 'PASS', 'refusals': [],
                                'reasons': snapshot['gate']['reasons']}


def test_an_empty_decisive_band_is_a_refusal_not_a_silent_pass():
    snapshot = make(('a', 'b'), seen={'a': observed(rank=40), 'b': observed(rank=50)})
    assert snapshot['gate']['verdict'] == 'FAIL'
    assert any('Band top10 is empty' in reason for reason in snapshot['gate']['refusals'])
    assert any('rank10_30 is empty' in reason for reason in snapshot['gate']['refusals'])


def test_a_decisive_band_carried_by_one_lineage_is_refused():
    bundles = {name: bundle(id=name, family='one', sha256=name * 64) for name in ('a', 'b')}
    snapshot = make(('a', 'b'), bundles=bundles)
    assert f'{MIN_LINEAGES_PER_BAND} independent lineages' in ' '.join(snapshot['gate']['refusals'])


def test_a_band_one_lineage_dominates_is_refused_even_with_two_lineages():
    names = ('a', 'b', 'c')
    bundles = {'a': bundle(id='a', family='one', sha256='a' * 64),
               'b': bundle(id='b', family='one', sha256='b' * 64),
               'c': bundle(id='c', family='two', sha256='c' * 64)}
    snapshot = make(names, bundles=bundles,
                    seen={name: observed(rank=index + 1) for index, name in enumerate(names)},
                    ratings={name: {'rating': 2500., 'kind': 'author_current'} for name in names})
    assert snapshot['coverage']['top10']['lineage_count'] == 2
    assert 'concentration limit' in ' '.join(snapshot['gate']['refusals'])


def test_a_stale_panel_is_refused():
    late = NOW + timedelta(days=MAX_PANEL_AGE_DAYS + 1)
    snapshot = make(('a', 'b'), now=late)
    assert snapshot['gate']['verdict'] == 'FAIL'
    assert 'no longer exists' in ' '.join(snapshot['gate']['refusals'])


def test_freshness_is_the_oldest_member_not_the_newest():
    """A stale opponent must not ride into a decision behind a freshly observed one."""
    snapshot = make(('a', 'b'), seen={'a': observed(observed_at='2026-08-01'),
                                      'b': observed(rank=2)})
    assert snapshot['observed_at'] == '2026-08-01'
    assert snapshot['gate']['verdict'] == 'FAIL'


# --- revisions are written once ---------------------------------------------------------

def test_the_revision_is_a_function_of_the_evidence_not_of_the_clock():
    first = make(('a', 'b'), now=NOW)
    later = make(('a', 'b'), now=NOW + timedelta(hours=3))
    assert first['revision'] == later['revision']


def test_any_change_to_the_evidence_is_a_different_revision():
    moved = make(('a', 'b'), seen={'a': observed(rank=9), 'b': observed(rank=2)})
    assert moved['revision'] != make(('a', 'b'))['revision']


def test_a_second_snapshot_never_overwrites_the_first(tmp_path):
    first, second = make(('a', 'b')), make(('a', 'b'), seen={'a': observed(rank=9),
                                                             'b': observed(rank=2)})
    save(first, tmp_path)
    save(second, tmp_path)
    filed = sorted(path.name for path in tmp_path.glob('panel-*.json'))
    assert len(filed) == 2
    assert load(tmp_path / f'panel-{first["revision"][:12]}.json')['revision'] == first['revision']


def test_refiling_the_same_revision_is_a_no_op(tmp_path):
    snapshot = make(('a', 'b'))
    save(snapshot, tmp_path)
    save(snapshot, tmp_path)
    assert len(json.loads((tmp_path / 'index.json').read_text())) == 1


def test_filing_different_content_under_a_taken_revision_is_an_error(tmp_path):
    snapshot = make(('a', 'b'))
    save(snapshot, tmp_path)
    forged = dict(snapshot)
    forged['opponents'] = {**snapshot['opponents'], 'a': {**snapshot['opponents']['a'],
                                                          'rank': 99}}
    with pytest.raises(ValueError, match='written once'):
        save(forged, tmp_path)


def test_an_edited_revision_no_longer_loads(tmp_path):
    snapshot = make(('a', 'b'))
    path = save(snapshot, tmp_path)
    edited = json.loads(path.read_text())
    edited['opponents']['a']['rank'] = 99
    path.write_text(json.dumps(edited, indent=2))
    with pytest.raises(ValueError, match='edited after it was written'):
        load(path)


def test_the_index_records_every_revision_in_the_order_it_was_filed(tmp_path):
    first = make(('a', 'b'))
    second = make(('a', 'b'), seen={'a': observed(rank=9), 'b': observed(rank=2)})
    save(first, tmp_path)
    save(second, tmp_path)
    index = json.loads((tmp_path / 'index.json').read_text())
    assert [row['revision'] for row in index] == [first['revision'], second['revision']]
    assert index[0]['verdict'] == first['gate']['verdict']


# --- what a standing does with one --------------------------------------------------------

def test_a_standing_derives_its_bands_from_the_snapshot():
    snapshot = make(('a', 'b'))
    assert ranks(snapshot) == {'a': 1, 'b': 2}
    assert lineages(snapshot) == {'a': 'a', 'b': 'b'}
    rows = [{'opponent': name, 'seat': seat, 'seed': seed, 'score': 1.}
            for name in ('a', 'b') for seat in (0, 1) for seed in range(5)]
    report = standing(rows, panel=snapshot)
    assert report['panel']['revision'] == snapshot['revision']
    assert report['per_band']['top10']['opponents'] == ['a', 'b']


def test_a_standing_over_a_refused_panel_fails_for_the_panel_reason():
    snapshot = make(('a', 'b'), seen={'a': observed(rank=40), 'b': observed(rank=50)})
    rows = [{'opponent': name, 'seat': seat, 'seed': seed, 'score': 1.}
            for name in ('a', 'b') for seat in (0, 1) for seed in range(5)]
    report = standing(rows, panel=snapshot)
    assert report['gate']['verdict'] == 'FAIL'
    assert any(reason.startswith('Panel:') for reason in report['gate']['reasons'])


def test_ranks_beside_a_panel_are_a_contradiction():
    snapshot = make(('a', 'b'))
    rows = [{'opponent': 'a', 'seat': 0, 'seed': 1, 'score': 1.}]
    with pytest.raises(ValueError, match='drift the snapshot removes'):
        standing(rows, {'a': 1}, panel=snapshot)


def test_a_standing_still_accepts_a_plain_ranks_mapping():
    """The pre-#45 form keeps working; it simply cannot say what it stood against."""
    rows = [{'opponent': 'a', 'seat': 0, 'seed': 1, 'score': 1.}]
    assert standing(rows, {'a': 1})['panel'] is None


def test_a_standing_needs_one_of_the_two():
    with pytest.raises(ValueError, match='panel snapshot or a ranks mapping'):
        standing([{'opponent': 'a', 'seat': 0, 'seed': 1, 'score': 1.}])


# --- the panel this repository actually has -----------------------------------------------

def test_the_filed_revisions_all_load():
    filed = sorted(PANELS.glob('panel-*.json'))
    assert filed, 'the repository must carry at least one filed panel revision'
    for path in filed:
        load(path)


def test_the_committed_observations_rebuild_the_filed_revision():
    """The snapshot in the repository is derived, and this is what says so."""
    observations = json.loads(OBSERVATIONS.read_text(encoding='utf-8'))
    rebuilt = build(observations, now=NOW)
    filed = {load(path)['revision'] for path in PANELS.glob('panel-*.json')}
    assert rebuilt['revision'] in filed


def test_the_real_panel_cannot_support_a_release_standing_yet():
    """The finding issue #45 opens on, kept honest rather than described in prose.

    Pinning yhay_router_0909 on 2026-09-09 moved its author team to rank 27, so the
    rank10_30 band is no longer empty - and the panel still authorizes nothing, for the
    two reasons that survive: top10 has no opponent at all, and the one band that now has
    an opponent is carried by a single lineage. When a top-ten opponent is pinned, and
    when a second lineage reaches rank10_30, this test is what changes, and it should
    change deliberately.
    """
    observations = json.loads(OBSERVATIONS.read_text(encoding='utf-8'))
    snapshot = build(observations, now=NOW)
    assert snapshot['gate']['verdict'] == 'FAIL'
    assert snapshot['coverage']['top10']['opponents'] == []
    assert snapshot['coverage']['rank10_30']['lineage_count'] == 1
    assert snapshot['coverage']['rank30_100']['lineage_count'] == 4


def test_every_pinned_bundle_is_either_observed_or_explicitly_absent():
    """Silence is the failure mode here too: an opponent nobody ranked must not exist."""
    observations = json.loads(OBSERVATIONS.read_text(encoding='utf-8'))
    seen = set(observations['opponents'])
    snapshot = build(observations, now=NOW)
    named = seen | set(snapshot['refused'])
    assert set(load_bundles()) - named <= set(
        json.loads((ROOT / 'opponents' / 'ratings.json').read_text())['unrated'])


def test_render_names_the_revision_and_the_refusals():
    text = render(make(('a', 'b'), seen={'a': observed(rank=40), 'b': observed(rank=50)}))
    assert 'PANEL' in text and 'gate = FAIL' in text and 'top10' in text
