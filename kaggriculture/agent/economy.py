"""Finite-season estimates; forecasts are hypotheses, never hidden observations."""
import math

# seed cost, first harvest, planned peak harvest, harvest quantity, occupancy days
CROPS = {
    'WHEAT': (10, 2, 4, 4, 5),
    'CARROT': (20, 2, 3, 3, 4),
    'TOMATO': (50, 8, 11, 4, 12),
    'STRAWBERRY': (100, 10, 16, 4, 17),
    'MELON': (80, 10, 10, 6, 11),
}
ANIMALS = {
    'GOOSE': (300, 'COOP', 'EGG', 4, 1),
    'COW': (400, 'PASTURE', 'MILK', 8, 2),
    'SHEEP': (500, 'PASTURE', 'WOOL', 6, 3),
}
# base, T, scarcity shape/target, glut shape/target
CURVES = {
    'WHEAT': (25, 400, 'sqrt', .8, 'log', .2),
    'CARROT': (35, 450, 'hinge', 1., 'sqrt', .7),
    'TOMATO': (60, 200, 'hinge', .4, 'sqrt', .6),
    'STRAWBERRY': (120, 100, 'sqrt', .7, 'linear', 1.6),
    'MELON': (250, 300, 'log', .2, 'sq', 3.6),
    'EGG': (50, 332, 'hinge', .4, 'log', .2),
    'MILK': (160, 122, 'sqrt', .6, 'linear', 1.6),
    'WOOL': (200, 105, 'log', .2, 'sq', 3.2),
    'FERTILIZER': (100, 200, 'linear', .4, 'linear', .4),
}
SHOPS = {
    'BAKERY': ('EGG', 'WHEAT'), 'PIZZA_SHOP': ('MILK', 'TOMATO', 'WHEAT'),
    'BRUNCH_SPOT': ('EGG', 'WHEAT', 'STRAWBERRY'), 'YARN_STORE': ('WOOL',),
    'ICE_CREAM_SHOP': ('STRAWBERRY', 'MILK', 'WHEAT'), 'PET_CAFE': ('CARROT',),
    'SMOOTHIE_SHOP': ('STRAWBERRY', 'MILK'),
    'FARMERS_MARKET': ('WHEAT', 'CARROT', 'TOMATO', 'STRAWBERRY'),
}


def shape(name, x, throughput):
    if name == 'sqrt':
        return math.sqrt(x)
    if name == 'log':
        return math.log1p(x)
    if name == 'log10':
        return math.log10(1 + x)
    if name == 'sq':
        return x * x
    if name == 'hinge':
        u = x / throughput
        return u + 8 * max(0, u - 1) ** 2
    return x


def price(item, inventory, overrides=None):
    base, throughput, low, lt, high, ht = CURVES[item]
    p = dict(base=base, T=throughput, I0=10000, below_func=low,
             below_target=lt, above_func=high, above_target=ht)
    p.update((overrides or {}).get(item, {}))
    delta = inventory - p['I0']
    side = 'above' if delta >= 0 else 'below'
    func, target = p[side + '_func'], p[side + '_target']
    move = target * p['base'] * shape(func, abs(delta), p['T']) / shape(func, p['T'], p['T'])
    return max(1, round(p['base'] + (-move if delta >= 0 else move)))


def sale_value(item, count, inventory, overrides=None):
    total = 0
    for _ in range(max(0, int(count))):
        quote = price(item, inventory, overrides)
        total += quote
        if quote > 1:
            inventory += 1
    return total


def daily_demand(state, item):
    if item == 'FERTILIZER':
        return 0
    rate = state.turns_per_day / state.config.get('townCenterSellInterval', 24)
    shop_rate = state.turns_per_day / state.config.get('townShopSellInterval', 4)
    for shop in state.obs['town']['unlocked_shops']:
        products = SHOPS[shop]
        if item in products:
            rate += shop_rate * (2 if len(products) == 1 else 1)
    return rate


def crop_scores(state, params, planned=None):
    counts = {c: 0 for c in CROPS}
    for _, _, tile in state.tiles():
        if isinstance(tile, dict) and tile.get('crop') in counts:
            counts[tile['crop']] += 1
    opponent = dict.fromkeys(CROPS, 0)
    for _, _, tile in state.tiles(opponent=True):
        if isinstance(tile, dict) and tile.get('crop') in opponent:
            opponent[tile['crop']] += 1
    scores = {}
    for crop, (seed, first, peak, quantity, occupancy) in CROPS.items():
        if state.days_left < first + 1:
            continue
        if params.get('only_crop') and crop != params['only_crop']:
            continue
        own = counts[crop] + (planned or {}).get(crop, 0)
        other = opponent[crop] * params['opponent_weight']
        demand = daily_demand(state, crop) if params['adaptive'] else 1
        inv = state.obs['market']['inventory'][crop]
        # With delayed sales, our unsold harvest is future supply too. Ignoring it
        # makes holding look like new demand and triggers more overproduction.
        held = state.private['shed'].get(crop, 0) + sum(
            bag.get(crop, 0) for bag in state.private['inventories'])
        forecast = inv + held + (own + other) * quantity - demand * min(peak, state.days_left)
        revenue = sale_value(crop, quantity, forecast, state.config.get('marketParams'))
        # Approximate occupied-tile cost includes daily watering, planting, harvest, travel.
        scores[crop] = (revenue - seed) / (occupancy + params['travel_cost'])
    return scores
