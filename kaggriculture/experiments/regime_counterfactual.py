"""The 2x2 that turns the day-7 regime split from an observation into a cause.

`docs/wide-loss-diagnosis-v006.md` found that `v006` runs two economies. At turn 144 the
router reads the first two unlocked shops: a pair containing `YARN_STORE` selects one of the
sheep-and-pasture tapes, and every other pair falls through to plan 0, the coop line. In the
67 cohort worlds the yarn regime wins 0.769 and the coop regime 0.296.

That comparison is observational. The shop context selects the regime, so the two cells that
exist cannot separate "the sheep economy is better" from "worlds with an early yarn store are
easier". The two missing cells are the whole experiment:

    context \\ regime      sheep/pasture        coop
    early YARN            observed  0.769      CELL B - forced
    no early YARN         CELL A - forced      observed  0.296

Both forced cells run the same worlds, the same opponents' recorded streams, the same seats
and the same seeds as the observed ones. The only edit is the single line that assigns
`state.plan` at the routing turn. Overlays that run afterwards - weed repair, advanced sales,
the room guard, the terminal liquidation plan - are untouched, so nothing but the regime
choice differs.

Forcing a plan is not the same as verifying it ran, and a tape can be selected and then be
unable to execute: a yarn tape in a world with no yarn store may simply fail to sell wool.
So every match captures state at the routing turn, at the day the branches are fully
separated, and at the final turn, and the branch is confirmed from those state transitions
rather than from the plan index we asked for.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import statistics
import sys
import tempfile

from arena.parallel import matches
from experiments.ladder_cohort import cohort
from experiments.ladder_ground_truth import archive_path, load

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'versions' / 'v006'
ROUTE_LINE = 'state.plan = SHOP_PLANS.get(tuple(shops[:2]), 0)'
COOP_PLAN = 0
SHEEP_PLAN = 3          # the sheep tape the router maps six of its fifteen yarn pairs to
CAPTURE = (144, 360, 718)


def forced_variant(plan, directory, source=SOURCE):
    """A copy of `v006` whose router always selects `plan`, and nothing else changed."""
    directory = Path(directory)
    if directory.exists():
        shutil.rmtree(directory)
    shutil.copytree(source, directory, ignore=shutil.ignore_patterns('__pycache__'))
    router = directory / 'router_parent.py'
    text = router.read_text()
    if text.count(ROUTE_LINE) != 1:
        raise ValueError(f'Expected exactly one routing line in {router}')
    router.write_text(text.replace(
        ROUTE_LINE, f'state.plan = {plan}  # forced by regime_counterfactual'))
    return directory / 'main.py'


def yarn_worlds(shops, worlds, *, day='7'):
    """Split the cohort by whether `YARN_STORE` is among the first two shops."""
    early, late = [], []
    for world in worlds:
        record = shops.get(str(world['episode_id'])) or {}
        unlocked = (record.get(day) or {}).get('shops') or []
        (early if 'YARN_STORE' in unlocked else late).append(world)
    return early, late


def observed_branch(state):
    """Which economy the recorded state is actually running."""
    if state is None:
        return None
    return 'sheep' if state.get('sheep', 0) >= 10 else 'coop'


def _capture(replay, seat):
    """Sheep, pasture, coop and stock at each captured turn of a lab replay."""
    out = {}
    for turn in replay.get('turns', ()):
        observation = turn['observations'][seat]
        if 'farms' not in observation:
            continue
        farm = observation['farms'][observation['player']]
        tiles = [tile for row in farm['tiles'] for tile in row]
        private = observation['private']
        stock = dict(private['shed'])
        for inventory in private['inventories']:
            for product, units in inventory.items():
                stock[product] = stock.get(product, 0) + units
        step = observation['day'] * 24 + observation['hour']
        out[step] = {
            'cash': farm['money'],
            'sheep': sum(1 for t in tiles if isinstance(t, dict) and t.get('animal') == 'SHEEP'),
            'cow': sum(1 for t in tiles if isinstance(t, dict) and t.get('animal') == 'COW'),
            'goose': sum(1 for t in tiles if isinstance(t, dict) and t.get('animal') == 'GOOSE'),
            'pasture': sum(1 for t in tiles if isinstance(t, dict) and t.get('kind') == 'PASTURE'),
            'coop': sum(1 for t in tiles if isinstance(t, dict) and t.get('kind') == 'COOP'),
            'stock': {p: v for p, v in stock.items() if v},
            'stock_units': sum(stock.values()),
            'wool': stock.get('WOOL', 0)}
    return out


def run_cell(worlds, candidate, directory, *, workers=6, backend='official',
             capture=CAPTURE):
    """One cell of the 2x2: this candidate, on these worlds, with state captured."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    jobs, replays = [], []
    for world in worlds:
        path = directory / f"replay-{world['episode_id']}.json"
        replays.append(path)
        jobs.append(dict(candidate=str(candidate), opponent=world['opponent_agent'],
                         seed=world['seed'], seat=world['seat'], backend=backend,
                         evidence_profile='full', replay=str(path),
                         replay_steps=list(capture)))
    rows = []
    for world, path, row in zip(worlds, replays, matches(jobs, workers, ordered=True)):
        ours, theirs = row['money'], row['opponent_money']
        captured = {}
        if path.is_file():
            captured = _capture(json.loads(path.read_text()), world['seat'])
        midgame = captured.get(360)
        rows.append({
            'episode_id': world['episode_id'], 'seat': world['seat'], 'seed': world['seed'],
            'opponent_team': world['opponent_team'],
            'score': 1.0 if ours > theirs else (0.0 if ours < theirs else 0.5),
            'our_money': ours, 'opponent_money': theirs, 'margin': ours - theirs,
            'ladder_margin': world['ladder_our_money'] - world['ladder_opponent_money'],
            'ladder_outcome': world['ladder_outcome'],
            'branch_executed': observed_branch(midgame),
            **{f'state_{step}': captured.get(step) for step in capture},
            'failures': list(row['failures']) + list(row['opponent_failures'])})
    return rows


