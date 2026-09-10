"""The diagnosis decides where the next weeks go, so its arithmetic is pinned here.

Two failure modes matter most: a degenerate envelope turning one coin of noise into a
"divergence", and a best-of-many-features score being read as evidence when the selection
itself was never validated."""
import pytest

from experiments.wide_loss_diagnosis import (balanced_accuracy, classify, cliffs_delta,
                                             decision_variability, first_divergence,
                                             leave_one_out_stump, material, overlap,
                                             percentile, populations, stump)


def world(episode, margin, days):
    return {'episode_id': episode, 'ladder_margin': margin, 'opponent_team': 'x',
            'opponent_rating': 2700.0, 'days': days}


def day(number, **values):
    row = {'day': number, 'turn': number * 24, 'cash': 0, 'delta_cash': 0,
           'land_owned': 0, 'land_used': 0, 'crops': 0, 'herd': 0, 'hands': 0,
           'stock_units': 0, 'units_out': 0, 'units_in': 0, 'known_shops': 0,
           'passes': 0, 'idle_hands': 0, 'animals': {}, 'structures': {},
           'market_prices': {}, 'opponent_cash': 0, 'opponent_land_used': 0,
           'opponent_hands': 0}
    row.update(values)
    return row


def test_classify_puts_a_thousand_exactly_in_the_wide_groups():
    assert classify({'ladder_margin': -1000.0}) == 'wide_loss'
    assert classify({'ladder_margin': -999.0}) == 'tight'
    assert classify({'ladder_margin': 999.0}) == 'tight'
    assert classify({'ladder_margin': 1000.0}) == 'wide_win'


def test_populations_keep_the_wide_wins_out_of_the_tight_group():
    timelines = {1: world(1, -2000.0, []), 2: world(2, 10.0, []), 3: world(3, 5000.0, [])}
    groups = populations(timelines)
    assert [w['episode_id'] for w in groups['wide_loss']] == [1]
    assert [w['episode_id'] for w in groups['tight']] == [2]
    assert [w['episode_id'] for w in groups['wide_win']] == [3]


def test_a_degenerate_envelope_does_not_turn_one_coin_into_a_divergence():
    """`v006` opens identically everywhere, so the tight envelope is often a single value."""
    assert material(27.0, 26.0, 26.0, absolute=1.0, relative=.05) is None
    assert material(26.0, 26.0, 26.0) is None
    verdict = material(40.0, 26.0, 26.0, absolute=1.0, relative=.05)
    assert verdict['direction'] == 'above' and verdict['distance'] == pytest.approx(14.0)


def test_materiality_scales_with_the_bound_so_large_quantities_are_not_over_reported():
    assert material(10_400.0, 0.0, 10_000.0, absolute=1.0, relative=.05) is None
    assert material(11_000.0, 0.0, 10_000.0, absolute=1.0, relative=.05) is not None


def test_first_divergence_reports_the_earliest_material_day_and_the_largest_signal():
    subject = world(1, -2000.0, [day(1, cash=27), day(2, cash=100, crops=500)])
    table = {('cash', 1): (26.0, 26.0), ('cash', 2): (26.0, 26.0),
             ('crops', 1): (0.0, 0.0), ('crops', 2): (0.0, 1.0)}
    found = first_divergence(subject, table, ['cash', 'crops'], [1, 2])
    assert found['day'] == 2
    assert found['signals'][0]['feature'] == 'crops'
    assert found['signals'][0]['direction'] == 'above'


def test_first_divergence_returns_no_day_when_nothing_is_material():
    subject = world(1, -2000.0, [day(1, cash=26), day(2, cash=26)])
    table = {('cash', 1): (26.0, 26.0), ('cash', 2): (26.0, 26.0)}
    assert first_divergence(subject, table, ['cash'], [1, 2]) == {'day': None, 'signals': []}


def test_decision_variability_counts_one_when_a_build_never_changes():
    timelines = {index: world(index, -2000.0, [day(1, land_owned=25, cash=index)])
                 for index in range(4)}
    rows = decision_variability(timelines, (1,))
    assert rows[0]['land_owned'] == 1
    assert rows[0]['cash'] == 4
    assert rows[0]['worlds'] == 4


