"""The ladder collector is only useful if its cuts cannot quietly lie about the ladder."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from experiments.ladder_ground_truth import (ViewLedger, archive_path, archived, cuts,
                                             ensure_replays, load, merge, normalise,
                                             outcome, replay_facts, save, store_path,
                                             trajectory)

OURS = 56125200


def agent(submission, reward, initial, updated, seat=None, team=1):
    entry = {'submissionId': submission, 'reward': reward, 'initialScore': initial,
             'updatedScore': updated, 'teamId': team}
    if seat is not None:
        entry['index'] = seat
    return entry


def episode(identifier, our_reward, their_reward, seat=0, initial=2500.0, updated=2505.0,
            opponent_initial=2500.0, state='COMPLETED', when=None):
    ours = agent(OURS, our_reward, initial, updated, seat if seat else None, team=10)
    theirs = agent(999, their_reward, opponent_initial, opponent_initial, None, team=20)
    agents = [ours, theirs] if seat == 0 else [theirs, ours]
    return {'id': identifier, 'state': state, 'type': 'EPISODE_TYPE_PUBLIC',
            'createTime': when or f'2026-09-09T{identifier % 24:02d}:00:00Z',
            'endTime': None, 'agents': agents}


def payload(*episodes):
    return {'episodes': list(episodes), 'teams': [{'id': 20, 'teamName': 'Rival'}],
            'submissions': []}


def test_normalise_reads_the_episode_from_our_seat():
    records = normalise(payload(episode(1, 100.0, 90.0, seat=1)), OURS)
    assert len(records) == 1
    record = records[0]
    assert record['seat'] == 1 and record['our_reward'] == 100.0
    assert record['opponent_reward'] == 90.0 and record['opponent_team'] == 'Rival'
    assert record['episode_id'] == 1 and record['submission_id'] == OURS


def test_normalise_drops_episodes_it_cannot_attribute():
    """A self-play episode would otherwise be counted twice with opposite signs."""
    mirror = {'id': 2, 'state': 'COMPLETED', 'createTime': '2026-09-09T00:00:00Z',
              'agents': [agent(OURS, 10.0, 1.0, 1.0), agent(OURS, 20.0, 1.0, 1.0)]}
    assert normalise({'episodes': [mirror]}, OURS) == []
    solo = {'id': 3, 'state': 'COMPLETED', 'createTime': '2026-09-09T00:00:00Z',
            'agents': [agent(OURS, 10.0, 1.0, 1.0)]}
    assert normalise({'episodes': [solo]}, OURS) == []


def test_a_missing_reward_is_not_scored_as_a_loss():
    record = normalise(payload(episode(4, None, 90.0)), OURS)[0]
    assert outcome(record) is None
    report = cuts([record])
    assert report['overall']['scored'] == 0 and report['overall']['unscored'] == 1
    assert 'win_rate' not in report['overall']


def test_ties_count_as_half_a_win():
    records = normalise(payload(episode(5, 100.0, 100.0)), OURS)
    assert outcome(records[0]) == 0.5
    assert cuts(records)['overall']['win_rate'] == 0.5


def test_the_seat_cut_would_expose_a_seat_disaster():
    good = [episode(10 + i, 100.0, 90.0, seat=0) for i in range(4)]
    bad = [episode(20 + i, 10.0, 90.0, seat=1) for i in range(4)]
    report = cuts(normalise(payload(*good, *bad), OURS))
    by_seat = {row['cut']: row for row in report['by_seat']}
    assert by_seat['seat 0']['win_rate'] == 1.0
    assert by_seat['seat 1']['win_rate'] == 0.0


def test_the_opponent_band_cut_separates_weak_from_strong_fields():
    weak = [episode(30 + i, 100.0, 90.0, opponent_initial=2300.0) for i in range(3)]
    strong = [episode(40 + i, 80.0, 90.0, opponent_initial=2700.0) for i in range(3)]
    report = cuts(normalise(payload(*weak, *strong), OURS))
    bands = {row['cut']: row for row in report['by_opponent_band']}
    assert bands['opponent 0-2400']['win_rate'] == 1.0
    assert bands['opponent 2600-2800']['win_rate'] == 0.0


def test_trajectory_finds_the_peak_and_the_fall_after_it():
    rising = [episode(50 + i, 100.0, 90.0, updated=2000.0 + 100 * i) for i in range(4)]
    falling = [episode(60 + i, 80.0, 90.0, updated=2300.0 - 50 * i) for i in range(1, 4)]
    series = trajectory(normalise(payload(*rising, *falling), OURS))
    assert series['peak'][1] == 2300.0 and series['peak_index'] == 3
    assert series['since_peak'] == pytest.approx(-150.0)


def test_merge_is_idempotent_and_never_drops_history():
    first = normalise(payload(episode(70, 100.0, 90.0)), OURS)
    second = normalise(payload(episode(71, 100.0, 90.0)), OURS)
    assert [r['episode_id'] for r in merge(first, first)] == [70]
    assert [r['episode_id'] for r in merge(first, second)] == [70, 71]
    assert [r['episode_id'] for r in merge(merge(first, second), first)] == [70, 71]


def test_the_cache_round_trips(tmp_path):
    records = normalise(payload(episode(80, 100.0, 90.0)), OURS)
    save(OURS, records, tmp_path)
    assert store_path(OURS, tmp_path).is_file()
    assert load(OURS, tmp_path) == records
    assert load(999999, tmp_path) == []


def test_the_view_ledger_refuses_to_overspend_the_daily_budget(tmp_path):
    ledger = ViewLedger(tmp_path / 'views.json', budget=3)
    ledger.charge(2)
    assert ledger.remaining() == 1
    ledger.charge(1)
    assert ledger.remaining() == 0
    with pytest.raises(RuntimeError, match='view budget exhausted'):
        ledger.charge(1)


def test_the_view_ledger_forgets_charges_older_than_the_window(tmp_path):
    path = tmp_path / 'views.json'
    stale = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    path.write_text(json.dumps({'views': [stale, stale]}))
    ledger = ViewLedger(path, budget=3)
    assert ledger.remaining() == 3
    ledger.charge(1)
    assert ledger.remaining() == 2


def test_archived_reads_the_ids_already_on_disk(tmp_path):
    import zipfile
    path = archive_path(OURS, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as sink:
        sink.writestr('101.json', '{}')
        sink.writestr('102.json', '{}')
        sink.writestr('notes.txt', 'ignored')
    assert archived(path) == {101, 102}
    assert archived(tmp_path / 'absent.zip') == set()


def test_ensure_replays_never_spends_a_view_on_an_archived_episode(tmp_path):
    import zipfile
    path = archive_path(OURS, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as sink:
        sink.writestr('101.json', '{"info": {}}')
    ledger = ViewLedger(tmp_path / 'views.json', budget=10)
    calls = []

    def fake(identifier, http, token):
        calls.append(identifier)
        return b'{"info": {"EpisodeId": %d}}' % identifier

    import experiments.ladder_ground_truth as module
    original = module.fetch_replay
    module.fetch_replay = fake
    try:
        result = ensure_replays([101, 102], path, ledger, http=object(), token='t')
    finally:
        module.fetch_replay = original
    assert calls == [102] and result['fetched'] == [102]
    assert result['already_archived'] == 1
    assert ledger.remaining() == 9
    assert archived(path) == {101, 102}


def test_ensure_replays_stops_at_the_budget_instead_of_being_denied(tmp_path):
    ledger = ViewLedger(tmp_path / 'views.json', budget=1)
    import experiments.ladder_ground_truth as module
    original = module.fetch_replay
    module.fetch_replay = lambda identifier, http, token: b'{"info": {}}'
    try:
        result = ensure_replays([201, 202, 203], archive_path(OURS, tmp_path), ledger,
                                http=object(), token='t')
    finally:
        module.fetch_replay = original
    assert result['fetched'] == [201]
    assert result['not_fetched'] == [202, 203]
    assert result['views_remaining'] == 0


def test_replay_facts_flags_an_episode_that_did_not_finish_clean():
    clean = {'info': {'EpisodeId': 7, 'seed': 42, 'TeamNames': ['us', 'them']},
             'statuses': ['DONE', 'DONE'], 'rewards': [10.0, 9.0],
             'module_version': '1.32.7', 'schema_version': 1, 'steps': [0] * 720}
    facts = replay_facts(clean, OURS)
    assert facts['seed'] == 42 and facts['turns'] == 720 and facts['clean'] is True
    assert facts['engine_version'] == '1.32.7'
    broken = dict(clean, statuses=['DONE', 'ERROR'])
    assert replay_facts(broken, OURS)['clean'] is False


def test_replay_facts_carries_the_seat_across_from_the_free_tier():
    records = normalise(payload(episode(7, 100.0, 90.0, seat=1)), OURS)
    replay = {'info': {'EpisodeId': 7, 'seed': 1}, 'statuses': ['DONE', 'DONE'],
              'rewards': [90.0, 100.0], 'module_version': '1.32.7', 'steps': []}
    assert replay_facts(replay, OURS, records)['seat'] == 1
