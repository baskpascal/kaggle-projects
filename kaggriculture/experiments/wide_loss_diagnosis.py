"""Why we lose 23 of the 27 decided worlds, and when we could have known.

`docs/LADDER_COHORT.md` split the 2600-2800 band in two. In the 40 worlds the ladder decided
by under a thousand coins we win 0.550; in the 27 it decided by more we win 0.148. The whole
deficit is the second population, and the question this module answers is what distinguishes
it - early enough to act on, and without a story that only holds at the final bell.

Three populations, never merged: **A** the 23 wide losses, **B** the 40 tight worlds, and
**W** the 4 wide wins, kept beside A and B as a reference rather than folded into either.

The statistics are chosen for the sample size rather than for familiarity. With 23 against
40 there is no power for a p-value to mean much, so nothing here is selected on one. Every
candidate feature is reported with both medians, Cliff's delta as an effect size that makes
no distributional assumption, the overlap of the two ranges, a bootstrap interval on the
difference of medians, and - the number that actually decides whether a feature is worth
acting on - how many of the 23 losses it touches.

The first-divergence pass is the primary output. For each feature the tight cohort defines a
per-day envelope, and a wide loss diverges on the first day it leaves that envelope. Reading
the earliest such day per world answers "when did this world stop looking normal", which is
a different and more useful question than "what is different at the end".

Nothing here fits or tunes an agent. The classifier section exists only to date the
information: if a shallow rule over day-3 features separates the groups under
leave-one-world-out validation, the regime is knowable early; if it does not, it is not.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import random
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
WIDE = 1000.0
CHECKPOINTS = (1, 3, 5, 10, 15, 20)
ENVELOPE = (10, 90)


# ---------------------------------------------------------------- populations

def classify(world):
    margin = world['ladder_margin']
    if margin <= -WIDE:
        return 'wide_loss'
    if margin >= WIDE:
        return 'wide_win'
    return 'tight'


def populations(timelines):
    groups = {'wide_loss': [], 'tight': [], 'wide_win': []}
    for world in timelines.values():
        groups[classify(world)].append(world)
    for rows in groups.values():
        rows.sort(key=lambda world: world['episode_id'])
    return groups


# ---------------------------------------------------------------- features

def day_row(world, day):
    for row in world['days']:
        if row['day'] == day:
            return row
    return None


def features(world, day):
    """The per-day quantities a decision could plausibly be taken against."""
    row = day_row(world, day)
    if row is None:
        return {}
    prices = row['market_prices']
    out = {
        'cash': row['cash'], 'delta_cash': row['delta_cash'],
        'land_owned': row['land_owned'], 'land_used': row['land_used'],
        'land_idle': row['land_owned'] - row['land_used'],
        'crops': row['crops'], 'herd': row['herd'], 'hands': row['hands'],
        'stock_units': row['stock_units'], 'units_out': row['units_out'],
        'units_in': row['units_in'], 'known_shops': row['known_shops'],
        'passes': row['passes'], 'idle_hands': row['idle_hands'],
        'pasture': row['structures'].get('PASTURE', 0),
        'coop': row['structures'].get('COOP', 0),
        'opponent_cash': row['opponent_cash'],
        'opponent_land_used': row['opponent_land_used'],
        'opponent_hands': row['opponent_hands'],
        'cash_lead': row['cash'] - row['opponent_cash'],
        'land_lead': row['land_used'] - row['opponent_land_used'],
    }
    for product, price in prices.items():
        out[f'price_{product}'] = price
    for kind, count in row['animals'].items():
        out[f'herd_{kind}'] = count
    return out


def feature_names(groups):
    names = set()
    for rows in groups.values():
        for world in rows:
            for day in CHECKPOINTS:
                names.update(features(world, day))
    return sorted(names)


def series(worlds, name, day):
    values = []
    for world in worlds:
        value = features(world, day).get(name)
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


# ---------------------------------------------------------------- statistics

def cliffs_delta(left, right):
    """Non-parametric effect size: P(left > right) - P(left < right), in [-1, 1]."""
    if not left or not right:
        return None
    greater = sum(1 for a in left for b in right if a > b)
    lesser = sum(1 for a in left for b in right if a < b)
    return (greater - lesser) / (len(left) * len(right))


def overlap(left, right):
    """Fraction of the combined range where both groups have mass."""
    if not left or not right:
        return None
    low = max(min(left), min(right))
    high = min(max(left), max(right))
    if high <= low:
        return 0.0
    span = max(max(left), max(right)) - min(min(left), min(right))
    return (high - low) / span if span else 1.0


def bootstrap_median_difference(left, right, samples=2000, seed=7):
    if len(left) < 2 or len(right) < 2:
        return [None, None]
    rng = random.Random(seed)
    draws = []
    for _ in range(samples):
        a = statistics.median(rng.choices(left, k=len(left)))
        b = statistics.median(rng.choices(right, k=len(right)))
        draws.append(a - b)
    draws.sort()
    return [draws[int(.025 * len(draws))], draws[int(.975 * len(draws)) - 1]]


def permutation_p(left, right, samples=2000, seed=11):
    """Reported for context only; nothing in this module is selected on it."""
    if len(left) < 2 or len(right) < 2:
        return None
    observed = abs(statistics.mean(left) - statistics.mean(right))
    pool = left + right
    rng = random.Random(seed)
    hits = 0
    for _ in range(samples):
        rng.shuffle(pool)
        a, b = pool[:len(left)], pool[len(left):]
        if abs(statistics.mean(a) - statistics.mean(b)) >= observed:
            hits += 1
    return (hits + 1) / (samples + 1)


def compare(groups, name, day):
    loss = series(groups['wide_loss'], name, day)
    tight = series(groups['tight'], name, day)
    win = series(groups['wide_win'], name, day)
    if len(loss) < 3 or len(tight) < 3:
        return None
    low, high = percentile(tight, ENVELOPE[0]), percentile(tight, ENVELOPE[1])
    outside = [value for value in loss if value < low or value > high]
    return {
        'feature': name, 'day': day,
        'wide_loss_n': len(loss), 'tight_n': len(tight), 'wide_win_n': len(win),
        'wide_loss_median': statistics.median(loss),
        'tight_median': statistics.median(tight),
        'wide_win_median': statistics.median(win) if win else None,
        'wide_loss_mean': statistics.mean(loss), 'tight_mean': statistics.mean(tight),
        'cliffs_delta': cliffs_delta(loss, tight),
        'overlap': overlap(loss, tight),
        'median_difference_ci': bootstrap_median_difference(loss, tight),
        'permutation_p': permutation_p(list(loss), list(tight)),
        'tight_envelope': [low, high],
        'losses_outside_envelope': len(outside),
        'losses_outside_fraction': len(outside) / len(loss),
    }


def percentile(values, probability):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (probability / 100) * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


# ---------------------------------------------------------------- divergence

def envelopes(groups, names, days):
    """The tight cohort's per-day corridor for every feature."""
    table = {}
    for name in names:
        for day in days:
            values = series(groups['tight'], name, day)
            if len(values) >= 5:
                table[(name, day)] = (percentile(values, ENVELOPE[0]),
                                      percentile(values, ENVELOPE[1]))
    return table


