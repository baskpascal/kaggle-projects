import copy

import pytest

from arena.engine import official
from arena.match import Audit
from eval.compare import compare


def test_audit_counts_actual_sales_and_both_overflow_paths():
    module = official()
    audit = Audit(module)
    farm = module._new_farm(10, 3000)
    private = module._new_private()
    private['shed']['MELON'] = 99
    private['inventories'][0]['MELON'] = 5
    audit.apply(farm, private, 0, ['DROP'], 10, 0, 24, 100)
    assert audit.counts[0]['overflow_items'] == 4
    market = module._new_market()
    assert audit.commit('SELL', 'MELON', 250, farm, private, market)
    assert audit.sales[0]['MELON'] == {'units': 1, 'revenue': 250}
    private['inventories'][0]['MELON'] = 3
    audit.drop(private, 100)
    assert audit.counts[0]['overflow_items'] == 6
    private['shed']['WOOL'] = 0
    assert not audit.commit('SELL', 'WOOL', 200, farm, private, market)
    assert 'WOOL' not in audit.sales[0]


def rows():
    return [dict(seed=seed, seat=seat, opponent='fixed', candidate_hash='old',
                 opponent_hash='fixed-hash', configuration={}, environment={}, backend='fast',
                 score=0, money=20, margin=-10, unsold_items=0, runtime_ms=[1],
                 failures=[], opponent_failures=[], audit={}, sales={})
            for seed in (1, 2) for seat in (0, 1)]


def test_paired_comparison_reports_absent_wool_as_unmeasured():
    before = rows()
    after = copy.deepcopy(before)
    for row in after:
        row.update(candidate_hash='new', score=1, money=40, margin=10)
    result = compare(before, after)
    assert result['paired_deltas']['score']['mean'] == 1
    assert result['paired_deltas']['money']['ci95'] == [20, 20]
    assert result['premium_sales']['WOOL']['delta_per_unit'] is None
    assert result['premium_sales']['WOOL']['delta_per_unit_ci95'] == [None, None]


def test_sales_interval_resamples_revenue_and_volume_together():
    before = rows()
    after = copy.deepcopy(before)
    for old, new in zip(before, after):
        old['sales'] = {'WOOL': {'units': 2, 'revenue': 20}}
        new['sales'] = {'WOOL': {'units': 4, 'revenue': 44}}
    result = compare(before, after)
    assert result['premium_sales']['WOOL']['delta_per_unit'] == 1
    assert result['premium_sales']['WOOL']['delta_per_unit_ci95'] == [1, 1]


@pytest.mark.parametrize('field,value', [('opponent_hash', 'changed'), ('configuration', {'x': 1}),
                                        ('failures', ['crash']), ('candidate_hash', 'mixed')])
def test_paired_comparison_rejects_contaminated_evidence(field, value):
    before = rows()
    after = copy.deepcopy(before)
    after[0][field] = value
    with pytest.raises(ValueError):
        compare(before, after)


def test_paired_comparison_rejects_missing_or_duplicate_games():
    before = rows()
    with pytest.raises(ValueError):
        compare(before, before[:-1])
    with pytest.raises(ValueError):
        compare(before + before[:1], before + before[:1])
