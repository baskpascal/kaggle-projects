"""Score a candidate the way the leaderboard scores it: wins, against the right opponents.

The competition's final standing is a Bradley-Terry fit over episodes, and an episode
contributes a win, a loss or a tie. Coin margin does not enter it. A change that wins the
same games by a wider margin is worth exactly nothing on the ladder, so margin must never be
fitness here; it stays as a diagnostic, because it is what separates a real effect from a
one-coin mirror tie-break (see `docs/SELL_LEAD_CORRECTION.md`), and that is its only job.

Matchmaking is rating-adaptive, which is the second half of the problem. An agent sitting
near the top stops being paired against the weak end of our panel, so an aggregate that
averages those families in is a number the ladder will never reproduce. The primary metric
is therefore win rate against the strong cohort. The whole panel is kept as a secondary
metric rather than discarded: the final fit runs over whatever agents remain active, not
over a formal rating cut, and a candidate that collapses against a weak family is still a
candidate with a hole in it.

An opponent whose rating is unknown, or too old to trust, falls **out** of the strong cohort
rather than into it. A rating that has drifted is the same as no rating, and defaulting an
unmeasured opponent into the primary metric would quietly decide the thing this module
exists to measure.
"""
from collections import defaultdict
from datetime import date, datetime
import json
from pathlib import Path
import statistics

from .metrics import blocked_interval
from .pairing import paired_rows

ROOT = Path(__file__).resolve().parents[1]
RATINGS = ROOT / 'opponents' / 'ratings.json'
STRONG_CUT = 2300.
MAX_RATING_AGE_DAYS = 14


def opponent_id(name):
    """`clock::opponents/public/thomas_t95/main.py` -> `thomas_t95`.

    Idempotent on purpose: callers pass either a full agent spec or an id that has already
    been extracted, and re-extracting an id must not silently turn it into the empty string.
    """
    text = str(name).split('::', 1)[-1]
    parent = Path(text).parent.name
    return parent or text


def load_ratings(path=RATINGS):
    """Dated ladder ratings, from the shared file and from each bundle's own manifest.

    The per-bundle manifest wins when both carry a rating, because that file travels with
    the pinned artifact and is the one a re-pin would update.
    """
    table = {}
    if Path(path).is_file():
        raw = json.loads(Path(path).read_text(encoding='utf-8'))
        for name, entry in raw.get('ratings', {}).items():
            table[name] = dict(entry)
    for manifest in sorted((ROOT / 'opponents' / 'public').glob('*/main.manifest.json')):
        entry = json.loads(manifest.read_text(encoding='utf-8')).get('ladder_rating')
        if entry:
            table[manifest.parent.name] = dict(entry)
    return table


KINDS = ('direct', 'author_current', 'author_upper_bound', 'episode_reconstruction')


def cohort(name, ratings, cut=STRONG_CUT, max_age_days=MAX_RATING_AGE_DAYS, today=None):
    """`strong`, `weak`, or `unknown` for one opponent. Unknown is never strong.

    `kind` says what the number is evidence of, and the three kinds do not license the same
    conclusion. `direct` is our own submission of the byte-identical artifact. `author_current`
    is the author team's rating while the pinned artifact is their newest published one: still
    an upper bound, because a team's active agent may be better than anything it published,
    but the closest attributable observation and the only reason the strong cohort is not a
    single mirror match. `author_upper_bound` is that same rating for an artifact the author
    has since superseded, and a bound is only one-sided evidence: below the cut it proves the
    artifact is weak, at or above the cut it proves nothing and the opponent stays unknown.

    The bias is measured, not assumed. `thomas_t95` is the author's newest published notebook
    and their team stands at 2519.6 while our own submission of that exact file rates 2359.8,
    so the proxy overstated the artifact by about 160 points. See `docs/RATINGS_REFRESH.md`.
    """
    entry = ratings.get(opponent_id(name))
    if not entry or entry.get('rating') is None:
        return 'unknown'
    observed = entry.get('observed_at')
    if not observed:
        return 'unknown'
    try:
        seen = datetime.fromisoformat(str(observed)[:10]).date()
    except ValueError:
        return 'unknown'
    if max_age_days is not None and ((today or date.today()) - seen).days > max_age_days:
        return 'unknown'
    kind = entry.get('kind')
    if kind not in KINDS:
        return 'unknown'
    if float(entry['rating']) < cut:
        return 'weak'
    return 'unknown' if kind == 'author_upper_bound' else 'strong'