def material(value, low, high, *, absolute=1.0, relative=.05):
    """How far outside the corridor, and whether that distance is worth reporting.

    The tight cohort is frequently degenerate - `v006` opens identically in every world, so
    an envelope can be a single number - and without a materiality floor a one-coin
    difference registers as a divergence. The floor is the larger of an absolute tolerance
    and a fraction of the bound, so neither tiny nor huge quantities are mis-scaled.
    """
    if low <= value <= high:
        return None
    bound = low if value < low else high
    distance = abs(value - bound)
    threshold = max(absolute, relative * abs(bound))
    if distance <= threshold:
        return None
    return {'direction': 'below' if value < low else 'above', 'distance': distance,
            'threshold': threshold}


def first_divergence(world, table, names, days, *, absolute=1.0, relative=.05):
    """The first day this world leaves the tight corridor by a material distance."""
    for day in days:
        escaped = []
        for name in names:
            bounds = table.get((name, day))
            value = features(world, day).get(name)
            if bounds is None or not isinstance(value, (int, float)):
                continue
            low, high = bounds
            verdict = material(value, low, high, absolute=absolute, relative=relative)
            if verdict:
                escaped.append({'feature': name, 'value': value,
                                'direction': verdict['direction'],
                                'distance': verdict['distance'],
                                'envelope': [low, high]})
        if escaped:
            escaped.sort(key=lambda item: -item['distance'])
            return {'day': day, 'signals': escaped}
    return {'day': None, 'signals': []}


