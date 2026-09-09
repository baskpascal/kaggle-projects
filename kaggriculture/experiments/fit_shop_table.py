"""Fit a routing table from a `shop_route_search` run, one shop pair at a time.

Every plan played the *same* worlds - the same opponent, seed and seat - because the
prefix up to the routing step is shared, so a cell is a paired comparison and not two
independent samples. That is what this module exploits: a plan is only allowed to replace
the incumbent route when it wins more of those paired games than it loses by a margin a
one-sided sign test would not call luck, on enough games to be worth having.

Cells that fail any of those tests keep the incumbent route. A table that changes fewer
cells is not a weaker result: the incumbent is a strong public artifact, and every changed
cell is a bet we have to pay for in validation games.
"""
import argparse
from collections import defaultdict
import json
from math import comb
from pathlib import Path
import statistics

PARENT_TABLE = {
    ('BAKERY', 'YARN_STORE'): 3, ('BRUNCH_SPOT', 'YARN_STORE'): 4,
    ('FARMERS_MARKET', 'YARN_STORE'): 5, ('ICE_CREAM_SHOP', 'YARN_STORE'): 6,
    ('PET_CAFE', 'YARN_STORE'): 5, ('PIZZA_SHOP', 'YARN_STORE'): 7,
    ('SMOOTHIE_SHOP', 'YARN_STORE'): 8, ('YARN_STORE', 'BAKERY'): 9,
    ('YARN_STORE', 'BRUNCH_SPOT'): 9, ('YARN_STORE', 'FARMERS_MARKET'): 1,
    ('YARN_STORE', 'ICE_CREAM_SHOP'): 9, ('YARN_STORE', 'PET_CAFE'): 10,
    ('YARN_STORE', 'PIZZA_SHOP'): 6, ('YARN_STORE', 'SMOOTHIE_SHOP'): 11,
    ('YARN_STORE', 'YARN_STORE'): 12,
}


def sign_test(wins, losses):
    """One-sided p that a fair coin gives at least this many wins out of the decided games."""
    decided = wins + losses
    if not decided:
        return 1.
    tail = sum(comb(decided, k) for k in range(wins, decided + 1))
    return tail / (2 ** decided)


def load(path):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    broken = [row for row in rows if row['failures'] or row['opponent_failures']]
    if broken:
        raise ValueError(f'{len(broken)} games recorded a callback failure; evidence refused')
    return rows


def fit(rows, *, min_games=20, min_gain=.10, alpha=.05):
    games = defaultdict(dict)
    for row in rows:
        games[(tuple(row['shops']), row['plan'])][(row['opponent'], row['seed'], row['seat'])] = row['score']
    cells = sorted({shops for shops, _ in games})
    table, report = dict(PARENT_TABLE), {}
    for shops in cells:
        incumbent = PARENT_TABLE.get(shops, 0)
        base = games.get((shops, incumbent), {})
        entry = {'games': len(base), 'incumbent': incumbent,
                 'incumbent_score': statistics.mean(base.values()) if base else None,
                 'candidates': {}, 'chosen': incumbent, 'reason': 'incumbent kept'}
        best, best_score = incumbent, entry['incumbent_score']
        for plan in sorted({p for s, p in games if s == shops}):
            if plan == incumbent:
                continue
            trial = games[(shops, plan)]
            shared = sorted(set(base) & set(trial))
            wins = sum(trial[key] > base[key] for key in shared)
            losses = sum(trial[key] < base[key] for key in shared)
            score = statistics.mean(trial[key] for key in shared) if shared else None
            candidate = {'score': score, 'paired_wins': wins, 'paired_losses': losses,
                         'p_value': sign_test(wins, losses)}
            entry['candidates'][str(plan)] = candidate
            eligible = (len(shared) >= min_games and score is not None
                        and entry['incumbent_score'] is not None
                        and score - entry['incumbent_score'] >= min_gain
                        and candidate['p_value'] < alpha)
            if eligible and (best_score is None or score > best_score):
                best, best_score = plan, score
        if best != incumbent:
            table[shops] = best
            entry.update(chosen=best, reason='replaced: paired win, significant and large enough')
        report['|'.join(shops)] = entry
    return table, report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--games', required=True)
    parser.add_argument('--min-games', type=int, default=20)
    parser.add_argument('--min-gain', type=float, default=.10)
    parser.add_argument('--alpha', type=float, default=.05)
    parser.add_argument('--table', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    rows = load(args.games)
    table, report = fit(rows, min_games=args.min_games, min_gain=args.min_gain, alpha=args.alpha)
    Path(args.table).write_text(json.dumps({'|'.join(k): v for k, v in sorted(table.items())}, indent=1) + '\n')
    Path(args.report).write_text(json.dumps(report, indent=1) + '\n')
    changed = {'|'.join(k): v for k, v in sorted(table.items()) if PARENT_TABLE.get(k, 0) != v}
    print(f'{len(table)} routes, {len(changed)} changed from the parent:')
    print(json.dumps(changed, indent=1))


if __name__ == '__main__':
    main()
