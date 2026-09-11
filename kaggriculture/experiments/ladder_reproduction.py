"""Does the local lab predict the ladder, and does it even reproduce it?

`ladder_ground_truth.py` established that `v006` wins 0.388 against opponents rated
2600-2800, on margins under one per cent, while the local panel scores the same agent
against margins of 81,000. The hypothesis that follows is deliberately weak:

    H1: local_eval is conditionally biased relative to live ladder

Weak, because the divergence has several candidate causes that the panel cannot separate -
opponent distribution, seat, engine version, matchmaking, or an outright execution
difference between Kaggle's environment and ours. This module runs the two levels that
separate them, in the order that makes the second interpretable.

**Level A, engine fidelity.** Replay each real episode's *own two recorded action streams*
at its own recorded seed. Neither agent decides anything: both replay what they actually
did on Kaggle. If our engine is the same engine, the terminal money must come back exactly
equal to the reward Kaggle published. If it does not, the discrepancy is environmental and
every local number in this repository is suspect. This level answers a question about the
simulator, not about the agent, so it is run first and on both backends.

**Level B, predictive validity.** Only for the episodes that passed level A: put the live
`v006` back in its own seat, at the same seed, against the opponent's recorded stream, and
compare `predicted_local_outcome` against `actual_ladder_outcome`. Agreement means the
local setup does predict the ladder and the panel's problem is which opponents it contains.
Systematic disagreement in one direction - we win locally and lost live - means the bias is
in the play itself, not in the panel's roster.

Level B carries a caveat that must not be forgotten when reading its output: a recorded
stream is fixed, and the opponent that produced it was reactive. Once `v006` diverges from
what it did on the ladder, the opponent is no longer responding to the world it faces. The
level is therefore evidence about agreement rates, not a faithful rerun of the match, and
it is reported next to level A rather than instead of it.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile

from arena.parallel import matches
from experiments.ladder_ground_truth import archive_path, archived, load
from experiments.tape_agent import build as build_tape_agent
from experiments.top_panel import episode_streams, reproduce

ROOT = Path(__file__).resolve().parents[1]


def winner(pair):
    return 0 if pair[0] > pair[1] else (1 if pair[1] > pair[0] else None)


def level_b(archive, episodes, records, candidate, directory, *, workers=8,
            backend='official'):
    """The live candidate, back in its own seat, against the opponent's recorded stream."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    seats = {record['episode_id']: record['seat'] for record in records}
    jobs, meta = [], []
    for episode in episodes:
        streams = episode_streams(archive, episode)
        ours = seats.get(episode)
        if ours is None:
            continue
        opponent = next(stream for stream in streams if stream['seat'] != ours)
        path = directory / f'{episode}-opponent-seat{opponent["seat"]}.py'
        if not path.exists():
            build_tape_agent(opponent, path, f'episode {episode} seat {opponent["seat"]}')
        jobs.append(dict(candidate=str(candidate), opponent=str(path),
                         seed=streams[0]['seed'], seat=ours, backend=backend,
                         evidence_profile='score'))
        mine = next(stream for stream in streams if stream['seat'] == ours)
        meta.append((episode, ours, mine['money'], opponent['money']))
    rows = []
    for (episode, seat, ladder_ours, ladder_theirs), row in zip(
            meta, matches(jobs, workers, ordered=True)):
        actual = winner([ladder_ours, ladder_theirs])
        predicted = winner([row['money'], row['opponent_money']])
        rows.append({
            'episode_id': episode, 'seat': seat, 'seed': row['seed'],
            'ladder_our_money': ladder_ours, 'ladder_opponent_money': ladder_theirs,
            'local_our_money': row['money'], 'local_opponent_money': row['opponent_money'],
            'actual_ladder_outcome': 'win' if actual == 0 else ('loss' if actual == 1 else 'tie'),
            'predicted_local_outcome': 'win' if predicted == 0 else ('loss' if predicted == 1 else 'tie'),
            'agrees': actual == predicted,
            'failures': [row['failures'], row['opponent_failures']]})
    return rows


def summarise(fidelity, predictions):
    exact = [proof for proof in fidelity.values() if proof['exact']]
    report = {'level_a': {'episodes': len(fidelity), 'exact': len(exact),
                          'mismatched': [proof['episode'] for proof in fidelity.values()
                                         if not proof['exact']]}}
    if predictions:
        agree = [row for row in predictions if row['agrees']]
        wrong_way = [row for row in predictions
                     if row['predicted_local_outcome'] == 'win'
                     and row['actual_ladder_outcome'] == 'loss']
        report['level_b'] = {
            'episodes': len(predictions),
            'agreement': len(agree) / len(predictions),
            'local_says_win_ladder_says_loss': len(wrong_way),
            'local_says_loss_ladder_says_win': sum(
                1 for row in predictions
                if row['predicted_local_outcome'] == 'loss'
                and row['actual_ladder_outcome'] == 'win'),
            'ladder_margin_median': statistics.median(
                row['ladder_our_money'] - row['ladder_opponent_money'] for row in predictions),
            'local_margin_median': statistics.median(
                row['local_our_money'] - row['local_opponent_money'] for row in predictions)}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submission', type=int, required=True)
    parser.add_argument('--root', help='where the episode cache and archive live')
    parser.add_argument('--candidate', default='versions/v006/main.py')
    parser.add_argument('--limit', type=int, help='smoke only: first N archived episodes')
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--skip-level-b', action='store_true')
    parser.add_argument('--output', required=True)
    arguments = parser.parse_args()

    archive = archive_path(arguments.submission, arguments.root)
    episodes = sorted(archived(archive))
    if arguments.limit:
        episodes = episodes[:arguments.limit]
    if not episodes:
        raise SystemExit(f'No archived replays in {archive}; fetch them first.')
    records = load(arguments.submission, arguments.root)

    workdir = Path(tempfile.mkdtemp(prefix='ladder-reproduction-'))
    print(f'level A: {len(episodes)} episodes', file=sys.stderr, flush=True)
    fidelity = reproduce(archive, episodes, workdir, workers=arguments.workers)

    predictions = []
    passed = [episode for episode in episodes if fidelity[episode]['exact']]
    if passed and not arguments.skip_level_b:
        print(f'level B: {len(passed)} episodes that reproduced exactly',
              file=sys.stderr, flush=True)
        predictions = level_b(archive, passed, records, arguments.candidate, workdir,
                              workers=arguments.workers)

    report = {'submission_id': arguments.submission, 'candidate': arguments.candidate,
              'summary': summarise(fidelity, predictions),
              'fidelity': fidelity, 'predictions': predictions}
    Path(arguments.output).write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(report['summary'], indent=1, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
