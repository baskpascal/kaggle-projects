"""Measure a candidate against the action streams in one current daily dump.

The daily dataset is selected from the strongest ladder episodes, so it answers a
different question from the fixed public-agent panel: how often would the candidate
beat the behaviours that are playing near the top now?  For each recorded episode
the candidate replaces each seat once, while the other seat replays the actions it
actually submitted.  This is a behavioural benchmark, not a reconstruction of the
other agent: its recorded stream cannot react differently after our actions alter the
world.  The limitation travels in the result file so the score cannot be presented as
a live-ladder win rate later.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import statistics

from arena.agents import agent_hash
from arena.engine import fingerprint
from arena.parallel import matches
from experiments.episode_tapes import episode_paths, read_episode, tape_digest
from experiments.tape_agent import build


LIMITATION = (
    'Recorded opponents replay their observed actions open-loop and cannot react '
    'differently after the candidate changes the world.'
)


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def behaviour_digest(actions, turns=24):
    """Stable opening lineage compatible with a fresh daily dump."""
    return tape_digest(actions[:turns])[:16]


def source_rows(source, workers=8, assignment_limit=0):
    """Expand every valid episode into the two candidate/opponent seat assignments."""
    paths = episode_paths(source)
    if assignment_limit:
        paths = paths[:math.ceil(assignment_limit / 2)]
    rows, errors = [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(read_episode, paths, chunksize=1):
            if 'error' in result:
                errors.append(result)
                continue
            seats = result['seats']
            for candidate_seat in (0, 1):
                opponent = seats[1 - candidate_seat]
                rows.append({
                    'episode': opponent['episode'], 'seed': opponent['seed'],
                    'candidate_seat': candidate_seat,
                    'opponent_seat': 1 - candidate_seat,
                    'opponent_team': opponent['team'],
                    'opponent_tape': opponent['sha256'],
                    'opponent_behaviour': behaviour_digest(opponent['actions']),
                    'opponent_actions': opponent['actions'],
                    'recorded_candidate_money': seats[candidate_seat]['money'],
                    'recorded_opponent_money': opponent['money'],
                    'engine': opponent['engine'],
                })
    return rows, errors


def require_engine(rows, expected):
    mismatches = [row for row in rows if row['engine'] != expected]
    if mismatches:
        versions = sorted({str(row['engine']) for row in mismatches})
        raise ValueError(f'{len(mismatches)} seat assignments use engine {versions}; '
                         f'this arena requires {expected}')


def score_interval(scores):
    """Normal 95% interval over independent episode-seat observations."""
    if not scores:
        raise ValueError('Cannot summarize an empty benchmark')
    mean = statistics.mean(scores)
    if len(scores) == 1:
        return [mean, mean]
    error = 1.96 * statistics.stdev(scores) / math.sqrt(len(scores))
    return [max(0.0, mean - error), min(1.0, mean + error)]


def _group_table(groups):
    return [{'name': name, 'games': len(values), 'score': statistics.mean(values)}
            for name, values in groups.items()]


def _worst_with_support(table, minimum=4):
    eligible = [row for row in table if row['games'] >= minimum]
    return min(eligible, key=lambda row: (row['score'], -row['games'], str(row['name']))) \
        if eligible else None


def summarize(rows):
    if not rows:
        raise ValueError('Cannot summarize an empty benchmark')
    scores = [row['score'] for row in rows]
    by_team, by_behaviour = defaultdict(list), defaultdict(list)
    by_seat, by_episode = defaultdict(list), defaultdict(list)
    for row in rows:
        by_team[row['opponent_team']].append(row['score'])
        by_behaviour[row['opponent_behaviour']].append(row['score'])
        by_seat[row['candidate_seat']].append(row['score'])
        by_episode[row['episode']].append(row['score'])
    teams, behaviours = _group_table(by_team), _group_table(by_behaviour)
    episode_scores = [statistics.mean(values) for values in by_episode.values()]
    return {
        'games': len(rows), 'score': statistics.mean(scores),
        # The two seats from one episode share a world. Treating them as independent
        # would make the interval narrower than the evidence permits.
        'score_ci95_by_episode': score_interval(episode_scores),
        'seat_score': {str(seat): statistics.mean(values)
                       for seat, values in sorted(by_seat.items())},
        'episodes': len(by_episode), 'teams': len(teams), 'behaviours': len(behaviours),
        'worst_team_min_4_games': _worst_with_support(teams),
        'median_team_score': statistics.median(row['score'] for row in teams),
        'worst_behaviour_min_4_games': _worst_with_support(behaviours),
        'failures': sum(bool(row['failures'] or row['opponent_failures']) for row in rows),
    }


def benchmark(candidate, source, output, agent_dir, *, workers=8, limit=0,
              required_engine=None):
    assignments, errors = source_rows(source, workers=workers, assignment_limit=limit)
    if errors:
        raise ValueError(f'{len(errors)} episodes could not be read; first: {errors[0]}')
    required_engine = required_engine or fingerprint()['version']
    require_engine(assignments, required_engine)
    if limit:
        assignments = assignments[:limit]
    directory = Path(agent_dir)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for row in assignments:
        digest = row['opponent_tape']
        if digest not in paths:
            path = directory / f'{digest[:16]}.py'
            if not path.exists():
                build(row['opponent_actions'], path,
                      f"daily episode {row['episode']} seat {row['opponent_seat']}")
            paths[digest] = str(path)
    jobs = [dict(candidate=str(candidate), opponent=paths[row['opponent_tape']],
                 seed=row['seed'], seat=row['candidate_seat'], telemetry_enabled=False)
            for row in assignments]
    measured = []
    for source_row, result in zip(assignments, matches(jobs, workers=workers), strict=True):
        measured.append({key: value for key, value in source_row.items()
                         if key != 'opponent_actions'} | {
            'score': result['score'], 'money': result['money'],
            'opponent_money': result['opponent_money'], 'margin': result['margin'],
            'failures': result['failures'],
            'opponent_failures': result['opponent_failures'],
        })
    payload = {
        'schema_version': 1, 'kind': 'current_meta_open_loop_benchmark',
        'candidate': str(candidate), 'candidate_hash': agent_hash(candidate),
        'source': str(source), 'source_sha256': file_digest(source),
        'required_engine': required_engine,
        'limitation': LIMITATION, 'summary': summarize(measured), 'rows': measured,
    }
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload) + '\n', encoding='utf-8')
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--source', required=True, help='official daily zip or extracted directory')
    parser.add_argument('--output', required=True)
    parser.add_argument('--agent-dir', required=True)
    parser.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument('--engine', help='required engine; defaults to the arena fingerprint')
    parser.add_argument('--limit', type=int, default=0, help='smoke only: first N seat assignments')
    args = parser.parse_args()
    payload = benchmark(args.candidate, args.source, args.output, args.agent_dir,
                        workers=args.workers, limit=args.limit, required_engine=args.engine)
    print(json.dumps(payload['summary'], indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