OWN_DECISIONS = ('land_owned', 'land_used', 'crops', 'herd', 'herd_COW', 'herd_SHEEP',
                 'herd_GOOSE', 'hands', 'pasture', 'coop', 'known_shops')


def decision_variability(timelines, days):
    """How much our own build actually varies across worlds - the prior question.

    A first-divergence analysis of our own trajectory only means something if that
    trajectory can differ between worlds. This counts distinct values per day across every
    world, so a feature that takes one value everywhere is reported as what it is: a
    decision the agent does not condition on anything.
    """
    worlds = list(timelines.values())
    rows = []
    for day in days:
        row = {'day': day, 'worlds': 0}
        for name in OWN_DECISIONS + ('cash', 'stock_units', 'opponent_cash'):
            values = [features(world, day).get(name) for world in worlds]
            present = [value for value in values if isinstance(value, (int, float))]
            row['worlds'] = max(row['worlds'], len(present))
            row[name] = len(set(present)) if present else None
        rows.append(row)
    return rows


# ---------------------------------------------------------------- opponent

def opponent_check(groups):
    """Does opponent identity or strength explain the split, or does it not?"""
    loss_teams = [world['opponent_team'] for world in groups['wide_loss']]
    tight_teams = [world['opponent_team'] for world in groups['tight']]
    from collections import Counter
    loss_counts, tight_counts = Counter(loss_teams), Counter(tight_teams)
    shared = set(loss_counts) & set(tight_counts)
    loss_ratings = [world['opponent_rating'] for world in groups['wide_loss']
                    if world['opponent_rating'] is not None]
    tight_ratings = [world['opponent_rating'] for world in groups['tight']
                     if world['opponent_rating'] is not None]
    return {
        'distinct_teams_in_losses': len(loss_counts),
        'distinct_teams_in_tight': len(tight_counts),
        'teams_in_both': sorted(shared),
        'teams_in_both_count': len(shared),
        'largest_team_share_of_losses': (max(loss_counts.values()) / len(loss_teams)
                                         if loss_teams else None),
        'most_common_loss_teams': loss_counts.most_common(5),
        'opponent_rating_loss_median': statistics.median(loss_ratings) if loss_ratings else None,
        'opponent_rating_tight_median': statistics.median(tight_ratings) if tight_ratings else None,
        'opponent_rating_cliffs_delta': cliffs_delta(loss_ratings, tight_ratings),
        'opponent_rating_ci': bootstrap_median_difference(loss_ratings, tight_ratings),
    }


def environment_check(groups):
    """The seed-determined world, compared across the two populations at turn zero."""
    rows = {}
    sample = next(iter(groups['wide_loss']), None)
    if sample is None or not sample.get('opening_turn0'):
        return rows
    for product in sorted(sample['opening_turn0']['market_prices']):
        name = f'price_{product}'
        loss = [world['opening_turn0']['market_prices'][product]
                for world in groups['wide_loss'] if world.get('opening_turn0')]
        tight = [world['opening_turn0']['market_prices'][product]
                 for world in groups['tight'] if world.get('opening_turn0')]
        rows[name] = {'wide_loss_median': statistics.median(loss) if loss else None,
                      'tight_median': statistics.median(tight) if tight else None,
                      'cliffs_delta': cliffs_delta([float(v) for v in loss],
                                                   [float(v) for v in tight]),
                      'permutation_p': permutation_p([float(v) for v in loss],
                                                     [float(v) for v in tight])}
    return rows


# ---------------------------------------------------------------- classifier

def stump(values, labels):
    """The best single threshold, by balanced accuracy. Interpretable on purpose."""
    ordered = sorted(set(values))
    # Midpoints, not the observed values themselves. A threshold sitting exactly on the
    # smallest positive training point makes leave-one-out reject that point when it is
    # the one held out, which understates a genuinely separable feature.
    candidates = [(low + high) / 2 for low, high in zip(ordered, ordered[1:])] or ordered
    best = None
    for threshold in candidates:
        for direction in (1, -1):
            predicted = [1 if direction * value >= direction * threshold else 0
                         for value in values]
            score = balanced_accuracy(labels, predicted)
            if best is None or score > best['balanced_accuracy']:
                best = {'threshold': threshold, 'direction': direction,
                        'balanced_accuracy': score}
    return best


