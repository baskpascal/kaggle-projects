"""The meta panel as a versioned dataset, so a standing can say what it stood against.

`eval/standing.py` asks the right question -- absolute win rate by rank band -- and until
now it was handed the answer to "who is in which band" as a JSON mapping typed beside the
run. That mapping is the same hand-carry `eval/dossier.py` exists to remove, one level
down: a correct standing over a panel nobody can reconstruct is a number about an
unnamed population.

Live rank cannot be the authority either. It lags, it depends on the trajectory of a
submission, and the strongest agents are withdrawn from the ladder, so the best
observable opponent is not the best existing one. What can be an authority is a dated
observation, pinned to an artifact we hold byte for byte, kept forever.

So the panel is a snapshot: every opponent carries its artifact hash, where it came from,
the rank and rating observed for it and when, its lineage and the engine fingerprint it
was pinned under. The content hashes to a `revision`, revisions are written once and never
edited, and a standing records the revision it ran against. Refreshing the meta creates a
new revision; it cannot rewrite the evidence a past decision was made on.

The gate refuses, never approves by omission. An empty decisive band, a band carried by
one lineage, a lineage holding more than half of a band, and a snapshot past its freshness
window are all refusals -- because each of them is a way for a statistically correct
number to be about the wrong population.

See docs/PANEL_SNAPSHOT.md.
"""
from datetime import date, datetime, timezone
import json
from pathlib import Path

from .comparison import digest
from .ladder import KINDS, ROOT, load_ratings, opponent_id
from .standing import BANDS, TOP20_BANDS

SCHEMA_VERSION = 1
OBSERVATIONS = ROOT / 'opponents' / 'panel-observations.json'
PANELS = ROOT / 'opponents' / 'panels'
INDEX = 'index.json'

# The public field moves every day -- `yhay_router_0908` was a notebook updated hours
# before we pinned it -- so a panel is stale long before a rating is. Ratings tolerate 14
# days because a rating is a slow statistic about a team; band membership is a claim about
# a leaderboard that reorders daily.
MAX_PANEL_AGE_DAYS = 7
# A band represented by one lineage measures that lineage. Two is the least that can
# distinguish "the candidate beats this band" from "the candidate beats this family".
MIN_LINEAGES_PER_BAND = 2
# ... and two lineages do not help if one of them carries the band. Half is the point at
# which the second lineage stops being a check on the first.
MAX_LINEAGE_SHARE = .5
# Which observations may place an opponent *into* a band. A team rank bounds its published
# artifact from above exactly as its rating does: `author_upper_bound` can prove an
# artifact is *not* top ten and can never prove that it is, so it bands nothing. See
# docs/RATINGS_REFRESH.md for the same asymmetry on the rating side.
BANDING_KINDS = ('direct', 'author_current', 'episode_reconstruction')


def _valid_digest(value):
    return isinstance(value, str) and len(value) == 64 and all(
        character in '0123456789abcdef' for character in value)