def summarise_cell(rows, label):
    scored = [row for row in rows if row['score'] is not None]
    wins = sum(1 for row in scored if row['score'] == 1.0)
    losses = sum(1 for row in scored if row['score'] == 0.0)
    ties = sum(1 for row in scored if row['score'] == 0.5)
    gained = [row for row in scored if row['ladder_outcome'] is not None
              and row['score'] > row['ladder_outcome']]
    regressed = [row for row in scored if row['ladder_outcome'] is not None
                 and row['score'] < row['ladder_outcome']]
    terminal = [row['state_718'] for row in rows if row['state_718']]
    branches = {}
    for row in rows:
        branches[row['branch_executed']] = branches.get(row['branch_executed'], 0) + 1
    summary = {
        'cell': label, 'worlds': len(rows),
        'wins': wins, 'losses': losses, 'ties': ties,
        'win_rate': (wins + .5 * ties) / len(scored) if scored else None,
        'ladder_win_rate': (sum(row['ladder_outcome'] for row in scored
                                if row['ladder_outcome'] is not None)
                            / len([r for r in scored if r['ladder_outcome'] is not None])
                            if scored else None),
        'gained': len(gained), 'regressed': len(regressed),
        'net_worlds': len(gained) - len(regressed),
        'gained_episodes': [row['episode_id'] for row in gained],
        'regressed_episodes': [row['episode_id'] for row in regressed],
        'branch_executed': branches,
        'failures': sum(len(row['failures']) for row in rows)}
    if scored:
        summary['paired_margin_delta_median'] = statistics.median(
            row['margin'] - row['ladder_margin'] for row in scored)
        summary['paired_margin_delta_mean'] = statistics.mean(
            row['margin'] - row['ladder_margin'] for row in scored)
        summary['terminal_cash_median'] = statistics.median(row['our_money'] for row in scored)
    if terminal:
        # Turn 718 is the last acting turn and `liquidate` runs *on* it, so this is the
        # inventory entering the terminal sale, not inventory left stranded after it.
        # Terminal cash is the authoritative endpoint and is reported separately.
        summary['stock_entering_liquidation_median'] = statistics.median(
            state['stock_units'] for state in terminal)
        summary['stock_entering_liquidation_max'] = max(
            state['stock_units'] for state in terminal)
    midgame = [row['state_360'] for row in rows if row['state_360']]
    if midgame:
        for key in ('sheep', 'cow', 'goose', 'pasture', 'coop', 'wool'):
            summary[f'day15_{key}_median'] = statistics.median(state[key] for state in midgame)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submission', type=int, default=56125200)
    parser.add_argument('--root')
    parser.add_argument('--shops', default='artifacts/wide-loss-shops-v006.json')
    parser.add_argument('--sheep-plan', type=int, default=SHEEP_PLAN)
    parser.add_argument('--coop-plan', type=int, default=COOP_PLAN)
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--limit', type=int, help='smoke only: N worlds per cell')
    parser.add_argument('--output', required=True)
    arguments = parser.parse_args()

    records = load(arguments.submission, arguments.root)
    archive = archive_path(arguments.submission, arguments.root)
    workdir = Path(tempfile.mkdtemp(prefix='regime-counterfactual-'))
    worlds = cohort(archive, records, workdir / 'opponents')
    shops = json.loads(Path(arguments.shops).read_text())
    early, late = yarn_worlds(shops, worlds)
    if arguments.limit:
        early, late = early[:arguments.limit], late[:arguments.limit]
    print(f'early-yarn worlds: {len(early)} | no-early-yarn worlds: {len(late)}',
          file=sys.stderr, flush=True)

    sheep = forced_variant(arguments.sheep_plan, workdir / 'forced-sheep')
    coop = forced_variant(arguments.coop_plan, workdir / 'forced-coop')

    cells = {}
    print('cell A: forcing sheep in no-early-yarn worlds', file=sys.stderr, flush=True)
    cells['A_no_yarn_forced_sheep'] = run_cell(late, sheep, workdir / 'A',
                                               workers=arguments.workers)
    print('cell B: forcing coop in early-yarn worlds', file=sys.stderr, flush=True)
    cells['B_yarn_forced_coop'] = run_cell(early, coop, workdir / 'B',
                                           workers=arguments.workers)

    report = {'submission_id': arguments.submission,
              'sheep_plan': arguments.sheep_plan, 'coop_plan': arguments.coop_plan,
              'observed': {
                  'yarn_sheep': {'worlds': len(early),
                                 'win_rate': sum(1 for w in early if w['ladder_outcome'] == 1)
                                             / len(early) if early else None},
                  'no_yarn_coop': {'worlds': len(late),
                                   'win_rate': sum(1 for w in late if w['ladder_outcome'] == 1)
                                               / len(late) if late else None}},
              'summary': {name: summarise_cell(rows, name) for name, rows in cells.items()},
              'cells': cells}
    Path(arguments.output).parent.mkdir(parents=True, exist_ok=True)
    Path(arguments.output).write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({'observed': report['observed'], 'summary': report['summary']},
                     indent=1, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
