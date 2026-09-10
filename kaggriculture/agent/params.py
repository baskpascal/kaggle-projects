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
    # Issue #26: reserve list positions only for planner-accepted essentials.
    'market_slot_reservation': True,
    # ENGINE_FINDINGS O3/O4, kept as switches so the lift can be ablated the way
    # `sell_fraction` and `fertilize` are. Both default on.
    'care_pricing': True,   # price CARE by the unit it banks, not a flat 45
    'last_day_water': True,  # water on the final day while a turn remains to harvest
    # Planner v1. Off keeps the historical static-plan baseline exactly reproducible;
    # candidate artifacts enable it explicitly until the competitive gate promotes it.
    'economic_planner': False,
    'animal_cap': 22,
    # A full demand-sized herd consumes nearly every starting tile before crops can fund
    # it. Stage a small mixed herd first, then unlock the demand target after this day.
    'animal_bootstrap_cap': 5,
    'animal_ramp_day': 5,
    'land_reservation': False,
    # Reactive inventory arbitrage. Disabled for all historical artifacts; candidates
    # opt in only after the observed town demand makes the round trip profitable.
    'market_arbitrage': False,
    'arbitrage_horizon_days': 3,
    'arbitrage_min_roi': 0.08,
    'arbitrage_max_units': 40,
    'arbitrage_storage_limit': 80,
    # Unit actions resolve before the market. A sale at a lower order index can fund a
    # later purchase in the same turn. Keep this at zero for the historical baseline.
    'sale_financing_fraction': 0.0,
}
