"""Issue #25: paired DEV evaluation of X-LIQ against the selected base."""
import argparse
import json
from pathlib import Path
import statistics

from arena.league import run_league
from eval.compare import compare


def _mean_day(summary, day, field):
    rows = summary['daily']['days']
    found = [row[field] for row in rows if row['day'] == day]
    return found[0] if found else None


def _daily_trajectory(baseline, candidate):
    left = {row['day']: row for row in baseline['daily']['days']}
    right = {row['day']: row for row in candidate['daily']['days']}
    fields = ('cash_end', 'cash_min', 'hands_mean', 'hires', 'plantings', 'revenue')
    return [{'day': day, **{field: {'baseline': left[day][field],
                                    'candidate': right[day][field],
                                    'delta': right[day][field] - left[day][field]}
                             for field in fields}}
            for day in sorted(left.keys() & right.keys())]


def _paired_money(rows_before, rows_after, families):
    keyed = lambda rows: {(row['seed'], row['seat'], row['opponent']): row for row in rows}
    left, right = keyed(rows_before), keyed(rows_after)
    ratios, by_family = [], {}
    for key, old in left.items():
        if old['money']:
            ratio = right[key]['money'] / old['money']
            ratios.append(ratio)
            by_family.setdefault(families[key[2]], []).append(ratio)
    return {'mean': statistics.mean(ratios) if ratios else None,
            'by_family': {family: statistics.mean(values)
                          for family, values in sorted(by_family.items())}}


def evaluate_acceptance(comparison, *, score_tolerance=.05, price_tolerance=0.,
                        runtime_budget_ms=100., runtime_ratio_tolerance=1.25,
                        opponent_families=None):
    baseline, candidate = comparison['baseline'], comparison['candidate']
    reasons = []
    delta = comparison['paired_deltas']['score']['mean']
    if delta < -score_tolerance:
        reasons.append(f'score delta {delta:.4f} exceeds {-score_tolerance:.4f} tolerance')
    family_deltas = {}
    opponent_families = opponent_families or {name: name for name in baseline['per_opponent']}
    for opponent, old in baseline['per_opponent'].items():
        new = candidate['per_opponent'][opponent]
        family = opponent_families[opponent]
        family_deltas[family] = new['score_rate'] - old['score_rate']
        if family_deltas[family] < -score_tolerance:
            reasons.append(f'{family} score regressed by {family_deltas[family]:.4f}')
    old_floor = min(row['cash_min'] for row in baseline['daily']['days'][:3])
    new_floor = min(row['cash_min'] for row in candidate['daily']['days'][:3])
    if new_floor < old_floor:
        reasons.append(f'cash floor fell from {old_floor:.2f} to {new_floor:.2f}')
    old_first = baseline['daily']['first_revenue_step']
    new_first = candidate['daily']['first_revenue_step']
    if old_first and (not new_first or new_first['mean'] > old_first['mean']):
        reasons.append('first revenue became later or disappeared')
    prices = {}
    for item in sorted(set(baseline['sales']) | set(candidate['sales'])):
        old = baseline['sales'].get(item, {}).get('revenue_per_unit')
        new = candidate['sales'].get(item, {}).get('revenue_per_unit')
        prices[item] = {'baseline': old, 'candidate': new,
                        'delta': new - old if old is not None and new is not None else None}
        if old is not None and (new is None or new < old - price_tolerance):
            reasons.append(f'{item} realized price fell')
    if candidate['failures'] or candidate['opponent_failures']:
        reasons.append('callback failure invalidates the experiment')
    if candidate['runtime_ms']['p99'] > runtime_budget_ms:
        reasons.append(f"p99 runtime {candidate['runtime_ms']['p99']:.2f}ms exceeds budget")
    runtime_ratio = candidate['runtime_ms']['p99'] / max(baseline['runtime_ms']['p99'], 1e-9)
    if runtime_ratio > runtime_ratio_tolerance:
        reasons.append(f'p99 runtime ratio {runtime_ratio:.3f} exceeds {runtime_ratio_tolerance:.3f}')
    trajectory = {
        'first_revenue_step': {'baseline': old_first, 'candidate': new_first},
        'cash_floor': {'baseline': old_floor, 'candidate': new_floor},
        'day_0_cash_end': {'baseline': _mean_day(baseline, 0, 'cash_end'),
                           'candidate': _mean_day(candidate, 0, 'cash_end')},
        'day_2_revenue': {'baseline': _mean_day(baseline, 2, 'revenue'),
                          'candidate': _mean_day(candidate, 2, 'revenue')},
        'family_score_deltas': family_deltas,
        'realized_prices': prices,
        'daily': _daily_trajectory(baseline, candidate),
        'runtime_p99_ratio': runtime_ratio,
    }
    improved = ((new_first and old_first and new_first['mean'] < old_first['mean'])
                or new_floor > old_floor
                or trajectory['day_2_revenue']['candidate'] > trajectory['day_2_revenue']['baseline'])
    if not improved:
        reasons.append('no measured liquidity trajectory improved')
    return {'accepted': not reasons, 'decision': 'accept' if not reasons else 'reject',
            'reasons': reasons, 'trajectory': trajectory,
            'score_tolerance': score_tolerance, 'price_tolerance': price_tolerance,
            'runtime_budget_ms': runtime_budget_ms}