def reproduction_error(bundle, rebuilt):
    """Return why a reconstructed bundle's claimed reproduction is not evidence."""
    proof = rebuilt.get('reproduction') or {}
    required = ('dataset_revision', 'episode_id', 'seat', 'engine_version',
                'engine_fingerprint', 'agent_sha256', 'stream_sha256',
                'expected', 'actual', 'verified_at')
    missing = [field for field in required if proof.get(field) is None]
    if missing:
        return f'incomplete reproduction ({", ".join(missing)} missing)'
    if not _valid_digest(proof['dataset_revision']):
        return 'reproduction dataset_revision is not a SHA-256 digest'
    if str(proof['episode_id']) != str(rebuilt.get('episode')):
        return 'reproduction episode does not match the reconstructed episode'
    recorded = bundle.get('recorded') or {}
    if proof['seat'] not in (0, 1) or proof['seat'] != recorded.get('seat'):
        return 'reproduction seat does not match the reconstructed seat'
    engine = (bundle.get('engine') or {}).get('kaggle_environments')
    fingerprint = proof['engine_fingerprint']
    if (proof['engine_version'] != engine or not isinstance(fingerprint, dict)
            or fingerprint.get('version') != engine):
        return 'reproduction engine does not match the pinned bundle engine'
    if proof['agent_sha256'] != bundle.get('sha256'):
        return 'reproduction agent digest does not match the pinned artifact'
    if proof['stream_sha256'] != bundle.get('tape_sha256'):
        return 'reproduction stream digest does not match the pinned tape'
    expected, actual = proof['expected'], proof['actual']
    result_fields = ('winner', 'our_money', 'opponent_money')
    if (not isinstance(expected, dict) or not isinstance(actual, dict)
            or any(expected.get(field) is None or actual.get(field) is None
                   for field in result_fields)):
        return 'reproduction result lacks winner or final money'
    recorded_expected = {'winner': (proof['seat'] if recorded.get('money', 0) >
                                     recorded.get('opponent_money', 0)
                                     else 1 - proof['seat']
                                     if recorded.get('money', 0) <
                                     recorded.get('opponent_money', 0) else None),
                         'our_money': recorded.get('money'),
                         'opponent_money': recorded.get('opponent_money')}
    if expected != recorded_expected:
        return 'reproduction expected result does not match the recorded bundle result'
    if expected != actual:
        return 'reproduction result does not match the published result'
    if _observed(proof['verified_at']) is None:
        return 'reproduction verified_at is not parseable'
    return None


def load_bundles(root=None, *, include_recorded=False):
    """Every pinned opponent, bundle manifest first because it travels with the artifact."""
    root = Path(root or ROOT)
    entries = {}
    aggregate = root / 'opponents' / 'manifest.json'
    if aggregate.is_file():
        for row in json.loads(aggregate.read_text(encoding='utf-8')):
            entries[row['id']] = dict(row)
    for path in sorted((root / 'opponents' / 'public').glob('*/main.manifest.json')):
        row = json.loads(path.read_text(encoding='utf-8'))
        entries[row.get('id', path.parent.name)] = dict(row)
    if include_recorded:
        for path in sorted((root / 'opponents').glob('recorded*/ep*/main.manifest.json')):
            row = json.loads(path.read_text(encoding='utf-8'))
            entries[row.get('id', path.parent.name)] = dict(row)
    return entries


def admission(bundle):
    """How this artifact got here, or why it must not be a panel opponent.

    A panel opponent has to be the thing it claims to be, byte for byte. A notebook or a
    repository commit is that claim and we hold the file. A reconstruction is not: it is
    an assertion that some episode would replay this way, and the assertion is only worth
    something once the episode it names has actually been reproduced. Generating and
    verifying those reproductions is issue #39; refusing the unverified ones is this
    module's job, and the rule exists before the tapes do so they cannot arrive silently.
    """
    rebuilt = bundle.get('reconstruction')
    if rebuilt:
        error = reproduction_error(bundle, rebuilt)
        if error:
            return None, (f'reconstructed from episode {rebuilt.get("episode", "unnamed")} '
                          f'without a verified reproduction ({error}); '
                          'a reconstruction enters the panel only after replaying exactly '
                          'the episode it declares it represents')
        return 'episode_reconstruction', None
    if bundle.get('kernel'):
        return 'pinned_notebook', None
    if bundle.get('repository') and bundle.get('commit'):
        return 'pinned_commit', None
    return None, ('neither a kernel nor a repository commit names where this artifact came '
                  'from, so nothing pins it to a source that can be checked again')


def band_of(rank, kind):
    """The band this observation licenses, which is not always the band it names."""
    if kind not in BANDING_KINDS:
        return None
    if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
        return None
    for name, low, high, _ in BANDS:
        if low <= rank <= high:
            return name
    return None


