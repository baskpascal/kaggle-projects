"""A daily dump is other people's games; extraction has to keep what makes it evidence."""
import json
import zipfile

import pytest

from experiments.episode_tapes import TURNS, collect, read_episode, tape_digest


def episode(path, *, teams=('alice', 'bob'), rewards=(120., 90.), marker='NORTH', states=TURNS + 1):
    steps = []
    for turn in range(states):
        first = 'PASS' if turn == 0 else marker
        second = 'PASS' if turn == 0 else 'SOUTH'
        if turn == TURNS:
            first, second = 'EAST', 'WEST'
        steps.append([{'action': {'farmer': [first], 'hands': [], 'market': []}},
                      {'action': {'farmer': [second], 'hands': [], 'market': []}}])
    path.write_text(json.dumps({
        'steps': steps, 'rewards': list(rewards), 'statuses': ['DONE', 'DONE'],
        'module_version': '1.32.7',
        'info': {'EpisodeId': 42, 'seed': 7, 'TeamNames': list(teams)}}))
    return path


def test_both_seats_are_extracted_with_the_outcome_that_produced_them(tmp_path):
    rows = read_episode(episode(tmp_path / 'e.json'))['seats']

    assert [row['seat'] for row in rows] == [0, 1]
    assert rows[0]['team'] == 'alice' and rows[0]['opponent_team'] == 'bob'
    assert rows[0]['money'] == 120. and rows[0]['opponent_money'] == 90.
    assert len(rows[0]['actions']) == TURNS
    assert rows[0]['actions'][0]['farmer'] == ['NORTH']
    assert rows[0]['actions'][-1]['farmer'] == ['EAST']


def test_a_truncated_episode_is_reported_rather_than_used(tmp_path):
    short = read_episode(episode(tmp_path / 'short.json', states=100))
    assert 'seats' not in short and 'expected' in short['error']

    broken = tmp_path / 'broken.json'
    broken.write_text('{not json')
    assert 'error' in read_episode(broken)


def test_a_missing_action_disqualifies_the_episode(tmp_path):
    path = episode(tmp_path / 'gap.json')
    payload = json.loads(path.read_text())
    del payload['steps'][5][0]['action']
    path.write_text(json.dumps(payload))

    assert 'missing actions' in read_episode(path)['error']


def test_identical_streams_collapse_into_one_tape_that_counts_its_repeats(tmp_path):
    source = tmp_path / 'dump'
    source.mkdir()
    episode(source / 'a.json')
    episode(source / 'b.json')                     # byte-identical actions, other file
    episode(source / 'c.json', marker='EAST', rewards=(200., 10.))

    payload = collect(source, tmp_path / 'library.json', workers=1)

    tapes = payload['tapes']
    assert payload['episodes'] == 3
    assert len(tapes) == 3, 'two distinct seat-0 streams and the shared seat-1 stream'
    assert [t['sha256'] for t in tapes] == sorted(t['sha256'] for t in tapes)
    repeats = {tape['actions'][0]['farmer'][0]: tape['repeats'] for tape in tapes}
    assert repeats['NORTH'] == 1 and repeats['EAST'] == 0
    assert repeats['SOUTH'] == 2, 'the same seat-1 stream appeared in all three episodes'


def test_winners_only_keeps_the_seats_that_actually_won(tmp_path):
    source = tmp_path / 'dump'
    source.mkdir()
    episode(source / 'a.json')

    payload = collect(source, tmp_path / 'library.json', workers=1, winners_only=True)

    assert [tape['seat'] for tape in payload['tapes']] == [0]


def test_a_library_is_never_silently_overwritten(tmp_path):
    source = tmp_path / 'dump'
    source.mkdir()
    episode(source / 'a.json')
    out = tmp_path / 'library.json'
    collect(source, out, workers=1)

    with pytest.raises(FileExistsError):
        collect(source, out, workers=1)


def test_daily_kaggle_zip_is_read_without_extracting_twenty_gigabytes(tmp_path):
    source = tmp_path / 'daily.zip'
    raw = episode(tmp_path / 'episode.json').read_bytes()
    with zipfile.ZipFile(source, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('42.json', raw)

    payload = collect(source, tmp_path / 'library.json', workers=1)

    assert payload['episodes'] == 1
    assert len(payload['tapes']) == 2
    assert all(tape['actions'][0]['farmer'] != ['PASS'] for tape in payload['tapes'])


def test_identity_is_the_action_stream(tmp_path):
    rows = read_episode(episode(tmp_path / 'e.json'))['seats']
    assert rows[0]['sha256'] == tape_digest(rows[0]['actions'])
    assert rows[0]['sha256'] != rows[1]['sha256']