def run_xliq(tournament_decision, seeds, output, *, workers=4, backend='fast',
             score_tolerance=.05, price_tolerance=0., runtime_budget_ms=100.):
    decision = json.loads(Path(tournament_decision).read_text(encoding='utf-8'))
    if decision.get('status') != 'selected':
        raise ValueError('X-LIQ requires a completed public base selection decision')
    protocol = decision['protocol']
    if protocol['split'] != 'dev':
        raise ValueError('The selected base must come from DEV evidence')
    base = decision['selected_base']['agent_spec']
    opponents = [entry['agent_spec'] for entry in protocol['opponents']]
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    try:
        baseline, _, base_meta = run_league(base, opponents, seeds, workers, backend,
                                            output / 'baseline', 'dev', evidence_profile='full')
        treatment = 'xliq::' + base
        challenger, _, challenger_meta = run_league(treatment, opponents, seeds, workers, backend,
                                                     output / 'challenger', 'dev',
                                                     evidence_profile='full')
        comparison = compare(baseline, challenger)
        opponent_families = {entry['agent_spec']: entry['family']
                             for entry in protocol['opponents']}
        comparison['paired_money_ratio'] = _paired_money(
            baseline, challenger, opponent_families)
        acceptance = evaluate_acceptance(comparison, score_tolerance=score_tolerance,
                                         price_tolerance=price_tolerance,
                                         runtime_budget_ms=runtime_budget_ms,
                                         opponent_families=opponent_families)
        artifact = {
            'schema_version': 1,
            'classification': 'experimental challenger; not a release',
            'base': decision['selected_base'],
            'treatment': 'agent/xliq.py opening-only wrapper',
            'treatment_parameters': {
                'opening_days': 3, 'capital_floor': 1000,
                'short_crop': 'WHEAT', 'premium_crop': 'STRAWBERRY',
                'short_seed_target': 6, 'premium_seed_target': 2,
            },
            'split': 'dev', 'seeds': seeds, 'backend': backend, 'seats': [0, 1],
            'opponents': protocol['opponents'],
            'baseline_metadata': base_meta, 'challenger_metadata': challenger_meta,
            'acceptance': acceptance,
        }
        (output / 'comparison.json').write_text(json.dumps(comparison, indent=2) + '\n', encoding='utf-8')
        (output / 'decision.json').write_text(json.dumps(artifact, indent=2) + '\n', encoding='utf-8')
        return artifact
    except Exception:
        (output / 'INCOMPLETE').write_text('This directory is not X-LIQ evidence.\n', encoding='utf-8')
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tournament-decision', required=True)
    parser.add_argument('--seeds', default='1000:1100')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--backend', choices=('fast', 'official'), default='fast')
    parser.add_argument('--output', required=True)
    parser.add_argument('--score-tolerance', type=float, default=.05)
    parser.add_argument('--price-tolerance', type=float, default=0.)
    parser.add_argument('--runtime-budget-ms', type=float, default=100.)
    args = parser.parse_args()
    from arena.seeds import parse_seeds
    result = run_xliq(args.tournament_decision, parse_seeds(args.seeds), args.output,
                      workers=args.workers, backend=args.backend,
                      score_tolerance=args.score_tolerance,
                      price_tolerance=args.price_tolerance,
                      runtime_budget_ms=args.runtime_budget_ms)
    print(json.dumps(result['acceptance'], indent=2))


if __name__ == '__main__':
    main()
