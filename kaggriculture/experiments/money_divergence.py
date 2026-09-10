"""Where the money gap against an elite stream opens, and what it is made of.

Comparing our terminal bank with a number printed in someone else's episode compares two
different markets. Here both seats come out of the same replay: our candidate plays the
recorded elite stream in the elite's own world, so the prices, the demand and the opponent
are shared and the two cash trajectories are directly subtractable.

The gap is then decomposed the only way that can point at a decision - by when it opens, by
which product carries it, and by the production standing behind that product - because a
deficit that appears at turn 400 cannot be explained by a purchase made at turn 600.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import tempfile

from experiments.hybrid_divergence import play
from experiments.tape_agent import build as build_tape_agent
from experiments.top_panel import episode_streams

PHASES = ((0, 180), (180, 360), (360, 540), (540, 720))


def _farm(observation, seat):
    return observation['farms'][seat]


def cash_series(replay, seat):
    return [turn['observations'][seat]['farms'][seat]['money'] for turn in replay['turns']]


def sales_by_phase(replay, seat):
    """Units actually settled, priced at the turn of sale, per product and phase."""
    revenue = defaultdict(lambda: defaultdict(float))
    units = defaultdict(lambda: defaultdict(int))
    for turn in replay['turns']:
        action = turn['actions'][seat]
        observation = turn['observations'][seat]
        private = observation['private']
        step = turn['step']
        phase = next((f'{a}-{b}' for a, b in PHASES if a <= step < b), '540-720')
        for order in (action or {}).get('market') or []:
            if not order or order[0] != 'SELL':
                continue
            item = order[1]
            wanted = order[2] if len(order) > 2 else 1
            held = private['shed'].get(item, 0) + sum(
                inventory.get(item, 0) for inventory in private['inventories'])
            settled = min(wanted, held)
            if settled <= 0:
                continue
            units[item][phase] += settled
            revenue[item][phase] += settled * observation['market']['prices'].get(item, 0.)
    return ({k: dict(v) for k, v in revenue.items()},
            {k: dict(v) for k, v in units.items()})


def production(replay, seat):
    """Animals and structures actually standing, sampled once per phase."""
    out = {}
    for start, end in PHASES:
        frame = next((t for t in replay['turns'] if t['step'] >= start), None)
        if frame is None:
            continue
        farm = _farm(frame['observations'][seat], seat)
        tiles = [tile for row in farm['tiles'] for tile in row]
        out[f'{start}-{end}'] = {
            'COW': sum(isinstance(t, dict) and t.get('animal') == 'COW' for t in tiles),
            'SHEEP': sum(isinstance(t, dict) and t.get('animal') == 'SHEEP' for t in tiles),
            'GOOSE': sum(isinstance(t, dict) and t.get('animal') == 'GOOSE' for t in tiles),
            'crops': sum(isinstance(t, dict) and 'crop' in t for t in tiles),
            'hands': len(farm.get('hands') or []),
            'cash': farm['money'],
        }
    return out


def first_persistent_divergence(ours, theirs, *, threshold=1000, hold=48):
    """The first turn after which their lead exceeds `threshold` and never closes."""
    gap = [b - a for a, b in zip(ours, theirs)]
    for turn in range(len(gap) - hold):
        if gap[turn] >= threshold and all(g >= threshold for g in gap[turn:]):
            return turn
    return None


def analyse(replay, ours_seat, theirs_seat):
    ours, theirs = cash_series(replay, ours_seat), cash_series(replay, theirs_seat)
    our_revenue, our_units = sales_by_phase(replay, ours_seat)
    their_revenue, their_units = sales_by_phase(replay, theirs_seat)
    return {
        'terminal_ours': ours[-1], 'terminal_theirs': theirs[-1],
        'terminal_gap': theirs[-1] - ours[-1],
        'divergence_turn': first_persistent_divergence(ours, theirs),
        'cash_at': {str(t): {'ours': ours[min(t, len(ours) - 1)],
                             'theirs': theirs[min(t, len(theirs) - 1)]}
                    for t in (180, 360, 540, 719)},
        'our_revenue': our_revenue, 'their_revenue': their_revenue,
        'our_units': our_units, 'their_units': their_units,
        'our_production': production(replay, ours_seat),
        'their_production': production(replay, theirs_seat),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmark', required=True, help='current_meta_benchmark output')
    parser.add_argument('--archive', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--team', required=True)
    parser.add_argument('--outcome', choices=('loss', 'win'), default='loss')
    parser.add_argument('--limit', type=int, default=8)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    benchmark = json.loads(Path(args.benchmark).read_text(encoding='utf-8'))
    target = 0.0 if args.outcome == 'loss' else 1.0
    rows = [r for r in benchmark['rows']
            if r.get('opponent_team') == args.team and r['score'] == target][:args.limit]
    workdir = Path(tempfile.mkdtemp(prefix='money-divergence-'))
    results, failures = [], []
    for row in rows:
        try:
            seats = episode_streams(args.archive, row['episode'])
            ours_seat = int(row['candidate_seat'])
            opponent_path = build_tape_agent(
                {'actions': seats[int(row['opponent_seat'])]['actions']},
                workdir / f"opp-{row['episode']}.py",
                provenance=f"recorded {args.team} stream, episode {row['episode']}")
            replay = play(args.candidate, str(opponent_path), row['seed'], ours_seat)
        except Exception as error:                                   # noqa: BLE001
            failures.append({'episode': row['episode'],
                             'reason': f'{type(error).__name__}: {error}'})
            continue
        results.append({'episode': row['episode'], 'seed': row['seed'],
                        **analyse(replay, ours_seat, 1 - ours_seat)})

    def med(key):
        values = [r[key] for r in results if isinstance(r.get(key), (int, float))]
        return round(statistics.median(values), 1) if values else None

    revenue_gap = defaultdict(float)
    for r in results:
        for item in set(r['our_revenue']) | set(r['their_revenue']):
            theirs = sum(r['their_revenue'].get(item, {}).values())
            ours = sum(r['our_revenue'].get(item, {}).values())
            revenue_gap[item] += theirs - ours
    payload = {
        'schema_version': 1, 'candidate': args.candidate, 'team': args.team,
        'outcome': args.outcome, 'worlds': len(results), 'failures': failures,
        'median_terminal_gap': med('terminal_gap'),
        'median_divergence_turn': med('divergence_turn'),
        'revenue_gap_by_item_total': {
            k: round(v, 1) for k, v in sorted(revenue_gap.items(), key=lambda kv: -kv[1])},
        'rows': results,
    }
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in payload.items() if k != 'rows'}, indent=2)[:2200])


if __name__ == '__main__':
    main()