def balanced_accuracy(labels, predicted):
    positives = [i for i, label in enumerate(labels) if label == 1]
    negatives = [i for i, label in enumerate(labels) if label == 0]
    if not positives or not negatives:
        return 0.0
    sensitivity = sum(predicted[i] for i in positives) / len(positives)
    specificity = sum(1 - predicted[i] for i in negatives) / len(negatives)
    return (sensitivity + specificity) / 2


def leave_one_out_stump(values, labels):
    """Each world is its own seed, so leaving one world out is the honest validation."""
    if len(set(labels)) < 2:
        return None
    predictions = []
    for index in range(len(values)):
        rest_values = values[:index] + values[index + 1:]
        rest_labels = labels[:index] + labels[index + 1:]
        rule = stump(rest_values, rest_labels)
        if rule is None:
            predictions.append(0)
            continue
        value = values[index]
        predictions.append(1 if rule['direction'] * value >= rule['direction'] * rule['threshold'] else 0)
    return {'balanced_accuracy': balanced_accuracy(labels, predictions),
            'in_sample': stump(values, labels)}


def classifier_diagnostic(groups, names, days):
    """When, if ever, does a single early threshold separate the populations?"""
    worlds = groups['wide_loss'] + groups['tight'] + groups['wide_win']
    labels = [1 if classify(world) == 'wide_loss' else 0 for world in worlds]
    base_rate = sum(labels) / len(labels)
    results = []
    for day in days:
        for name in names:
            values, kept = [], []
            for world, label in zip(worlds, labels):
                value = features(world, day).get(name)
                if isinstance(value, (int, float)):
                    values.append(float(value))
                    kept.append(label)
            if len(values) < len(worlds) * .8 or len(set(kept)) < 2:
                continue
            outcome = leave_one_out_stump(values, kept)
            if outcome is None:
                continue
            results.append({'day': day, 'feature': name,
                            'loo_balanced_accuracy': outcome['balanced_accuracy'],
                            'in_sample_balanced_accuracy': outcome['in_sample']['balanced_accuracy'],
                            'threshold': outcome['in_sample']['threshold'],
                            'direction': outcome['in_sample']['direction'],
                            'n': len(values)})
    results.sort(key=lambda row: (-row['loo_balanced_accuracy'], row['day']))
    by_day = {}
    for row in results:
        by_day.setdefault(row['day'], row)
    return {'base_rate_wide_loss': base_rate, 'best_overall': results[:15],
            'best_per_day': [by_day[day] for day in sorted(by_day)],
            'candidates_screened': len(results)}


def selection_null(groups, names, days, *, rounds=200, seed=17):
    """What the best-of-many-features score looks like when the labels mean nothing.

    Leave-one-world-out protects a single feature from being fitted to the world it is
    scored on. It does not protect the *choice* of feature: screening roughly a hundred
    candidates and reporting the winner is optimistic even when every candidate is noise.
    So the whole procedure - screen everything, keep the best - is rerun on shuffled labels,
    and the reported accuracy is only interesting if it beats this distribution.
    """
    worlds = groups['wide_loss'] + groups['tight'] + groups['wide_win']
    labels = [1 if classify(world) == 'wide_loss' else 0 for world in worlds]
    columns = []
    for day in days:
        for name in names:
            values, mask = [], []
            for world in worlds:
                value = features(world, day).get(name)
                values.append(float(value) if isinstance(value, (int, float)) else None)
            if sum(1 for value in values if value is not None) >= len(worlds) * .8:
                columns.append(values)
    rng = random.Random(seed)
    best = []
    for _ in range(rounds):
        shuffled = labels[:]
        rng.shuffle(shuffled)
        top = 0.0
        for values in columns:
            kept = [(value, label) for value, label in zip(values, shuffled)
                    if value is not None]
            outcome = leave_one_out_stump([value for value, _ in kept],
                                          [label for _, label in kept])
            if outcome and outcome['balanced_accuracy'] > top:
                top = outcome['balanced_accuracy']
        best.append(top)
    best.sort()
    return {'rounds': rounds, 'columns_screened': len(columns),
            'null_median': statistics.median(best),
            'null_p95': best[int(.95 * len(best)) - 1], 'null_max': best[-1]}


# ---------------------------------------------------------------- flip curve