def tally(rows):
    """Win, loss and tie counts, and the win rate the fit actually sees (ties at .5)."""
    wins = sum(1 for row in rows if row['score'] == 1)
    ties = sum(1 for row in rows if row['score'] == .5)
    losses = sum(1 for row in rows if row['score'] == 0)
    if wins + ties + losses != len(rows):
        raise ValueError('A game must score 1, .5 or 0; margin is not a score')
    return {'games': len(rows), 'wins': wins, 'ties': ties, 'losses': losses,
            'win_rate': statistics.mean(row['score'] for row in rows) if rows else None,
            'win_rate_ci95': blocked_interval(rows) if rows else [None, None],
            # Diagnostic only. Never a selection key: see this module's docstring.
            'mean_margin_diagnostic': statistics.mean(row['margin'] for row in rows) if rows else None}


def score(rows, ratings=None, **cohort_kwargs):
    """Primary `win_rate_strong`, secondary `win_rate_all`, and a per-opponent breakdown."""
    ratings = load_ratings() if ratings is None else ratings
    groups = defaultdict(list)
    for row in rows:
        groups[opponent_id(row['opponent'])].append(row)
    labels = {name: cohort(name, ratings, **cohort_kwargs) for name in groups}
    strong = [row for name, batch in groups.items() if labels[name] == 'strong' for row in batch]
    return {
        'primary': {'metric': 'win_rate_strong', **tally(strong)} if strong else
                   {'metric': 'win_rate_strong', 'games': 0,
                    'note': 'no opponent has a current rating at or above the cut'},
        'secondary': {'metric': 'win_rate_all', **tally(rows)},
        'cohorts': labels,
        'per_opponent': {name: {'cohort': labels[name], **tally(batch)}
                         for name, batch in sorted(groups.items())},
    }


def _paired(baseline, candidate):
    """Both legs must have met the same opponents on the same seeds and seats."""
    left, right = paired_rows(baseline, candidate)
    specs = {key[2] for key in left}
    if len({opponent_id(name) for name in specs}) != len(specs):
        raise ValueError('Ambiguous opponent IDs; use distinct bundle names')
    return ({(seed, seat, opponent_id(name)): row for (seed, seat, name), row in left.items()},
            {(seed, seat, opponent_id(name)): row for (seed, seat, name), row in right.items()})


def delta_interval(rows):
    # Differences range from -1 to 1. One block cannot estimate sampling uncertainty.
    return blocked_interval([dict(row, delta=row['score']) for row in rows],
                            samples=10000, field='delta')


def delta_win(baseline, candidate, ratings=None, **cohort_kwargs):
    """Paired change in win rate, with a bootstrap over seed blocks rather than games.

    The two legs meet the same opponents on the same seeds, so their scores are strongly
    correlated and an interval built from two independent win rates is far too wide. The
    statistic is the per-block difference, and the resampling unit is the block.

    This cannot manufacture signal. If the true effect is near zero, more blocks buy a
    tighter interval around zero, which is the answer we want before spending a submission.
    """
    ratings = load_ratings() if ratings is None else ratings
    left, right = _paired(baseline, candidate)
    labels = {name: cohort(name, ratings, **cohort_kwargs)
              for name in {key[2] for key in left}}
    deltas = [{'seed': key[0], 'opponent': key[2], 'cohort': labels[key[2]],
               'score': right[key]['score'] - old['score'],
               'margin': right[key]['margin'] - old['margin']}
              for key, old in left.items()]

    def summarize(subset):
        if not subset:
            return {'games': 0, 'delta_win': None, 'ci95': [None, None]}
        return {'games': len(subset),
                'delta_win': statistics.mean(row['score'] for row in subset),
                'ci95': delta_interval(subset),
                'delta_margin_diagnostic': statistics.mean(row['margin'] for row in subset)}

    per_opponent = {}
    for name in sorted(labels):
        subset = [row for row in deltas if row['opponent'] == name]
        per_opponent[name] = {'cohort': labels[name], **summarize(subset)}
    return {
        'bootstrap': {'method': 'percentile', 'unit': 'seed', 'samples': 10000, 'seed': 771},
        'games_per_leg': len(left), 'seed_blocks': len({key[0] for key in left}),
        'strong': summarize([row for row in deltas if row['cohort'] == 'strong']),
        'all': summarize(deltas),
        'per_opponent': per_opponent,
        'baseline': score(baseline, ratings, **cohort_kwargs),
        'candidate': score(candidate, ratings, **cohort_kwargs),
    }


