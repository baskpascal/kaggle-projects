"""The four detector errors that produced wrong conclusions, as tests.

Each one asserts the property the broken detector violated: a threshold must express the
decision and not the size of the farm taking it; the state read must be the one before the
order resolved; and a window must be longer than the effect it claims to have seen.
"""
import json

import pytest

from experiments.elite_events import strategic_events
from experiments.expansion_telemetry import TURNS, attribute, fill_times, trace
from experiments.land_diagnostics import land_report

CONFIG = {'turnsPerDay': 24}
CROPS = ('WHEAT', 'CARROT', 'TOMATO', 'STRAWBERRY', 'MELON')
GOODS = CROPS + ('MILK', 'WOOL', 'EGG', 'FERTILIZER', 'COW', 'SHEEP', 'GOOSE')


def observation(day=0, hour=0, *, tiles=None, money=3000., seeds=None, locked=0):
    grid = tiles if tiles is not None else [[None, None], [None, None]]
    flat = [tile for row in grid for tile in row]
    assert len(flat) >= locked
    farm = {'farmer': [0, 0], 'hands': [], 'hires_today': 0, 'money': money,
            'tiles': grid, 'unlocked_quadrants': [0]}
    other = dict(farm, money=100., tiles=[[None, None], [None, None]])
    return {'day': day, 'hour': hour, 'player': 0, 'farms': [farm, other],
            'town': {'unlocked_shops': ['YARN_STORE']},
            'market': {'prices': {g: 10. for g in GOODS},
                       'inventory': {g: 100 for g in GOODS}},
            'private': {'inventories': [{}],
                        'seeds': seeds if seeds is not None else {c: 0 for c in CROPS},
                        'shed': {g: 0 for g in GOODS}}}


def frame(obs, action):
    return [{'observation': obs, 'action': action}, {'observation': obs}]


def test_terminal_sale_detection_does_not_depend_on_farm_size():
    """A small farm that clears its whole shed has liquidated just as much as a big one."""
    small = frame(observation(day=29, hour=23),
                  {'farmer': ['PASS'], 'hands': [], 'market': [['SELL', 'WHEAT', 3]]})
    events = strategic_events([small] * 1, 0, CONFIG, large_sale_units=1)
    kinds = {kind for kind, _, _ in events}
    assert kinds, 'a shed of three units cleared at the end is still a liquidation'


def test_hire_burst_threshold_is_documented_as_scale_not_onset():
    """Three hires in a day is a crew size. Onset is the first hire, and they differ."""
    day = [frame(observation(hour=h),
                 {'farmer': ['PASS'], 'hands': [], 'market': [['HIRE']]})
           for h in range(4)]
    events = strategic_events(day, 0, CONFIG, hire_burst=3)
    bursts = [state['turn'] for kind, _, state in events if kind == 'HIRE_BURST']
    assert bursts == [2], 'the burst fires on the third hire, two turns after onset'


def test_land_report_reads_the_state_before_the_order_resolved():
    """`turns[0]` is step 1, so the frame carrying BUY_LAND already holds the new land."""
    before = observation(hour=0, money=1500.,
                         tiles=[[None, 'LOCKED'], ['LOCKED', 'LOCKED']])
    after = observation(hour=1, money=500., tiles=[[None, None], [None, None]])
    steps = [frame(before, {'market': []}),
             frame(after, {'market': [['BUY_LAND']]}),
             *[frame(after, {'market': []}) for _ in range(30)]]
    report = land_report(steps, 0, CONFIG)
    assert report['bought'] and report['turn_land1'] == 1
    assert report['cash_before_land1'] == 1500., 'read the pre-decision frame, not the next'
    assert report['owned_after'] > report['owned_before']


def test_fill_time_window_reports_none_rather_than_a_false_never():
    """A target never reached inside the trace is unknown, not zero and not 'never'."""
    occupancy = [(1, 4)] * 10 + [(2, 4)] * 10
    times = fill_times(occupancy, 0)
    assert times['turns_to_50_percent'] == 10
    assert times['turns_to_100_percent'] is None


