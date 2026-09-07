import pytest

from agent.economy import price, sale_value
from agent.market import projected_shed, sale_orders
from agent.params import DEFAULTS
from agent.state import State
from arena.engine import make_environment, official
from arena.match import observations


def state(item='MELON', amount=40, inventory=10130, step=240, **config):
    env = make_environment(42, config)
    obs = observations(env.state)[0]
    obs.update(step=step, day=step // 24, hour=step % 24)
    obs['private']['shed'][item] = amount
    obs['market']['inventory'][item] = inventory
    return State(obs, dict(env.configuration))


@pytest.mark.parametrize('item,inventory', [('MELON', 10130), ('WOOL', 10045), ('STRAWBERRY', 10035)])
def test_premium_sales_stop_and_eventually_liquidate(item, inventory):
    s = state(item, inventory=inventory)
    orders = sale_orders(s, DEFAULTS, s.private['shed'])
    sold = sum(o[2] for o in orders)
    assert sold < 40
    s.obs.update(step=718, day=29, hour=22)
    assert sale_orders(s, DEFAULTS, s.private['shed']) == [['SELL', item, 40]]


def test_good_price_without_material_recovery_sells_small_batch_in_full():
    s = state(amount=6, inventory=9900)
    assert sale_orders(s, DEFAULTS, s.private['shed']) == [['SELL', 'MELON', 6]]
    assert sale_value('MELON', 6, 10000) > 1490


def test_capacity_pressure_overrides_holding():
    s = state(amount=100)
    s.private['inventories'][0]['MELON'] = 30
    orders = sale_orders(s, DEFAULTS, s.private['shed'])
    assert sum(o[2] for o in orders) >= 50


def test_custom_capacity_order_limit_and_feed_reserve():
    s = state(amount=12, shedCapacity=20, maxMarketOrdersPerTurn=1)
    s.private['shed']['WHEAT'] = 8
    orders = sale_orders(s, DEFAULTS, s.private['shed'], reserve_wheat=6)
    assert len(orders) <= 1
    assert all(o[2] <= 2 for o in orders if o[1] == 'WHEAT')


def test_projected_drop_sells_on_last_turn_in_official_engine():
    env = make_environment(7)
    env.state[0].observation.step = 718
    env.state[0].observation.day = 29
    env.state[0].observation.hour = 22
    env.state[0].observation.private.inventories[0]['MELON'] = 6
    from agent.planner import policy
    obs = observations(env.state)[0]
    action = policy(obs, dict(env.configuration))
    assert action['farmer'] == ['DROP']
    assert ['SELL', 'MELON', 6] in action['market']
    before = obs['farms'][0]['money']
    env.step([action, {'farmer': ['PASS'], 'market': []}])
    assert env.state[0].observation.farms[0]['money'] > before
    assert env.state[0].observation.private.shed['MELON'] == 0


def test_pickups_are_reserved_before_sale():
    s = state('WHEAT', 8)
    projected = projected_shed(s, [['PICKUP', 'WHEAT', 4]])
    assert projected['WHEAT'] == 4
    assert s.private['shed']['WHEAT'] == 8


@pytest.mark.parametrize('item', ['MELON', 'WOOL', 'STRAWBERRY'])
def test_sale_value_matches_actual_commits_with_overrides_and_floor(item):
    module = official()
    overrides = {item: {'T': 20, 'base': 150}}
    params = module._resolve_market_params(overrides)
    market = module._new_market(params)
    market['inventory'][item] = 10015
    farm, private = {'money': 0}, {'shed': {item: 30}}
    expected = sale_value(item, 30, 10015, overrides)
    for _ in range(30):
        quote = module.market_price(item, market['inventory'][item], params)
        assert module._commit_unit('SELL', item, quote, farm, private, market)
    assert farm['money'] == expected


def test_champion_is_immutable_artifact():
    from arena.agents import ROOT, agent_hash
    assert agent_hash('champion') == agent_hash(str(ROOT / 'versions/v000/main.py'))


def test_held_harvest_reduces_new_crop_score():
    from agent.economy import crop_scores
    s = state(amount=0, inventory=10000)
    before = crop_scores(s, DEFAULTS)['MELON']
    s.private['shed']['MELON'] = 80
    s.private['inventories'][0]['MELON'] = 20
    assert crop_scores(s, DEFAULTS)['MELON'] < before


def test_duplicate_shops_contribute_separately_and_no_demand_sells_fertilizer():
    from agent.economy import daily_demand
    s = state('FERTILIZER', 20)
    s.obs['town']['unlocked_shops'] = ['YARN_STORE', 'YARN_STORE']
    assert daily_demand(s, 'WOOL') == 25
    assert sale_orders(s, DEFAULTS, s.private['shed']) == [['SELL', 'FERTILIZER', 20]]

