"""Issue #47: three correct reports are not a decision until they are about one thing.

The dossier's whole job is the join. Each test below breaks exactly one link between the
paired comparison, the absolute standing, the preflight and the declared account state,
and asserts the verdict stops being a release rather than quietly staying one.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from eval.comparison import build_comparison
from eval.dossier import DAILY_QUOTA, LAST_PLANNED_SWAP, dossier, render, run_spec, split_of
from eval.standing import standing

ARTIFACT = 'a' * 64        # the file that would be uploaded
INCUMBENT = 'b' * 64       # the agent holding slot A, and the baseline leg
OTHER = 'c' * 64          # the agent holding slot B
SPARE = 'f' * 64          # a hash that is nobody's: never active, never evaluated
ENGINE = {'version': '1.32.7', 'interpreter_sha256': 'd' * 64, 'specification_sha256': 'e' * 64}
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
# seed_registry.json revision 6 reserves exactly this range for validation: 100125:100165
# and 500000:500100 were spent by the v004 and v006 gates and are `seen` from then on.
VALIDATION = range(500100, 500200)
STRONG = ('top_a', 'top_b', 'mid_a')
RATINGS = {name: dict(rating=2500., kind='direct', observed_at='2026-09-08')
           for name in ('top_a', 'top_b', 'mid_a', 'mid_b', 'low_a', 'low_b')}
RANKS = {'top_a': 3, 'top_b': 7, 'mid_a': 15, 'mid_b': 25, 'low_a': 40, 'low_b': 80}
WINNING = {name: 1. for name in RANKS}


def legs(score=1., count=100, candidate=ARTIFACT, baseline=INCUMBENT, engine=ENGINE,
         backend='fast'):
    def rows(agent, value):
        return [dict(seed=seed, seat=seat, opponent=name, candidate_hash=agent,
                     opponent_hash=name + '-hash', environment=engine, backend=backend,
                     configuration={'seed': seed}, failures=[], opponent_failures=[],
                     score=value, margin=0)
                for seed in range(count) for name in STRONG for seat in (0, 1)]
    return rows(baseline, 0.), rows(candidate, score)


def comparison(**kwargs):
    before, after = legs(**kwargs)
    return build_comparison(before, after, ratings=RATINGS, families={}, today=NOW.date(),
                            split='validation')


def standing_report(rates=None, candidate=ARTIFACT, seeds=VALIDATION, engine=ENGINE,
                    backend='fast', bound=True):
    rows = []
    for name, rate in (rates or WINNING).items():
        games = [(seed, seat) for seed in seeds for seat in (0, 1)]
        wins = round(rate * len(games))
        for index, (seed, seat) in enumerate(games):
            row = dict(opponent=name, seat=seat, seed=seed, score=1. if index < wins else 0.)
            if bound:
                row.update(candidate_hash=candidate, opponent_hash=name + '-hash',
                           environment=engine, backend=backend, configuration={'seed': seed})
            rows.append(row)
    return standing(rows, RANKS)


def preflight(sha=ARTIFACT, engine=ENGINE, status='PASSED'):
    return {'artifact': 'versions/v009/main.py', 'sha256': sha, 'environment': engine,
            'seed': 314159, 'states': 720, 'rewards': [1., 2.], 'status': status}


def slots(used=0, checked=NOW, incumbent=INCUMBENT):
    return {'checked_at': checked.isoformat(), 'daily_submissions_used': used,
            'slots': [{'slot': 'A', 'id': 'thomas_t95', 'hash': incumbent,
                       'submitted_at': '2026-09-01T00:00:00+00:00', 'episodes': 412},
                      {'slot': 'B', 'id': 'v003', 'hash': OTHER,
                       'submitted_at': '2026-09-05T00:00:00+00:00', 'episodes': 96}]}


def finalist(name='v009', displaces='A', **overrides):
    return {'id': name, 'displaces': displaces, 'comparison': comparison(),
            'standing': standing_report(), 'preflight': preflight(), **overrides}


def run(finalists=None, declaration=None, now=NOW):
    return dossier(finalists or [finalist()], declaration or slots(), now=now)


def test_a_bound_dossier_submits_and_names_the_history_it_destroys():
    result = run()
    row = result['candidates'][0]
    assert (result['verdict'], row['verdict']) == ('SUBMIT', 'SUBMIT')
    assert row['refusals'] == [] and row['failures'] == []
    # The incumbent is derived from the named slot, never re-typed beside it, so the
    # baseline leg submit_gate checks and the history the send destroys are one fact.
    assert row['incumbent']['hash'] == INCUMBENT
    assert '412 observable episodes' in row['incumbent']['displaces']
    assert row['paired']['comparison_role'] == 'incumbent'
    assert 'thomas_t95' in render(result)


@pytest.mark.parametrize('broken,expected', [
    ({'standing': standing_report(candidate=OTHER)}, 'The standing measured'),
    ({'preflight': preflight(sha=SPARE)}, 'the preflight flew'),
    ({'standing': standing_report(bound=False)}, 'not bound to an artifact'),
    ({'standing': standing_report(engine={'version': 'other'})}, 'the engine fingerprint'),
    ({'standing': standing_report(backend='official')}, 'the backend')])
def test_reports_about_different_things_decide_nothing(broken, expected):
    """Issue #47's done-when: no combination of mismatched inputs can reach SUBMIT."""
    result = run([finalist(**broken)])
    row = result['candidates'][0]
    assert result['verdict'] == row['verdict'] == 'NO DECISION'
    assert any(expected in reason for reason in row['refusals'])