def concentrated_regression(delta, tolerance=0.):
    """Opponents the candidate is worse against, which a positive average can hide.

    A gain bought by losing to the two agents nearest the top is not a gain worth a
    submission slot, however healthy the average looks.
    """
    return sorted(name for name, row in delta['per_opponent'].items()
                  if row['delta_win'] is not None and row['delta_win'] < -tolerance)


def leave_one_out(baseline, candidate, ratings=None, **cohort_kwargs):
    """Recompute the primary delta with each strong opponent dropped in turn.

    An average over a handful of opponents can be carried entirely by one of them, and that
    case has to be visible rather than inferred. The classic instance here is a mirror match:
    a candidate that shares its tapes with a panel member beats that member decisively and is
    indistinguishable from the incumbent against everything else, yet reports a healthy
    aggregate. Dropping the opponent that carries it is what exposes it.

    Returns, for each strong opponent, the delta and interval that survive without it.
    """
    ratings = load_ratings() if ratings is None else ratings
    left, right = _paired(baseline, candidate)
    labels = {name: cohort(name, ratings, **cohort_kwargs) for name in {key[2] for key in left}}
    strong = sorted(name for name, label in labels.items() if label == 'strong')
    result = {}
    for dropped in strong:
        keep = [key for key in left if labels[key[2]] == 'strong' and key[2] != dropped]
        rows = [{'seed': key[0], 'score': right[key]['score'] - left[key]['score']}
                for key in keep]
        result[dropped] = ({'games': 0, 'delta_win': None, 'ci95': [None, None]} if not rows else
                           {'games': len(rows),
                            'delta_win': statistics.mean(row['score'] for row in rows),
                            'ci95': delta_interval(rows)})
    return result


def carried_by(leave_one_out_result):
    """Strong opponents whose removal leaves the primary delta indistinguishable from zero.

    If any single opponent appears here, the remaining evidence does not establish a positive
    field-wide effect. This does not prove all improvement comes from that opponent.
    """
    return sorted(name for name, row in leave_one_out_result.items()
                  if row['delta_win'] is None or row['ci95'][0] is None or row['ci95'][0] <= 0)


def rows_from(directory):
    path = Path(directory) / 'matches.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('baseline', help='league result directory for the incumbent')
    parser.add_argument('candidate', help='league result directory for the challenger')
    parser.add_argument('--cut', type=float, default=STRONG_CUT)
    parser.add_argument('--max-rating-age-days', type=int, default=MAX_RATING_AGE_DAYS)
    parser.add_argument('--ratings', help='frozen ratings JSON (ratings mapping or ratings.json envelope)')
    parser.add_argument('--as-of', type=date.fromisoformat, default=date.today())
    parser.add_argument('--mirrors', default='', help='comma-separated known shared-tape opponent IDs')
    parser.add_argument('--output')
    args = parser.parse_args()
    from .comparison import build_comparison
    ratings = None
    if args.ratings:
        raw = json.loads(Path(args.ratings).read_text())
        ratings = raw.get('ratings', raw)
    splits = []
    for directory in (args.baseline, args.candidate):
        summary = Path(directory) / 'summary.json'
        splits.append(json.loads(summary.read_text()).get('metadata', {}).get('split')
                      if summary.is_file() else None)
    split = splits[0] if splits[0] == splits[1] else None
    result = build_comparison(rows_from(args.baseline), rows_from(args.candidate),
                              ratings=ratings, today=args.as_of, split=split,
                              mirrors=[name for name in args.mirrors.split(',') if name],
                              cut=args.cut, max_age_days=args.max_rating_age_days)
    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        Path(args.output).write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
