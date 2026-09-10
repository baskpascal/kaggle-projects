"""The 2x2 is only causal if the forced regime actually ran, so the patch and the
verification are pinned here. A silently unpatched router would report the observational
result again and look like a clean experiment."""
import pytest

from experiments.regime_counterfactual import (ROUTE_LINE, forced_variant, observed_branch,
                                               summarise_cell, yarn_worlds)


def write_source(directory, routing=ROUTE_LINE):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'router_parent.py').write_text(
        'SHOP_PLANS = {}\n\n\ndef act(state, shops):\n    ' + routing + '\n')
    (directory / 'main.py').write_text('def agent(observation, configuration=None):\n    pass\n')
    (directory / 'actions.json').write_text('[]')
    return directory


def test_forcing_a_plan_rewrites_the_one_routing_line(tmp_path):
    source = write_source(tmp_path / 'src')
    main = forced_variant(7, tmp_path / 'out', source=source)
    text = (tmp_path / 'out' / 'router_parent.py').read_text()
    assert 'state.plan = 7' in text
    assert ROUTE_LINE not in text
    assert main.name == 'main.py' and main.is_file()
    assert (tmp_path / 'out' / 'actions.json').is_file()


def test_a_router_without_the_expected_line_is_refused_not_silently_copied(tmp_path):
    source = write_source(tmp_path / 'src', routing='state.plan = 0')
    with pytest.raises(ValueError, match='exactly one routing line'):
        forced_variant(3, tmp_path / 'out', source=source)


def test_a_router_with_the_line_twice_is_also_refused(tmp_path):
    source = write_source(tmp_path / 'src', routing=ROUTE_LINE + '\n    ' + ROUTE_LINE)
    with pytest.raises(ValueError, match='exactly one routing line'):
        forced_variant(3, tmp_path / 'out', source=source)


def test_forcing_replaces_an_existing_variant_rather_than_merging_into_it(tmp_path):
    source = write_source(tmp_path / 'src')
    forced_variant(3, tmp_path / 'out', source=source)
    (tmp_path / 'out' / 'stale.txt').write_text('left over')
    forced_variant(5, tmp_path / 'out', source=source)
    assert not (tmp_path / 'out' / 'stale.txt').exists()
    assert 'state.plan = 5' in (tmp_path / 'out' / 'router_parent.py').read_text()


def test_the_branch_is_read_from_state_not_from_the_plan_we_asked_for():
    assert observed_branch({'sheep': 11}) == 'sheep'
    assert observed_branch({'sheep': 6}) == 'coop'
    assert observed_branch({'sheep': 10}) == 'sheep'
    assert observed_branch(None) is None


def test_yarn_worlds_splits_on_the_first_two_shops():
    shops = {'1': {'7': {'shops': ['BRUNCH_SPOT', 'YARN_STORE']}},
             '2': {'7': {'shops': ['BAKERY', 'PIZZA_SHOP']}},
             '3': {'7': {'shops': []}}}
    worlds = [{'episode_id': 1}, {'episode_id': 2}, {'episode_id': 3}]
    early, late = yarn_worlds(shops, worlds)
    assert [w['episode_id'] for w in early] == [1]
    assert [w['episode_id'] for w in late] == [2, 3]


def row(episode, score, ladder_outcome, margin=100.0, ladder_margin=100.0, branch='sheep'):
    return {'episode_id': episode, 'seat': 0, 'seed': 1, 'opponent_team': 'x',
            'score': score, 'our_money': 1000.0, 'opponent_money': 900.0,
            'margin': margin, 'ladder_margin': ladder_margin,
            'ladder_outcome': ladder_outcome, 'branch_executed': branch,
            'state_360': {'sheep': 11, 'cow': 0, 'goose': 0, 'pasture': 18, 'coop': 0,
                          'wool': 3, 'stock_units': 20, 'cash': 1.0},
            'state_718': {'stock_units': 9, 'sheep': 11, 'cow': 0, 'goose': 0,
                          'pasture': 18, 'coop': 0, 'wool': 1, 'cash': 2.0},
            'failures': []}


def test_the_cell_summary_reports_gains_and_regressions_against_the_ladder():
    rows = [row(1, 1.0, 0.0), row(2, 0.0, 1.0), row(3, 1.0, 1.0)]
    summary = summarise_cell(rows, 'A')
    assert summary['wins'] == 2 and summary['losses'] == 1
    assert summary['gained'] == 1 and summary['regressed'] == 1
    assert summary['net_worlds'] == 0
    assert summary['regressed_episodes'] == [2]
    assert summary['ladder_win_rate'] == pytest.approx(2 / 3)


def test_the_cell_summary_records_which_branch_actually_ran():
    rows = [row(1, 1.0, 0.0, branch='sheep'), row(2, 1.0, 0.0, branch='coop')]
    summary = summarise_cell(rows, 'A')
    assert summary['branch_executed'] == {'sheep': 1, 'coop': 1}


def test_the_paired_margin_delta_is_against_the_same_world_on_the_ladder():
    rows = [row(1, 1.0, 0.0, margin=500.0, ladder_margin=-500.0),
            row(2, 1.0, 0.0, margin=200.0, ladder_margin=100.0)]
    summary = summarise_cell(rows, 'A')
    assert summary['paired_margin_delta_median'] == pytest.approx(550.0)


def test_a_tie_is_half_a_win_and_is_counted_separately():
    rows = [row(1, 0.5, 0.0), row(2, 0.0, 0.0)]
    summary = summarise_cell(rows, 'A')
    assert summary['ties'] == 1 and summary['win_rate'] == pytest.approx(0.25)