def flip_curve(timelines, shifts=(0, 50, 100, 200, 350, 500, 630, 750, 1000, 1500, 2000)):
    """How many real worlds a uniform gain of X coins would turn, and at what win rate.

    This is the arithmetic the wide/tight split was hiding. The 23 wide losses are the
    *expensive* worlds: turning one costs at least a thousand coins. The cheap ones are in
    the tight population, where eighteen losses run from two coins to 986.

    A uniform shift is an idealisation - no real intervention adds the same money in every
    world - so the curve is a floor on what an intervention has to deliver, not a promise
    that delivering it flips those worlds. Its value is that it is denominated in the same
    units the ladder decides in, which no local objective in this repository has been.
    """
    margins = sorted(world['ladder_margin'] for world in timelines.values())
    total = len(margins)
    base = sum(1 for margin in margins if margin > 0)
    rows = []
    for shift in shifts:
        wins = sum(1 for margin in margins if margin + shift > 0)
        rows.append({'shift': shift, 'wins': wins, 'flips': wins - base,
                     'win_rate': wins / total if total else None})
    losses = sorted((margin for margin in margins if margin <= 0), key=abs)
    cost = [{'flips': index + 1, 'coins_required': abs(margin),
             'win_rate': (base + index + 1) / total}
            for index, margin in enumerate(losses)]
    return {'worlds': total, 'base_wins': base,
            'base_win_rate': base / total if total else None,
            'curve': rows, 'cost_per_flip': cost[:20]}


# ---------------------------------------------------------------- assembly

def analyse(timelines):
    groups = populations(timelines)
    names = feature_names(groups)
    days = sorted({row['day'] for world in timelines.values() for row in world['days']})
    early = [day for day in days if day <= 20]

    comparisons = []
    for day in CHECKPOINTS:
        for name in names:
            row = compare(groups, name, day)
            if row:
                comparisons.append(row)
    comparisons.sort(key=lambda row: (-abs(row['cliffs_delta'] or 0), row['day']))

    table = envelopes(groups, names, early)
    divergences = []
    for world in groups['wide_loss']:
        found = first_divergence(world, table, names, early)
        divergences.append({'episode_id': world['episode_id'],
                            'opponent_team': world['opponent_team'],
                            'opponent_rating': world['opponent_rating'],
                            'ladder_margin': world['ladder_margin'],
                            'first_divergence_day': found['day'],
                            'signals': found['signals'][:4]})
    divergences.sort(key=lambda row: (row['first_divergence_day'] is None,
                                      row['first_divergence_day'] or 0))

    classifier = classifier_diagnostic(groups, names, (1, 3, 5, 10))
    classifier['selection_null'] = selection_null(groups, names, (1, 3, 5, 10))
    return {
        'counts': {name: len(rows) for name, rows in groups.items()},
        'flip_curve': flip_curve(timelines),
        'decision_variability': decision_variability(timelines, CHECKPOINTS + (25, 29)),
        'comparisons': comparisons,
        'first_divergence': divergences,
        'opponent_check': opponent_check(groups),
        'environment_check': environment_check(groups),
        'classifier': classifier,
    }


def write_csv(report, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ['feature', 'day', 'wide_loss_n', 'tight_n', 'wide_win_n',
              'wide_loss_median', 'tight_median', 'wide_win_median',
              'wide_loss_mean', 'tight_mean', 'cliffs_delta', 'overlap',
              'permutation_p', 'losses_outside_envelope', 'losses_outside_fraction']
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in report['comparisons']:
            writer.writerow(row)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timelines', default='artifacts/wide-loss-timelines-v006.json')
    parser.add_argument('--json', default='artifacts/wide_loss_comparison_v006.json')
    parser.add_argument('--csv', default='artifacts/wide_loss_comparison_v006.csv')
    arguments = parser.parse_args()

    timelines = json.loads(Path(arguments.timelines).read_text())
    report = analyse(timelines)
    Path(arguments.json).parent.mkdir(parents=True, exist_ok=True)
    Path(arguments.json).write_text(json.dumps(report, indent=1, default=str))
    write_csv(report, arguments.csv)
    print(json.dumps({'counts': report['counts'],
                      'flip_curve': report['flip_curve'],
                      'top_comparisons': report['comparisons'][:8],
                      'classifier_per_day': report['classifier']['best_per_day'],
                      'opponent_check': report['opponent_check']},
                     indent=1, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