def test_an_opponent_that_changed_artifact_between_the_two_panels_is_refused():
    report = standing_report()
    report['provenance']['opponent_hashes']['top_a'] = 'a-different-build'
    row = run([finalist(standing=report)])['candidates'][0]
    assert row['verdict'] == 'NO DECISION'
    assert any('changed artifact' in reason for reason in row['refusals'])


def test_the_recorded_v004_evidence_cannot_be_joined_to_the_v004_artifact():
    """The gap this issue is about, on the only evidence the repo actually recorded.

    `docs/incumbent-t95-comparison.json` evaluated an agent by name, so its candidate
    hash is a digest of the sources; `versions/v004/preflight.json` flew the built file,
    so its hash is the file. Both are correct and they are not the same identity, which
    is precisely the join a human would otherwise make by eye.
    """
    root = Path(__file__).resolve().parents[1]
    recorded = json.loads((root / 'docs/incumbent-t95-comparison.json').read_text())
    flight = json.loads((root / 'versions/v004/preflight.json').read_text())
    assert recorded['snapshot']['candidate_hash'] != flight['sha256']
    row = dossier([{'id': 'v004', 'displaces': 'A', 'comparison': recorded,
                    'standing': standing_report(candidate=flight['sha256'],
                                                engine=flight['environment']),
                    'preflight': flight}],
                  slots(incumbent=recorded['snapshot']['baseline_hash']),
                  now=NOW)['candidates'][0]
    assert any('Re-run the comparison with the artifact path' in reason
               for reason in row['refusals'])
    # The measured failures underneath survive the refusal instead of being replaced.
    assert row['verdict'] == 'NO SUBMIT'
    assert any('validation provenance' in reason for reason in row['failures'])


@pytest.mark.parametrize('seeds,label', [(range(1000, 1075), 'dev'),
                                         (range(100000, 100075), 'seen'),
                                         (range(600000, 600075), 'unregistered')])
def test_only_reserved_validation_seeds_authorize_an_absolute_standing(seeds, label):
    row = run([finalist(standing=standing_report(seeds=seeds))])['candidates'][0]
    assert row['standing_split'] == (None if label == 'unregistered' else label)
    assert row['verdict'] == 'NO SUBMIT'
    assert any(f'ran on {label} seeds' in reason for reason in row['failures'])


def test_the_split_is_read_from_the_registry_and_not_from_a_field():
    assert split_of(list(VALIDATION)) == 'validation'
    assert split_of([500099, 500100]) is None   # straddles seen and validation
    assert split_of([]) is None


def test_a_decisive_band_carried_by_one_artifact_is_not_a_band():
    rates = {name: 1. for name in RANKS if name != 'top_b'}
    row = run([finalist(standing=standing_report(rates))])['candidates'][0]
    assert row['verdict'] == 'NO SUBMIT'
    assert any('band top10 carries 1 opponent' in reason for reason in row['failures'])


def test_a_lineage_that_holds_us_down_blocks_the_release():
    row = run([finalist(standing=standing_report({**WINNING, 'low_a': .2}))])['candidates'][0]
    assert row['verdict'] == 'NO SUBMIT'
    assert any('structural matchup' in reason for reason in row['failures'])


def test_a_stale_rating_snapshot_cannot_carry_a_decision():
    late = NOW + timedelta(days=30)
    row = dossier([finalist()], slots(checked=late), now=late)['candidates'][0]
    assert any('rating snapshot is 30 days old' in reason for reason in row['failures'])


def test_a_failed_preflight_is_a_failure_and_not_a_refusal():
    row = run([finalist(preflight=preflight(status=None))])['candidates'][0]
    assert row['refusals'] == []
    assert row['verdict'] == 'NO SUBMIT'
    assert any('not PASSED' in reason for reason in row['failures'])


def test_two_finalists_cannot_be_sent_into_the_same_slot():
    pair = [finalist('v009', 'A'), finalist('v010', 'A')]
    result = run(pair)
    assert result['verdict'] == 'NO SUBMIT'
    assert any('both displace slot A' in reason for reason in result['candidates'][0]['failures'])


def test_an_undeclared_slot_leaves_the_incumbent_underived():
    row = run([finalist(displaces='C')])['candidates'][0]
    assert row['incumbent'] is None
    assert any("displace slot 'C'" in reason for reason in row['failures'])
    # Without a declared slot there is no incumbent, and submit_gate says so itself.
    assert any('No incumbent declared' in reason for reason in row['failures'])


