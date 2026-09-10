from experiments.spataro_imitation import features


def observation(player=0):
    farm = {'farmer': [4, 4], 'hands': [], 'hires_today': 0, 'money': 3000,
            'tiles': [[None] * 10 for _ in range(10)], 'unlocked_quadrants': ['NW']}
    return {'player': player, 'farms': [dict(farm), dict(farm)],
            'private': {'inventories': [], 'seeds': {}, 'shed': {}},
            'market': {'prices': {}, 'inventory': {}}, 'town': {'unlocked_shops': []}}


def test_features_are_seat_canonical():
    left = observation(0)
    right = observation(1)
    left['farms'][0]['money'] = 1234
    right['farms'][1]['money'] = 1234
    assert features(left) == features(right)


def test_features_track_worker_local_tile_and_private_inventory():
    base = observation()
    changed = observation()
    changed['farms'][0]['hands'] = [[2, 3]]
    changed['farms'][0]['tiles'][2][3] = {'kind': 'PASTURE', 'animal': 'COW'}
    changed['private']['inventories'] = [{'WHEAT': 2}]
    assert features(base) != features(changed)