def test_cliffs_delta_is_signed_and_saturates():
    assert cliffs_delta([3.0, 4.0], [1.0, 2.0]) == 1.0
    assert cliffs_delta([1.0, 2.0], [3.0, 4.0]) == -1.0
    assert cliffs_delta([1.0, 2.0], [1.0, 2.0]) == 0.0


def test_overlap_is_zero_for_disjoint_ranges_and_one_for_identical_ones():
    assert overlap([1.0, 2.0], [5.0, 6.0]) == 0.0
    assert overlap([1.0, 2.0], [1.0, 2.0]) == 1.0


def test_percentile_interpolates_like_the_envelope_expects():
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == pytest.approx(2.5)
    assert percentile([1.0, 2.0, 3.0, 4.0], 0) == 1.0
    assert percentile([5.0], 90) == 5.0


def test_balanced_accuracy_is_half_for_a_constant_prediction():
    labels = [1, 1, 0, 0, 0]
    assert balanced_accuracy(labels, [1] * 5) == 0.5
    assert balanced_accuracy(labels, [0] * 5) == 0.5
    assert balanced_accuracy(labels, labels) == 1.0


def test_a_stump_on_pure_noise_does_not_survive_leave_one_out():
    """Six alternating labels over a monotone feature: separable in sample, not out."""
    values = [float(index) for index in range(6)]
    labels = [1, 0, 1, 0, 1, 0]
    outcome = leave_one_out_stump(values, labels)
    assert outcome['in_sample']['balanced_accuracy'] >= outcome['balanced_accuracy']
    assert outcome['balanced_accuracy'] <= 0.6


def test_a_stump_on_a_real_split_survives_leave_one_out():
    values = [0.0, 1.0, 2.0, 10.0, 11.0, 12.0]
    labels = [0, 0, 0, 1, 1, 1]
    outcome = leave_one_out_stump(values, labels)
    assert outcome['balanced_accuracy'] == 1.0


def test_the_stump_threshold_sits_between_points_not_on_one():
    """On the boundary the rule must admit the smallest positive, or leave-one-out lies."""
    rule = stump([0.0, 1.0, 2.0, 11.0, 12.0], [0, 0, 0, 1, 1])
    assert 2.0 < rule['threshold'] < 11.0


def test_stump_reports_the_direction_it_used():
    rule = stump([0.0, 1.0, 10.0, 11.0], [0, 0, 1, 1])
    assert rule['balanced_accuracy'] == 1.0
    assert rule['direction'] == 1
    inverted = stump([0.0, 1.0, 10.0, 11.0], [1, 1, 0, 0])
    assert inverted['balanced_accuracy'] == 1.0
    assert inverted['direction'] == -1


def test_the_flip_curve_counts_worlds_a_uniform_gain_would_turn():
    from experiments.wide_loss_diagnosis import flip_curve
    timelines = {index: world(index, margin, [])
                 for index, margin in enumerate([-2.0, -300.0, -5000.0, 10.0, 4000.0])}
    report = flip_curve(timelines, shifts=(0, 100, 500, 6000))
    assert report['base_wins'] == 2 and report['worlds'] == 5
    curve = {row['shift']: row for row in report['curve']}
    assert curve[0]['flips'] == 0
    assert curve[100]['flips'] == 1        # only the two-coin loss turns
    assert curve[500]['flips'] == 2        # the 300 turns as well
    assert curve[6000]['flips'] == 3       # and finally the blowout
    assert curve[6000]['win_rate'] == 1.0


def test_the_cost_per_flip_is_ordered_by_how_cheap_each_world_is():
    from experiments.wide_loss_diagnosis import flip_curve
    timelines = {index: world(index, margin, [])
                 for index, margin in enumerate([-900.0, -2.0, -50.0, 5.0])}
    cost = flip_curve(timelines)['cost_per_flip']
    assert [row['coins_required'] for row in cost] == [2.0, 50.0, 900.0]
    assert [row['flips'] for row in cost] == [1, 2, 3]
    assert cost[-1]['win_rate'] == 1.0


def test_a_tie_counts_as_a_world_still_to_be_won():
    """The ladder pays ties as half a win, but a flip curve asks what a gain converts."""
    from experiments.wide_loss_diagnosis import flip_curve
    timelines = {0: world(0, 0.0, []), 1: world(1, 5.0, [])}
    report = flip_curve(timelines, shifts=(0, 1))
    assert report['base_wins'] == 1
    assert report['curve'][1]['flips'] == 1