def _observed(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def entries(observations, *, bundles=None, ratings=None):
    """One record per opponent, and one refusal for every opponent that cannot be one."""
    include_recorded = any(row.get('kind') == 'episode_reconstruction'
                           for row in (observations.get('opponents') or {}).values())
    bundles = (load_bundles(include_recorded=include_recorded)
               if bundles is None else dict(bundles))
    ratings = load_ratings() if ratings is None else dict(ratings)
    admitted, refused = {}, {}
    for name, seen in sorted((observations.get('opponents') or {}).items()):
        bundle = bundles.get(name)
        if bundle is None:
            refused[name] = 'no pinned bundle carries this id'
            continue
        how, why = admission(bundle)
        if how is None:
            refused[name] = why
            continue
        # Corpus ingestion carries the dated rating beside the rank. Requiring a second,
        # hand-maintained ratings file would reintroduce the drift this join removes.
        rating = ratings.get(name) or ({'rating': seen.get('rating'),
                                        'kind': seen.get('kind'),
                                        'observed_at': seen.get('observed_at')}
                                       if seen.get('kind') == 'episode_reconstruction' else {})
        kind = seen.get('kind')
        if kind not in KINDS:
            refused[name] = (f'rank observation kind {kind!r} is not one of '
                             f'{", ".join(KINDS)}, so what the rank is evidence of is '
                             'undeclared')
            continue
        # The rank and the rating are two observations of the same team on the same day.
        # If they disagree about what they are evidence of, one of the two files was
        # edited without the other, and that is exactly the drift a snapshot exists to
        # make impossible.
        if rating.get('kind') and rating['kind'] != kind:
            refused[name] = (f'the rank is declared {kind} and opponents/ratings.json '
                             f'declares the rating {rating["kind"]}; one of the two files '
                             'was updated without the other')
            continue
        when = _observed(seen.get('observed_at'))
        if when is None:
            refused[name] = 'the rank observation carries no parseable observed_at'
            continue
        if not str(seen.get('source') or '').strip():
            refused[name] = 'the rank observation names no source snapshot'
            continue
        rank = seen.get('rank')
        admitted[name] = {
            'sha256': bundle.get('sha256') or bundle.get('bundle_sha256'),
            'source_url': bundle.get('source_url'), 'admission': how,
            'lineage': (bundle.get('lineage_h136') or seen.get('lineage_h136')
                        or bundle.get('family') or name),
            'lineage_h24': bundle.get('lineage_h24') or seen.get('lineage_h24'),
            'lineage_h48': bundle.get('lineage_h48') or seen.get('lineage_h48'),
            'lineage_h136': bundle.get('lineage_h136') or seen.get('lineage_h136'),
            'stream_full_hash': (bundle.get('stream_full_hash')
                                 or seen.get('stream_full_hash')),
            'engine': bundle.get('engine'),
            'rank': rank if isinstance(rank, int) and not isinstance(rank, bool) else None,
            'rank_kind': kind, 'rank_of': seen.get('of'),
            'rating': rating.get('rating'), 'rating_kind': rating.get('kind') or None,
            'observed_at': when.isoformat(), 'source': seen.get('source'),
            'episode': seen.get('episode'),
            'coverage': seen.get('coverage'),
            'sample_weight': seen.get('sample_weight'),
            'band': band_of(rank, kind),
            'bands_nothing_because': None if band_of(rank, kind) else (
                f'{kind} bounds the artifact from above and cannot place it in a band'
                if kind not in BANDING_KINDS else
                'no rank observed' if rank is None else f'rank {rank} is outside every band'),
        }
    return admitted, refused


def coverage(opponents):
    """Who is in each band, how many independent lineages carry it, and the holes."""
    report = {}
    for name, _, _, threshold in BANDS:
        members = sorted(key for key, row in opponents.items() if row['band'] == name)
        lineages = {}
        for key in members:
            lineages.setdefault(opponents[key]['lineage'], []).append(key)
        largest = max((len(group) for group in lineages.values()), default=0)
        report[name] = {
            'opponents': members, 'threshold': threshold,
            'lineages': {group: sorted(keys) for group, keys in sorted(lineages.items())},
            'lineage_count': len(lineages),
            'largest_lineage_share': (largest / len(members)) if members else None,
            'decisive': name in TOP20_BANDS}
    report['unbanded'] = {
        'opponents': sorted(key for key, row in opponents.items() if row['band'] is None),
        'reasons': {key: row['bands_nothing_because'] for key, row in sorted(opponents.items())
                    if row['band'] is None}}
    return report


def gate(snapshot, *, now=None):
    """Why this panel cannot support a standing, if it cannot. Silence is never a pass."""
    now = (now or datetime.now(timezone.utc)).date()
    refusals = []
    observed = _observed(snapshot.get('observed_at'))
    if observed is None:
        refusals.append('The snapshot carries no parseable observation date, so its '
                        'freshness cannot be checked and it cannot be trusted to describe '
                        'the field as it is now.')
    else:
        age = (now - observed).days
        if age > MAX_PANEL_AGE_DAYS:
            refusals.append(f'The panel was observed {age} days ago, past the '
                            f'{MAX_PANEL_AGE_DAYS}-day window; the public field reorders '
                            'daily and a stale panel measures a field that no longer exists.')
    for name, _, _, _ in BANDS:
        band = snapshot['coverage'][name]
        if not band['decisive']:
            continue
        if not band['opponents']:
            refusals.append(f'Band {name} is empty. A gate over a band with no opponent in '
                            'it cannot orient a top-ten finish, whatever the arithmetic '
                            'underneath says.')
            continue
        if band['lineage_count'] < MIN_LINEAGES_PER_BAND:
            refusals.append(f'Band {name} is carried by {band["lineage_count"]} lineage(s); '
                            f'{MIN_LINEAGES_PER_BAND} independent lineages are the least '
                            'that separates beating a band from beating a family.')
        elif band['largest_lineage_share'] > MAX_LINEAGE_SHARE:
            refusals.append(f'Band {name} is {band["largest_lineage_share"]:.0%} one '
                            f'lineage, over the {MAX_LINEAGE_SHARE:.0%} concentration '
                            'limit; the second lineage is not checking the first.')
    return {'verdict': 'FAIL' if refusals else 'PASS', 'refusals': refusals,
            'reasons': refusals or ['Every decisive band is populated by at least '
                                    f'{MIN_LINEAGES_PER_BAND} independent lineages and the '
                                    'observation is inside the freshness window.']}


def build(observations, *, bundles=None, ratings=None, now=None):
    """One immutable panel revision from the pinned artifacts and a dated observation."""
    now = now or datetime.now(timezone.utc)
    admitted, refused = entries(observations, bundles=bundles, ratings=ratings)
    dates = sorted(row['observed_at'] for row in admitted.values())
    body = {
        'schema_version': SCHEMA_VERSION,
        # The oldest admitted observation, not the newest: a panel is only as current as
        # its most out-of-date member, and averaging that away is how a stale opponent
        # rides in behind a fresh one.
        'observed_at': dates[0] if dates else None,
        'newest_observation': dates[-1] if dates else None,
        'source': observations.get('source'),
        'opponents': admitted, 'refused': refused,
        'policy': {'max_panel_age_days': MAX_PANEL_AGE_DAYS,
                   'min_lineages_per_band': MIN_LINEAGES_PER_BAND,
                   'max_lineage_share': MAX_LINEAGE_SHARE,
                   'banding_kinds': list(BANDING_KINDS),
                   'bands': [[name, low, high, threshold]
                             for name, low, high, threshold in BANDS]}}
    sampling_weights = [row['sample_weight'] for row in admitted.values()
                        if isinstance(row.get('sample_weight'), (int, float))]
    total_weight = sum(sampling_weights)
    squared_weight = sum(value * value for value in sampling_weights)
    body['sampling'] = {'weighted_opponents': len(sampling_weights),
                        'weight_sum': total_weight,
                        'effective_sample_size': (total_weight * total_weight /
                                                  squared_weight
                                                  if squared_weight else 0.0)}
    snapshot = {**body, 'coverage': coverage(admitted)}
    snapshot['gate'] = gate(snapshot, now=now)
    # The revision is a function of the evidence and the policy, never of the clock: two
    # builds from the same inputs are the same revision, and any change to either is a
    # different one.
    return {**snapshot, 'revision': digest(body), 'built_at': now.isoformat()}


def ranks(snapshot):
    """The `ranks` mapping `eval.standing` bands by, derived rather than typed."""
    return {name: row['rank'] for name, row in snapshot['opponents'].items()
            if row['band'] is not None}


def lineages(snapshot):
    return {name: row['lineage'] for name, row in snapshot['opponents'].items()}


def reference(snapshot):
    """What a standing records about the panel it ran against."""
    return {'revision': snapshot['revision'], 'observed_at': snapshot['observed_at'],
            'built_at': snapshot.get('built_at'), 'source': snapshot.get('source'),
            'opponents': {name: row['sha256'] for name, row
                          in sorted(snapshot['opponents'].items())},
            'coverage': snapshot['coverage'], 'gate': snapshot['gate']}


def save(snapshot, directory=None):
    """Write a revision once. Re-writing it is a no-op; changing it is an error.

    Updating the meta must not rewrite the evidence a past decision was made on, so the
    file is named by the revision and a second, different snapshot is a second file. The
    index is append-only for the same reason: it is the record of what we believed and
    when, not a view of what we believe now.
    """
    directory = Path(directory or PANELS)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'panel-{snapshot["revision"][:12]}.json'
    body = json.dumps(snapshot, indent=2, sort_keys=True, default=str) + '\n'
    if path.exists():
        kept = json.loads(path.read_text(encoding='utf-8'))
        # `built_at` is the only field allowed to differ: it is when we wrote the file,
        # not part of what the file claims.
        if digest({k: v for k, v in kept.items() if k != 'built_at'}) != \
           digest({k: v for k, v in snapshot.items() if k != 'built_at'}):
            raise ValueError(f'Revision {snapshot["revision"][:12]} already exists with '
                             'different content; a revision is written once')
        return path
    path.write_text(body, encoding='utf-8')
    index_path = directory / INDEX
    index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else []
    index.append({'revision': snapshot['revision'], 'file': path.name,
                  'observed_at': snapshot['observed_at'], 'built_at': snapshot['built_at'],
                  'verdict': snapshot['gate']['verdict'],
                  'opponents': sorted(snapshot['opponents'])})
    index_path.write_text(json.dumps(index, indent=2, default=str) + '\n', encoding='utf-8')
    return path


def load(path):
    """A revision from disk, refusing one whose content no longer hashes to its name."""
    snapshot = json.loads(Path(path).read_text(encoding='utf-8'))
    body = {key: value for key, value in snapshot.items()
            if key not in ('revision', 'built_at', 'coverage', 'gate')}
    if digest(body) != snapshot.get('revision'):
        raise ValueError(f'{path} was edited after it was written: its content hashes to '
                         f'{digest(body)[:12]}, not the {str(snapshot.get("revision"))[:12]} '
                         'it is filed under')
    return snapshot


def render(snapshot):
    lines = [f'PANEL {snapshot["revision"][:12]}  observed {snapshot["observed_at"]}',
             f'gate = {snapshot["gate"]["verdict"]}', '']
    for name, _, _, threshold in BANDS:
        band = snapshot['coverage'][name]
        share = band['largest_lineage_share']
        lines.append(f'  {name:<12} {len(band["opponents"]):>2} opponent(s)  '
                     f'{band["lineage_count"]} lineage(s)  '
                     f'{"concentration " + format(share, ".0%") if share is not None else "empty"}'
                     f'{"  decisive" if band["decisive"] else ""}')
        if band['opponents']:
            lines.append('               ' + ', '.join(band['opponents']))
    unbanded = snapshot['coverage']['unbanded']
    for name in unbanded['opponents']:
        lines.append(f'  unbanded     {name}: {unbanded["reasons"][name]}')
    for name, why in sorted((snapshot.get('refused') or {}).items()):
        lines.append(f'  refused      {name}: {why}')
    lines += ['', 'gate']
    lines += ['  - ' + reason for reason in snapshot['gate']['reasons']]
    return '\n'.join(lines) + '\n'


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--observations', default=str(OBSERVATIONS),
                        help='dated rank observations for the pinned opponents')
    parser.add_argument('--panels', default=str(PANELS), help='revision directory')
    parser.add_argument('--write', action='store_true',
                        help='file the revision; without it nothing is written')
    args = parser.parse_args()
    observations = json.loads(Path(args.observations).read_text(encoding='utf-8'))
    snapshot = build(observations)
    print(render(snapshot), end='')
    if args.write:
        print(f'filed {save(snapshot, args.panels)}')


if __name__ == '__main__':
    main()
