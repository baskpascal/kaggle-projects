from agent.params import DEFAULTS
from agent.planner import phase_target, scheduled_phase_value
from experiments.compiled_schedule_gate import summarize_replay


def farm(*, quadrants=1, crops=0, pasture=0, hands=0):
    tiles = ([{'kind': 'PLANT', 'crop': 'WHEAT'}] * crops
             + [{'kind': 'PASTURE'}] * pasture)
    tiles += [None] * (100 - len(tiles))
    return {'tiles': [tiles[i:i + 10] for i in range(0, 100, 10)],
            'unlocked_quadrants': list(range(quadrants)),
            'hands': [[0, 0]] * hands, 'money': 1000}


def turn(step, **state):
    own = farm(**state)
    return {'step': step,
            # Requests intentionally claim successful work; gates must ignore them.
            'actions': [{'farmer': ['PLANT', 'WHEAT'],
                         'market': [['BUY_LAND']]}, {}],
            'observations': [{'farms': [own, farm()]}, {'farms': [own, farm()]}]}


def test_compiled_schedule_is_off_by_default():
    assert DEFAULTS['compiled_schedule'] is False


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


def test_step_zero_snapshot_survives_replay_reshape():
    from experiments.planner_milestones import replay_frames
    frames = replay_frames({'turns': [{
        'step': 0, 'actions': [],
        'observations': [{'farms': [farm(), farm()]},
                         {'farms': [farm(), farm()]}],
    }]})
    assert len(frames) == 1 and len(frames[0]) == 2
    assert frames[0][0]['action'] is None
