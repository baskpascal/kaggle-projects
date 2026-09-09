"""Market resolution, pricing, hiring, land, and the engine's silent-no-op contract."""
import pytest
from conftest import act, farm10, fingerprint, private

from agent.economy import price, sale_value
from arena.engine import make_environment, official
from arena.match import observations, run_match

INVENTORIES = list(range(0, 20001, 11)) + list(range(9500, 10501))


def test_price_model_matches_official_across_the_whole_curve():
    """Category 14: agent price() reproduces market_price() on both sides of I0."""
    module = official()
    for item in module.PRODUCTS:
        for inventory in INVENTORIES:
            assert price(item, inventory) == module.market_price(item, inventory), (item, inventory)


def test_price_model_honours_market_param_overrides():
    """Category 14: sparse marketParams overrides feed the same formula."""
    module = official()
    overrides = {'WOOL': {'above_target': 0.95}, 'WHEAT': {'base': 40, 'below_func': 'linear'}}
    resolved = module._resolve_market_params(overrides)
    for item in ('WOOL', 'WHEAT', 'MELON'):
        for inventory in (0, 9800, 10000, 10500, 12000):
            assert price(item, inventory, overrides) == module.market_price(item, inventory, resolved)


def test_sale_value_matches_a_real_sell_order():
    """Category 14: revenue is quoted per unit at the pre-sell inventory."""
    env = make_environment(5)
    env.state[0].observation.private['shed']['STRAWBERRY'] = 30
    before = env.state[0].observation.farms[0]['money']
    inventory = env.state[0].observation.market['inventory']['STRAWBERRY']
    obs = act(env, market=[['SELL', 'STRAWBERRY', 30]])
    assert obs['farms'][0]['money'] - before == sale_value('STRAWBERRY', 30, inventory)


def test_sales_at_the_price_floor_do_not_add_market_supply():
    """Category 14: the $1 floor stays responsive because floored units are not stocked."""
    env = make_environment(5)
    env.state[0].observation.private['shed']['STRAWBERRY'] = 90
    before = env.state[0].observation.farms[0]['money']
    obs = act(env, market=[['SELL', 'STRAWBERRY', 90]])
    added = obs['market']['inventory']['STRAWBERRY'] - 10000 + 1  # town center took one
    assert added == 62 < 90  # supply stops growing once the quote floors at $1
    assert price('STRAWBERRY', 10000 + added) == 1
    assert obs['farms'][0]['money'] - before == sale_value('STRAWBERRY', 90, 10000)


def test_both_players_are_quoted_the_same_price_for_each_unit():
    """Category 13: market orders resolve in per-unit lockstep, not player by player."""
    env = make_environment(5)
    for player in (0, 1):
        env.state[player].observation.private['shed']['STRAWBERRY'] = 20
    before = [f['money'] for f in env.state[0].observation.farms]
    inventory = env.state[0].observation.market['inventory']['STRAWBERRY']
    env.step([{'farmer': ['PASS'], 'hands': [], 'market': [['SELL', 'STRAWBERRY', 5]]},
              {'farmer': ['PASS'], 'hands': [], 'market': [['SELL', 'STRAWBERRY', 5]]}])
    after = [f['money'] for f in env.state[0].observation.farms]
    gains = [after[i] - before[i] for i in (0, 1)]
    assert gains[0] == gains[1]
    assert gains[0] == sum(price('STRAWBERRY', inventory + 2 * k) for k in range(5))
    # A sequential resolution would have given player 0 a strictly better book.
    assert gains[0] != sum(price('STRAWBERRY', inventory + k) for k in range(5))


def test_buy_then_sell_round_trip_is_cash_neutral():
    """Category 11/14: buys quote at post-buy inventory, so a round trip nets zero."""
    env = make_environment(5)
    env.state[0].observation.private['shed']['WHEAT'] = 5
    before = env.state[0].observation.farms[0]['money']
    obs = act(env, market=[['SELL', 'WHEAT', 5], ['BUY_PRODUCT', 'WHEAT', 5]])
    assert obs['farms'][0]['money'] == before
    assert obs['private']['shed']['WHEAT'] == 5


