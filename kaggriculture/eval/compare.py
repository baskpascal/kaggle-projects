"""Paired comparison of completed leagues; never infer missing product sales."""
import argparse
import json
from pathlib import Path
import random
import statistics

from .metrics import blocked_interval, quantile, summarize


def sales_interval(left, right, item, samples=5000):
    blocks = {}
    for key, old in left.items():
        values = blocks.setdefault(key[0], [0, 0, 0, 0])
        for offset, row in ((0, old), (2, right[key])):
            sale = row.get('sales', {}).get(item, {})
            values[offset] += sale.get('revenue', 0)
            values[offset + 1] += sale.get('units', 0)
    if len(blocks) < 2 or not all(sum(v[i] for v in blocks.values()) for i in (1, 3)):
        return [None, None]
    blocks = list(blocks.values())
    rng = random.Random(771)
    estimates = []
    for _ in range(samples):
        totals = list(map(sum, zip(*rng.choices(blocks, k=len(blocks)))))
        if totals[1] and totals[3]:
            estimates.append(totals[2] / totals[3] - totals[0] / totals[1])
    return [quantile(estimates, .025), quantile(estimates, .975)]


def compare(baseline, candidate):
    def keyed(rows):
        result = {(r['seed'], r['seat'], r['opponent']): r for r in rows}
        if len(result) != len(rows):
            raise ValueError('Duplicate match keys')
        if len({r['candidate_hash'] for r in rows}) != 1:
            raise ValueError('Candidate changed during the league')
        return result
    left, right = keyed(baseline), keyed(candidate)
    if not left or left.keys() != right.keys():
        raise ValueError('Require identical nonempty seed/seat/opponent sets')
    deltas = []
    for key, old in left.items():
        new = right[key]
        for field in ('opponent_hash', 'configuration', 'environment', 'backend'):
            if old[field] != new[field]:
                raise ValueError(f'Unpaired {field}: {key}')
        if old['failures'] or new['failures'] or old['opponent_failures'] or new['opponent_failures']:
            raise ValueError(f'Callback failure invalidates comparison: {key}')
        deltas.append({'seed': key[0], **{field: new[field] - old[field]
                       for field in ('score', 'money', 'margin', 'unsold_items')}})
    summaries = [summarize(rows) for rows in (baseline, candidate)]
    products = {}
    for item in ('MELON', 'WOOL', 'STRAWBERRY'):
        old, new = [s['sales'].get(item, {}) for s in summaries]
        a, b = old.get('revenue_per_unit'), new.get('revenue_per_unit')
        products[item] = {'baseline': old, 'candidate': new,
                          'delta_per_unit': b - a if a is not None and b is not None else None,
                          'delta_per_unit_ci95': sales_interval(left, right, item)}
    return {'games_per_agent': len(left), 'seed_blocks': len({k[0] for k in left}),
            'paired_deltas': {field: {'mean': statistics.mean(d[field] for d in deltas),
                                      'ci95': blocked_interval(deltas, field=field)}
                              for field in ('score', 'money', 'margin', 'unsold_items')},
            'premium_sales': products,
            'baseline': summaries[0], 'candidate': summaries[1]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('baseline')
    parser.add_argument('candidate')
    parser.add_argument('--output')
    args = parser.parse_args()
    rows = [[json.loads(line) for line in (Path(directory) / 'matches.jsonl').read_text().splitlines()]
            for directory in (args.baseline, args.candidate)]
    result = json.dumps(compare(*rows), indent=2) + '\n'
    if args.output:
        Path(args.output).write_text(result)
    print(result)


if __name__ == '__main__':
    main()
