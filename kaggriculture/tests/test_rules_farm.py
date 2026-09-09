"""Plant/animal lifecycle, CARE, fertilizer, shed capacity, locked tiles, weeds."""
import pytest
from conftest import act, farm10, fingerprint, private

from agent.economy import CROPS, MAX_YIELD_DAY, WATER_BONUS_FROM
from arena.engine import make_environment, official
from arena.match import observations


def grow(crop, days, water=True, fertilize=()):
    """Daily loop against the official refresh; returns yield_units per day."""
    module = official()
    farm = {'tiles': [[module._new_plant(crop, 0, 24)]], 'farmer': [0, 0], 'hands': []}
    priv = private(inventories=[{'FERTILIZER': 9}])
    history = []
    for day in range(days):
        tile = farm['tiles'][0][0]
        if tile.get('kind') != 'PLANT':
            history.append(None)
            continue
        if day in fertilize:
            module._apply_unit_action(farm, priv, 0, ['FERTILIZE'], 1, day, 24, 100)
        if water:
            module._apply_unit_action(farm, priv, 0, ['WATER'], 1, day, 24, 100)
        history.append(farm['tiles'][0][0]['yield_units'])
        module._daily_refresh_plants(farm, day, 24)
    return history, farm['tiles'][0][0]


def raise_animal(animal, days, feed=True, care=False, harvest=False):
    module = official()
    farm = {'tiles': [[module._new_animal(animal, 0)]], 'farmer': [0, 0], 'hands': []}
    priv = private(inventories=[{'WHEAT': 99}])
    history = []
    for day in range(days):
        tile = farm['tiles'][0][0]
        if 'animal' not in tile:
            history.append(None)
            continue
        ops = ['FEED'] * feed + ['CARE'] * care + ['HARVEST'] * harvest
        for op in ops:
            module._apply_unit_action(farm, priv, 0, [op], 1, day, 24, 100)
        module._daily_refresh_animals(farm, day)
        tile = farm['tiles'][0][0]
        history.append(tile.get('yield_units'))
    return history, farm['tiles'][0][0], priv


def test_plant_and_water_land_on_the_same_tile_within_one_turn():
    """Category 4: one unit plants, a later unit waters -- the tile survives its first night."""
    env = make_environment(5)
    act(env, market=[['BUY_SEED', 'CARROT', 1], ['HIRE']])
    act(env, hands=[['WEST']])
    obs = act(env, farmer=['PLANT', 'CARROT'], hands=[['WATER']])
    tile = obs['farms'][0]['tiles'][4][4]
    assert tile['crop'] == 'CARROT' and tile['watered_today'] is True


def test_water_is_once_per_day_and_planting_day_counts_as_unwatered():
    """Category 5: consecutive_unwatered starts at 1; a second WATER the same day is a no-op."""
    module = official()
    farm = {'tiles': [[module._new_plant('CARROT', 0, 24)]], 'farmer': [0, 0], 'hands': []}
    priv = private()
    assert farm['tiles'][0][0]['consecutive_unwatered'] == 1
    module._apply_unit_action(farm, priv, 0, ['WATER'], 1, 0, 24, 100)
    before = dict(farm['tiles'][0][0])
    module._apply_unit_action(farm, priv, 0, ['WATER'], 1, 0, 24, 100)
    assert farm['tiles'][0][0] == before


def test_plant_survives_one_dry_day_and_weeds_on_the_second():
    """Category 5: two consecutive unwatered days turn a plant into a weed."""
    module = official()
    farm = {'tiles': [[module._new_plant('CARROT', 0, 24)]], 'farmer': [0, 0], 'hands': []}
    module._apply_unit_action(farm, private(), 0, ['WATER'], 1, 0, 24, 100)
    module._daily_refresh_plants(farm, 0, 24)
    assert farm['tiles'][0][0]['consecutive_unwatered'] == 0
    module._daily_refresh_plants(farm, 1, 24)
    assert farm['tiles'][0][0]['kind'] == 'PLANT'
    module._daily_refresh_plants(farm, 2, 24)
    assert farm['tiles'][0][0] == {'kind': 'WEED'}


