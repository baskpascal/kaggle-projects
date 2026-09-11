"""One-line, world-independent changes to `v006`'s market overlays, measured at ladder scale.

`docs/DELAYED_COMMITMENT.md` closed late adaptation of *build* decisions: land, pasture, coop
and herd accumulate physical state, so a regime reached by a bridge on day 11 scores 0.000
where the same regime started on day 6 scores 0.471. It explicitly did not close market
decisions, which accumulate no physical state and are switchable on any turn.

The arithmetic says that is where to look. A uniform hundred coins turns eight of the
sixty-seven cohort worlds, against the three that a perfect regime oracle would be worth, and
the median ladder game in this band is decided by 102 coins.

Two candidates were rejected before being built, by measurement rather than by argument. The
terminal dump realises 0.9941 of the quoted value of the nine units it sells, for a median
shortfall of seven coins. And no tape ever emits more than `MAX_ORDERS` market orders, so the
truncation cannot be costing anything.

What remains is the throttle inside `advance_sales`. That overlay exists to bring a sale one
turn forward, which in a shared town is the whole contention mechanism, and it declines to do
so on every fourth turn. This module removes exactly that clause and measures the result on
the worlds the ladder actually decided, with flips as the headline rather than mean margin.

The patch is verified textually before the run, the way the regime patches are: a source that
does not contain the expected guard exactly once is refused rather than silently copied.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import statistics
import sys
import tempfile

from experiments.ladder_cohort import cohort, evaluate, interval, summary
from experiments.ladder_ground_truth import archive_path, load

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'versions' / 'v006'
ADVANCE_GUARD = 'if next_step > LAST_STEP or next_step % 72 == 0 or step % 4 == 0:'
RELAXED_GUARD = 'if next_step > LAST_STEP or next_step % 72 == 0:'


def patched(directory, old, new, source=SOURCE, filename='router_parent.py'):
    """A copy of the agent with one clause replaced, refused if the clause is not unique."""
    directory = Path(directory)
    if directory.exists():
        shutil.rmtree(directory)
    shutil.copytree(source, directory, ignore=shutil.ignore_patterns('__pycache__'))
    target = directory / filename
    text = target.read_text()
    if text.count(old) != 1:
        raise ValueError(f'Expected exactly one occurrence of the clause in {target}')
    target.write_text(text.replace(old, new))
    return directory / 'main.py'


def relaxed_advance_variant(directory, source=SOURCE):
    """`advance_sales` without the every-fourth-turn throttle, and nothing else changed."""
    return patched(directory, ADVANCE_GUARD, RELAXED_GUARD, source)


def paired(rows, baseline):
    """Flip bookkeeping against a baseline run of the same worlds, not against the ladder."""
    reference = {row['episode_id']: row for row in baseline}
    gained, regressed, deltas = [], [], []
    for row in rows:
        other = reference.get(row['episode_id'])
        if other is None:
            continue
        deltas.append(row['margin'] - other['margin'])
        if row['score'] > other['score']:
            gained.append(row['episode_id'])
        elif row['score'] < other['score']:
            regressed.append(row['episode_id'])
    out = {'compared': len(deltas), 'gained': len(gained), 'regressed': len(regressed),
           'net_worlds': len(gained) - len(regressed),
           'gained_episodes': gained, 'regressed_episodes': regressed}
    if deltas:
        out['margin_delta_median'] = statistics.median(deltas)
        out['margin_delta_mean'] = statistics.mean(deltas)
        out['margin_delta_positive'] = sum(1 for delta in deltas if delta > 0)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submission', type=int, default=56125200)
    parser.add_argument('--root')
    parser.add_argument('--band', nargs=2, type=float, default=[2600., 2800.])
    parser.add_argument('--baseline', default='versions/v006/main.py')
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--backend', default='official', choices=('fast', 'official'))
    parser.add_argument('--limit', type=int)
    parser.add_argument('--output', required=True)
    arguments = parser.parse_args()

    records = load(arguments.submission, arguments.root)
    archive = archive_path(arguments.submission, arguments.root)
    workdir = Path(tempfile.mkdtemp(prefix='market-overlay-'))
    worlds = cohort(archive, records, workdir / 'opponents', band=tuple(arguments.band))
    if arguments.limit:
        worlds = worlds[:arguments.limit]
    if not worlds:
        raise SystemExit('No archived worlds in that band; fetch replays first.')
    print(f'{len(worlds)} worlds, band {arguments.band}', file=sys.stderr, flush=True)

    candidate = relaxed_advance_variant(workdir / 'relaxed-advance')
    print('baseline', file=sys.stderr, flush=True)
    base = evaluate(worlds, arguments.baseline, workers=arguments.workers,
                    backend=arguments.backend)
    print('relaxed advance_sales', file=sys.stderr, flush=True)
    rows = evaluate(worlds, candidate, workers=arguments.workers, backend=arguments.backend)

    report = {'submission_id': arguments.submission, 'band': arguments.band,
              'worlds': len(worlds),
              'baseline': {'summary': summary(base), 'interval': interval(base)},
              'candidate': {'summary': summary(rows), 'interval': interval(rows)},
              'paired_against_baseline': paired(rows, base),
              'rows': rows, 'baseline_rows': base}
    Path(arguments.output).parent.mkdir(parents=True, exist_ok=True)
    Path(arguments.output).write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: report[k] for k in
                      ('worlds', 'baseline', 'candidate', 'paired_against_baseline')},
                     indent=1, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
