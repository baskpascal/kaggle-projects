from experiments.top_panel import (admissible, panel_observations,
                                   reproduction_record)


def tape():
    actions = [{'farmer': ['PASS'], 'hands': [], 'market': []} for _ in range(719)]
    return {'episode': 7, 'seed': 9, 'seat': 1, 'team': 'alpha',
            'opponent_team': 'beta', 'money': 12, 'opponent_money': 10,
            'engine': '1.32.7', 'sha256': 'bb' * 32, 'actions': actions}


def test_admissible_rows_carry_mechanical_horizon_lineage():
    board = {'alpha': {'rating': 2900, 'rank': 1, 'of': 2},
             'beta': {'rating': 2800, 'rank': 2, 'of': 2}}
    rows, refused = admissible([tape()], board, 2700)
    assert refused == {}
    row, = rows
    assert all(len(row[name]) == 64 for name in
               ('lineage_h24', 'lineage_h48', 'lineage_h136', 'stream_full_hash'))


def test_reproduction_record_proves_the_published_result_for_the_selected_seat():
    row = {key: value for key, value in tape().items() if key != 'actions'}
    proof = {'expected': [10, 12], 'official': [10, 12], 'fast': [10, 12],
             'official_environment': {'version': '1.32.7'},
             'verified_at': '2026-09-10T00:00:00+00:00'}
    record = reproduction_record(row, proof, 'aa' * 32, 'cc' * 32)
    assert record['expected'] == record['actual'] == {
        'winner': 1, 'our_money': 12, 'opponent_money': 10}
    assert record['agent_sha256'] == 'aa' * 32
    assert record['stream_sha256'] == 'bb' * 32


def test_panel_observations_are_derived_from_emitted_manifests():
    entry = {'id': 'ep7s1', 'recorded': {'episode': 7, 'seat': 1},
             'observed_rating': {'observed_at': '2026-09-10', 'source': 'board.csv',
                                 'rank': 3, 'of': 100, 'rating': 2900},
             'lineage_h24': '24', 'lineage_h48': '48',
             'lineage_h136': '136', 'stream_full_hash': '719'}
    result = panel_observations([entry], {'leaderboard': 'board.csv'},
                                'dataset', 'cc' * 32)
    observation = result['opponents']['ep7s1']
    assert observation['kind'] == 'episode_reconstruction'
    assert observation['rank'] == 3 and observation['rating'] == 2900
    assert result['source']['dataset_revision'] == 'cc' * 32
