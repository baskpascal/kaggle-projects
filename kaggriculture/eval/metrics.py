from collections import defaultdict
import random
import statistics


def quantile(values, probability):
    if not values:
        return 0.
    values = sorted(values)
    x = (len(values) - 1) * probability
    lo = int(x)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] * (hi - x) + values[hi] * (x - lo) if hi != lo else values[lo]


def blocked_interval(rows, samples=5000, seed=771, field='score'):
    """Resample complete seed blocks, retaining seats and all opponents together."""
    blocks = defaultdict(list)
    for row in rows:
        blocks[row['seed']].append(row[field])
    means = [statistics.mean(values) for values in blocks.values()]
    if len(means) < 2:
        return [0., 1.] if field == 'score' else [None, None]
    rng = random.Random(seed)
    boot = [statistics.mean(rng.choices(means, k=len(means))) for _ in range(samples)]
    return [quantile(boot, .025), quantile(boot, .975)]


def summarize(rows, samples=5000):
    if not rows:
        raise ValueError('Cannot summarize an empty league')
    runtimes = [t for r in rows for t in r['runtime_ms']]
    n = len(rows)
    per_opponent = {}
    sales = {}
    for row in rows:
        for item, values in row.get('sales', {}).items():
            total = sales.setdefault(item, {'units': 0, 'revenue': 0})
            total['units'] += values['units']
            total['revenue'] += values['revenue']
    for values in sales.values():
        values['revenue_per_unit'] = values['revenue'] / values['units'] if values['units'] else None
    for opponent in sorted({r['opponent'] for r in rows}):
        subset = [r for r in rows if r['opponent'] == opponent]
        per_opponent[opponent] = {
            'games': len(subset), 'score_rate': statistics.mean(r['score'] for r in subset),
            'score_ci95': blocked_interval(subset, samples),
            'mean_margin': statistics.mean(r['margin'] for r in subset),
        }
    return {
        'games': n, 'seed_blocks': len({r['seed'] for r in rows}),
        'win_rate': sum(r['score'] == 1 for r in rows) / n,
        'draw_rate': sum(r['score'] == .5 for r in rows) / n,
        'loss_rate': sum(r['score'] == 0 for r in rows) / n,
        'score_rate': statistics.mean(r['score'] for r in rows),
        'score_ci95': blocked_interval(rows, samples),
        'mean_margin': statistics.mean(r['margin'] for r in rows),
        'median_margin': statistics.median(r['margin'] for r in rows),
        'failures': sum(len(r['failures']) for r in rows),
        'opponent_failures': sum(len(r['opponent_failures']) for r in rows),
        'no_effect_actions': sum(r['audit'].get('no_effect_actions', 0) for r in rows),
        'unit_actions': sum(r['audit'].get('unit_actions', 0) for r in rows),
        'unsold_items_mean': statistics.mean(r['unsold_items'] for r in rows),
        'overflow_items': sum(r['audit'].get('overflow_items', 0) for r in rows),
        'sales': sales,
        'runtime_ms': {name: quantile(runtimes, q) for name, q in
                       [('p50', .5), ('p95', .95), ('p99', .99), ('max', 1.)]},
        'per_opponent': per_opponent,
    }
