"""The cohort's headline is flips, so its bookkeeping must not lose a regression in an
average, and it must never claim a world the ladder never recorded."""
import pytest

from experiments.ladder_cohort import summary


def row(episode, score, ladder_outcome, margin=100.0, ladder_margin=100.0, seed=1,
        failures=()):
    return {'episode_id': episode, 'seed': seed, 'seat': 0, 'score': score,
            'our_money': 1000.0, 'opponent_money': 1000.0 - margin, 'margin': margin,
            'ladder_margin': ladder_margin, 'ladder_outcome': ladder_outcome,
            'flipped': None if ladder_outcome is None else score - ladder_outcome,
            'failures': list(failures)}


def test_a_regression_is_counted_even_when_the_win_rate_rises():
    """Two worlds gained and one lost is +1 net, and the lost one is named."""
    rows = [row(1, 1.0, 0.0), row(2, 1.0, 0.0), row(3, 0.0, 1.0), row(4, 1.0, 1.0)]
    report = summary(rows)
    assert report['gained'] == 2 and report['regressed'] == 1
    assert report['net_worlds'] == 1
    assert report['regressed_episodes'] == [3]
    assert report['gained_episodes'] == [1, 2]


def test_the_ladder_win_rate_is_reported_beside_the_local_one():
    rows = [row(1, 1.0, 0.0), row(2, 1.0, 0.0), row(3, 1.0, 1.0), row(4, 0.0, 0.0)]
    report = summary(rows)
    assert report['win_rate'] == pytest.approx(0.75)
    assert report['ladder_win_rate'] == pytest.approx(0.25)


def test_a_world_the_ladder_never_scored_is_not_counted_as_a_flip():
    rows = [row(1, 1.0, None), row(2, 1.0, 0.0)]
    report = summary(rows)
    assert report['gained'] == 1 and report['regressed'] == 0
    assert report['ladder_win_rate'] == pytest.approx(0.0)


def test_close_worlds_are_scored_separately_because_that_is_where_the_ladder_lives():
    close = [row(index, 1.0, 0.0, ladder_margin=50.0) for index in range(4)]
    blowout = [row(9, 0.0, 1.0, ladder_margin=40000.0)]
    report = summary(close + blowout)
    assert report['worlds_decided_under_1000'] == 4
    assert report['win_rate_in_close_worlds'] == 1.0
    assert report['win_rate'] == pytest.approx(0.8)


def test_failures_are_summed_rather_than_silently_dropped():
    rows = [row(1, 1.0, 0.0, failures=['timeout']), row(2, 0.0, 0.0)]
    assert summary(rows)['failures'] == 1


def test_the_median_margin_does_not_replace_the_flip_count():
    """Two blowout wins and three narrow losses is a positive median and a net of -3."""
    rows = [row(1, 1.0, 1.0, margin=40000.0), row(2, 1.0, 1.0, margin=40000.0),
            row(3, 0.0, 1.0, margin=-10.0), row(4, 0.0, 1.0, margin=-10.0),
            row(5, 0.0, 1.0, margin=-10.0)]
    report = summary(rows)
    assert report['margin_median'] == pytest.approx(-10.0)
    assert report['net_worlds'] == -3
    assert report['regressed'] == 3
