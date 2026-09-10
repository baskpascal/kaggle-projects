"""What the live ladder says about a submission, as opposed to what our panel says.

Every evaluation in this repository so far has been local: a paired arena against a panel
of public artifacts. That panel is built from the public meta, and `v006` replays the
public meta, so the panel cannot tell us whether the agent is strong - only whether it
resembles the tapes it came from. This module reads the other side of that comparison,
the episodes the agent actually played on Kaggle, and answers three questions with them:

1. are we losing on strategy - do we lose to opponents at our own rating and above?
2. are we losing on execution - do episodes error, time out, or come back seat-asymmetric?
3. does the local panel predict the ladder at all?

The data arrives in two tiers, and the split matters because only one of them is metered.

`ListEpisodes` is free: it takes a submission id and returns every episode that submission
played, each with both agents' rewards, seat index, team, and the Bradley-Terry
`initialScore`/`updatedScore` pair that brackets the episode. That is enough for questions
1 and 2 and for the whole rating trajectory, at no cost.

`GetEpisode` is metered - Kaggle allows 3,600 episode views per 24 hours - and needs
credentials. It carries the fields the free tier omits: the world seed, the engine version,
the per-agent terminal status, and the action stream. Question 3 needs the seed, because
reproducing a ladder episode locally is the only way to compare `predicted_local_outcome`
against `actual_ladder_outcome`. So tier two is fetched deliberately, deduplicated against
everything already on disk, and charged against a persistent ledger that survives restarts.
Nothing is ever downloaded twice.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import statistics
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
SERVICE = 'https://www.kaggle.com/api/i/competitions.EpisodeService/'
STORE = ROOT / 'data' / 'kaggle' / 'ladder'
VIEW_BUDGET = 3600
VIEW_WINDOW = timedelta(hours=24)
RATING_BANDS = ((0, 2400), (2400, 2600), (2600, 2800), (2800, 3000), (3000, 10000))


def session(user_agent='kaggriculture-lab/1.0'):
    http = requests.Session()
    http.headers.update({'Content-Type': 'application/json', 'User-Agent': user_agent})
    return http


def list_episodes(submission_id, http=None):
    """Tier one. Free, unauthenticated, and the only call this module needs by default."""
    http = http or session()
    response = http.post(SERVICE + 'ListEpisodes',
                         json={'submissionId': int(submission_id)}, timeout=120)
    response.raise_for_status()
    return response.json()


def normalise(payload, submission_id):
    """One record per episode, from our submission's point of view.

    Episodes where our submission does not appear exactly once are dropped rather than
    guessed at: a self-play episode would otherwise be counted twice with opposite signs.
    """
    submission_id = int(submission_id)
    teams = {team['id']: team.get('teamName') for team in payload.get('teams', ())}
    records = []
    for episode in payload.get('episodes', ()):
        agents = episode.get('agents') or []
        mine = [agent for agent in agents if agent.get('submissionId') == submission_id]
        if len(mine) != 1 or len(agents) != 2:
            continue
        ours = mine[0]
        theirs = next(agent for agent in agents if agent is not ours)
        records.append({
            'episode_id': episode.get('id'),
            'submission_id': submission_id,
            'state': episode.get('state'),
            'type': episode.get('type'),
            'create_time': episode.get('createTime'),
            'end_time': episode.get('endTime'),
            'seat': ours.get('index', 0),
            'our_reward': ours.get('reward'),
            'opponent_reward': theirs.get('reward'),
            'initial_score': ours.get('initialScore'),
            'updated_score': ours.get('updatedScore'),
            'updated_confidence': ours.get('updatedConfidence'),
            'opponent_submission_id': theirs.get('submissionId'),
            'opponent_team_id': theirs.get('teamId'),
            'opponent_team': teams.get(theirs.get('teamId')),
            'opponent_initial_score': theirs.get('initialScore'),
        })
    records.sort(key=lambda record: (record['create_time'] or '', record['episode_id']))
    return records


def outcome(record):
    """+1 win, 0 loss, 0.5 tie, or None when either reward is missing.

    A missing reward is not a loss. It means the episode did not produce a score for that
    agent, which is an execution signal in its own right and is counted separately.
    """
    ours, theirs = record['our_reward'], record['opponent_reward']
    if ours is None or theirs is None:
        return None
    return 1.0 if ours > theirs else (0.0 if ours < theirs else 0.5)


def _summary(records, label):
    scored = [(record, outcome(record)) for record in records]
    played = [(record, result) for record, result in scored if result is not None]
    row = {'cut': label, 'episodes': len(records), 'scored': len(played),
           'unscored': len(records) - len(played)}
    if played:
        row['win_rate'] = sum(result for _, result in played) / len(played)
        row['margin_median'] = statistics.median(
            record['our_reward'] - record['opponent_reward'] for record, _ in played)
    deltas = [record['updated_score'] - record['initial_score'] for record in records
              if record['updated_score'] is not None and record['initial_score'] is not None]
    if deltas:
        row['rating_delta_mean'] = statistics.mean(deltas)
        row['rating_delta_sum'] = sum(deltas)
    return row


def cuts(records):
    """The four cuts that separate a strategy problem from an execution problem."""
    report = {'overall': _summary(records, 'overall'), 'by_seat': [], 'by_state': [],
              'by_outcome': [], 'by_opponent_band': []}
    for seat in sorted({record['seat'] for record in records}):
        report['by_seat'].append(
            _summary([r for r in records if r['seat'] == seat], f'seat {seat}'))
    for state in sorted({record['state'] for record in records if record['state']}):
        report['by_state'].append(
            _summary([r for r in records if r['state'] == state], state))
    for label, keep in (('wins', 1.0), ('losses', 0.0), ('ties', 0.5)):
        chosen = [r for r in records if outcome(r) == keep]
        if chosen:
            report['by_outcome'].append(_summary(chosen, label))
    for low, high in RATING_BANDS:
        chosen = [r for r in records if r['opponent_initial_score'] is not None
                  and low <= r['opponent_initial_score'] < high]
        if chosen:
            report['by_opponent_band'].append(_summary(chosen, f'opponent {low}-{high}'))
    return report


def trajectory(records):
    """The rating series, which is what the public leaderboard is actually showing."""
    series = [(record['create_time'], record['updated_score']) for record in records
              if record['updated_score'] is not None]
    if not series:
        return {}
    scores = [score for _, score in series]
    peak = max(range(len(scores)), key=scores.__getitem__)
    return {'episodes': len(series), 'first': series[0], 'last': series[-1],
            'peak': series[peak], 'peak_index': peak,
            'since_peak': scores[-1] - scores[peak]}


def access_token():
    """The Kaggle credential, read from where the CLI already keeps it.

    Machine credentials live in the user's home, never in this repository, so this reads
    `~/.kaggle/access_token` and never writes it. `KAGGLE_ACCESS_TOKEN` overrides for CI.
    """
    from os import environ
    token = environ.get('KAGGLE_ACCESS_TOKEN')
    if token:
        return token.strip()
    path = Path.home() / '.kaggle' / 'access_token'
    if not path.is_file():
        raise RuntimeError('No Kaggle credential: set KAGGLE_ACCESS_TOKEN or run `kaggle auth login`.')
    return path.read_text().strip()


def fetch_replay(episode_id, http=None, token=None):
    """Tier two. Authenticated, metered, and the only source of the world seed.

    The public REST route is used rather than the internal one, because it is the route
    the Kaggle CLI itself calls and it returns the replay exactly as the daily dumps store
    it - `info.seed`, per-agent `statuses`, `module_version`, and all 720 steps.
    """
    http = http or session()
    url = f'https://www.kaggle.com/api/v1/competitions/episodes/{int(episode_id)}/replay'
    response = http.get(url, timeout=(30, 600),
                        headers={'Authorization': f'Bearer {token or access_token()}'})
    response.raise_for_status()
    return response.content


def archive_path(submission_id, root=None):
    return Path(root or STORE) / f'replays-{int(submission_id)}.zip'


def archived(archive):
    """Episode ids already on disk, so a view is never spent twice on the same episode."""
    import zipfile
    archive = Path(archive)
    if not archive.is_file():
        return set()
    with zipfile.ZipFile(archive) as source:
        return {int(Path(name).stem) for name in source.namelist() if name.endswith('.json')}


def ensure_replays(episode_ids, archive, ledger, http=None, token=None, progress=None):
    """Fetch the replays not already archived, charging one view each, and stop on budget.

    The archive is written in the same shape as Kaggle's own daily dumps - one
    `<episode_id>.json` member per episode - so `top_panel.episode_streams` and
    `top_panel.reproduce` read it without changes.
    """
    import zipfile
    archive = Path(archive)
    archive.parent.mkdir(parents=True, exist_ok=True)
    have = archived(archive)
    wanted = [identifier for identifier in episode_ids if int(identifier) not in have]
    http = http or session()
    token = token or access_token()
    fetched, skipped = [], len(episode_ids) - len(wanted)
    for identifier in wanted:
        if ledger.remaining() < 1:
            break
        payload = fetch_replay(identifier, http, token)
        ledger.charge(1)
        with zipfile.ZipFile(archive, 'a', zipfile.ZIP_DEFLATED) as sink:
            sink.writestr(f'{int(identifier)}.json', payload)
        fetched.append(int(identifier))
        if progress:
            progress(len(fetched), len(wanted), int(identifier), len(payload))
    return {'fetched': fetched, 'already_archived': skipped,
            'not_fetched': [i for i in wanted if int(i) not in set(fetched)],
            'views_remaining': ledger.remaining()}


def replay_facts(replay, submission_id, records=None):
    """The fields tier one cannot supply, pulled out of one replay."""
    info = replay.get('info') or {}
    statuses = replay.get('statuses') or []
    seat = None
    if records:
        match = {record['episode_id']: record for record in records}
        seat = match.get(info.get('EpisodeId'), {}).get('seat')
    return {'episode_id': info.get('EpisodeId'), 'submission_id': int(submission_id),
            'seed': info.get('seed'), 'team_names': info.get('TeamNames'),
            'engine_version': replay.get('module_version'),
            'schema_version': replay.get('schema_version'),
            'statuses': statuses, 'rewards': replay.get('rewards'),
            'turns': len(replay.get('steps') or []), 'seat': seat,
            'clean': all(status == 'DONE' for status in statuses) and len(statuses) == 2}


def store_path(submission_id, root=None):
    return Path(root or STORE) / f'episodes-{int(submission_id)}.json'


def merge(existing, fresh):
    """Union by episode id, newest record winning, so a refresh never loses history."""
    merged = {record['episode_id']: record for record in existing}
    merged.update({record['episode_id']: record for record in fresh})
    return sorted(merged.values(),
                  key=lambda record: (record['create_time'] or '', record['episode_id']))


def load(submission_id, root=None):
    path = store_path(submission_id, root)
    if not path.is_file():
        return []
    return json.loads(path.read_text())['episodes']


def save(submission_id, records, root=None):
    path = store_path(submission_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'submission_id': int(submission_id),
                                'fetched': datetime.now(timezone.utc).isoformat(),
                                'episodes': records}, indent=1))
    return path


class ViewLedger:
    """Kaggle allows 3,600 episode views per 24 hours, so spending them is accounted for.

    Only tier two costs a view. The ledger is a file rather than a counter in memory
    because the limit spans a day and the collector is expected to be run repeatedly.
    """

    def __init__(self, path, budget=VIEW_BUDGET, window=VIEW_WINDOW):
        self.path = Path(path)
        self.budget = budget
        self.window = window

    def _entries(self):
        if not self.path.is_file():
            return []
        return json.loads(self.path.read_text()).get('views', [])

    def spent(self, now=None):
        now = now or datetime.now(timezone.utc)
        cutoff = now - self.window
        return [stamp for stamp in self._entries()
                if datetime.fromisoformat(stamp) > cutoff]

    def remaining(self, now=None):
        return self.budget - len(self.spent(now))

    def charge(self, count=1, now=None):
        now = now or datetime.now(timezone.utc)
        if self.remaining(now) < count:
            raise RuntimeError(
                f'Episode view budget exhausted: {self.remaining(now)} left of {self.budget} '
                f'in the last {self.window}. Wait for the window to roll rather than '
                f'refetching, because a denied view still counts.')
        views = self.spent(now) + [now.isoformat()] * count
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({'views': views}, indent=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submission', type=int, required=True,
                        help='the submission whose ladder episodes to read')
    parser.add_argument('--root', default=str(STORE), help='where the episode cache lives')
    parser.add_argument('--offline', action='store_true',
                        help='report from the cache without calling Kaggle')
    parser.add_argument('--report', help='write the cuts to this JSON file')
    parser.add_argument('--fetch-replays', type=int, metavar='N',
                        help='tier two: archive up to N replays, newest band first')
    parser.add_argument('--band', nargs=2, type=float, metavar=('LOW', 'HIGH'),
                        help='restrict --fetch-replays to this opponent rating band')
    arguments = parser.parse_args()

    cached = load(arguments.submission, arguments.root)
    if arguments.offline:
        records = cached
        if not records:
            raise SystemExit('No cached episodes; run once without --offline first.')
    else:
        payload = list_episodes(arguments.submission)
        records = merge(cached, normalise(payload, arguments.submission))
        save(arguments.submission, records, arguments.root)

    report = {'submission_id': arguments.submission,
              'trajectory': trajectory(records), 'cuts': cuts(records)}

    if arguments.fetch_replays:
        chosen = records
        if arguments.band:
            low, high = arguments.band
            chosen = [record for record in records
                      if record['opponent_initial_score'] is not None
                      and low <= record['opponent_initial_score'] < high]
        wanted = [record['episode_id'] for record in chosen][:arguments.fetch_replays]
        ledger = ViewLedger(Path(arguments.root) / 'view-ledger.json')

        def progress(done, total, identifier, size):
            print(f'  replay {done}/{total}  episode {identifier}  {size / 1e6:.1f} MB',
                  file=sys.stderr, flush=True)

        report['replays'] = ensure_replays(
            wanted, archive_path(arguments.submission, arguments.root), ledger,
            progress=progress)
    print(json.dumps(report, indent=1, default=str))
    if arguments.report:
        Path(arguments.report).write_text(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
