"""A one-line overlay change is only measurable if the line really changed and the
comparison is paired world by world against a baseline run of the same worlds."""
import pytest

from experiments.market_overlay import (ADVANCE_GUARD, RELAXED_GUARD, paired, patched,
                                        relaxed_advance_variant)


def write_source(directory, guard=ADVANCE_GUARD):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'router_parent.py').write_text(
        'LAST_STEP = 718\n\n\ndef advance_sales(action, view, state, tape, step):\n'
        '    next_step = step + 1\n    ' + guard + '\n        return\n')
    (directory / 'main.py').write_text('def agent(o, c=None):\n    pass\n')
    (directory / 'actions.json').write_text('[]')
    return directory


def test_the_relaxed_variant_drops_only_the_every_fourth_turn_clause(tmp_path):
    source = write_source(tmp_path / 'src')
    relaxed_advance_variant(tmp_path / 'out', source=source)
    text = (tmp_path / 'out' / 'router_parent.py').read_text()
    assert RELAXED_GUARD in text
    assert 'step % 4 == 0' not in text
    # the other two conditions must survive
    assert 'next_step > LAST_STEP' in text and 'next_step % 72 == 0' in text


def test_a_source_without_the_guard_is_refused(tmp_path):
    source = write_source(tmp_path / 'src', guard='if next_step > LAST_STEP:')
    with pytest.raises(ValueError, match='exactly one occurrence'):
        relaxed_advance_variant(tmp_path / 'out', source=source)


def test_a_guard_appearing_twice_is_refused(tmp_path):
    source = tmp_path / 'src'
    source.mkdir(parents=True)
    (source / 'router_parent.py').write_text(ADVANCE_GUARD + '\n' + ADVANCE_GUARD + '\n')
    (source / 'main.py').write_text('')
    with pytest.raises(ValueError, match='exactly one occurrence'):
        relaxed_advance_variant(tmp_path / 'out', source=source)


def test_patched_replaces_the_whole_tree_rather_than_merging(tmp_path):
    source = write_source(tmp_path / 'src')
    relaxed_advance_variant(tmp_path / 'out', source=source)
    (tmp_path / 'out' / 'stale.txt').write_text('left over')
    relaxed_advance_variant(tmp_path / 'out', source=source)
    assert not (tmp_path / 'out' / 'stale.txt').exists()


def row(episode, score, margin):
    return {'episode_id': episode, 'score': score, 'margin': margin}


def test_paired_counts_flips_against_the_baseline_not_against_the_ladder():
    base = [row(1, 0.0, -50.0), row(2, 1.0, 300.0), row(3, 0.0, -900.0)]
    rows = [row(1, 1.0, 20.0), row(2, 0.0, -10.0), row(3, 0.0, -400.0)]
    report = paired(rows, base)
    assert report['gained'] == 1 and report['gained_episodes'] == [1]
    assert report['regressed'] == 1 and report['regressed_episodes'] == [2]
    assert report['net_worlds'] == 0
    assert report['compared'] == 3


def test_paired_reports_margin_movement_separately_from_flips():
    """Every world can improve by a few coins and flip nothing; both facts must survive."""
    base = [row(index, 0.0, -5000.0) for index in range(4)]
    rows = [row(index, 0.0, -4900.0) for index in range(4)]
    report = paired(rows, base)
    assert report['net_worlds'] == 0
    assert report['margin_delta_median'] == pytest.approx(100.0)
    assert report['margin_delta_positive'] == 4


def test_paired_skips_worlds_the_baseline_does_not_have():
    report = paired([row(1, 1.0, 10.0), row(9, 1.0, 10.0)], [row(1, 0.0, -10.0)])
    assert report['compared'] == 1 and report['gained'] == 1


def test_paired_handles_an_empty_comparison_without_inventing_statistics():
    report = paired([], [])
    assert report['compared'] == 0 and report['net_worlds'] == 0
    assert 'margin_delta_median' not in report
