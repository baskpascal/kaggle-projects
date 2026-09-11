"""An evaluator built from the worlds that actually decide our rating.

The panel in `eval/panel.py` is organised around leaderboard *rank*: top ten, ranks ten to
thirty, ranks thirty to a hundred. That was the right question while the goal was stated as
beating the top of the board. The ladder does not work that way. It pairs by rating
proximity, and `docs/LADDER_GROUND_TRUTH.md` measured the consequence: across 204 real
episodes `v006` never once met an opponent above 2800, won 0.917 below 2400, 0.614 between
2400 and 2600, and 0.388 between 2600 and 2800. The band that decides our rating is the one
next to us, and the panel contains almost none of it.

The second half of the same measurement is the scale. In the 2600-2800 band the median
absolute margin is 102 coins on typical money of 91,813 - a tenth of a per cent - and half
the games are decided by under 100 coins. Every objective this repository has optimised
against moves margins by tens of thousands, which is a ruler roughly 800 times too coarse
to see what the ladder is resolving.

So this cohort is built from the archived replays of our own band episodes. Each world
contributes the opponent's recorded action stream as a replayer, the episode's own seed,
the seat we actually occupied, and the outcome the ladder actually recorded. A candidate is
scored on exactly those worlds.

**What this instrument is, and is not.** `experiments/ladder_reproduction.py` established
that the incumbent reproduces its ladder result on these worlds exactly, because it is
deterministic and the opponent stream is fixed. That makes the cohort perfectly calibrated
for the incumbent by construction, and it means the cohort cannot predict a rating for a
*changed* candidate: the moment a candidate diverges, the opponent's recorded actions stop
being a response to the world in front of it. What the cohort does measure, and what
nothing else here measures, is which real games a change flips and in which direction, at
the margins those games were actually decided by. It is a flip detector at ladder scale,
and the headline number is the count of worlds gained against the count regressed - not a
mean margin, which at this scale is dominated by the handful of blowouts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile

from arena.parallel import matches
from experiments.ladder_ground_truth import archive_path, archived, load, outcome
from experiments.tape_agent import build as build_tape_agent
from experiments.top_panel import episode_streams

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BAND = (2600.0, 2800.0)


def cohort(archive, records, directory, *, band=DEFAULT_BAND, episodes=None):
    """One world per archived band episode, carrying what the ladder actually recorded."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    low, high = band
    available = archived(archive) if episodes is None else set(int(e) for e in episodes)
    worlds = []
    for record in records:
        identifier = record['episode_id']
        if identifier not in available:
            continue
        rating = record['opponent_initial_score']
        if rating is None or not low <= rating < high:
            continue
        streams = episode_streams(archive, identifier)
        seat = record['seat']
        opponent = next(stream for stream in streams if stream['seat'] != seat)
        path = directory / f'{identifier}-opponent-seat{opponent["seat"]}.py'
        if not path.exists():
            build_tape_agent(opponent, path, f'episode {identifier} seat {opponent["seat"]}')
        worlds.append({
            'episode_id': identifier, 'seed': streams[0]['seed'], 'seat': seat,
            'opponent_agent': str(path), 'opponent_team': record['opponent_team'],
            'opponent_rating': rating, 'opponent_stream_sha256': opponent.get('sha256'),
            'ladder_our_money': record['our_reward'],
            'ladder_opponent_money': record['opponent_reward'],
            'ladder_outcome': outcome(record)})
    worlds.sort(key=lambda world: world['episode_id'])
    return worlds