def test_trace_refuses_a_horizon_shorter_than_the_game():
    """A 200-turn window inside a 719-turn game is how 'slow' was read as 'never'."""
    short = [frame(observation(), {'farmer': ['PASS'], 'hands': [], 'market': []})
             for _ in range(200)]
    with pytest.raises(ValueError, match='shorter than the effect'):
        trace(short, 0, CONFIG)


def test_attribution_names_the_first_binding_constraint():
    idle = {'farmer': ['PASS'], 'hands': [], 'market': []}
    late = observation(hour=20)
    assert attribute(late, idle, CONFIG, 0) == 'OUTSIDE_PLANTING_HOURS'
    broke = observation(hour=2, money=0.)
    assert attribute(broke, idle, CONFIG, 0, cheapest_seed=5) == 'NO_CASH_FOR_SEED'
    stocked = observation(hour=2, seeds={**{c: 0 for c in CROPS}, 'WHEAT': 4})
    assert attribute(stocked, idle, CONFIG, 0) == 'NOT_SELECTED_BY_PLANNER'
    busy = {'farmer': ['WATER'], 'hands': [['WATER']], 'market': []}
    assert attribute(stocked, busy, CONFIG, 0) == 'NO_IDLE_UNIT'
    full = observation(hour=2, tiles=[[{'crop': 'WHEAT'}]])
    assert attribute(full, idle, CONFIG, 0) is None


def test_milestones_come_from_executed_state_not_from_requested_orders():
    """A refused BUY_LAND looks identical to a bought one in the action stream.

    Counting requests made a team that attempts expansion repeatedly and succeeds twice
    look adaptive, and put its first expansion at turn 2 instead of 151.
    """
    from experiments.elite_events import executed_milestones

    def farm(quadrants, tiles):
        return {'farmer': [0, 0], 'hands': [], 'hires_today': 0, 'money': 10.,
                'tiles': tiles, 'unlocked_quadrants': list(range(quadrants))}

    empty = [[None, None], [None, None]]
    request = {'farmer': ['PASS'], 'hands': [], 'market': [['BUY_LAND']]}
    steps = []
    for turn in range(6):
        # The order is issued on every turn; the engine only grants it at turn 4.
        quadrants = 2 if turn >= 4 else 1
        observation = {'day': 0, 'hour': turn, 'player': 0,
                       'farms': [farm(quadrants, empty), farm(1, empty)],
                       'town': {'unlocked_shops': []},
                       'market': {'prices': {}, 'inventory': {}},
                       'private': {'inventories': [{}], 'seeds': {}, 'shed': {}}}
        steps.append([{'observation': observation, 'action': request},
                      {'observation': observation}])
    assert executed_milestones(steps, 0) == {'land1': 4}


def test_first_animal_milestone_ignores_refused_purchases():
    from experiments.elite_events import executed_milestones

    def observation(cows):
        tiles = [[{'animal': 'COW'} if cows else None, None], [None, None]]
        farm = {'farmer': [0, 0], 'hands': [], 'hires_today': 0, 'money': 0.,
                'tiles': tiles, 'unlocked_quadrants': [0]}
        return {'day': 0, 'hour': 0, 'player': 0, 'farms': [farm, farm],
                'town': {'unlocked_shops': []},
                'market': {'prices': {}, 'inventory': {}},
                'private': {'inventories': [{}], 'seeds': {}, 'shed': {}}}

    order = {'farmer': ['PASS'], 'hands': [], 'market': [['BUY_ANIMAL', 'COW', 1]]}
    steps = [[{'observation': observation(0), 'action': order},
              {'observation': observation(0)}] for _ in range(3)]
    steps.append([{'observation': observation(1), 'action': order},
                  {'observation': observation(1)}])
    assert executed_milestones(steps, 0) == {'first_COW': 3}
