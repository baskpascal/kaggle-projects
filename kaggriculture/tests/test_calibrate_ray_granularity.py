import pytest

from scripts.calibrate_ray_granularity import best_measurement, parse_groups
from scripts.calibrate_ray_capacity import result_signature


def test_cpu_groups_are_positive_unique_in_declared_order():
    assert parse_groups('2, 4,2,8') == [2, 4, 8]


@pytest.mark.parametrize('value', ['', '0', '-2', 'fast'])
def test_cpu_groups_refuse_invalid_values(value):
    with pytest.raises(ValueError):
        parse_groups(value)


def test_recommendation_uses_measured_distributed_throughput():
    rows = [
        {'cpus_per_worker': 2, 'jobs_per_second': 4.25},
        {'cpus_per_worker': 4, 'jobs_per_second': 5.58},
    ]
    assert best_measurement(rows)['cpus_per_worker'] == 4


def test_distributed_signature_ignores_completion_order_but_preserves_exact_values():
    first = {'seed': 1, 'winner': 0, 'our_money': 1.0, 'opponent_money': 0.0}
    second = {'seed': 2, 'winner': 1, 'our_money': 0.0, 'opponent_money': 1.0}

    assert result_signature([first, second]) == result_signature([second, first])
    assert result_signature([first]) != result_signature([{**first, 'our_money': 1.0000001}])
