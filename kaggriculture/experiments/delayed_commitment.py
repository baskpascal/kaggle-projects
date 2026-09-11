"""Is the regime worth deciding late, and can it still be executed when it is?

`docs/REGIME_COUNTERFACTUAL.md` left one thing unresolved. The variable that decides whether
the sheep economy pays - whether wool still has a buyer - is invisible on day 6, where the
router commits, and only separates around day 11. That suggests preserving optionality. It
also might not: a tape that builds eighteen pasture tiles by day 11 cannot be started on day
11, and the cost of arriving late at an economy can exceed the value of choosing it correctly.

So this module measures the ceiling before anything is designed. `v006`'s policy reads
`self.tapes[state.plan]` every turn, and the plan is assigned once at turn 144. A bridge
assigns it twice: a prefix plan at 144 and a suffix plan at the switch turn. Every overlay
after that is untouched, so the only thing that differs from `v006` is when the regime is
chosen and what it is chosen to be.

Two families are built.

**The oracle ceiling.** For each prefix, both suffixes are run over the same worlds, and the
per-world choice is composed offline using a label that reads the world's *later* wool market.
This is future information and is legal only here: the composition never runs as an agent, it
exists to answer whether perfect regime knowledge, arriving on day 11 and paying the
transition cost, would beat the current router at all. If it does not, no classifier can
rescue the idea and the direction is closed.

**The observable rule.** The same bridge, but the suffix is decided inside the agent from the
wool price it can actually see at the switch turn. No future label, one threshold, no fitting.

Both are scored against the same worlds, seats, seeds and recorded opponent streams as the
router they are compared with, and the branch that ran is read back from state rather than
assumed from the plan index that was requested.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import statistics
import sys
import tempfile

from experiments.ladder_cohort import cohort
from experiments.ladder_ground_truth import archive_path, load
from experiments.regime_counterfactual import (SOURCE, run_cell, summarise_cell,
                                               yarn_worlds)

CAPTURE = (144, 264, 360, 540, 718)

ROOT = Path(__file__).resolve().parents[1]
ROUTE_BLOCK = '''        if step == ROUTE_STEP:
            shops = observation["town"]["unlocked_shops"]
            state.plan = SHOP_PLANS.get(tuple(shops[:2]), 0)'''
SWITCH_STEP = 264               # day 11, hour 0 - the first turn wool demand separates
COOP_PLAN = 0
SHEEP_PLAN = 3
WOOL_THRESHOLD = 5.0            # "collapsed" in every measurement so far


def _patch(directory, block, source=SOURCE):
    directory = Path(directory)
    if directory.exists():
        shutil.rmtree(directory)
    shutil.copytree(source, directory, ignore=shutil.ignore_patterns('__pycache__'))
    router = directory / 'router_parent.py'
    text = router.read_text()
    if text.count(ROUTE_BLOCK) != 1:
        raise ValueError(f'Expected exactly one routing block in {router}')
    router.write_text(text.replace(ROUTE_BLOCK, block))
    return directory / 'main.py'


def bridged_variant(prefix, suffix, directory, *, switch_step=SWITCH_STEP, source=SOURCE):
    """Commit `prefix` at the routing turn and `suffix` at the switch turn."""
    block = (f'        if step == ROUTE_STEP:\n'
             f'            state.plan = {prefix}  # delayed_commitment prefix\n'
             f'        if step == {switch_step}:\n'
             f'            state.plan = {suffix}  # delayed_commitment suffix')
    return _patch(directory, block, source)


def observable_variant(prefix, directory, *, switch_step=SWITCH_STEP,
                       threshold=WOOL_THRESHOLD, sheep=SHEEP_PLAN, coop=COOP_PLAN,
                       source=SOURCE):
    """Commit `prefix` at the routing turn, then read the wool price and choose.

    The only input is the price the agent can see at the switch turn. No future label, no
    fitted parameter beyond the threshold, and the yarn prior is deliberately not consulted
    here so the rule can be judged on its own.
    """
    block = (f'        if step == ROUTE_STEP:\n'
             f'            state.plan = {prefix}  # delayed_commitment prefix\n'
             f'        if step == {switch_step}:\n'
             f'            _wool = observation["market"]["prices"].get("WOOL", 0)\n'
             f'            state.plan = {sheep} if _wool > {threshold} else {coop}')
    return _patch(directory, block, source)


def wool_revenue(row):
    """Wool actually turned into money, read as stock falling while cash rises."""
    states = [row.get(f'state_{step}') for step in (144, 264, 360, 540, 718)]
    states = [state for state in states if state]
    if len(states) < 2:
        return None
    sold = 0
    for earlier, later in zip(states, states[1:]):
        change = earlier.get('wool', 0) - later.get('wool', 0)
        if change > 0 and later.get('cash', 0) > earlier.get('cash', 0):
            sold += change
    return sold


def compose(healthy, when_healthy, when_collapsed):
    """The oracle: per world, the run that the future wool market says to take."""
    by_id = {row['episode_id']: row for row in when_healthy}
    other = {row['episode_id']: row for row in when_collapsed}
    out = []
    for episode_id, is_healthy in healthy.items():
        chosen = (by_id if is_healthy else other).get(episode_id)
        if chosen is not None:
            out.append(dict(chosen, oracle_label='healthy' if is_healthy else 'collapsed'))
    out.sort(key=lambda row: row['episode_id'])
    return out


def wool_health(timelines, episode_ids, *, day=15, threshold=WOOL_THRESHOLD):
    """The oracle label. Future information, offline only, never an agent input."""
    health = {}
    for episode_id in episode_ids:
        world = timelines.get(str(episode_id)) or timelines.get(episode_id)
        price = None
        for row in (world or {}).get('days', ()):
            if row['day'] == day:
                price = row['market_prices'].get('WOOL')
        if price is not None:
            health[episode_id] = price > threshold
    return health


def enrich(rows):
    for row in rows:
        row['wool_units_sold'] = wool_revenue(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submission', type=int, default=56125200)
    parser.add_argument('--root')
    parser.add_argument('--shops', default='artifacts/wide-loss-shops-v006.json')
    parser.add_argument('--timelines', default='artifacts/wide-loss-timelines-v006.json')
    parser.add_argument('--switch-step', type=int, default=SWITCH_STEP)
    parser.add_argument('--threshold', type=float, default=WOOL_THRESHOLD)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--skip-observable', action='store_true')
    parser.add_argument('--output', required=True)
    arguments = parser.parse_args()

    records = load(arguments.submission, arguments.root)
    archive = archive_path(arguments.submission, arguments.root)
    workdir = Path(tempfile.mkdtemp(prefix='delayed-commitment-'))
    worlds = cohort(archive, records, workdir / 'opponents')
    shops = json.loads(Path(arguments.shops).read_text())
    _, late = yarn_worlds(shops, worlds)
    if arguments.limit:
        late = late[:arguments.limit]
    print(f'no-early-yarn worlds: {len(late)}', file=sys.stderr, flush=True)

    timelines = json.loads(Path(arguments.timelines).read_text())
    healthy = wool_health(timelines, [world['episode_id'] for world in late])

    runs = {}
    plan = [('coop_prefix_to_sheep', COOP_PLAN, SHEEP_PLAN),
            ('sheep_prefix_to_coop', SHEEP_PLAN, COOP_PLAN)]
    for name, prefix, suffix in plan:
        candidate = bridged_variant(prefix, suffix, workdir / name,
                                    switch_step=arguments.switch_step)
        print(f'running {name}', file=sys.stderr, flush=True)
        runs[name] = enrich(run_cell(late, candidate, workdir / f'run-{name}',
                                     workers=arguments.workers, capture=CAPTURE))

    if not arguments.skip_observable:
        for prefix, label in ((COOP_PLAN, 'coop'), (SHEEP_PLAN, 'sheep')):
            name = f'observable_{label}_prefix'
            candidate = observable_variant(prefix, workdir / name,
                                           switch_step=arguments.switch_step,
                                           threshold=arguments.threshold)
            print(f'running {name}', file=sys.stderr, flush=True)
            runs[name] = enrich(run_cell(late, candidate, workdir / f'run-{name}',
                                         workers=arguments.workers, capture=CAPTURE))

    report = {'submission_id': arguments.submission, 'worlds': len(late),
              'switch_step': arguments.switch_step, 'threshold': arguments.threshold,
              'oracle_healthy': {str(k): v for k, v in healthy.items()},
              'summary': {name: summarise_cell(rows, name) for name, rows in runs.items()},
              'runs': runs}
    Path(arguments.output).parent.mkdir(parents=True, exist_ok=True)
    Path(arguments.output).write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(report['summary'], indent=1, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
