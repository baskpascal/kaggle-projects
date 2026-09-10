"""The extractor must read the recorded clock and never see past the decision turn."""
import json

import pytest

from experiments.elite_events import (agrees, canonical_turn, planner_intent,
                                      rank_divergences, strategic_events,
                                      summarize_state)

CONFIG = {'turnsPerDay': 24}
CROPS = ('WHEAT', 'CARROT', 'TOMATO', 'STRAWBERRY', 'MELON')
GOODS = CROPS + ('MILK', 'WOOL', 'EGG', 'FERTILIZER', 'COW', 'SHEEP', 'GOOSE')


def _tiles(size=2):
    return [[None] * size for _ in range(size)]


def _observation(day=0, hour=0, *, step=None, money=3000., shops=('YARN_STORE',)):
    farm = {'farmer': [0, 0], 'hands': [], 'hires_today': 0, 'money': money,
            'tiles': _tiles(), 'unlocked_quadrants': [0]}
    other = {'farmer': [1, 1], 'hands': [], 'hires_today': 0, 'money': 2500.,
             'tiles': _tiles(), 'unlocked_quadrants': [0]}
    observation = {
        'day': day, 'hour': hour, 'player': 0, 'farms': [farm, other],
        'town': {'unlocked_shops': list(shops)},
        'market': {'prices': {good: 10. for good in GOODS},
                   'inventory': {good: 100 for good in GOODS}},
        'private': {'inventories': [{}], 'seeds': {crop: 0 for crop in CROPS},
                    'shed': {good: 0 for good in GOODS}},
    }
    if step is not None:
        observation['step'] = step
    return observation


def test_canonical_turn_survives_missing_step_for_seat_one():
    # Recorded dumps omit `step` for seat 1; day/hour is the authority.
    assert canonical_turn(_observation(day=3, hour=7), CONFIG) == 79


def test_canonical_turn_refuses_a_clock_that_disagrees():
    with pytest.raises(ValueError):
        canonical_turn(_observation(day=3, hour=7, step=80), CONFIG)


def test_state_summary_reports_only_observable_fields():
    state = summarize_state(_observation(day=1, hour=2, money=1234.), CONFIG)
    assert state['turn'] == 26 and state['cash'] == 1234.
    assert state['known_shops'] == ['YARN_STORE']
    assert state['opponent_public'] == {'cash': 2500., 'hands': 0, 'land_used': 0}
    # The opponent's private state is never part of a decision record.
    assert 'shed' not in state['opponent_public']


def _frame(observation, action):
    return [{'observation': observation, 'action': action}, {'observation': observation}]


def test_land_purchases_are_indexed_in_order():
    steps = [_frame(_observation(hour=h), {'farmer': ['PASS'], 'hands': [],
                                           'market': [['BUY_LAND']]})
             for h in range(2)]
    events = strategic_events(steps, 0, CONFIG)
    indexes = [d['expansion_index'] for k, d, _ in events if k == 'BUY_LAND']
    assert indexes == [1, 2]


def test_first_animal_of_each_species_is_flagged_once():
    orders = [['BUY_ANIMAL', 'COW', 1], ['BUY_ANIMAL', 'COW', 1],
              ['BUY_ANIMAL', 'SHEEP', 1]]
    steps = [_frame(_observation(hour=h), {'farmer': ['PASS'], 'hands': [],
                                           'market': [order]})
             for h, order in enumerate(orders)]
    flags = [(d['species'], d['first_of_species'], d['mix_switch'])
             for k, d, _ in strategic_events(steps, 0, CONFIG) if k == 'BUY_ANIMAL']
    assert flags == [('COW', True, False), ('COW', False, False), ('SHEEP', True, True)]


def test_shop_reveal_reports_only_the_new_shop():
    steps = [_frame(_observation(hour=0, shops=('YARN_STORE',)), {'market': []}),
             _frame(_observation(hour=1, shops=('YARN_STORE', 'BAKERY')), {'market': []})]
    reveals = [d['revealed'] for k, d, _ in strategic_events(steps, 0, CONFIG)
               if k == 'SHOP_REVEAL']
    assert reveals == [['BAKERY']]


def test_planner_intent_reads_one_turn_and_returns_macro_counts():
    macro = planner_intent(_observation(money=50000.), CONFIG)
    assert set(macro) == {'buy_land', 'buy_animal', 'hire', 'build', 'sell_units'}
    assert isinstance(macro['buy_land'], int)


def test_agreement_is_undefined_for_context_events():
    macro = {'buy_land': 1, 'buy_animal': {}, 'hire': 0, 'build': [], 'sell_units': 0}
    assert agrees('BUY_LAND', {'expansion_index': 1}, macro) is True
    assert agrees('SHOP_REVEAL', {'revealed': ['BAKERY']}, macro) is None


def test_ranking_ignores_context_events_and_orders_by_disagreements():
    rows = [
        {'event': 'BUY_LAND', 'detail': {'expansion_index': 1}, 'agrees': False,
         'team': 'A', 'state': {'turn': 40, 'cash': 1000.}},
        {'event': 'BUY_LAND', 'detail': {'expansion_index': 1}, 'agrees': False,
         'team': 'B', 'state': {'turn': 50, 'cash': 1200.}},
        {'event': 'HIRE_BURST', 'detail': {}, 'agrees': True,
         'team': 'A', 'state': {'turn': 60, 'cash': 900.}},
        {'event': 'SHOP_REVEAL', 'detail': {}, 'agrees': None,
         'team': 'A', 'state': {'turn': 10, 'cash': 3000.}},
    ]
    ranked = rank_divergences(rows)
    assert ranked[0]['decision'] == 'BUY_LAND#1'
    assert ranked[0]['disagreements'] == 2 and ranked[0]['teams'] == 2
    assert all(row['decision'] != 'SHOP_REVEAL' for row in ranked)
