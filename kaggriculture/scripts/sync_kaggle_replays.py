#!/usr/bin/env python3
"""Fetch the newest public Kaggriculture daily replay dump without Kaggle credentials."""
from __future__ import annotations

import argparse
import csv
import hashlib
from io import BytesIO, TextIOWrapper
import json
from pathlib import Path
import sys
import zipfile

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments.episode_tapes import collect  # noqa: E402

INDEX_REF = 'kaggle/kaggriculture-episodes-index'
API = 'https://www.kaggle.com/api/v1/datasets/download'


def index_rows(content):
    with zipfile.ZipFile(BytesIO(content)) as archive:
        names = [name for name in archive.namelist() if name.endswith('.csv')]
        if names != ['manifest.csv']:
            raise ValueError(f'Unexpected episode index contents: {names}')
        with archive.open(names[0]) as raw:
            rows = list(csv.DictReader(TextIOWrapper(raw, encoding='utf-8')))
    required = {'date', 'daily_dataset_slug', 'daily_dataset_url',
                'episode_count', 'total_bytes'}
    if not rows or not required.issubset(rows[0]):
        raise ValueError('Episode index schema is incomplete')
    return rows


def select_release(rows, date=None):
    matches = [row for row in rows if date is None or row['date'] == date]
    if not matches:
        raise ValueError(f'No replay dump for {date}')
    return max(matches, key=lambda row: row['date'])


def download(session, url, target):
    target = Path(target)
    if target.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.part')
    with session.get(url, stream=True, timeout=(30, 300)) as response:
        response.raise_for_status()
        with temporary.open('wb') as output:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    output.write(chunk)
    temporary.replace(target)
    return True


def archive_evidence(path, expected_episodes):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    with zipfile.ZipFile(path) as archive:
        episodes = [name for name in archive.namelist() if name.lower().endswith('.json')]
    if len(episodes) != expected_episodes:
        raise ValueError(f'Archive has {len(episodes)} episodes; index declares '
                         f'{expected_episodes}')
    return {'path': str(path), 'bytes': path.stat().st_size,
            'sha256': digest.hexdigest(), 'episode_files': len(episodes)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', help='YYYY-MM-DD; default is the newest indexed dump')
    parser.add_argument('--destination', default='data/kaggle/official')
    parser.add_argument('--library', help='also extract a corrected, deduplicated tape library')
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()

    session = requests.Session()
    response = session.get(f'{API}/{INDEX_REF}', timeout=60)
    response.raise_for_status()
    release = select_release(index_rows(response.content), args.date)
    slug = release['daily_dataset_slug']
    target = Path(args.destination) / f'{slug}.zip'
    fetched = download(session, f'{API}/kaggle/{slug}', target)
    evidence = archive_evidence(target, int(release['episode_count']))
    report = {'schema_version': 1, 'index_ref': INDEX_REF, 'release': release,
              'downloaded': fetched, 'archive': evidence,
              'action_stream_slice': 'steps[1:]'}
    if args.library:
        library = collect(target, args.library, workers=args.workers)
        report['library'] = {'path': args.library, 'episodes': library['episodes'],
                             'tapes': len(library['tapes']),
                             'errors': library['error_count']}
    sidecar = target.with_suffix('.source.json')
    sidecar.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
