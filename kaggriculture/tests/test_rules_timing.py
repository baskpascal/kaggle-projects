"""Season length, intra-turn resolution order, end-of-day, town demand, reward."""
from conftest import PASS, act

from agent.economy import daily_demand
from agent.state import State
from arena.engine import make_environment, official
from arena.match import observations


def test_season_is_719_agent_turns_and_turns_left_matches():
    """Category 1: episodeSteps 720 records 720 states, so agents act 719 times."""
    env = make_environment(4)
    seen = []

    def probe(observation, configuration):
        seen.append(State(dict(observation), dict(configuration)))
        return dict(PASS)

    env.run([probe, 'pass'])
    assert len(seen) == 719
    assert [s.step for s in seen] == list(range(719))
    assert seen[0].turns_left == 719 and seen[-1].turns_left == 1
    assert (seen[-1].day, seen[-1].hour) == (29, 22)
    assert len(env.steps) == 720
    assert all(s.config['episodeSteps'] == 720 for s in seen)


def test_market_orders_resolve_after_unit_actions():
    """Category 2/3: a seed bought this turn cannot be planted until the next one."""
    env = make_environment(5)
    obs = act(env, farmer=['PLANT', 'CARROT'], market=[['BUY_SEED', 'CARROT', 1]])
    assert obs['private']['seeds']['CARROT'] == 1
    assert obs['farms'][0]['tiles'][4][4] is None
    obs = act(env, farmer=['PLANT', 'CARROT'])
    assert obs['private']['seeds']['CARROT'] == 0
    assert obs['farms'][0]['tiles'][4][4]['crop'] == 'CARROT'


def test_drop_then_sell_settles_within_one_turn():
    """Category 2: unit actions run before the market, so a same-turn DROP is sellable."""
    env = make_environment(5)
    env.state[0].observation.private['inventories'][0] = {'WHEAT': 5}
    before = env.state[0].observation.farms[0]['money']
    obs = act(env, farmer=['DROP'], market=[['SELL', 'WHEAT', 5]])
    assert obs['farms'][0]['money'] > before
    assert obs['private']['shed']['WHEAT'] == 0
    assert obs['private']['inventories'] == [{}]


def test_hired_hand_cannot_act_on_its_hire_turn():
    """Category 3/15: HIRE resolves in the market phase, after every unit action."""
    env = make_environment(5)
    obs = act(env, hands=[['WEST']], market=[['HIRE']])
    assert obs['farms'][0]['hands'] == [[5, 4]]  # spawned, did not move
    assert obs['farms'][0]['hires_today'] == 1
    assert obs['farms'][0]['money'] == 2999.
    assert obs['private']['inventories'] == [{}, {}]
    obs = act(env, hands=[['WEST']])
    assert obs['farms'][0]['hands'] == [[4, 4]]


def test_bought_land_is_only_usable_from_the_next_turn():
    """Category 3/17: BUY_LAND unlocks during the market phase."""
    env = make_environment(5)
    obs = act(env, farmer=['EAST'], market=[['BUY_LAND'], ['BUY_SEED', 'CARROT', 1]])
    assert obs['farms'][0]['unlocked_quadrants'] == ['NW', 'NE']
    assert obs['farms'][0]['money'] == 3000. - 1000 - 20
    assert obs['farms'][0]['tiles'][4][5] is None
    obs = act(env, farmer=['PLANT', 'CARROT'])
    assert obs['farms'][0]['tiles'][4][5]['crop'] == 'CARROT'


def test_end_of_day_drops_inventories_and_resets_units():
    """Category 10: hands vanish nightly, positions reset, inventories land in the shed."""
    env = make_environment(5)
    act(env, market=[['HIRE']])
    env.state[0].observation.private['inventories'][0] = {'WHEAT': 3}
    env.state[0].observation.private['inventories'][1] = {'CARROT': 2}
    env.state[0].observation.farms[0]['farmer'] = [0, 0]
    for _ in range(23):
        act(env)
    obs = observations(env.state)[0]
    assert (obs['day'], obs['hour']) == (1, 0)
    assert obs['farms'][0]['hands'] == [] and obs['farms'][0]['hires_today'] == 0
    assert obs['farms'][0]['farmer'] == [4, 4]
    assert obs['private']['inventories'] == [{}]
    assert obs['private']['shed']['WHEAT'] == 3 and obs['private']['shed']['CARROT'] == 2


def test_final_day_never_runs_an_end_of_day_refresh():
    """Category 10/19: the season stops at day 29 hour 22, so nothing is auto-delivered."""
    env = make_environment(5)
    for _ in range(718):
        act(env)
    assert observations(env.state)[0]['step'] == 718  # last acting step, day 29 hour 22
    env.state[0].observation.private['inventories'][0] = {'MELON': 6}
    obs = act(env)
    assert env.done
    assert obs['private']['inventories'] == [{'MELON': 6}]  # never auto-dropped
    assert obs['private']['shed']['MELON'] == 0


def test_reward_is_final_money_and_ignores_unsold_stock():
    """Category 19: no liquidation; shed and inventory contribute nothing."""
    env = make_environment(5)
    for _ in range(718):
        act(env)
    env.state[0].observation.private['shed']['MELON'] = 40
    act(env)
    assert env.done
    farms = env.state[0].observation.farms
    for i, s in enumerate(env.state):
        assert s.status == 'DONE'
        assert s.reward == float(farms[i]['money'])
    assert env.state[0].observation.private['shed']['MELON'] == 40


def test_town_center_and_shops_consume_on_fixed_step_intervals():
    """Category 12: center every townCenterSellInterval steps, shops every townShopSellInterval."""
    module = official()
    env = make_environment(7)
    previous, drained, first_shop_step = None, {}, None
    for _ in range(100):
        obs = observations(env.state)[0]
        inventory = obs['market']['inventory']
        if previous is not None:
            drop = {k: previous[k] - inventory[k] for k in inventory if previous[k] != inventory[k]}
            if drop:
                drained[obs['step'] - 1] = drop
            assert 'FERTILIZER' not in drop
        if obs['town']['unlocked_shops'] and first_shop_step is None:
            first_shop_step = obs['step']
        previous = dict(inventory)
        act(env)
    assert first_shop_step == 72  # first unlock lands at the end of day 2
    center = [s for s, d in drained.items() if len(d) == len(module.TOWN_CENTER_PRODUCTS)]
    assert center == [0, 24, 48, 72, 96]
    assert all(s % 4 == 0 for s in drained)
    assert sorted(s for s in drained if s % 24) == [76, 80, 84, 88, 92]


def test_daily_demand_matches_measured_town_drain():
    """Category 12: economy.daily_demand reproduces one full day of town consumption."""
    env = make_environment(7)
    for _ in range(24 * 6):
        act(env)
    start = observations(env.state)[0]
    assert start['town']['unlocked_shops']
    for _ in range(24):
        act(env)
    end = observations(env.state)[0]
    state = State(start, dict(env.configuration))
    for item, before in start['market']['inventory'].items():
        assert before - end['market']['inventory'][item] == daily_demand(state, item), item
