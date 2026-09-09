import pytest

from scripts.calibrate_ray_capacity import parse_worker_caps, recommendations


def test_worker_caps_accept_max_and_remove_duplicates():
    assert parse_worker_caps('1, 4,8,max,8') == [1, 4, 8, None]


@pytest.mark.parametrize('value', ['', '0', '-2', 'fast'])
def test_worker_caps_refuse_invalid_values(value):
    with pytest.raises(ValueError):
        parse_worker_caps(value)


def test_recommendations_are_per_hostname_and_use_measured_throughput():
    rows = [
        {'hostname': 'pc-b', 'workers': 4, 'jobs_per_second': 4.0},
        {'hostname': 'pc-a', 'workers': 8, 'jobs_per_second': 8.0},
        {'hostname': 'pc-b', 'workers': 8, 'jobs_per_second': 5.0},
        {'hostname': 'pc-a', 'workers': 4, 'jobs_per_second': 7.0},
    ]
    assert [(row['hostname'], row['workers']) for row in recommendations(rows)] == [
        ('pc-a', 8), ('pc-b', 8)]
