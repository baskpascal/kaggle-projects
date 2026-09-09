"""Re-route the public 0909 shop tapes on the pair of shops the town actually unlocks.

The `yhay81/shop-router-0909` bundle carries thirteen complete 719-turn tapes and a
routing table that reads the first two unlocked shops at step 144. Every key in that
table contains `YARN_STORE`; shops are drawn with replacement from eight kinds, so about
three quarters of games never match a key and fall back to plan 0. Whether that fallback
costs anything is an empirical question, and this module answers it by playing every tape
in every world.

The prefix up to step 144 is plan 0 for every route, because the agent only reads the
table at that step. Both shops unlock inside that shared prefix (end of day 2 and end of
day 5), so the pair a game presents does not depend on which tape we would go on to play.
That is what makes the join legal: one probe leg records the pair per (opponent, seed,
seat), and the thirteen forced-plan legs are scored against it.

Selection is per world and by score against a panel, never by mean money: the ladder
counts wins. A cell with few games is reported and not routed - a table fitted on three
games is a table fitted on noise.
"""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import re
import statistics

from arena.parallel import matches
from arena.seeds import parse_seeds, validate_seeds

ROUTE_LINE = re.compile(r'^(\s*)state\.plan = SHOP_PLANS\.get\(tuple\(shops\[:2\]\), 0\)\s*$',
                        re.MULTILINE)
PLANS = 13


def build_variants(bundle, directory):
    """One agent per forced route, sharing the bundle's data files by symlink."""
    bundle, directory = Path(bundle).resolve(), Path(directory)
    source = (bundle / 'main.py').read_text()
    if not ROUTE_LINE.search(source):
        raise ValueError('The bundle does not carry the expected routing line')
    paths = {}
    for plan in range(PLANS):
        folder = directory / f'plan{plan}'
        folder.mkdir(parents=True, exist_ok=True)
        for data in bundle.glob('*.json'):
            link = folder / data.name
            if not link.exists():
                link.symlink_to(data)
        forced = ROUTE_LINE.sub(rf'\g<1>state.plan = {plan}', source)
        (folder / 'main.py').write_text(forced)
        paths[plan] = str(folder / 'main.py')
    return paths


def plan_jobs(paths, opponents, seeds, replay_dir):
    """Thirteen scored legs; the plan 0 leg also snapshots the town at the routing step."""
    jobs, meta = [], []
    for plan, path in sorted(paths.items()):
        for name, opponent in sorted(opponents.items()):
            for seed in seeds:
                for seat in (0, 1):
                    job = dict(candidate=path, opponent=opponent, seed=seed, seat=seat,
                               evidence_profile='score')
                    if plan == 0:
                        snapshot = Path(replay_dir) / f'{name}-{seed}-{seat}.json'
                        job.update(evidence_profile='full', replay=str(snapshot),
                                   replay_steps=[144])
                    jobs.append(job)
                    meta.append((plan, name, seed, seat))
    return jobs, meta


def shops_at_route(path):
    payload = json.loads(Path(path).read_text())
    for turn in payload['turns']:
        if turn['step'] == 144:
            return tuple(turn['observations'][0]['town']['unlocked_shops'][:2])
    raise ValueError(f'{path} holds no step 144 snapshot')


def run(bundle, opponents, seeds, *, workers, output):
    output = Path(output)
    if output.exists():
        raise ValueError(f'{output} already holds a run; reports are written once')
    output.mkdir(parents=True)
    paths = build_variants(bundle, output / 'agents')
    jobs, meta = plan_jobs(paths, opponents, seeds, output / 'snapshots')
    rows = []
    for index, ((plan, name, seed, seat), row) in enumerate(
            zip(meta, matches(jobs, workers, ordered=True))):
        rows.append({'plan': plan, 'opponent': name, 'seed': seed, 'seat': seat,
                     'score': row['score'], 'margin': row['margin'], 'money': row['money'],
                     'failures': row['failures'], 'opponent_failures': row['opponent_failures']})
        if (index + 1) % 200 == 0:
            print(f'{index + 1}/{len(jobs)} games', flush=True)
    worlds = {}
    for _, name, seed, seat in meta:
        key = (name, seed, seat)
        if key not in worlds:
            worlds[key] = shops_at_route(output / 'snapshots' / f'{name}-{seed}-{seat}.json')
    for row in rows:
        row['shops'] = list(worlds[(row['opponent'], row['seed'], row['seat'])])
    with (output / 'games.jsonl').open('w') as handle:
        for row in rows:
            handle.write(json.dumps(row) + '\n')
    report = analyse(rows)
    (output / 'report.json').write_text(json.dumps(report, indent=2))
    return rows, report


def wilson(wins, games, z=1.96):
    if not games:
        return None
    p = wins / games
    denominator = 1 + z * z / games
    centre = (p + z * z / (2 * games)) / denominator
    spread = z * math.sqrt(p * (1 - p) / games + z * z / (4 * games * games)) / denominator
    return [centre - spread, centre + spread]


def analyse(rows):
    """Per world: how each plan scores, and what routing to the best of them would buy."""
    by_plan = defaultdict(list)
    by_world = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_plan[row['plan']].append(row['score'])
        by_world[tuple(row['shops'])][row['plan']].append(row['score'])
    overall = {str(plan): {'games': len(scores), 'score_rate': statistics.mean(scores)}
               for plan, scores in sorted(by_plan.items())}
    worlds = {}
    for shops, plans in sorted(by_world.items()):
        table = {str(plan): {'games': len(scores), 'score_rate': statistics.mean(scores)}
                 for plan, scores in sorted(plans.items())}
        best = max(plans, key=lambda plan: statistics.mean(plans[plan]))
        worlds['|'.join(shops)] = {
            'games_per_plan': len(plans[0]),
            'plan0_score_rate': statistics.mean(plans[0]),
            'best_plan': best,
            'best_score_rate': statistics.mean(plans[best]),
            'best_minus_plan0': statistics.mean(plans[best]) - statistics.mean(plans[0]),
            'per_plan': table,
        }
    return {'games': len(rows), 'plans': overall, 'worlds': worlds}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--bundle', default='opponents/public/yhay_router_0909')
    parser.add_argument('--opponents', required=True,
                        help='comma-separated name=path pairs, or bare paths')
    parser.add_argument('--seeds', required=True)
    parser.add_argument('--split', default='dev')
    parser.add_argument('--workers', type=int, default=12)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    opponents = {}
    for item in args.opponents.split(','):
        name, _, path = item.partition('=')
        opponents[name if path else Path(name).parent.name] = path or name
    seeds = parse_seeds(args.seeds)
    validate_seeds(seeds, args.split)
    _, report = run(args.bundle, opponents, seeds, workers=args.workers, output=args.output)
    print(json.dumps(report['plans'], indent=2))


if __name__ == '__main__':
    main()