def test_resending_the_artifact_already_in_a_slot_is_refused():
    row = run([finalist(preflight=preflight(sha=OTHER),
                        comparison=comparison(candidate=OTHER),
                        standing=standing_report(candidate=OTHER))])['candidates'][0]
    assert row['refusals'] == []
    assert any('byte-identical to the agent already in slot B' in reason
               for reason in row['failures'])


def test_the_recovery_reserve_is_held_back_from_the_daily_quota():
    assert run(declaration=slots(used=2))['verdict'] == 'SUBMIT'
    result = run(declaration=slots(used=3))
    assert result['verdict'] == 'NO SUBMIT'
    assert any(f"of today's {DAILY_QUOTA} uploads are spent" in reason
               for reason in result['candidates'][0]['failures'])


def test_an_undated_or_stale_account_state_cannot_support_a_send():
    stale = run(declaration=slots(checked=NOW - timedelta(hours=30)))
    assert any('past the 24 h limit' in reason for reason in stale['candidates'][0]['failures'])
    undated = dict(slots())
    del undated['checked_at']
    assert any('no parseable `checked_at`' in reason
               for reason in run(declaration=undated)['candidates'][0]['failures'])


def test_nothing_is_planned_after_the_planning_cutoff():
    late = LAST_PLANNED_SWAP + timedelta(hours=1)
    result = dossier([finalist()], slots(checked=late), now=late)
    assert result['verdict'] == 'NO SUBMIT'
    assert any('planning cut-off' in reason for reason in result['candidates'][0]['failures'])


def test_complementarity_names_a_hole_both_finalists_share():
    weak = {**WINNING, 'low_a': .1}
    pair = [finalist('v009', 'A', standing=standing_report(weak)),
            finalist('v010', 'B', standing=standing_report(weak))]
    report = run(pair)['complementarity']
    assert report['both_below_floor'] == ['low_a']
    assert any('Two slots, one hole' in note for note in report['warnings'])
    # Reported, never a veto: the pair still fails on its own standing, not on this.
    assert report['covered_by_exactly_one'] == []


def test_complementarity_credits_a_finalist_that_covers_the_others_hole():
    pair = [finalist('v009', 'A', standing=standing_report({**WINNING, 'low_a': .1})),
            finalist('v010', 'B', standing=standing_report({**WINNING, 'low_b': .1}))]
    report = run(pair)['complementarity']
    assert report['both_below_floor'] == []
    assert sorted(report['covered_by_exactly_one']) == ['low_a', 'low_b']


def test_one_finalist_is_not_a_complementarity_question():
    assert run()['complementarity']['applies'] is False


@pytest.mark.parametrize('change', [
    {'standing': standing_report(backend='official'), 'preflight': preflight()},
    {'comparison': comparison(engine={**ENGINE, 'version': '1.33.0'})}])
def test_the_run_spec_hash_moves_when_a_material_field_moves(change):
    assert run_spec(finalist())['sha256'] != run_spec(finalist(**change))['sha256']


def test_the_dossier_reads_only_and_says_so():
    result = run()
    assert 'No submission was made' in result['note']
    assert result['policy']['source'] == 'docs/FINAL_SUBMISSION_POLICY.md'


def test_the_same_file_cannot_be_sent_into_both_slots():
    # The degenerate end of the complementarity question, and invisible to the per-slot
    # check: neither finalist is active yet, so neither collides with a declared hash.
    result = run([finalist('v009', 'A'), finalist('v010', 'B')])
    assert result['verdict'] == 'NO SUBMIT'
    assert any('are the same file' in reason
               for reason in result['candidates'][0]['failures'])
    # Two genuinely different finalists, each compared against the occupant of the slot
    # it displaces, are two sends and both are approved.
    distinct = [finalist('v009', 'A'),
                finalist('v010', 'B', comparison=comparison(candidate=SPARE, baseline=OTHER),
                         preflight=preflight(sha=SPARE),
                         standing=standing_report(candidate=SPARE))]
    result = run(distinct)
    assert [row['verdict'] for row in result['candidates']] == ['SUBMIT', 'SUBMIT']
    assert result['candidates'][1]['incumbent']['id'] == 'v003'


def test_a_malformed_slot_declaration_is_reported_and_not_dereferenced():
    declaration = slots()
    del declaration['slots'][0]['hash']
    result = run(declaration=declaration)
    row = result['candidates'][0]
    assert row['incumbent'] is None
    assert any('non-empty strings' in reason for reason in row['failures'])
    assert row['verdict'] == 'NO SUBMIT'


def test_a_malformed_snapshot_date_is_reported_and_does_not_raise():
    broken = comparison()
    broken['snapshot']['as_of'] = 'sometime last week'
    row = run([finalist(comparison=broken)])['candidates'][0]
    assert any('does not say when the ratings were observed' in reason
               for reason in row['failures'])