def test_market_orders_resolve_in_list_order():
    """Category 11: an earlier order settles fully before a later one is quoted."""
    env = make_environment(5)
    env.state[0].observation.private['shed']['WHEAT'] = 4
    env.state[0].observation.farms[0]['money'] = 0.
    obs = act(env, market=[['BUY_PRODUCT', 'WHEAT', 1], ['SELL', 'WHEAT', 4]])
    assert obs['private']['shed']['WHEAT'] == 0  # the buy was unaffordable, the sell still ran
    assert obs['farms'][0]['money'] > 0


def test_a_failed_unit_aborts_its_order_but_not_the_queue():
    """Category 11/20: running out of stock stops that order only."""
    env = make_environment(5)
    env.state[0].observation.private['shed'].update({'WHEAT': 2, 'CARROT': 2})
    obs = act(env, market=[['SELL', 'WHEAT', 50], ['SELL', 'CARROT', 2]])
    assert obs['private']['shed']['WHEAT'] == 0
    assert obs['private']['shed']['CARROT'] == 0


def test_orders_past_the_per_turn_limit_are_dropped():
    """Category 11: maxMarketOrdersPerTurn truncates the queue silently."""
    env = make_environment(5)
    env.state[0].observation.private['shed']['WHEAT'] = 12
    limit = env.configuration['maxMarketOrdersPerTurn']
    obs = act(env, market=[['SELL', 'WHEAT', 1] for _ in range(12)])
    assert obs['private']['shed']['WHEAT'] == 12 - limit == 2


def test_buy_product_is_restricted_and_animals_cannot_be_sold():
    """Category 20: unsupported market sub-ops are silent no-ops."""
    env = make_environment(5)
    env.state[0].observation.private['shed']['GOOSE'] = 1
    before = env.state[0].observation.farms[0]['money']
    obs = act(env, market=[['BUY_PRODUCT', 'MELON', 1], ['SELL', 'GOOSE', 1],
                           ['BUY_SEED', 'BANANA', 1], ['BUY_ANIMAL', 'DRAGON', 1]])
    assert obs['farms'][0]['money'] == before
    assert obs['private']['shed']['GOOSE'] == 1


def test_malformed_market_orders_never_raise():
    """Category 20: bad shapes are dropped by _parse_order without touching state."""
    env = make_environment(5)
    before = env.state[0].observation.farms[0]['money']
    obs = act(env, market=[['SELL'], ['SELL', 'WHEAT', 0], ['SELL', 'WHEAT', 'x'],
                           ['SELL', 'WHEAT', -3], [], ['NONSENSE'], 'garbage', None])
    assert obs['farms'][0]['money'] == before


def test_hire_cost_is_fibonacci_and_resets_each_day():
    """Category 15: cost = farmHandCostMult * fib(hires_today), fib starting 1, 1, 2, 3, 5."""
    module = official()
    assert [module._hire_cost(n) for n in range(8)] == [1, 1, 2, 3, 5, 8, 13, 21]
    # The planner walks the same sequence with a rolling (a, b) pair.
    rolling, a, b = [], 1, 1
    for _ in range(8):
        rolling.append(a)
        a, b = b, a + b
    assert rolling == [module._hire_cost(n) for n in range(8)]
    assert [module._hire_cost(n, 5) for n in range(4)] == [5, 5, 10, 15]

    env = make_environment(5)
    obs = act(env, market=[['HIRE'], ['HIRE'], ['HIRE'], ['HIRE']])
    assert obs['farms'][0]['money'] == 3000. - (1 + 1 + 2 + 3)
    assert obs['farms'][0]['hires_today'] == 4 and len(obs['farms'][0]['hands']) == 4
    for _ in range(23):
        act(env)
    obs = observations(env.state)[0]
    assert obs['farms'][0]['hires_today'] == 0 and obs['farms'][0]['hands'] == []
    obs = act(env, market=[['HIRE']])
    assert obs['farms'][0]['money'] == 3000. - 7 - 1  # cost restarts at fib(0)


def test_hands_spawn_on_shed_access_tiles_in_nwse_order():
    """Category 15: spawn picks the least-occupied access tile, ties broken NWSE."""
    module = official()
    assert module._shed_access_tiles(10) == [(4, 4), (5, 4), (4, 5), (5, 5)]
    env = make_environment(5)
    obs = act(env, market=[['HIRE'], ['HIRE'], ['HIRE'], ['HIRE']])
    assert obs['farms'][0]['farmer'] == [4, 4]
    assert obs['farms'][0]['hands'] == [[5, 4], [4, 5], [5, 5], [4, 4]]


