"""The regime library indexes economies by shop context, so its grouping and its naming
have to be stable: a regime that is really a mixed herd must not be filed as sheep-led, and
a configuration supported by two seats must not be reported beside one supported by forty."""
import pytest

from experiments.regime_library import by_shop_identity, regime, tabulate


def seat(team, shops, animals, reward, opponent_reward, crops=10):
    return {'episode_id': 1, 'seat': 0, 'team': team, 'opponent_team': 'other',
            'reward': reward, 'opponent_reward': opponent_reward,
            'shops_at_route': list(shops), 'shops_sorted': sorted(shops),
            'midgame': {'cash': 1.0, 'hands': 3,
                        'animals': {'COW': animals.get('COW', 0),
                                    'SHEEP': animals.get('SHEEP', 0),
                                    'GOOSE': animals.get('GOOSE', 0)},
                        'structures': {'PASTURE': 0, 'COOP': 0},
                        'crops': {'WHEAT': crops}, 'crop_tiles': crops,
                        'land_used': 40, 'prices': {}}}


def test_regime_names_a_herd_by_its_dominant_species_only_when_it_dominates():
    assert regime({'animals': {'SHEEP': 10, 'COW': 1, 'GOOSE': 0}}) == 'sheep_led'
    assert regime({'animals': {'COW': 9, 'SHEEP': 0, 'GOOSE': 0}}) == 'cow_led'
    assert regime({'animals': {'SHEEP': 4, 'COW': 4, 'GOOSE': 3}}) == 'mixed_herd'
    assert regime({'animals': {'SHEEP': 0, 'COW': 0, 'GOOSE': 0}}) == 'crops_only'


def test_a_bare_majority_still_counts_as_led():
    assert regime({'animals': {'SHEEP': 5, 'COW': 4, 'GOOSE': 1}}) == 'sheep_led'
    assert regime({'animals': {'SHEEP': 5, 'COW': 5, 'GOOSE': 1}}) == 'mixed_herd'


def test_tabulate_keeps_only_strong_teams():
    rows = [seat('strong', ['A', 'B'], {'SHEEP': 10}, 100, 50),
            seat('weak', ['A', 'B'], {'SHEEP': 10}, 100, 50)]
    ratings = {'strong': 2950.0, 'weak': 2500.0}
    report = tabulate(rows, ratings, min_rating=2900., min_support=1)
    assert report['strong_seats'] == 1
    assert report['configurations'][0]['n'] == 1


def test_tabulate_drops_configurations_without_support():
    rows = [seat('strong', ['A', 'B'], {'SHEEP': 10}, 100, 50),
            seat('strong', ['C', 'D'], {'COW': 10}, 100, 50),
            seat('strong', ['C', 'D'], {'COW': 10}, 100, 50)]
    report = tabulate(rows, {'strong': 2950.0}, min_rating=2900., min_support=2)
    assert [entry['shops'] for entry in report['configurations']] == [['C', 'D']]


def test_tabulate_orders_shops_so_the_two_arrival_orders_are_one_configuration():
    rows = [seat('strong', ['YARN_STORE', 'BAKERY'], {'SHEEP': 10}, 100, 50),
            seat('strong', ['BAKERY', 'YARN_STORE'], {'SHEEP': 10}, 100, 50)]
    report = tabulate(rows, {'strong': 2950.0}, min_rating=2900., min_support=1)
    assert len(report['configurations']) == 1
    assert report['configurations'][0]['shops'] == ['BAKERY', 'YARN_STORE']
    assert report['configurations'][0]['n'] == 2


def test_a_configuration_reports_each_regime_with_its_own_win_rate():
    rows = [seat('a', ['X', 'Y'], {'SHEEP': 10}, 100, 50),
            seat('b', ['X', 'Y'], {'SHEEP': 10}, 100, 50),
            seat('c', ['X', 'Y'], {'COW': 10}, 10, 50)]
    ratings = {'a': 2950., 'b': 2950., 'c': 2950.}
    report = tabulate(rows, ratings, min_rating=2900., min_support=1)
    variants = {entry['regime']: entry for entry in report['configurations'][0]['regimes']}
    assert variants['sheep_led']['n'] == 2 and variants['sheep_led']['win_rate'] == 1.0
    assert variants['cow_led']['n'] == 1 and variants['cow_led']['win_rate'] == 0.0


def test_by_shop_identity_needs_both_sides_before_it_reports_anything():
    rows = [seat(f'team{index}', ['YARN_STORE', 'BAKERY'], {'SHEEP': 10}, 100, 50)
            for index in range(8)]
    ratings = {f'team{index}': 2950. for index in range(8)}
    assert by_shop_identity(rows, ratings, min_rating=2900.) == []


def test_by_shop_identity_contrasts_present_against_absent():
    rows = [seat(f'y{i}', ['YARN_STORE', 'BAKERY'], {'SHEEP': 12}, 100, 50) for i in range(6)]
    rows += [seat(f'n{i}', ['PIZZA_SHOP', 'BAKERY'], {'COW': 12}, 100, 50) for i in range(6)]
    ratings = {row['team']: 2950. for row in rows}
    entries = {entry['shop']: entry for entry in by_shop_identity(rows, ratings)}
    yarn = entries['YARN_STORE']
    assert yarn['present'] == 6 and yarn['absent'] == 6
    assert yarn['sheep_present_median'] == 12 and yarn['sheep_absent_median'] == 0
    assert yarn['regimes_present'][0][0] == 'sheep_led'
    assert yarn['regimes_absent'][0][0] == 'cow_led'
