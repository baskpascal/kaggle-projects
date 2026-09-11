"""A delayed commitment is only a fair test if the bridge really switches, the oracle
really uses the future label and never reaches the agent, and the observable rule really
uses only what the agent can see."""
import pytest

from experiments.delayed_commitment import (ROUTE_BLOCK, bridged_variant, compose,
                                            observable_variant, wool_health, wool_revenue)


def write_source(directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'router_parent.py').write_text(
        'ROUTE_STEP = 144\nSHOP_PLANS = {}\n\n\nclass P:\n'
        '    def act(self, observation):\n'
        '        step = 1\n'
        + ROUTE_BLOCK + '\n        return state.plan\n')
    (directory / 'main.py').write_text('def agent(o, c=None):\n    pass\n')
    return directory


def test_bridged_variant_writes_prefix_and_suffix(tmp_path):
    source = write_source(tmp_path / 'src')
    bridged_variant(0, 3, tmp_path / 'out', switch_step=264, source=source)
    text = (tmp_path / 'out' / 'router_parent.py').read_text()
    assert 'state.plan = 0  # delayed_commitment prefix' in text
    assert 'if step == 264:' in text
    assert 'state.plan = 3  # delayed_commitment suffix' in text
    assert 'SHOP_PLANS.get' not in text


def test_the_observable_rule_reads_only_the_price_it_can_see(tmp_path):
    source = write_source(tmp_path / 'src')
    observable_variant(0, tmp_path / 'out', switch_step=264, threshold=5.0, source=source)
    text = (tmp_path / 'out' / 'router_parent.py').read_text()
    assert 'observation["market"]["prices"].get("WOOL", 0)' in text
    assert 'if step == 264:' in text
    # The oracle label must never appear inside an agent.
    assert 'oracle' not in text.lower()
    assert 'healthy' not in text.lower()


def test_a_source_without_the_routing_block_is_refused(tmp_path):
    source = tmp_path / 'src'
    source.mkdir(parents=True)
    (source / 'router_parent.py').write_text('state.plan = 0\n')
    (source / 'main.py').write_text('')
    with pytest.raises(ValueError, match='exactly one routing block'):
        bridged_variant(0, 3, tmp_path / 'out', source=source)


def test_wool_health_labels_from_the_future_day_only():
    timelines = {'1': {'days': [{'day': 11, 'market_prices': {'WOOL': 200}},
                                {'day': 15, 'market_prices': {'WOOL': 1}}]},
                 '2': {'days': [{'day': 11, 'market_prices': {'WOOL': 200}},
                                {'day': 15, 'market_prices': {'WOOL': 225}}]}}
    health = wool_health(timelines, [1, 2], day=15, threshold=5.0)
    assert health == {1: False, 2: True}
    early = wool_health(timelines, [1, 2], day=11, threshold=5.0)
    assert early == {1: True, 2: True}      # day 11 cannot tell them apart


def test_compose_picks_the_run_the_future_label_points_at():
    healthy = {1: True, 2: False}
    when_healthy = [{'episode_id': 1, 'score': 1.0}, {'episode_id': 2, 'score': 0.0}]
    when_collapsed = [{'episode_id': 1, 'score': 0.0}, {'episode_id': 2, 'score': 1.0}]
    picked = compose(healthy, when_healthy, when_collapsed)
    assert [(row['episode_id'], row['score']) for row in picked] == [(1, 1.0), (2, 1.0)]
    assert [row['oracle_label'] for row in picked] == ['healthy', 'collapsed']


def test_compose_skips_worlds_a_run_is_missing_rather_than_inventing_them():
    picked = compose({1: True, 9: True}, [{'episode_id': 1, 'score': 1.0}], [])
    assert [row['episode_id'] for row in picked] == [1]


def test_wool_revenue_counts_only_stock_that_fell_while_cash_rose():
    row = {'state_144': {'wool': 10, 'cash': 100},
           'state_264': {'wool': 4, 'cash': 500},     # sold six
           'state_360': {'wool': 9, 'cash': 400},     # produced five, cash fell
           'state_540': {'wool': 2, 'cash': 900},     # sold seven
           'state_718': {'wool': 2, 'cash': 900}}
    assert wool_revenue(row) == 13


def test_wool_revenue_ignores_stock_lost_without_a_sale():
    row = {'state_144': {'wool': 10, 'cash': 100},
           'state_264': {'wool': 0, 'cash': 50}}      # gone, and cash fell
    assert wool_revenue(row) == 0


def test_wool_revenue_needs_two_captures():
    assert wool_revenue({'state_144': {'wool': 10, 'cash': 1}}) is None
