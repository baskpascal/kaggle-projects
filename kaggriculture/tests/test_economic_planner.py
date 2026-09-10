"""Planner-v1 allocates a mixed herd from observable town demand."""

from agent.economy import (ANIMAL_FLOOR, arbitrage_opportunities, livestock_targets,
                           price, purchase_cost, sale_value)
from agent.params import DEFAULTS
from agent.planner import policy
from agent.state import State
from arena.engine import make_environment
from arena.match import observations


def state(shops=()):
    env = make_environment(173)
    obs = observations(env.state)[0]
    obs['town']['unlocked_shops'] = list(shops)
    return State(obs, dict(env.configuration))


def params(**updates):
    return {**DEFAULTS, **updates}


def test_disabled_planner_is_the_static_baseline():
    target = livestock_targets(state(), params(animal_target=8, animal_type='COW'))
    assert target == {'GOOSE': 0, 'COW': 8, 'SHEEP': 0}


def test_planner_starts_with_a_diversified_market_depth_floor():
    target = livestock_targets(state(), params(economic_planner=True))
    assert sum(target.values()) == 5
    assert all(target[name] >= 1 for name in ANIMAL_FLOOR)


def test_planner_reaches_market_depth_floor_after_bootstrap():
    current = state()
    current.obs.update(day=5, hour=0, step=5 * current.turns_per_day)
    target = livestock_targets(current, params(economic_planner=True))
    assert target == ANIMAL_FLOOR


def test_shop_demand_expands_only_the_matching_chain():
    base = livestock_targets(state(), params(economic_planner=True, animal_ramp_day=0))
    yarn = livestock_targets(
        state(('YARN_STORE',)), params(economic_planner=True, animal_ramp_day=0))
    assert yarn['SHEEP'] > base['SHEEP']
    assert yarn['COW'] == base['COW']
    assert yarn['GOOSE'] == base['GOOSE']


def test_herd_cap_preserves_the_diversified_floor_before_optional_slots():
    target = livestock_targets(
        state(('YARN_STORE', 'YARN_STORE', 'PIZZA_SHOP', 'SMOOTHIE_SHOP')),
        params(economic_planner=True, animal_cap=sum(ANIMAL_FLOOR.values()) + 2,
               animal_ramp_day=0))
    assert sum(target.values()) == sum(ANIMAL_FLOOR.values()) + 2
    assert all(target[name] >= floor for name, floor in ANIMAL_FLOOR.items())


def test_enabled_policy_buys_multiple_animal_chains_and_builds_for_them():
    env = make_environment(173)
    obs = observations(env.state)[0]
    action = policy(obs, dict(env.configuration), params(
        economic_planner=True, planned_feedback=True, max_hands=10,
        max_quadrants=3, expand_day=2))
    bought = {order[1] for order in action['market'] if order[0] == 'BUY_ANIMAL'}
    assert len(bought) >= 2
    assert action['farmer'][0] in {'BUILD_PASTURE', 'BUILD_COOP'}


def test_purchase_cost_uses_each_post_purchase_inventory_quote():
    current = state()
    inventory = current.obs['market']['inventory']['WOOL']
    assert purchase_cost('WOOL', 3, inventory) == sum(
        price('WOOL', inventory - offset) for offset in (1, 2, 3))


def test_arbitrage_requires_a_profitable_demand_backed_round_trip():
    current = state(('YARN_STORE',))
    enabled = params(market_arbitrage=True, arbitrage_min_roi=0)
    rows = arbitrage_opportunities(current, 20, 10000, enabled)
    wool = next(row for row in rows if row['item'] == 'WOOL')
    future = (current.obs['market']['inventory']['WOOL'] - wool['count']
              - 3 * 13)
    assert sale_value('WOOL', wool['count'], future) > wool['cost']
    assert arbitrage_opportunities(current, 0, 10000, enabled) == []