def test_unwatered_fresh_planting_dies_the_same_night():
    """Category 5: no grace period on the planting day."""
    module = official()
    farm = {'tiles': [[module._new_plant('WHEAT', 3, 24)]]}
    module._daily_refresh_plants(farm, 3, 24)
    assert farm['tiles'][0][0] == {'kind': 'WEED'}


@pytest.mark.parametrize('crop,peak_day,peak_units,ongoing', [
    ('WHEAT', 4, 4, False), ('CARROT', 3, 3, False), ('MELON', 10, 6, False),
    ('TOMATO', 11, 4, True), ('STRAWBERRY', 16, 4, True)])
def test_crop_table_matches_engine_yield_curve(crop, peak_day, peak_units, ongoing):
    """Category 6: agent CROPS (first, peak, quantity, occupancy) reproduce the engine."""
    module = official()
    _, first, peak, quantity, occupancy = CROPS[crop]
    assert (peak, quantity, occupancy) == (peak_day, peak_units, peak_day + 1)
    assert module.CROPS[crop]['first_yield_day'] == first
    assert module.CROPS[crop]['ongoing'] is ongoing
    history, _ = grow(crop, peak_day + 1)
    assert history[peak_day] == peak_units
    assert history[first] >= 1
    # One-time crops carry a guaranteed unit from the moment they are planted.
    assert history[0] == (0 if ongoing else 1)


