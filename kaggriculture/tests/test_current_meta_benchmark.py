import hashlib
import json
import zipfile

import pytest

from experiments.current_meta_benchmark import (benchmark, behaviour_digest, file_digest,
                                                score_interval, require_engine, source_identity,
                                                source_rows, summarize)


def test_behaviour_digest_uses_only_the_opening():
    opening = [{'farmer': ['PASS'], 'hands': [], 'market': []}] * 24
    assert behaviour_digest(opening + [{'farmer': ['NORTH']}]) == \
        behaviour_digest(opening + [{'farmer': ['SOUTH']}])


def test_summary_reports_seats_and_worst_lineages():
    rows = [
        {'score': 1.0, 'episode': 1, 'candidate_seat': 0, 'opponent_team': 'a',
         'opponent_behaviour': 'x', 'failures': [], 'opponent_failures': []},
        {'score': 0.0, 'episode': 1, 'candidate_seat': 1, 'opponent_team': 'a',
         'opponent_behaviour': 'y', 'failures': [], 'opponent_failures': []},
        {'score': 1.0, 'episode': 2, 'candidate_seat': 1, 'opponent_team': 'b',
         'opponent_behaviour': 'y', 'failures': [], 'opponent_failures': []},
    ]
    result = summarize(rows)
    assert result['score'] == pytest.approx(2 / 3)
    assert result['seat_score'] == {'0': 1.0, '1': 0.5}
    assert result['episodes'] == 2
    assert result['worst_team_min_4_games'] is None
    assert result['worst_behaviour_min_4_games'] is None
    assert result['failures'] == 0
    assert result['score_ci95_by_episode'][0] <= result['score'] <= \
        result['score_ci95_by_episode'][1]


def test_score_interval_single_observation_is_explicit():
    assert score_interval([0.5]) == [0.5, 0.5]


def test_source_rows_reverses_candidate_and_opponent_seats(tmp_path):
    actions = [
        {'farmer': ['NORTH'], 'hands': [], 'market': []},
        {'farmer': ['SOUTH'], 'hands': [], 'market': []},
    ]
    episode = {
        'info': {'EpisodeId': 7, 'seed': 9, 'TeamNames': ['left', 'right']},
        'rewards': [11.0, 12.0], 'statuses': ['DONE', 'DONE'],
        'module_version': '1.32.7',
        'steps': [[{'action': {'farmer': ['PASS'], 'hands': [], 'market': []}}
                   for _ in range(2)]] +
                 [[{'action': actions[seat]} for seat in range(2)] for _ in range(719)],
    }
    archive = tmp_path / 'daily.zip'
    with zipfile.ZipFile(archive, 'w') as target:
        target.writestr('7.json', json.dumps(episode))
    rows, errors = source_rows(archive, workers=1)
    assert errors == []
    assert [(row['candidate_seat'], row['opponent_seat'], row['opponent_team'])
            for row in rows] == [(0, 1, 'right'), (1, 0, 'left')]
    assert rows[0]['opponent_actions'][0] == actions[1]


def test_file_digest_streams_the_source(tmp_path):
    path = tmp_path / 'source.bin'
    path.write_bytes(b'current meta')
    assert file_digest(path) == hashlib.sha256(b'current meta').hexdigest()


def test_directory_identity_is_ordered_and_content_addressed(tmp_path):
    source = tmp_path / 'episodes'
    source.mkdir()
    (source / 'b.json').write_bytes(b'b')
    (source / 'a.json').write_bytes(b'a')
    first = source_identity(source)
    assert [row['path'] for row in first['manifest']] == ['a.json', 'b.json']
    assert source_identity(source) == first
    (source / 'a.json').write_bytes(b'changed')
    assert source_identity(source)['sha256'] != first['sha256']


def test_directory_benchmark_resumes_from_shared_cache(tmp_path, monkeypatch):
    actions = {'farmer': ['PASS'], 'hands': [], 'market': []}
    episode = {'info': {'EpisodeId': 7, 'seed': 9, 'TeamNames': ['left', 'right']},
        'rewards': [11., 12.], 'statuses': ['DONE', 'DONE'], 'module_version': '1.32.7',
        'steps': [[{'action': actions}, {'action': actions}]] * 720}
    source = tmp_path / 'episodes'
    source.mkdir()
    (source / '7.json').write_text(json.dumps(episode))
    candidate = tmp_path / 'candidate.py'
    candidate.write_text('def agent(obs): return {}\n')
    played = []

    def runner(jobs, workers):
        for job in jobs:
            played.append(job['job_id'])
            yield {**job, 'configuration': {'seed': job['seed']},
                   'environment': {'version': '1.32.7'}, 'score': .5, 'money': 1.,
                   'opponent_money': 1., 'margin': 0., 'failures': [],
                   'opponent_failures': [], 'wall_seconds': .01}
    monkeypatch.setattr('experiments.current_meta_benchmark._runner',
                        lambda address: (runner, {'kind': 'test'}))
    cache = tmp_path / 'shared.sqlite3'
    output = tmp_path / 'report.json'
    first = benchmark(candidate, source, output, tmp_path / 'agents', workers=1,
                      required_engine='1.32.7', cache=cache)
    second = benchmark(candidate, source, output, tmp_path / 'agents', workers=1,
                       required_engine='1.32.7', cache=cache, resume=True)
    assert len(played) == 2
    assert first['cache']['misses'] == 2 and second['cache']['hits'] == 2
    assert first['source_identity']['kind'] == 'directory'


def test_engine_mismatch_is_a_hard_failure():
    with pytest.raises(ValueError, match='requires 1.32.7'):
        require_engine([{'engine': '1.32.6'}, {'engine': '1.32.7'}], '1.32.7')
