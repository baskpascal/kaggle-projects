"""A splice tool is only useful if its distance means something."""
import json

import pytest

from arena.match import run_match
from experiments.tape_splice import (BLOCK, boundaries, fingerprint, join_distance,
                                     splice, states_at)


def observation(money=1000.0, farmer=(4, 4), hands=2, quadrants=1,
                shed=None, seeds=None, carried=None, crop='WHEAT', planted=3):
    tiles = [[None] * 10 for _ in range(10)]
    for i in range(planted):
        tiles[0][i] = {'kind': 'PLANT', 'crop': crop}
    return {
        'farms': [{'money': money, 'farmer': list(farmer),
                   'hands': [[1, 1]] * hands, 'tiles': tiles,
                   'unlocked_quadrants': list(range(quadrants)), 'hires_today': 0}],
        'private': {'shed': shed or {'WHEAT': 10}, 'seeds': seeds or {'WHEAT': 2},
                    'inventories': [carried or {}]},
    }


def test_boundaries_follow_the_block_schedule():
    cuts = boundaries()
    assert cuts[0] == BLOCK and cuts[-1] < 719
    assert all(cut % BLOCK == 0 for cut in cuts)
    assert boundaries(block=144)[:2] == [144, 288]


def test_fingerprint_records_what_a_following_block_depends_on():
    state = fingerprint(observation(), 0)
    assert state['money'] == 1000.0 and state['quadrants'] == 1
    assert len(state['hands']) == 2 and state['farmer'] == [4, 4]
    assert state['tiles'] == {'PLANT:WHEAT': 3}
    assert state['shed'] == {'WHEAT': 10}


def test_an_identical_world_joins_at_zero_distance():
    left = fingerprint(observation(), 0)
    right = fingerprint(observation(), 0)
    assert join_distance(left, right)['distance'] == 0.0


@pytest.mark.parametrize('change,term', [
    ({'hands': 8}, 'hands'),
    ({'quadrants': 4}, 'quadrants'),
    ({'money': 90000.0}, 'money'),
    ({'farmer': (0, 9)}, 'farmer'),
    ({'crop': 'MELON'}, 'tiles'),
    ({'shed': {'WOOL': 40}}, 'shed'),
    ({'seeds': {'MELON': 9}}, 'seeds'),
    ({'carried': {'MILK': 5}}, 'carried'),
])
def test_each_term_moves_only_on_its_own_difference(change, term):
    left = fingerprint(observation(), 0)
    right = fingerprint(observation(**change), 0)
    parts = join_distance(left, right)['parts']
    assert parts[term] > 0, f'{term} should register the change'
    assert [k for k, v in parts.items() if v > 0] == [term], 'no other term should move'


def test_an_unrecoverable_gap_outweighs_a_recoverable_one():
    """A block cannot hire eight hands or unlock a quadrant inside itself; it can
    earn cash. The weighting has to say so, or a search will chase the wrong joins."""
    base = fingerprint(observation(), 0)
    poorer = join_distance(base, fingerprint(observation(money=1.0), 0))['distance']
    handless = join_distance(base, fingerprint(observation(hands=0), 0))['distance']
    locked = join_distance(base, fingerprint(observation(quadrants=4), 0))['distance']
    assert handless > poorer and locked > poorer


def test_splice_plays_the_prefix_then_the_suffix():
    left = [{'farmer': ['NORTH'], 'i': i} for i in range(719)]
    right = [{'farmer': ['SOUTH'], 'i': i} for i in range(719)]
    joined = splice(left, right, 144)
    assert len(joined) == 719
    assert joined[143]['farmer'] == ['NORTH'] and joined[144]['farmer'] == ['SOUTH']
    assert [a['i'] for a in joined] == list(range(719)), 'turn indices stay aligned'


def test_splice_refuses_a_cut_it_cannot_honour():
    tape = [{'farmer': ['PASS']}] * 719
    for cut in (0, 719, 5000, -3):
        with pytest.raises(ValueError, match='cut'):
            splice(tape, tape, cut)
    with pytest.raises(ValueError, match='same number of turns'):
        splice(tape, tape[:100], 72)


def test_states_at_reads_a_lab_replay(tmp_path):
    replay = {'format': 'kaggriculture-lab-v2', 'turns': [
        {'step': step, 'actions': [], 'observations': [observation(money=float(step))]}
        for step in (72, 144, 216)]}
    path = tmp_path / 'r.json'
    path.write_text(json.dumps(replay))
    states = states_at(path, 0, [72, 144, 999])
    assert sorted(states) == [72, 144], 'a cut the replay does not reach is skipped'
    assert states[144]['money'] == 144.0


def test_inline_sparse_snapshots_do_not_require_a_replay_file():
    result = run_match('pass', 'starter', 12, seat=1, replay_inline=True,
                       replay_steps=[0, 72, 144, 719])

    assert [turn['step'] for turn in result['replay_turns']] == [0, 72, 144, 719]
    for turn in result['replay_turns']:
        assert turn['observations'][1]['step'] == turn['step']


def test_zero_distance_is_the_only_claim_the_tool_makes():
    """Measured over 720 games and two donors: distance 0 means a free join, and
    distance above 0 says only "play it to find out". Ranking by distance does
    not survive — see docs/SPLICE_DISTANCE.md — so nothing here should tempt a
    caller into ordering candidates by it."""
    same = fingerprint(observation(), 0)
    assert join_distance(same, same)['distance'] == 0.0

    near = fingerprint(observation(money=1010.0), 0)
    far = fingerprint(observation(money=1010.0, shed={'WOOL': 1}), 0)
    # Both are non-zero, which is the whole verdict; their order carries no
    # measured meaning, so the test asserts only what was actually established.
    assert join_distance(same, near)['distance'] > 0
    assert join_distance(same, far)['distance'] > 0
