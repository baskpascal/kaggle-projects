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
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
import hashlib
import json
import math
import os
from pathlib import Path
import statistics

from arena.agents import agent_hash
from arena.batch import batched_runner
from arena.engine import fingerprint
from arena.jobs import JobStore, execute, identity_of, job_id, shared_cache_path
from arena.parallel import stream
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


def source_identity(path):
    """Hash a file or an extracted tree by a stable, ordered content manifest."""
    path = Path(path)
    if path.is_file():
        return {'kind': 'file', 'sha256': file_digest(path), 'bytes': path.stat().st_size}
    if not path.is_dir():
        raise ValueError(f'Expected a source file or directory: {path}')
    manifest = []
    for child in sorted(entry for entry in path.rglob('*') if entry.is_file()):
        manifest.append({'path': child.relative_to(path).as_posix(),
                         'bytes': child.stat().st_size, 'sha256': file_digest(child)})
    return {'kind': 'directory', 'sha256': hashlib.sha256(json.dumps(
        manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        'files': len(manifest), 'bytes': sum(row['bytes'] for row in manifest),
        'manifest': manifest}


def behaviour_digest(actions, turns=24):
    """Stable opening lineage compatible with a fresh daily dump."""
    return tape_digest(actions[:turns])[:16]


def iter_source_rows(source, workers=8, assignment_limit=0):
    """Yield assignments while holding at most the process pool's input window."""
    paths = episode_paths(source)
    if assignment_limit:
        paths = paths[:math.ceil(assignment_limit / 2)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        source = iter(enumerate(paths))
        pending, ready, next_index = {}, {}, 0

        def fill():
            while len(pending) + len(ready) < max(1, workers * 2):
                item = next(source, None)
                if item is None:
                    break
                index, path = item
                pending[pool.submit(read_episode, path)] = index

        fill()
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                ready[pending.pop(future)] = future.result()
            fill()
            while next_index in ready:
                result = ready.pop(next_index)
                next_index += 1
                if 'error' in result:
                    yield None, result
                    continue
                seats = result['seats']
                for candidate_seat in (0, 1):
                    opponent = seats[1 - candidate_seat]
                    yield {
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
                    }, None


def source_rows(source, workers=8, assignment_limit=0):
    """Compatibility materialisation; benchmark itself consumes the streaming iterator."""
    rows, errors = [], []
    for row, error in iter_source_rows(source, workers, assignment_limit):
        (errors if error else rows).append(error or row)
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


def _runner(ray_address):
    if ray_address is None:
        return stream, {'kind': 'local'}
    from arena.ray_transport import connect
    mapper = connect(ray_address)
    return batched_runner(size=None, map_batches=mapper,
                          available_slots=mapper.available_slots), {
        'kind': 'ray', 'address': ray_address, 'nodes': mapper.describe_nodes()}


def benchmark(candidate, source, output, agent_dir, *, workers=8, limit=0,
              required_engine=None, resume=False, ray_address=None, cache=None):
    candidate = str(candidate)
    assignments, errors, paths = [], [], {}
    directory = Path(agent_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for row, error in iter_source_rows(source, workers=workers, assignment_limit=limit):
        if error:
            errors.append(error)
            continue
        if limit and len(assignments) >= limit:
            break
        digest_value = row['opponent_tape']
        path = directory / f'{digest_value[:16]}.py'
        if digest_value not in paths:
            if not path.exists():
                build(row.pop('opponent_actions'), path,
                      f"daily episode {row['episode']} seat {row['opponent_seat']}")
            else:
                row.pop('opponent_actions')
            paths[digest_value] = str(path)
        else:
            row.pop('opponent_actions')
        assignments.append(row)
    if errors:
        raise ValueError(f'{len(errors)} episodes could not be read; first: {errors[0]}')
    required_engine = required_engine or fingerprint()['version']
    require_engine(assignments, required_engine)
    environment = fingerprint()
    candidate_hash = agent_hash(candidate)
    jobs = []
    for row in assignments:
        spec = dict(candidate=str(candidate), opponent=paths[row['opponent_tape']],
                    seed=row['seed'], seat=row['candidate_seat'], backend='fast',
                    split='current-meta', evidence_profile='audit', configuration={},
                    candidate_hash=candidate_hash,
                    opponent_hash=agent_hash(paths[row['opponent_tape']]),
                    engine_identity=environment)
        identity = identity_of(spec)
        spec.update({key: identity[key] for key in ('configuration_hash', 'engine_hash',
                                                    'replay_steps', 'replay_schema')})
        spec['job_id'] = job_id(spec)
        jobs.append(spec)
    runner, execution = _runner(ray_address)
    cache_path = Path(cache) if cache else shared_cache_path()
    target = Path(output)
    if target.exists() and not resume:
        raise FileExistsError(f'{target} exists; pass resume=True to rebuild from the cache')
    progress = target.with_suffix(target.suffix + '.progress.jsonl')
    progress.parent.mkdir(parents=True, exist_ok=True)
    with JobStore(cache_path) as store:
        identities = {job['job_id'] for job in jobs}
        hits = len(identities & store.completed())

    def record_progress(result):
        with progress.open('a', encoding='utf-8') as output_file:
            output_file.write(json.dumps({'job_id': result['job_id']}) + '\n')

    with JobStore(cache_path) as store:
        results = execute(jobs, store, 'current-meta', runner, workers=workers,
                          on_row=record_progress)
        cache_report = store.cache_summary(jobs, hits)
    measured = []
    for source_row, result in zip(assignments, results, strict=True):
        measured.append(source_row | {
            'score': result['score'], 'money': result['money'],
            'opponent_money': result['opponent_money'], 'margin': result['margin'],
            'failures': result['failures'],
            'opponent_failures': result['opponent_failures'],
        })
    source_provenance = source_identity(source)
    payload = {
        'schema_version': 1, 'kind': 'current_meta_open_loop_benchmark',
        'candidate': str(candidate), 'candidate_hash': candidate_hash,
        'source': str(source), 'source_sha256': source_provenance['sha256'],
        'source_identity': source_provenance, 'cache': cache_report,
        'execution': execution,
        'required_engine': required_engine,
        'limitation': LIMITATION, 'summary': summarize(measured), 'rows': measured,
    }
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
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--ray-address', help='Ray address (for example auto or ray://host:10001)')
    parser.add_argument('--cache', help='shared SQLite cache; defaults outside the checkout')
    args = parser.parse_args()
    payload = benchmark(args.candidate, args.source, args.output, args.agent_dir,
                        workers=args.workers, limit=args.limit, required_engine=args.engine,
                        resume=args.resume, ray_address=args.ray_address, cache=args.cache)
    print(json.dumps(payload['summary'], indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
