"""Planned-planting feedback (issue #7) and economic FERTILIZE (issue #6)."""
from types import SimpleNamespace

from agent.economy import crop_context, fertilizer_units, score_crops
from agent.params import DEFAULTS
from agent.planner import policy
from arena.engine import make_environment
from arena.match import observations

IDLE = {'farmer': ['PASS'], 'hands': [], 'market': []}


def episode(seed, parameters, collect):
    env = make_environment(seed)
    seen = []
    while not env.done:
        action = policy(observations(env.state)[0], dict(env.configuration), parameters)
        seen.extend(collect(action))
        env.step([action, IDLE])
    return seen


def unit_ops(action):
    return [unit[0] for unit in (action['farmer'], *action['hands'])]


def market_ops(action):
    return [tuple(order[:2]) for order in action['market']]


# --- issue #7 -------------------------------------------------------------

def test_planned_feedback_defaults_off_and_is_inert():
    assert DEFAULTS['planned_feedback'] is False
    env = make_environment(11)
    while not env.done:
        obs, cfg = observations(env.state)[0], dict(env.configuration)
        action = policy(obs, cfg)
        assert action == policy(obs, cfg, {'planned_feedback': False})
        env.step([action, IDLE])


def test_score_degrades_as_plantings_commit():
    """The whole point of `planned`: the Nth planting must not be priced as the first."""
    env = make_environment(11)
    state_obs, cfg = observations(env.state)[0], dict(env.configuration)
    from agent.state import State
    context = crop_context(State(state_obs, cfg), DEFAULTS)
    first = score_crops(context, DEFAULTS, {})
    crop = max(first, key=first.get)
    previous = first[crop]
    for count in (1, 4, 12):
        current = score_crops(context, DEFAULTS, {crop: count})[crop]
        assert current <= previous
        previous = current
    assert score_crops(context, DEFAULTS, {crop: 12})[crop] < first[crop]


def test_planned_feedback_still_allows_monoculture():
    """Correcting the marginal forecast must not force artificial variety."""
    planted = [op for op in episode(11, {'planned_feedback': True}, unit_ops) if op == 'PLANT']
    assert planted, 'the agent should still be planting'


# --- issue #6 -------------------------------------------------------------

def test_fertilizer_units_one_time_crop():
    """Wheat waters +1/day over ages 2-4 and +2/day fertilized, capped at 6."""
    state = SimpleNamespace(day=2, days_left=20.0)
    wheat = {'crop': 'WHEAT', 'planted_day': 0, 'yield_units': 1, 'fertilized_until_day': -1}
    assert fertilizer_units(state, wheat) == 2


def test_fertilizer_units_zero_when_already_covered():
    state = SimpleNamespace(day=2, days_left=20.0)
    wheat = {'crop': 'WHEAT', 'planted_day': 0, 'yield_units': 1, 'fertilized_until_day': 4}
    assert fertilizer_units(state, wheat) == 0


def test_fertilizer_units_counts_only_days_inside_the_window():
    """Fertilizing on planting day reaches only age 2, the first bonus day of three."""
    state = SimpleNamespace(day=0, days_left=20.0)
    wheat = {'crop': 'WHEAT', 'planted_day': 0, 'yield_units': 1, 'fertilized_until_day': -1}
    assert fertilizer_units(state, wheat) == 1


def test_fertilizer_units_zero_before_the_window_opens():
    """Melon's bonus window starts at age 6, so coverage of ages 0-2 buys nothing."""
    state = SimpleNamespace(day=0, days_left=20.0)
    melon = {'crop': 'MELON', 'planted_day': 0, 'yield_units': 1, 'fertilized_until_day': -1}
    assert fertilizer_units(state, melon) == 0


def test_fertilize_is_on_by_default_and_paid_for():
    assert DEFAULTS['fertilize'] is True
    assert 'FERTILIZE' in episode(11, None, unit_ops)
    assert ('BUY_PRODUCT', 'FERTILIZER') in episode(11, None, market_ops)


def test_fertilize_can_be_ablated_off():
    assert 'FERTILIZE' not in episode(11, {'fertilize': False}, unit_ops)
