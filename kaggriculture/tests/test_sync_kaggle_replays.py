from io import BytesIO
import zipfile

import pytest

from scripts.sync_kaggle_replays import (archive_evidence, index_rows, local_identity,
                                         reusable_evidence, select_release)


def index_archive(text):
    output = BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('manifest.csv', text)
    return output.getvalue()


def test_latest_release_comes_from_the_public_index():
    rows = index_rows(index_archive(
        'date,daily_dataset_slug,daily_dataset_url,episode_count,total_bytes\n'
        '2026-09-07,kaggriculture-episodes-2026-09-07,url7,663,10\n'
        '2026-09-08,kaggriculture-episodes-2026-09-08,url8,664,20\n'))
    assert select_release(rows)['daily_dataset_slug'].endswith('09-08')
    assert select_release(rows, '2026-09-07')['episode_count'] == '663'


def test_unknown_date_is_not_silently_replaced_by_latest():
    rows = index_rows(index_archive(
        'date,daily_dataset_slug,daily_dataset_url,episode_count,total_bytes\n'
        '2026-09-08,kaggriculture-episodes-2026-09-08,url,664,20\n'))
    with pytest.raises(ValueError, match='No replay dump'):
        select_release(rows, '2026-09-09')


def test_verified_zip_sidecar_avoids_a_second_full_read(tmp_path, monkeypatch):
    archive = tmp_path / 'daily.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('1.json', '{}')
    evidence = archive_evidence(archive, 1)
    release = {'daily_dataset_slug': 'daily', 'episode_count': '1'}
    sidecar = {'release': release, 'archive': evidence}
    assert reusable_evidence(sidecar, archive, release)
    sidecar['archive']['local_identity']['mtime_ns'] -= 1
    assert not reusable_evidence(sidecar, archive, release)


def test_tampered_or_replaced_sidecar_is_not_reused(tmp_path):
    archive = tmp_path / 'daily.zip'
    archive.write_bytes(b'zip bytes')
    release = {'daily_dataset_slug': 'daily', 'episode_count': '1'}
    base = {'release': release, 'archive': {'sha256': '0' * 64, 'episode_files': 1,
                                            'local_identity': local_identity(archive)}}
    assert reusable_evidence(base, archive, release)
    assert not reusable_evidence({**base, 'release': {**release,
        'daily_dataset_slug': 'other'}}, archive, release)
    base['archive']['sha256'] = 'not-a-digest'
    assert not reusable_evidence(base, archive, release)