def evaluate(worlds, candidate, *, workers=6, backend='official'):
    """Score a candidate on every world, and record what it did to the ladder result."""
    jobs = [dict(candidate=str(candidate), opponent=world['opponent_agent'],
                 seed=world['seed'], seat=world['seat'], backend=backend,
                 evidence_profile='score') for world in worlds]
    rows = []
    for world, row in zip(worlds, matches(jobs, workers, ordered=True)):
        ours, theirs = row['money'], row['opponent_money']
        score = 1.0 if ours > theirs else (0.0 if ours < theirs else 0.5)
        failures = list(row['failures']) + list(row['opponent_failures'])
        rows.append(dict(world, score=score, our_money=ours, opponent_money=theirs,
                         margin=ours - theirs,
                         ladder_margin=world['ladder_our_money'] - world['ladder_opponent_money'],
                         flipped=None if world['ladder_outcome'] is None
                                 else score - world['ladder_outcome'],
                         failures=failures))
    return rows


def summary(rows):
    """Flips first, because at a hundred coins a mean margin is noise plus two blowouts."""
    scored = [row for row in rows if row['score'] is not None]
    gained = [row for row in scored if row['flipped'] is not None and row['flipped'] > 0]
    regressed = [row for row in scored if row['flipped'] is not None and row['flipped'] < 0]
    report = {
        'worlds': len(rows),
        'failures': sum(len(row['failures']) for row in rows),
        'win_rate': sum(row['score'] for row in scored) / len(scored) if scored else None,
        'ladder_win_rate': (sum(row['ladder_outcome'] for row in scored
                                if row['ladder_outcome'] is not None)
                            / len([r for r in scored if r['ladder_outcome'] is not None])
                            if scored else None),
        'gained': len(gained), 'regressed': len(regressed),
        'net_worlds': len(gained) - len(regressed),
        'gained_episodes': [row['episode_id'] for row in gained],
        'regressed_episodes': [row['episode_id'] for row in regressed]}
    if scored:
        report['margin_median'] = statistics.median(row['margin'] for row in scored)
        report['ladder_margin_median'] = statistics.median(
            row['ladder_margin'] for row in scored)
        report['abs_margin_median'] = statistics.median(abs(row['margin']) for row in scored)
        decided = [row for row in scored if abs(row['ladder_margin']) < 1000]
        report['worlds_decided_under_1000'] = len(decided)
        if decided:
            report['win_rate_in_close_worlds'] = (
                sum(row['score'] for row in decided) / len(decided))
    return report


def interval(rows, samples=5000, seed=771):
    """Bootstrap over worlds, so the interval says how much the cohort itself can support."""
    from eval.metrics import blocked_interval
    return blocked_interval([{'seed': row['seed'], 'score': row['score']} for row in rows],
                            samples=samples, seed=seed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submission', type=int, default=56125200,
                        help='the submission whose ladder episodes define the cohort')
    parser.add_argument('--root', help='where the episode cache and replay archive live')
    parser.add_argument('--candidate', default='versions/v006/main.py')
    parser.add_argument('--band', nargs=2, type=float, default=list(DEFAULT_BAND))
    parser.add_argument('--limit', type=int, help='smoke only: first N worlds')
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--backend', default='official', choices=('fast', 'official'))
    parser.add_argument('--output', required=True)
    arguments = parser.parse_args()

    archive = archive_path(arguments.submission, arguments.root)
    records = load(arguments.submission, arguments.root)
    workdir = Path(tempfile.mkdtemp(prefix='ladder-cohort-'))
    worlds = cohort(archive, records, workdir, band=tuple(arguments.band))
    if arguments.limit:
        worlds = worlds[:arguments.limit]
    if not worlds:
        raise SystemExit('No archived worlds in that band; fetch replays first.')

    print(f'cohort: {len(worlds)} worlds, band {arguments.band}', file=sys.stderr, flush=True)
    rows = evaluate(worlds, arguments.candidate, workers=arguments.workers,
                    backend=arguments.backend)
    report = {'submission_id': arguments.submission, 'candidate': arguments.candidate,
              'band': arguments.band, 'summary': summary(rows),
              'interval': interval(rows), 'worlds': rows}
    Path(arguments.output).write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({'summary': report['summary'], 'interval': report['interval']},
                     indent=1, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