def test_land_prices_and_order_match_the_planner_literals():
    """Category 17: the planner indexes (1000, 2000, 4000) by unlocked quadrant count."""
    module = official()
    assert module.LAND_ORDER == ['NE', 'SW', 'SE']
    assert tuple(module.LAND_PRICES) == (1000, 2000, 4000)
    assert len(module.LAND_PRICES) == len(module.LAND_ORDER) == 3


def test_illegal_unit_actions_are_silent_no_ops():
    """Category 20: every illegal farmer/hand op leaves state byte-identical."""
    module = official()
    farm, priv = farm10(), private()
    farm['farmer'] = [0, 0]
    illegal = [
        ['HARVEST'], ['WATER'], ['FEED'], ['CARE'], ['COLLECT_FERTILIZER'], ['FERTILIZE'],
        ['DIG'], ['PLANT', 'CARROT'], ['PLANT', 'BANANA'], ['PLANT'],
        ['PICKUP', 'WHEAT', 1], ['PICKUP'], ['DROP'], ['PLACE', 'GOOSE'], ['PLACE'],
        ['NORTH'], ['WEST'], ['NONSENSE'], [], 'garbage', None, 42,
    ]
    snapshot = fingerprint(farm, priv)
    for action in illegal:
        module._apply_unit_action(farm, priv, 0, action, 10, 0, 24, 100)
        assert fingerprint(farm, priv) == snapshot, action


def test_actions_for_units_that_do_not_exist_are_ignored():
    """Category 20: extra entries in `hands` never create a unit or an inventory slot."""
    module = official()
    farm, priv = farm10(), private(seeds={'CARROT': 5})
    module._apply_unit_action(farm, priv, 3, ['PLANT', 'CARROT'], 10, 0, 24, 100)
    assert priv['inventories'] == [{}] and priv['seeds']['CARROT'] == 5
    env = make_environment(5)
    obs = act(env, hands=[['PLANT', 'CARROT'], ['NORTH']])
    assert obs['farms'][0]['hands'] == [] and obs['private']['inventories'] == [{}]


def test_audit_tolerates_actions_for_units_that_do_not_exist(tmp_path):
    """Category 20: the lab audit must be as forgiving as the engine, or matches crash."""
    ghost = tmp_path / 'ghost.py'
    ghost.write_text("def agent(observation, configuration=None):\n"
                     "    return {'farmer': ['PASS'], 'hands': [['NORTH'], ['SOUTH']], 'market': []}\n")
    result = run_match(str(ghost), 'pass', 3)
    assert not result['failures'] and result['steps'] == 719
    assert result['audit']['no_effect_NORTH'] == result['audit']['op_NORTH'] == 719


def test_oversubscribed_plant_requests_are_all_dropped():
    """Category 4/20: the interpreter blocks every PLANT of a crop it cannot fully fund."""
    env = make_environment(5)
    act(env, market=[['BUY_SEED', 'CARROT', 1], ['HIRE']])
    act(env, hands=[['WEST']])
    obs = act(env, hands=[['NORTH']])
    assert obs['farms'][0]['farmer'] == [4, 4] and obs['farms'][0]['hands'] == [[4, 3]]
    obs = act(env, farmer=['PLANT', 'CARROT'], hands=[['PLANT', 'CARROT']])
    assert obs['farms'][0]['tiles'][4][4] is None and obs['farms'][0]['tiles'][3][4] is None
    assert obs['private']['seeds']['CARROT'] == 1
    obs = act(env, farmer=['PLANT', 'CARROT'])
    assert obs['farms'][0]['tiles'][4][4]['crop'] == 'CARROT'


@pytest.mark.parametrize('action', [None, 'garbage', 42, [], {'farmer': 'nope'},
                                    {'farmer': ['PLANT'], 'hands': 'nope', 'market': 'nope'}])
def test_malformed_top_level_actions_do_not_crash_the_interpreter(action):
    """Category 20: the engine never raises on a malformed action payload."""
    env = make_environment(5)
    env.step([action, {'farmer': ['PASS'], 'hands': [], 'market': []}])
    assert env.state[0].observation.farms[0]['money'] == 3000.
