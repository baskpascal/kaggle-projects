"""Marginal-price sales with bounded holding and explicit storage pressure."""
import math

from .economy import CURVES, daily_demand, price, sale_value


def sale_orders(state, params, shed, reserve_wheat=0, reserve_fertilizer=0):
    limit = state.config.get('maxMarketOrdersPerTurn', 10)
    overrides = state.config.get('marketParams')
    # Hold back stock a unit is already walking to the shed for. Without this the
    # fertilizer is sold before the unit arrives and the FERTILIZE no-ops.
    reserved = {'WHEAT': reserve_wheat, 'FERTILIZER': reserve_fertilizer}
    available = {item: max(0, amount - reserved.get(item, 0))
                 for item, amount in shed.items() if item in CURVES and amount > 0}
    counts = {}
    for item, amount in available.items():
        if not amount:
            continue
        if state.days_left < 1:
            counts[item] = amount
            continue
        if not params['adaptive_sales']:
            counts[item] = (amount if state.days_left < 2 else
                            max(1, int(amount * params['sell_fraction'])))
            continue
        inventory = state.obs['market']['inventory'][item]
        demand = daily_demand(state, item)
        base = (overrides or {}).get(item, {}).get('base', CURVES[item][0])
        # Only existing shops count. Future unlocks and opponent trades are unknown.
        recovered = price(item, inventory - demand, overrides)
        threshold = max(base * params['sale_floor_fraction'],
                        recovered * params['sale_recovery_fraction'])
        count = 0
        while count < amount:
            marginal = sale_value(item, 1, inventory, overrides)
            if marginal < threshold:
                break
            count += 1
            if marginal > 1:
                inventory += 1
        # Do not hoard more than known demand can absorb before liquidation.
        future_demand = math.floor(demand * max(0, state.days_left - 1))
        counts[item] = max(count, amount - future_demand)

    # Reserve space for carried goods and a harvest buffer before the next deposit.
    carried = sum(sum(inv.values()) for inv in state.private['inventories'])
    capacity = state.config.get('shedCapacity', 100)
    target = max(0, capacity - carried - params['shed_buffer'])
    pressure = max(0, sum(shed.values()) - sum(counts.values()) - target)
    storage_pressure = pressure > 0
    # Prefer selling products with the least one-day price recovery to make room.
    def holding_gain(item):
        inventory = state.obs['market']['inventory'][item]
        return (price(item, inventory - daily_demand(state, item), overrides)
                - price(item, inventory, overrides))
    for item in sorted(available, key=lambda item: (holding_gain(item), item)):
        extra = min(pressure, available[item] - counts.get(item, 0))
        counts[item] = counts.get(item, 0) + extra
        pressure -= extra
    # Usually nine products fit in ten slots; small custom limits need prioritization.
    items = sorted((item for item, count in counts.items() if count),
                   key=lambda item: (-counts[item] if storage_pressure else
                       -sale_value(item, counts[item], state.obs['market']['inventory'][item], overrides), item))
    return [['SELL', item, counts[item]] for item in items[:limit]]


def projected_shed(state, actions):
    """Mirror PICKUP/DROP order so newly delivered stock can sell this turn."""
    shed = dict(state.private['shed'])
    capacity = state.config.get('shedCapacity', 100)
    half = state.size // 2
    access = {(half - 1, half - 1), (half, half - 1),
              (half - 1, half), (half, half)}
    for pos, inv, action in zip(state.positions, state.private['inventories'], actions):
        if tuple(pos) not in access:
            continue
        if action[0] == 'PICKUP':
            item, count = action[1:3]
            shed[item] = max(0, shed.get(item, 0) - count)
        elif action[0] == 'DROP':
            for item, count in inv.items():
                room = max(0, capacity - sum(shed.values()))
                shed[item] = shed.get(item, 0) + min(count, room)
    return shed
