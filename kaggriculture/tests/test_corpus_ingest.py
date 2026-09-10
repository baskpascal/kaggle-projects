import csv
import json

import pytest

from experiments.corpus_ingest import (capped_weight, effective_sample_size, ingest,
                                        source_identity)


def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8', newline='') as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def corpus(root):
    root.mkdir()
    write_csv(root / 'episodes.csv',
              ['episode_id', 'create_time', 'end_time', 'type', 'state',
               'sub_0', 'team_0', 'bank_0', 'rating_0',
               'sub_1', 'team_1', 'bank_1', 'rating_1'], [
        {'episode_id': 1, 'create_time': '2026-09-09', 'end_time': '2026-09-10T01:00:00Z',
         'type': 'EPISODE_TYPE_PUBLIC', 'state': 'COMPLETED',
         'sub_0': 10, 'team_0': 100, 'bank_0': 5, 'rating_0': 2900,
         'sub_1': 11, 'team_1': 101, 'bank_1': 4, 'rating_1': 2800},
        {'episode_id': 2, 'create_time': '2026-09-09', 'end_time': '2026-09-10',
         'type': 'EPISODE_TYPE_VALIDATION', 'state': 'COMPLETED',
         'sub_0': 10, 'team_0': 100, 'bank_0': 5, 'rating_0': 2900,
         'sub_1': 11, 'team_1': 101, 'bank_1': 4, 'rating_1': 2800},
        {'episode_id': 3, 'create_time': '2026-09-09', 'end_time': '2026-09-10',
         'type': 'EPISODE_TYPE_PUBLIC', 'state': 'COMPLETED',
         'sub_0': 10, 'team_0': 100, 'bank_0': 5, 'rating_0': 2900,
         'sub_1': 11, 'team_1': 101, 'bank_1': 4, 'rating_1': 2800},
    ])
    seat_rows = [{'episode_id': episode, 'seat': seat,
                  'engine_version': '1.32.6' if episode == 3 else '1.32.7',
                  'final_money': 100 + seat}
                 for episode in (1, 2, 3) for seat in (0, 1)]
    write_csv(root / 'episode_features.csv',
              ['episode_id', 'seat', 'engine_version', 'final_money'], seat_rows)
    write_csv(root / 'stream_hashes.csv',
              ['episode_id', 'seat', 'turns', 'stream_h24', 'stream_h48',
               'stream_h136', 'stream_h719'], [
        {'episode_id': episode, 'seat': seat, 'turns': 719,
         'stream_h24': f'h24-{episode}-{seat}', 'stream_h48': f'h48-{episode}-{seat}',
         'stream_h136': f'h136-{episode}-{seat}', 'stream_h719': f'full-{episode}-{seat}'}
        for episode in (1, 2, 3) for seat in (0, 1)])
    write_csv(root / 'per_submission_coverage.csv',
              ['submission_id', 'episodes_indexed', 'episodes_stored', 'coverage'], [
        {'submission_id': 10, 'episodes_indexed': 10, 'episodes_stored': 5, 'coverage': .5},
        {'submission_id': 11, 'episodes_indexed': 100, 'episodes_stored': 1, 'coverage': .01},
    ])
    write_csv(root / 'teams.csv',
              ['team_id', 'team_name', 'ladder_score', 'last_submission'], [
        {'team_id': 100, 'team_name': 'alpha', 'ladder_score': 2900, 'last_submission': 10},
        {'team_id': 101, 'team_name': 'beta', 'ladder_score': 2800, 'last_submission': 11},
    ])
    return root


def test_only_public_current_engine_rows_are_eligible(tmp_path):
    result = ingest(corpus(tmp_path / 'metadata'), tmp_path / 'out')
    library = result['library']
    assert [(row['episode_id'], row['seat']) for row in library['rows']] == [(1, 0), (1, 1)]
    assert library['refused'] == {'engine_mismatch': 2, 'non_public_episode': 1}
    assert all(row['engine_version'] == '1.32.7' and
               row['episode_type'] == 'EPISODE_TYPE_PUBLIC' for row in library['rows'])


def test_lineage_rating_rank_and_capped_coverage_are_derived(tmp_path):
    result = ingest(corpus(tmp_path / 'metadata'), tmp_path / 'out', weight_cap=10)
    first, second = result['library']['rows']
    assert (first['team'], first['rating'], first['rank']) == ('alpha', 2900., 1)
    assert first['lineage_h24'] == 'h24-1-0'
    assert first['lineage_h48'] == 'h48-1-0'
    assert first['lineage_h136'] == 'h136-1-0'
    assert first['stream_full_hash'] == 'full-1-0'
    assert first['sample_weight'] == 2
    assert second['sample_weight'] == 10, 'inverse coverage is capped'
    observation = result['panel_observations']['opponents']['corpus-ep1s0']
    assert observation['kind'] == 'episode_reconstruction'
    assert observation['observed_at'] == '2026-09-10'
    assert observation['submission_id'] == 10 and observation['team'] == 'alpha'
    assert result['library']['effective_sample_size'] == pytest.approx(144 / 104)


def test_revision_and_outputs_are_byte_deterministic(tmp_path):
    metadata = corpus(tmp_path / 'metadata')
    first = ingest(metadata, tmp_path / 'one')
    second = ingest(metadata, tmp_path / 'two')
    assert first == second
    for name in ('library.json', 'panel-observations.json'):
        assert (tmp_path / 'one' / name).read_bytes() == (tmp_path / 'two' / name).read_bytes()
    revision = source_identity(metadata, 'georgymamarin/kaggriculture-episodes')['revision']
    assert first['library']['source']['dataset_revision'] == revision
    with (metadata / 'teams.csv').open('a') as target:
        target.write('\n')
    assert source_identity(metadata, 'georgymamarin/kaggriculture-episodes')['revision'] != revision


def test_refiling_same_revision_is_noop_but_different_content_is_refused(tmp_path):
    metadata = corpus(tmp_path / 'metadata')
    output = tmp_path / 'out'
    ingest(metadata, output)
    before = (output / 'library.json').read_bytes()
    ingest(metadata, output)
    assert (output / 'library.json').read_bytes() == before
    with (metadata / 'teams.csv').open('a') as target:
        target.write('\n')
    with pytest.raises(FileExistsError, match='different corpus revision'):
        ingest(metadata, output)


@pytest.mark.parametrize('coverage,cap', [(-.1, 10), (1.1, 10), (float('nan'), 10),
                                           (.5, .5)])
def test_invalid_weight_inputs_are_refused(coverage, cap):
    with pytest.raises(ValueError):
        capped_weight(coverage, cap)


def test_effective_sample_size_handles_empty_input():
    assert effective_sample_size([]) == 0
