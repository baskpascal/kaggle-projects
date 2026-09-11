from agent.params import DEFAULTS
from types import SimpleNamespace

from agent.planner import (_committed_action, _reconcile_schedule,
                           _scheduled_hire_reserve, _trim_schedule,
                           phase_target, scheduled_phase_value)
from experiments.compiled_schedule_gate import structural_execution, summarize_replay


def farm(*, quadrants=1, crops=0, pasture=0, hands=0):
    tiles = ([{'kind': 'PLANT', 'crop': 'WHEAT'}] * crops
             + [{'kind': 'PASTURE'}] * pasture)
    tiles += [None] * (100 - len(tiles))
    return {'tiles': [tiles[i:i + 10] for i in range(0, 100, 10)],
            'unlocked_quadrants': list(range(quadrants)),
            'farmer': [0, 0], 'hands': [[0, 0]] * hands, 'money': 1000}


def turn(step, **state):
    own = farm(**state)
    observation = {'day': step // 24, 'hour': step % 24,
                   'private': {'seeds': {'WHEAT': 0}},
                   'farms': [own, farm()]}
    return {'step': step,
            # Requests intentionally claim successful work; gates must ignore them.
            'actions': [{'farmer': ['PLANT', 'WHEAT'],
                         'market': [['BUY_LAND']]}, {}],
            'observations': [observation, observation]}


def test_compiled_schedule_is_off_by_default():
    assert DEFAULTS['compiled_schedule'] is False
    assert DEFAULTS['schedule_hires_ignore_land_reserve'] is False
    assert DEFAULTS['schedule_animal_deferral_horizon'] == 240
    assert DEFAULTS['schedule_persistent_assignments'] is False


def test_hire_land_reserve_ablation_is_explicit_and_off_by_default():
    baseline = dict(DEFAULTS)
    assert _scheduled_hire_reserve(True, baseline, None) is None
    assert _scheduled_hire_reserve(True, baseline, 500) == 500
    changed = {**baseline, 'schedule_hires_ignore_land_reserve': True}
    assert _scheduled_hire_reserve(True, changed, None) == 0
    assert _scheduled_hire_reserve(False, changed, 500) == 500


def test_phase_targets_commit_one_day_ahead_and_never_exceed_target():
    assert phase_target(0, 240, 42, 24) == 5
    assert phase_target(216, 240, 42, 24) == 42
    assert phase_target(480, 240, 42, 24) == 42
    assert scheduled_phase_value(215, ((0, 6), (216, 12))) == 6
    assert scheduled_phase_value(216, ((0, 6), (216, 12))) == 12


def test_gate_uses_executed_state_not_requested_actions():
    replay = {'turns': [turn(step, quadrants=(3 if step == 480 else 1),
                             crops=(40 if step == 480 else 0),
                             pasture=(12 if step == 480 else 0), hands=10)
                        for step in range(481)]}
    result = summarize_replay(replay, 0)
    assert not result['gates']['forty_crops_t288']
    assert not result['gates']['pasture_t288']
    assert not result['structural_pass']
    assert result['requested_unit_actions_before_t288']['PLANT'] == 288
    assert result['structural_execution']['failure_count'] > 0


def test_daily_crew_gate_cannot_be_satisfied_by_one_peak_per_phase():
    replay = {'turns': [turn(step, quadrants=3, crops=40, pasture=12,
                             hands=(10 if step in (0, 96, 192) else 0))
                        for step in range(481)]}
    result = summarize_replay(replay, 0)
    assert result['gates']['crew_each_phase']
    assert not result['gates']['crew_each_day_before_t288']
    assert not result['gates']['crew_sustained_before_t288']


def test_structural_execution_requires_the_following_observed_transition():
    before = turn(0)
    after = turn(1, crops=1)
    after['actions'][0]['farmer'] = ['PLANT', 'WHEAT']
    assert structural_execution({'turns': [before, after]}, 0)['operations']['PLANT'] == {
        'requested': 1, 'executed': 1, 'failed': 0,
    }


def test_structural_execution_does_not_double_count_one_tile_transition():
    before = turn(0, hands=1)
    after = turn(1, crops=1, hands=1)
    after['actions'][0]['hands'] = [['PLANT', 'WHEAT']]
    measured = structural_execution({'turns': [before, after]}, 0)
    assert measured['operations']['PLANT'] == {
        'requested': 2, 'executed': 1, 'failed': 1,
    }


def test_persistent_schedule_reconciles_workers_duplicates_and_real_tiles():
    own = farm(hands=1)
    own['farmer'] = [0, 0]
    own['hands'] = [[2, 0]]
    own['tiles'][0][1] = {'kind': 'WEED'}
    state = SimpleNamespace(positions=[[0, 0], [2, 0]], me=own)
    plant = {'target': (1, 0), 'action': ['PLANT', 'WHEAT'], 'assigned_step': 4}
    vanished = {'target': (3, 0), 'action': ['BUILD_PASTURE'], 'assigned_step': 4}
    live = {
        'assignments': {0: plant, 1: dict(plant), 2: vanished},
        'released_workers': 0,
        'completed': {'PLANT': 0, 'BUILD_PASTURE': 0},
        'invalidated': 0,
        'duplicate_targets': 0,
    }
    _reconcile_schedule(live, state)
    assert set(live['assignments']) == {0}
    assert live['duplicate_targets'] == 1
    assert live['released_workers'] == 1
    assert _committed_action(state, 0, plant, {'WHEAT': 1}) == ['EAST']
    state.positions[0] = [1, 0]
    assert _committed_action(state, 0, plant, {'WHEAT': 0}) == ['DIG']
    own['tiles'][0][1] = None
    assert _committed_action(state, 0, plant, {'WHEAT': 0}) == ['PASS']


def test_persistent_schedule_trims_excess_deterministically():
    state = SimpleNamespace(positions=[[0, 0], [9, 9]], me=farm(hands=1))
    live = {'assignments': {
        0: {'target': (5, 0), 'action': ['PLANT', 'WHEAT'], 'assigned_step': 2},
        1: {'target': (8, 9), 'action': ['PLANT', 'WHEAT'], 'assigned_step': 3},
    }}
    _trim_schedule(live, state, crop_need=1, pasture_need=0)
    assert set(live['assignments']) == {1}


def test_step_zero_snapshot_survives_replay_reshape():
    from experiments.planner_milestones import replay_frames
    frames = replay_frames({'turns': [{
        'step': 0, 'actions': [],
        'observations': [{'farms': [farm(), farm()]},
                         {'farms': [farm(), farm()]}],
    }]})
    assert len(frames) == 1 and len(frames[0]) == 2
    assert frames[0][0]['action'] is None
