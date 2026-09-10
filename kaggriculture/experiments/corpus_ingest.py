"""Admit untrusted public-corpus metadata through a deterministic engine gate.

The input directory is a revision of the public ``kaggriculture-episodes`` dataset.
Nothing in it is trusted implicitly: episode type and engine version are joined and
checked before a seat reaches the eligible library.  The result deliberately contains
metadata only; reconstructed executable opponents still need the reproduction proof
enforced by :mod:`eval.panel`.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

ENGINE = '1.32.7'
PUBLIC = 'EPISODE_TYPE_PUBLIC'
SCHEMA_VERSION = 1
FILES = ('episodes.csv', 'episode_features.csv', 'stream_hashes.csv',
         'per_submission_coverage.csv', 'teams.csv')


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def source_identity(directory, dataset):
    """Content-address a dataset revision by an ordered file manifest."""
    root = Path(directory)
    missing = [name for name in FILES if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(f'Corpus revision lacks {", ".join(missing)}')
    files = [{'path': name, 'size': (root / name).stat().st_size,
              'sha256': file_digest(root / name)} for name in FILES]
    body = {'dataset': dataset, 'files': files}
    return {**body, 'revision': hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}


def _key(row):
    return int(row['episode_id']), int(row['seat'])


def _seat_table(path):
    with Path(path).open(encoding='utf-8', newline='') as source:
        return {_key(row): row for row in csv.DictReader(source)}


def _coverage(path):
    with Path(path).open(encoding='utf-8', newline='') as source:
        return {int(row['submission_id']): float(row['coverage'])
                for row in csv.DictReader(source)}


def _teams(path):
    with Path(path).open(encoding='utf-8', newline='') as source:
        rows = list(csv.DictReader(source))
    required = {'team_id', 'team_name', 'ladder_score'}
    if rows and not required <= set(rows[0]):
        raise ValueError(f'teams.csv needs {", ".join(sorted(required))}')
    ranked = sorted(rows, key=lambda row: (-float(row['ladder_score']),
                                           int(row['team_id'])))
    total = len(ranked)
    return {int(row['team_id']): {'team': row['team_name'],
                                  'rating': float(row['ladder_score']),
                                  'rank': rank, 'of': total}
            for rank, row in enumerate(ranked, 1)}


def capped_weight(coverage, cap=10.0):
    if not math.isfinite(coverage) or coverage < 0 or coverage > 1:
        raise ValueError(f'Invalid submission coverage {coverage!r}')
    if not math.isfinite(cap) or cap < 1:
        raise ValueError('Coverage weight cap must be finite and at least one')
    return min(cap, 1.0 / coverage) if coverage else cap


def effective_sample_size(weights):
    total = sum(weights)
    squares = sum(value * value for value in weights)
    return total * total / squares if squares else 0.0


def eligible_rows(directory, *, required_engine=ENGINE, weight_cap=10.0):
    """Return eligible seats and refusal counts from one metadata revision."""
    root = Path(directory)
    features = _seat_table(root / 'episode_features.csv')
    streams = _seat_table(root / 'stream_hashes.csv')
    coverage = _coverage(root / 'per_submission_coverage.csv')
    teams = _teams(root / 'teams.csv')
    rows, refusals = [], {}

    def refuse(reason):
        refusals[reason] = refusals.get(reason, 0) + 1

    with (root / 'episodes.csv').open(encoding='utf-8', newline='') as source:
        for episode in csv.DictReader(source):
            if episode.get('type') != PUBLIC:
                refuse('non_public_episode')
                continue
            episode_id = int(episode['episode_id'])
            for seat in (0, 1):
                key = episode_id, seat
                feature, stream = features.get(key), streams.get(key)
                if feature is None or stream is None:
                    refuse('missing_seat_metadata')
                    continue
                if feature.get('engine_version') != required_engine:
                    refuse('engine_mismatch')
                    continue
                submission = int(episode[f'sub_{seat}'])
                team_id = int(episode[f'team_{seat}'])
                team = teams.get(team_id)
                if team is None:
                    refuse('team_absent_from_snapshot')
                    continue
                observed_coverage = coverage.get(submission)
                if observed_coverage is None:
                    refuse('coverage_missing')
                    continue
                ended = episode.get('end_time') or episode.get('create_time')
                row = {
                    'opponent_id': f'corpus-ep{episode_id}s{seat}',
                    'episode_id': episode_id, 'seat': seat,
                    'observed_at': str(ended)[:10],
                    'submission_id': submission, 'team_id': team_id,
                    **team, 'engine_version': required_engine,
                    'final_money': float(feature['final_money']),
                    'episode_type': episode['type'],
                    'lineage_h24': stream['stream_h24'],
                    'lineage_h48': stream['stream_h48'],
                    'lineage_h136': stream['stream_h136'],
                    'stream_full_hash': stream['stream_h719'],
                    'coverage': observed_coverage,
                    'sample_weight': capped_weight(observed_coverage, weight_cap),
                }
                rows.append(row)
    rows.sort(key=lambda row: (row['episode_id'], row['seat']))
    return rows, dict(sorted(refusals.items()))


def _write_once(path, payload):
    encoded = (json.dumps(payload, sort_keys=True, indent=2,
                          ensure_ascii=False) + '\n').encode()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise FileExistsError(f'{path} already contains a different corpus revision')
        return path
    path.write_bytes(encoded)
    return path


def ingest(directory, output, *, dataset='georgymamarin/kaggriculture-episodes',
           required_engine=ENGINE, weight_cap=10.0):
    """Write a deterministic eligible library and derived panel observations."""
    identity = source_identity(directory, dataset)
    rows, refusals = eligible_rows(directory, required_engine=required_engine,
                                   weight_cap=weight_cap)
    weights = [row['sample_weight'] for row in rows]
    source = {'dataset': dataset, 'dataset_revision': identity['revision'],
              'files': identity['files']}
    library = {'schema_version': SCHEMA_VERSION, 'kind': 'public_corpus_metadata',
               'source': source, 'required_engine': required_engine,
               'selection': {'episode_type': PUBLIC, 'weight_cap': weight_cap},
               'refused': refusals, 'eligible_seats': len(rows),
               'effective_sample_size': effective_sample_size(weights), 'rows': rows}
    observations = {
        'schema_version': SCHEMA_VERSION, 'source': source,
        'opponents': {row['opponent_id']: {
            'kind': 'episode_reconstruction', 'observed_at': row['observed_at'],
            'source': (f'{dataset}@{identity["revision"][:12]} episode '
                       f'{row["episode_id"]} seat {row["seat"]}, submission '
                       f'{row["submission_id"]}'),
            'rank': row['rank'], 'of': row['of'], 'rating': row['rating'],
            'submission_id': row['submission_id'], 'team_id': row['team_id'],
            'team': row['team'], 'lineage_h24': row['lineage_h24'],
            'lineage_h48': row['lineage_h48'],
            'lineage_h136': row['lineage_h136'],
            'stream_full_hash': row['stream_full_hash'],
            'coverage': row['coverage'], 'sample_weight': row['sample_weight'],
            'episode': str(row['episode_id']), 'seat': row['seat']}
            for row in rows}}
    output = Path(output)
    _write_once(output / 'library.json', library)
    _write_once(output / 'panel-observations.json', observations)
    return {'library': library, 'panel_observations': observations}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--metadata', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--dataset', default='georgymamarin/kaggriculture-episodes')
    parser.add_argument('--engine', default=ENGINE)
    parser.add_argument('--weight-cap', type=float, default=10.0)
    args = parser.parse_args()
    result = ingest(args.metadata, args.output, dataset=args.dataset,
                    required_engine=args.engine, weight_cap=args.weight_cap)
    library = result['library']
    print(json.dumps({'dataset_revision': library['source']['dataset_revision'],
                      'eligible_seats': library['eligible_seats'],
                      'refused': library['refused'],
                      'effective_sample_size': library['effective_sample_size']},
                     indent=2))


if __name__ == '__main__':
    main()
