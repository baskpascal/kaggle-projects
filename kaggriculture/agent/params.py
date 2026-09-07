DEFAULTS = {
    'max_hands': 8,
    'cash_reserve': 250,
    'travel_cost': 3.0,
    'opponent_weight': 0.8,
    'adaptive': True,
    'only_crop': None,
    'max_quadrants': 2,
    'expand_day': 7,
    'animal_target': 0,
    'animal_type': 'GOOSE',
    'water_daily': True,
    'plant_until_hour': 18,
    'sell_fraction': 1.0,  # Legacy dump policy, for controlled ablations.
    'adaptive_sales': True,
    'sale_floor_fraction': 0.85,
    'sale_recovery_fraction': 0.98,
    'shed_buffer': 20,
    'risk_window_days': 5,
    'risk_min_buffer': 1000,
    'risk_buffer_fraction': 0.1,
    'continuity_completion': 1.0,  # >1 favours finishing the job underfoot.
    'planned_feedback': False,  # Issue #7: re-score crops as plantings commit.
    'fertilize': True,  # Issue #6: emit FERTILIZE when it pays for itself.
}
