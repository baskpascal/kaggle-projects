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
    means = [statistics.mean(blocks[key]) for key in sorted(blocks)]
    if len(means) < 2:
        return [0., 1.] if field == 'score' else [None, None]
    rng = random.Random(seed)
    boot = [sum(rng.choices(means, k=len(means))) / len(means) for _ in range(samples)]
    return [quantile(boot, .025), quantile(boot, .975)]


def summarize(rows, samples=5000):
    if not rows:
        raise ValueError('Cannot summarize an empty league')
    runtimes = [t for r in rows for t in (r.get('runtime_ms') or [])
                if isinstance(r.get('runtime_ms'), list)]
    runtime_summaries = [r['runtime_ms'] for r in rows if isinstance(r.get('runtime_ms'), dict)]
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
            'mean_margin_diagnostic': statistics.mean(r['margin'] for r in subset),
        }
    return {
        'games': n, 'seed_blocks': len({r['seed'] for r in rows}),
        'win_rate': sum(r['score'] == 1 for r in rows) / n,
        'draw_rate': sum(r['score'] == .5 for r in rows) / n,
        'loss_rate': sum(r['score'] == 0 for r in rows) / n,
        'score_rate': statistics.mean(r['score'] for r in rows),
        'score_ci95': blocked_interval(rows, samples),
        'mean_margin_diagnostic': statistics.mean(r['margin'] for r in rows),
        'median_margin_diagnostic': statistics.median(r['margin'] for r in rows),
        'failures': sum(len(r['failures']) for r in rows),
        'opponent_failures': sum(len(r['opponent_failures']) for r in rows),
        'no_effect_actions': sum(r['audit'].get('no_effect_actions', 0) for r in rows),
        'unit_actions': sum(r['audit'].get('unit_actions', 0) for r in rows),
        'unsold_items_mean': (statistics.mean(values) if
                              (values := [r.get('unsold_items') for r in rows
                                          if r.get('unsold_items') is not None]) else None),
        'overflow_items': sum(r['audit'].get('overflow_items', 0) for r in rows),
        'sales': sales,
        'evidence_profiles': sorted({r.get('evidence_profile', 'full') for r in rows}),
        'telemetry_version': max((r.get('telemetry_version') or 0) for r in rows) or None,
        'daily': daily_profile(rows),
        'runtime_ms': ({name: quantile(runtimes, q) for name, q in
                        [('p50', .5), ('p95', .95), ('p99', .99), ('max', 1.)]}
                       if runtimes else
                       {name: quantile([row[name] for row in runtime_summaries], q)
                        for name, q in [('p50', .5), ('p95', .95),
                                        ('p99', .99), ('max', 1.)]}),
        'per_opponent': per_opponent,
    }


def reconcile(row):
    """Per-day telemetry must reproduce the audited totals of the same game."""
    daily = row.get('daily')
    if not daily:
        return [] if row.get('evidence_profile') in ('score', 'audit') else ['telemetry missing']
    problems = []
    units, revenue = defaultdict(int), defaultdict(float)
    for day in daily:
        for item, sale in day['sales'].items():
            units[item] += sale['units']
            revenue[item] += sale['revenue']
    for item, sale in row.get('sales', {}).items():
        if units[item] != sale['units'] or abs(revenue[item] - sale['revenue']) > 1e-6:
            problems.append(f'sales disagree for {item}')
    for item in set(units) - set(row.get('sales', {})):
        problems.append(f'telemetry invented sales for {item}')
    if abs(daily[-1]['cash_end'] - row['money']) > 1e-6:
        problems.append('final cash disagrees')
    audit = row.get('audit', {})
    for name, key in [('plantings', 'op_PLANT'), ('harvests', 'op_HARVEST')]:
        expected = audit.get(key, 0) - audit.get('no_effect_' + key[3:], 0)
        if sum(day[name] for day in daily) != expected:
            problems.append(f'{name} disagree with the audit')
    if sum(day['overflow_items'] for day in daily) != audit.get('overflow_items', 0):
        problems.append('overflow disagrees with the audit')
    if [day['day'] for day in daily] != sorted({day['day'] for day in daily}):
        problems.append('days are duplicated or out of order')
    return problems


def daily_profile(rows):
    """Average the per-day economic trajectory across games, day index by day index.

    Reports the questions issue #22 asks: when revenue starts, how low cash
    runs, how much labour is bought, and how many market orders survive
    parsing, truncation and execution.
    """
    profile, problems = defaultdict(list), []
    first_revenue, cash_floor = [], []
    for row in rows:
        found = reconcile(row)
        label = ' '.join(f'{key}={row[key]}' for key in
                         ('candidate', 'opponent', 'seed', 'seat') if key in row)
        problems.extend(f'{label}: {problem}' for problem in found)
        daily = row.get('daily') or []
        for day in daily:
            profile[day['day']].append(day)
        steps = [day['first_revenue_step'] for day in daily if day['first_revenue_step'] is not None]
        first_revenue.append(min(steps) if steps else None)
        if daily:
            cash_floor.append(min(day['cash_min'] for day in daily))
    if not profile:
        return None
    days = []
    for index in sorted(profile):
        batch = profile[index]
        orders = defaultdict(int)
        for day in batch:
            for key, value in day['orders'].items():
                orders[key] += value
        days.append({
            'day': index, 'games': len(batch),
            'cash_end': statistics.mean(day['cash_end'] for day in batch),
            'cash_min': statistics.mean(day['cash_min'] for day in batch),
            'revenue': statistics.mean(day['revenue'] for day in batch),
            'hands_mean': statistics.mean(day['hands_mean'] for day in batch),
            'hires': statistics.mean(day['hires'] for day in batch),
            'plantings': statistics.mean(day['plantings'] for day in batch),
            'harvests': statistics.mean(day['harvests'] for day in batch),
            'pass_actions': statistics.mean(day['pass_actions'] for day in batch),
            'ineffective_actions': statistics.mean(day['ineffective_actions'] for day in batch),
            'overflow_items': statistics.mean(day['overflow_items'] for day in batch),
            'shed_peak': statistics.mean(day['shed_peak'] for day in batch),
            'carried_peak': statistics.mean(day['carried_peak'] for day in batch),
            'quadrants_end': statistics.mean(day['quadrants_end'] for day in batch),
            'occupied_end': statistics.mean(day['occupied_end'] for day in batch),
            'orders': dict(orders),
            'realized_price': {item: values['revenue'] / values['units'] for item, values in
                               _day_sales(batch).items() if values['units']},
        })
    measured = [step for step in first_revenue if step is not None]
    return {
        'days': days,
        'games_without_revenue': sum(step is None for step in first_revenue),
        'first_revenue_step': {'mean': statistics.mean(measured), 'median': statistics.median(measured),
                               'p95': quantile(measured, .95)} if measured else None,
        'cash_floor': {'mean': statistics.mean(cash_floor), 'min': min(cash_floor)} if cash_floor else None,
        'reconciliation_problems': problems,
    }


def _day_sales(batch):
    totals = defaultdict(lambda: {'units': 0, 'revenue': 0.})
    for day in batch:
        for item, sale in day['sales'].items():
            totals[item]['units'] += sale['units']
            totals[item]['revenue'] += sale['revenue']
    return totals
