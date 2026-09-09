from io import BytesIO
import zipfile

import pytest

from scripts.sync_kaggle_replays import index_rows, select_release


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
