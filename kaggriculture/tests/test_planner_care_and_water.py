"""The two ENGINE_FINDINGS opportunities that were still open after #64.

O3 -- CARE was priced at a flat 45 for every animal, which is right only for GOOSE
(interval 1, cheap product) and badly underprices COW and SHEEP, whose banked bonus
is one unit of a far more valuable product.
O4 -- watering was switched off for the whole final day, although WATER inside the
bonus window raises `yield_units` immediately and can still be harvested.

Both are asserted through `agent.planner.policy` against a real engine state, not
against the formulas, so a later refactor that keeps the numbers but loses the
behaviour still fails here.
"""
import pytest

from agent.economy import (ANIMAL_MAX_HELD, ANIMALS, CROPS, WATER_BONUS_FROM,
                           care_priority, next_production_day)
from agent.planner import policy
from arena.engine import make_environment, official
from arena.match import observations

IDLE = {'farmer': ['PASS'], 'hands': [], 'market': []}


def env_at(seed, steps):
    env = make_environment(seed)
    for _ in range(steps):
        if env.done:
            break
        env.step([policy(observations(env.state)[0], dict(env.configuration)), IDLE])
    return env


def place(env, tile):
    """Put the tile under the farmer, so the chosen job is acted on rather than walked to."""
    farm = env.state[0]['observation']['farms'][0]
    x, y = farm['farmer']
    farm['tiles'][y][x] = tile
    return observations(env.state)[0], dict(env.configuration)


def ops_on(action):
    """The verbs the units actually perform this turn."""
    return [unit[0] for unit in (action['farmer'], *action['hands']) if unit]


def animal(name, *, placed_day, yield_units=0, banked=0):
    return {'kind': ANIMALS[name][1], 'animal': name, 'placed_day': placed_day,
            'fed_today': True, 'cared_today': False, 'consecutive_unfed': 0,
            'yield_units': yield_units, 'pending_care_bonus': banked,
            'fertilizer_available': False}


def plant(crop, *, planted_day, yield_units=1, watered=False):
    return {'kind': 'PLANT', 'crop': crop, 'planted_day': planted_day,
            'watered_today': watered, 'consecutive_unwatered': 0,
            'yield_units': yield_units}


# --- O3: CARE is priced by what it banks ----------------------------------

def test_animal_max_held_matches_the_engine():
    module = official()
    assert ANIMAL_MAX_HELD == {name: a['max_held'] for name, a in module.ANIMALS.items()}


def test_next_production_day_lands_on_the_engine_yield_days():
    module = official()
    for name, a in module.ANIMALS.items():
        tile = {'animal': name, 'placed_day': 3}
        produces = {day for day in range(3, 60)
                    if day - 3 - a['first_yield_day'] >= 0
                    and (day - 3 - a['first_yield_day']) % a['interval'] == 0}
        for today in range(3, 40):
            nxt = next_production_day(tile, today)
            assert nxt > today and nxt in produces
            assert not [d for d in produces if today < d < nxt]


def test_care_is_priced_by_the_product_it_banks():
    """The whole point of O3: a wool unit and an egg unit stop being worth the same."""
    prices = {'EGG': 20., 'MILK': 60., 'WOOL': 180.}
    values = {}
    for name in ANIMALS:
        tile = {'animal': name, 'placed_day': 0, 'yield_units': 0,
                'pending_care_bonus': 0}
        values[name] = care_priority(tile, prices, day=20, days_left=8)
    assert values['SHEEP'] > values['COW'] > values['GOOSE'] > 45
    assert values['SHEEP'] == 45 + prices['WOOL'] * .3


def test_care_is_worth_nothing_when_max_held_leaves_no_headroom():
    prices = {'WOOL': 180.}
    full = {'animal': 'SHEEP', 'placed_day': 0, 'pending_care_bonus': 0,
            'yield_units': ANIMAL_MAX_HELD['SHEEP']}
    assert care_priority(full, prices, day=20, days_left=8) is None
    nearly = {'animal': 'SHEEP', 'placed_day': 0, 'pending_care_bonus': 1,
              'yield_units': ANIMAL_MAX_HELD['SHEEP'] - 1}
    assert care_priority(nearly, prices, day=20, days_left=8) is None
    room = {'animal': 'SHEEP', 'placed_day': 0, 'pending_care_bonus': 1,
            'yield_units': ANIMAL_MAX_HELD['SHEEP'] - 2}
    assert care_priority(room, prices, day=20, days_left=8) is not None


def test_care_is_worth_nothing_once_no_production_day_remains():
    """The bank is only cashed on a production day; past the last one the turn is dead."""
    prices = {'MILK': 60.}
    # COW first yields eight days after placement and then every second day.
    tile = {'animal': 'COW', 'placed_day': 0, 'yield_units': 0, 'pending_care_bonus': 0}
    assert care_priority(tile, prices, day=20, days_left=3) is not None
    assert care_priority(tile, prices, day=20, days_left=1) is None


def test_the_planner_emits_care_when_the_animal_is_the_only_job():
    """The priority is not enough on its own: the planner has to act on it."""
    env = env_at(5, 40)
    day = env.state[0]['observation']['day']
    obs, cfg = place(env, animal('SHEEP', placed_day=max(0, day - 8)))
    quiet = {**cfg}
    emitted = set()
    for _ in range(6):
        action = policy(obs, quiet)
        emitted.update(ops_on(action))
        env.step([action, IDLE])
        if env.done:
            break
        obs = observations(env.state)[0]
        farm = env.state[0]['observation']['farms'][0]
        x, y = farm['farmer']
        farm['tiles'][y][x] = animal('SHEEP', placed_day=max(0, day - 8))
        obs = observations(env.state)[0]
    assert 'CARE' in emitted


# --- O4: the last day still waters ----------------------------------------

def test_the_final_day_waters_inside_the_bonus_window():
    env = make_environment(11)
    cfg = dict(env.configuration)
    turns_per_day = cfg.get('turnsPerDay', 24)
    total = cfg.get('episodeSteps', 720) - 1
    # Park the clock on the last day with several turns still to play.
    obs_state = env.state[0]['observation']
    obs_state['step'] = total - 4
    obs_state['day'] = (total - 4) // turns_per_day
    obs_state['hour'] = (total - 4) % turns_per_day
    day = obs_state['day']
    crop = 'MELON'
    age = max(WATER_BONUS_FROM[crop], CROPS[crop][1])
    obs, cfg = place(env, plant(crop, planted_day=day - age))
    assert 'WATER' in ops_on(policy(obs, cfg)) or 'HARVEST' in ops_on(policy(obs, cfg))


def test_the_very_last_turn_does_not_water():
    """With no turn left to harvest what the water adds, the turn is wasted."""
    env = make_environment(11)
    cfg = dict(env.configuration)
    turns_per_day = cfg.get('turnsPerDay', 24)
    total = cfg.get('episodeSteps', 720) - 1
    obs_state = env.state[0]['observation']
    obs_state['step'] = total
    obs_state['day'] = total // turns_per_day
    obs_state['hour'] = total % turns_per_day
    day = obs_state['day']
    crop = 'MELON'
    age = max(WATER_BONUS_FROM[crop], CROPS[crop][1])
    obs, cfg = place(env, plant(crop, planted_day=day - age))
    assert 'WATER' not in ops_on(policy(obs, cfg))