def test_water_bonus_window_starts_at_half_max_yield_day():
    """Category 6: the bonus window keys off max_yield_day, not the planned harvest day."""
    module = official()
    assert MAX_YIELD_DAY == {c: d['max_yield_day'] for c, d in module.CROPS.items()}
    assert WATER_BONUS_FROM == {c: (d['max_yield_day'] + 1) // 2 for c, d in module.CROPS.items()}
    history, _ = grow('MELON', 8)
    assert history[5] == 1 and history[6] == 2  # first bonus lands at age 6, not 5
    assert WATER_BONUS_FROM['MELON'] == 6


def test_mature_one_time_crop_decays_every_other_step_then_weeds():
    """Category 6: max_lifespan_step = (planted_day + max_yield_day + 1) * turnsPerDay."""
    module = official()
    plant = module._new_plant('CARROT', 0, 24)
    plant['yield_units'] = 3
    assert plant['max_lifespan_step'] == 96
    farm = {'tiles': [[plant]]}
    observed = {}
    for step in range(94, 104):
        module._decay_plants(farm, step)
        tile = farm['tiles'][0][0]
        observed[step] = tile.get('yield_units') if tile.get('kind') == 'PLANT' else tile['kind']
    assert observed == {94: 3, 95: 3, 96: 2, 97: 2, 98: 1, 99: 1,
                        100: 'WEED', 101: 'WEED', 102: 'WEED', 103: 'WEED'}


def test_ongoing_crop_decay_starts_after_its_last_scheduled_production():
    """Category 6: ongoing crops carry max_lifespan_step -1 until the yield cap is reached."""
    history, tile = grow('TOMATO', 12)
    assert history[10] == 3 and history[11] == 4
    assert tile['max_lifespan_step'] == 12 * 24  # decay opens on day 12


def test_harvest_clears_one_time_crops_and_keeps_ongoing_ones():
    """Category 6: HARVEST removes non-ongoing plants; ongoing plants stay and reset to 0."""
    module = official()
    for crop, remains in (('CARROT', False), ('TOMATO', True)):
        plant = module._new_plant(crop, 0, 24)
        plant['yield_units'] = 2
        farm = {'tiles': [[plant]], 'farmer': [0, 0], 'hands': []}
        priv = private()
        module._apply_unit_action(farm, priv, 0, ['HARVEST'], 1, 20, 24, 100)
        assert priv['inventories'][0] == {crop: 2}
        assert (farm['tiles'][0][0] is not None) is remains


def test_harvest_before_first_yield_day_is_refused():
    """Category 6/20: an immature plant yields nothing and is not consumed."""
    module = official()
    plant = module._new_plant('MELON', 0, 24)
    plant['yield_units'] = 3
    farm = {'tiles': [[plant]], 'farmer': [0, 0], 'hands': []}
    priv = private()
    module._apply_unit_action(farm, priv, 0, ['HARVEST'], 1, 5, 24, 100)
    assert priv['inventories'][0] == {} and farm['tiles'][0][0]['yield_units'] == 3


def test_animal_escapes_after_two_unfed_days_leaving_the_structure():
    """Category 5: consecutive_unfed starts at 0, so the placement day may be skipped once."""
    history, tile, _ = raise_animal('GOOSE', 4, feed=False)
    assert history[0] == 0 and history[1] is None
    assert tile == {'kind': 'COOP'}


def test_animal_produces_even_on_unfed_days():
    """Category 5: feeding governs survival, not the base production tick."""
    history, tile, _ = raise_animal('GOOSE', 8, feed=False)
    assert tile == {'kind': 'COOP'}
    fed_alternate = official()
    farm = {'tiles': [[fed_alternate._new_animal('GOOSE', 0)]], 'farmer': [0, 0], 'hands': []}
    priv = private(inventories=[{'WHEAT': 99}])
    produced = []
    for day in range(8):
        if day % 2 == 0:
            fed_alternate._apply_unit_action(farm, priv, 0, ['FEED'], 1, day, 24, 100)
        fed_alternate._daily_refresh_animals(farm, day)
        produced.append(farm['tiles'][0][0]['yield_units'])
    assert produced == [0, 0, 0, 1, 2, 3, 4, 4]  # first yield at end of day 3, capped at max_held


def test_care_banks_a_bonus_paid_on_the_next_fed_production():
    """Category 7: CARE accrues 1 per fed+cared day and is cashed in at the next production."""
    plain, _, _ = raise_animal('SHEEP', 6, harvest=True)
    cared, tile, _ = raise_animal('SHEEP', 6, care=True, harvest=True)
    assert plain[5] == 1  # base production only
    assert cared[5] == 6  # base 1 + 5 banked days, clipped by max_held
    assert tile['pending_care_bonus'] == 1  # day 5 was also a care day


def test_care_bonus_is_dropped_when_the_production_day_is_unfed():
    """Category 7: an unfed production day still yields 1 but wipes the banked bonus."""
    module = official()
    farm = {'tiles': [[module._new_animal('GOOSE', 0)]], 'farmer': [0, 0], 'hands': []}
    priv = private(inventories=[{'WHEAT': 99}])
    for day in (0, 1, 2):
        module._apply_unit_action(farm, priv, 0, ['FEED'], 1, day, 24, 100)
        module._apply_unit_action(farm, priv, 0, ['CARE'], 1, day, 24, 100)
        module._daily_refresh_animals(farm, day)
    assert farm['tiles'][0][0]['pending_care_bonus'] == 3
    module._daily_refresh_animals(farm, 3)  # production day, not fed
    assert farm['tiles'][0][0]['yield_units'] == 1
    assert farm['tiles'][0][0]['pending_care_bonus'] == 0


def test_fertilizer_is_one_per_animal_per_day_and_does_not_stack():
    """Category 8: fertilizer_available is a flag, refreshed nightly for every surviving animal."""
    module = official()
    farm = {'tiles': [[module._new_animal('GOOSE', 0)]], 'farmer': [0, 0], 'hands': []}
    priv = private(inventories=[{'WHEAT': 99}])
    assert farm['tiles'][0][0]['fertilizer_available'] is False
    for day in range(3):
        module._apply_unit_action(farm, priv, 0, ['FEED'], 1, day, 24, 100)
        module._daily_refresh_animals(farm, day)
    assert farm['tiles'][0][0]['fertilizer_available'] is True  # three days, still just one
    module._apply_unit_action(farm, priv, 0, ['COLLECT_FERTILIZER'], 1, 3, 24, 100)
    module._apply_unit_action(farm, priv, 0, ['COLLECT_FERTILIZER'], 1, 3, 24, 100)
    assert priv['inventories'][0]['FERTILIZER'] == 1


def test_fertilize_doubles_the_water_bonus_for_three_days():
    """Category 8: fertilized_until_day = day + 2 (inclusive); the crop cap still applies."""
    module = official()
    plant = module._new_plant('MELON', 0, 24)
    farm = {'tiles': [[plant]], 'farmer': [0, 0], 'hands': []}
    priv = private(inventories=[{'FERTILIZER': 1}])
    module._apply_unit_action(farm, priv, 0, ['FERTILIZE'], 1, 6, 24, 100)
    assert plant['fertilized_until_day'] == 8 and priv['inventories'][0] == {}
    history, _ = grow('MELON', 11, fertilize=(6,))
    assert history[5:10] == [1, 3, 5, 6, 6]  # +2 on days 6-7, then max_yield 6 clips


def test_fertilizer_window_covers_exactly_three_ongoing_productions():
    """Category 8: an ongoing crop yields 2 while fertilized and watered, else 1."""
    module = official()
    plant = module._new_plant('TOMATO', 0, 24)
    farm = {'tiles': [[plant]], 'farmer': [0, 0], 'hands': []}
    priv = private(inventories=[{'FERTILIZER': 1}])
    module._apply_unit_action(farm, priv, 0, ['FERTILIZE'], 1, 7, 24, 100)
    assert plant['fertilized_until_day'] == 9
    produced = []
    for day in range(7, 12):
        module._apply_unit_action(farm, priv, 0, ['WATER'], 1, day, 24, 100)
        module._daily_refresh_plants(farm, day, 24)
        produced.append(plant['yield_units'])
        plant['yield_units'] = 0  # stand in for a same-day HARVEST, which caps at max_yield
    assert produced == [2, 2, 2, 1, 0]  # days 7-9 fertilized, day 10 plain, then capped out


def test_shed_capacity_bounds_every_deposit_path():
    """Category 9: DROP and the nightly drop discard overflow; PLACE leaves it in inventory."""
    module = official()
    farm = farm10()
    priv = private(shed={'WHEAT': 98}, inventories=[{'CARROT': 10}])
    module._apply_unit_action(farm, priv, 0, ['DROP'], 10, 0, 24, 100)
    assert priv['shed'] == {'WHEAT': 98, 'CARROT': 2} and priv['inventories'] == [{}]

    priv = private(shed={'WHEAT': 98}, inventories=[{'CARROT': 10}])
    module._apply_unit_action(farm, priv, 0, ['PLACE', 'CARROT', 10], 10, 0, 24, 100)
    assert priv['shed'] == {'WHEAT': 98, 'CARROT': 2} and priv['inventories'] == [{'CARROT': 8}]

    priv = private(shed={'WHEAT': 95}, inventories=[{'CARROT': 10}, {'MELON': 7}])
    module._drop_inventories_to_shed(priv, 100)
    assert priv['shed'] == {'WHEAT': 95, 'CARROT': 5} and priv['inventories'] == [{}, {}]


def test_market_purchases_are_refused_once_the_shed_is_full():
    """Category 9: BUY_PRODUCT/BUY_ANIMAL check the shed before spending."""
    module = official()
    market = {'inventory': {i: 10000 for i in module.PRODUCTS}, 'prices': {}}
    farm, priv = {'money': 10000.}, private(shed={'WHEAT': 100})
    assert module._commit_unit('BUY_PRODUCT', 'WHEAT', 25, farm, priv, market, 100) is False
    assert module._commit_unit('BUY_ANIMAL', 'GOOSE', 300, farm, priv, market, 100) is False
    assert farm['money'] == 10000.
    priv['shed']['WHEAT'] = 99
    assert module._commit_unit('BUY_PRODUCT', 'WHEAT', 25, farm, priv, market, 100) is True


def test_locked_tiles_are_passable_but_reject_tile_actions():
    """Category 16: movement ignores LOCKED; PLANT/BUILD/DIG no-op there."""
    module = official()
    farm = farm10()
    farm['farmer'] = [5, 4]  # NE shed-access tile, locked at game start
    priv = private(seeds={'CARROT': 1})
    assert farm['tiles'][4][5] == 'LOCKED'
    for action in (['PLANT', 'CARROT'], ['BUILD_COOP'], ['BUILD_PASTURE'], ['DIG'], ['WATER']):
        module._apply_unit_action(farm, priv, 0, action, 10, 0, 24, 100)
    assert farm['tiles'][4][5] == 'LOCKED' and priv['seeds']['CARROT'] == 1
    module._apply_unit_action(farm, priv, 0, ['WEST'], 10, 0, 24, 100)
    assert farm['farmer'] == [4, 4]


def test_shed_actions_work_from_a_locked_access_tile():
    """Category 16: PICKUP/DROP/PLACE only need shed adjacency, not tile ownership."""
    module = official()
    farm = farm10()
    farm['farmer'] = [5, 5]  # SE shed-access tile, locked
    priv = private(shed={'WHEAT': 5})
    module._apply_unit_action(farm, priv, 0, ['PICKUP', 'WHEAT', 3], 10, 0, 24, 100)
    assert priv['inventories'][0] == {'WHEAT': 3} and priv['shed']['WHEAT'] == 2
    module._apply_unit_action(farm, priv, 0, ['DROP'], 10, 0, 24, 100)
    assert priv['shed']['WHEAT'] == 5 and farm['tiles'][5][5] == 'LOCKED'


def test_buy_land_unlocks_quadrants_in_ne_sw_se_order():
    """Category 16/17: LAND_ORDER is fixed and only empty LOCKED tiles are converted."""
    module = official()
    assert module.LAND_ORDER == ['NE', 'SW', 'SE']
    farm = farm10(money=10000.)
    for expected, cost in zip(module.LAND_ORDER, module.LAND_PRICES):
        money = farm['money']
        module._do_buy_land(farm, 10)
        assert farm['unlocked_quadrants'][-1] == expected
        assert money - farm['money'] == cost
    assert not any(tile == 'LOCKED' for row in farm['tiles'] for tile in row)
    money = farm['money']
    module._do_buy_land(farm, 10)  # no fifth quadrant exists
    assert farm['money'] == money and len(farm['unlocked_quadrants']) == 4


def test_weeds_persist_until_dug_and_can_respawn():
    """Category 18: nothing clears a weed except DIG; a cleared tile is eligible again."""
    module = official()
    farm = farm10()
    farm['tiles'][4][4] = {'kind': 'WEED'}
    priv = private()
    for day in range(5):
        module._daily_refresh_plants(farm, day, 24)
        module._daily_refresh_animals(farm, day)
        module._decay_plants(farm, day * 24)
    assert farm['tiles'][4][4] == {'kind': 'WEED'}
    module._apply_unit_action(farm, priv, 0, ['PLANT', 'CARROT'], 10, 0, 24, 100)
    assert farm['tiles'][4][4] == {'kind': 'WEED'}  # occupied tiles reject PLANT
    module._apply_unit_action(farm, priv, 0, ['DIG'], 10, 0, 24, 100)
    assert farm['tiles'][4][4] is None
    module._spawn_weeds(farm, 10, 1.0, __import__('random').Random(0))
    assert farm['tiles'][4][4] == {'kind': 'WEED'}


def test_weeds_only_spawn_on_empty_unlocked_tiles():
    """Category 18: LOCKED tiles and occupied tiles are never overwritten."""
    module = official()
    farm = farm10()
    farm['tiles'][0][0] = {'kind': 'COOP'}
    module._spawn_weeds(farm, 10, 1.0, __import__('random').Random(0))
    assert farm['tiles'][0][0] == {'kind': 'COOP'}
    assert farm['tiles'][4][5] == 'LOCKED'
    assert farm['tiles'][1][1] == {'kind': 'WEED'}


def test_dig_removes_plants_weeds_and_empty_structures_but_not_animals():
    """Category 18: an occupied coop/pasture cannot be dug."""
    module = official()
    farm, priv = farm10(), private()
    cases = [({'kind': 'WEED'}, None), (module._new_plant('WHEAT', 0, 24), None),
             ({'kind': 'COOP'}, None), (module._new_animal('GOOSE', 0), 'keep')]
    for tile, expected in cases:
        farm['tiles'][4][4] = tile
        module._apply_unit_action(farm, priv, 0, ['DIG'], 10, 0, 24, 100)
        assert farm['tiles'][4][4] == (tile if expected == 'keep' else None)
    farm['tiles'][4][4] = None
    snapshot = fingerprint(farm, priv)
    module._apply_unit_action(farm, priv, 0, ['DIG'], 10, 0, 24, 100)
    assert fingerprint(farm, priv) == snapshot


def test_weed_spawn_is_seeded_and_reproducible():
    """Category 18: weeds derive from env.info['seed'] and the day, so replays match."""
    a = make_environment(1234)
    b = make_environment(1234)
    for _ in range(48):
        act(a)
        act(b)
    assert observations(a.state)[0]['farms'] == observations(b.state)[0]['farms']
