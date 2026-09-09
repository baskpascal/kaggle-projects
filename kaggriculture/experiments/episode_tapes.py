"""Turn a daily top-episodes dump into a labelled library of action tapes.

The hosts publish the day's highest-rated episodes as a Kaggle dataset and have
said in the forum that building a submission from public replays is allowed. Each
episode is a complete 720-state recording of two leaderboard agents, so the action
stream of either seat is a tape produced by a live agent on the real ladder. That
is a strictly better donor than anything reconstructed locally: it is the current
meta rather than an approximation of it, and it arrives with the final money of
both seats, so the outcome that produced the tape travels with the tape.

What a recorded episode is not is a measurement. It was played on the ladder's own
seed against one specific opponent, and a tape replayed on a fresh seed meets a
different town. Scoring belongs to the arena; this module only extracts and labels.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import zipfile

SCHEMA = 1
TURNS = 719


def tape_digest(actions):
    """Identity of a tape is its action stream, so duplicates cannot inflate a library."""
    return hashlib.sha256(json.dumps(actions, sort_keys=True, separators=(',', ':'))
                          .encode()).hexdigest()


def read_episode(path):
    """Both seats of one episode, or the reason the file cannot be used."""
    try:
        if isinstance(path, tuple):
            archive, member = path
            with zipfile.ZipFile(archive) as source:
                episode = json.loads(source.read(member))
            source_name = f'{archive}!{member}'
        else:
            episode = json.loads(Path(path).read_text())
            source_name = str(path)
    except Exception as exc:                       # a truncated dump is not evidence
        return {'path': str(path), 'error': f'{type(exc).__name__}: {exc}'[:160]}
    steps = episode.get('steps')
    if not isinstance(steps, list) or len(steps) != TURNS + 1:
        return {'path': str(path), 'error': f'expected {TURNS + 1} states'}
    info = episode.get('info') or {}
    teams = info.get('TeamNames') or [None, None]
    rewards = episode.get('rewards') or [None, None]
    seats = []
    for seat in (0, 1):
        # Kaggle's step 0 is the initial state with a placeholder action. Entry k + 1
        # stores the action produced from state k and the state after applying it. The
        # executable 719-turn stream is therefore steps[1:], not steps[:-1].
        actions = [steps[k + 1][seat].get('action') for k in range(TURNS)]
        if any(action is None for action in actions):
            return {'path': str(path), 'error': f'seat {seat} is missing actions'}
        seats.append({
            'episode': info.get('EpisodeId'), 'seed': info.get('seed'), 'seat': seat,
            'team': teams[seat], 'opponent_team': teams[1 - seat],
            'money': rewards[seat], 'opponent_money': rewards[1 - seat],
            'status': (episode.get('statuses') or [None, None])[seat],
            'engine': episode.get('module_version'),
            'sha256': tape_digest(actions), 'actions': actions,
        })
    return {'path': source_name, 'seats': seats}


def episode_paths(source):
    """Return process-safe references from an extracted directory or Kaggle zip."""
    source = Path(source)
    if source.is_dir():
        return sorted(str(path) for path in source.glob('*.json'))
    if source.is_file() and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            return [(str(source), name) for name in sorted(archive.namelist())
                    if name.lower().endswith('.json') and not name.endswith('/')]
    raise ValueError(f'Expected an episode directory or zip archive: {source}')


def collect(source, output, *, workers=8, winners_only=False):
    """Every distinct action stream in a dump, ordered by action digest."""
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    paths = episode_paths(source)
    if not paths:
        raise ValueError(f'No episodes under {source}')
    library, errors, seen = [], [], {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(read_episode, paths, chunksize=1):
            if 'error' in result:
                errors.append(result)
                continue
            for row in result['seats']:
                if winners_only and not (row['money'] or 0) > (row['opponent_money'] or 0):
                    continue
                previous = seen.get(row['sha256'])
                if previous is not None:
                    # A repeated stream is one agent replaying a tape, which is
                    # evidence about the meta rather than a second sample.
                    previous['repeats'] += 1
                    continue
                row['repeats'] = 0
                seen[row['sha256']] = row
                library.append(row)
    library.sort(key=lambda row: row['sha256'])
    payload = {'schema_version': SCHEMA, 'source': str(source),
               'created_at': datetime.now(timezone.utc).isoformat(),
               'episodes': len(paths), 'errors': errors[:20], 'error_count': len(errors),
               'diagnostic_fields': ['money', 'opponent_money'],
               'selection_rule': 'action digest; optional win filter',
               'winners_only': winners_only, 'tapes': library}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload) + '\n', encoding='utf-8')
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True,
                        help='directory of episode JSON files or an official daily zip')
    parser.add_argument('--output', required=True)
    parser.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) // 2))
    parser.add_argument('--winners-only', action='store_true')
    args = parser.parse_args()
    payload = collect(args.source, args.output, workers=args.workers,
                      winners_only=args.winners_only)
    best = payload['tapes'][0] if payload['tapes'] else {}
    print(json.dumps({'episodes': payload['episodes'], 'errors': payload['error_count'],
                      'tapes': len(payload['tapes']), 'first_money_diagnostic': best.get('money'),
                      'first_team': best.get('team')}, indent=2))


if __name__ == '__main__':
    main()
