"""The reproduction report decides whether every local number is trustworthy, so its
bookkeeping has to be wrong-proof: a level A mismatch must never be summarised away, and a
level B disagreement must keep its direction."""
import pytest

from experiments.ladder_reproduction import summarise, winner


def proof(episode, exact):
    return {'episode': episode, 'exact': exact, 'expected': [10.0, 9.0],
            'fast': [10.0, 9.0], 'official': [10.0, 9.0]}


def prediction(episode, predicted, actual, ladder=(100.0, 90.0), local=(100.0, 90.0)):
    return {'episode_id': episode, 'seat': 0, 'seed': 1,
            'ladder_our_money': ladder[0], 'ladder_opponent_money': ladder[1],
            'local_our_money': local[0], 'local_opponent_money': local[1],
            'actual_ladder_outcome': actual, 'predicted_local_outcome': predicted,
            'agrees': actual == predicted, 'failures': [0, 0]}


def test_winner_reads_a_tie_as_neither_side():
    assert winner([10.0, 9.0]) == 0
    assert winner([9.0, 10.0]) == 1
    assert winner([10.0, 10.0]) is None


def test_a_level_a_mismatch_is_named_not_averaged_away():
    fidelity = {1: proof(1, True), 2: proof(2, False), 3: proof(3, False)}
    report = summarise(fidelity, [])
    assert report['level_a'] == {'episodes': 3, 'exact': 1, 'mismatched': [2, 3]}
    assert 'level_b' not in report


def test_level_b_keeps_the_direction_of_each_disagreement():
    predictions = [prediction(1, 'win', 'loss'), prediction(2, 'win', 'loss'),
                   prediction(3, 'loss', 'win'), prediction(4, 'win', 'win')]
    report = summarise({}, predictions)['level_b']
    assert report['episodes'] == 4
    assert report['agreement'] == pytest.approx(0.25)
    assert report['local_says_win_ladder_says_loss'] == 2
    assert report['local_says_loss_ladder_says_win'] == 1


def test_level_b_reports_both_margin_scales_because_the_gap_is_the_finding():
    predictions = [prediction(1, 'win', 'win', ladder=(100.0, 99.0), local=(100.0, 20.0)),
                   prediction(2, 'win', 'win', ladder=(100.0, 99.0), local=(100.0, 20.0))]
    report = summarise({}, predictions)['level_b']
    assert report['ladder_margin_median'] == pytest.approx(1.0)
    assert report['local_margin_median'] == pytest.approx(80.0)


def test_perfect_agreement_is_reported_as_such_without_hiding_the_sample_size():
    predictions = [prediction(index, 'win', 'win') for index in range(3)]
    report = summarise({1: proof(1, True)}, predictions)
    assert report['level_b']['agreement'] == 1.0
    assert report['level_b']['episodes'] == 3
    assert report['level_a']['exact'] == 1
