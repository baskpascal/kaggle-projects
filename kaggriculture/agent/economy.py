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
# Engine max_yield_day; watering only adds yield from age (max_yield_day + 1) // 2.
MAX_YIELD_DAY = {'WHEAT': 4, 'CARROT': 3, 'TOMATO': 8, 'STRAWBERRY': 10, 'MELON': 12}
WATER_BONUS_FROM = {crop: (last + 1) // 2 for crop, last in MAX_YIELD_DAY.items()}
ANIMALS = {
    'GOOSE': (300, 'COOP', 'EGG', 4, 1),
    'COW': (400, 'PASTURE', 'MILK', 8, 2),
    'SHEEP': (500, 'PASTURE', 'WOOL', 6, 3),
}
# Engine max_held: yield_units, plus any banked care bonus, is clipped to this at
# production. Without it a CARE can be queued for a bonus the engine will discard.
ANIMAL_MAX_HELD = {'GOOSE': 4, 'COW': 6, 'SHEEP': 6}


def care_priority(tile, prices, day, days_left):
    """What one CARE on this animal is worth, or None when it is worth nothing.

    A CARE banks exactly one unit, cashed at the next production if the animal is fed
    that day, and `yield_units + bonus` is clipped to `max_held`
    (kaggriculture.py:822-830). So the turn pays only when there is headroom under the
    cap and a production day still inside the season. Priced like FERTILIZE -- a base
    that keeps it in the queue plus a share of the product it buys -- because a flat
    constant valued a wool unit and an egg unit the same.
    """
    name = tile['animal']
    banked = tile.get('pending_care_bonus', 0)
    headroom = ANIMAL_MAX_HELD[name] - tile.get('yield_units', 0) - banked
    if headroom < 1:
        return None
    if next_production_day(tile, day) - day > days_left:
        return None
    return 45 + prices[ANIMALS[name][2]] * .3


def next_production_day(tile, day):
    """The next day this animal produces, or None if it never produces again.

    The engine yields when `(d - placed_day - first_yield_day)` is non-negative and
    divisible by `interval` (kaggriculture.py:822), evaluated as the day rolls over. A
    banked CARE bonus is only cashed on such a day, and only if the animal was fed, so
    knowing when the next one falls is what separates a CARE that pays from one that
    just spends a turn.
    """
    _, _, _, first, interval = ANIMALS[tile['animal']]
    start = tile['placed_day'] + first
    if day < start:
        return start
    elapsed = day - start
    return day + (interval - elapsed % interval)
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


def crop_context(state, params):
    """Per-crop inputs that do not depend on what we are about to plant."""
    counts = {c: 0 for c in CROPS}
    for _, _, tile in state.tiles():
        if isinstance(tile, dict) and tile.get('crop') in counts:
            counts[tile['crop']] += 1
    opponent = dict.fromkeys(CROPS, 0)
    for _, _, tile in state.tiles(opponent=True):
        if isinstance(tile, dict) and tile.get('crop') in opponent:
            opponent[tile['crop']] += 1
    held = {crop: state.private['shed'].get(crop, 0)
            + sum(bag.get(crop, 0) for bag in state.private['inventories'])
            for crop in CROPS}
    return {'counts': counts, 'opponent': opponent, 'held': held,
            'inventory': state.obs['market']['inventory'],
            'demand': {c: (daily_demand(state, c) if params['adaptive'] else 1) for c in CROPS},
            'days_left': state.days_left,
            'market_params': state.config.get('marketParams')}


def score_crops(context, params, planned=None):
    """Marginal value per occupied tile-day, given the plantings already committed.

    Split out of crop_scores so the planner can re-score cheaply after each tile
    it commits, without rescanning both farms every time.
    """
    scores = {}
    for crop, (seed, first, peak, quantity, occupancy) in CROPS.items():
        if context['days_left'] < first + 1:
            continue
        if params.get('only_crop') and crop != params['only_crop']:
            continue
        own = context['counts'][crop] + (planned or {}).get(crop, 0)
        other = context['opponent'][crop] * params['opponent_weight']
        # With delayed sales, our unsold harvest is future supply too. Ignoring it
        # makes holding look like new demand and triggers more overproduction.
        forecast = (context['inventory'][crop] + context['held'][crop]
                    + (own + other) * quantity
                    - context['demand'][crop] * min(peak, context['days_left']))
        revenue = sale_value(crop, quantity, forecast, context['market_params'])
        # Approximate occupied-tile cost includes daily watering, planting, harvest, travel.
        scores[crop] = (revenue - seed) / (occupancy + params['travel_cost'])
    return scores


def crop_scores(state, params, planned=None):
    return score_crops(crop_context(state, params), params, planned)


# Official yield rules, mirrored from the interpreter and pinned by tests:
# one-time crops gain +1 per watered day inside [ceil(max_yield_day/2), max_yield_day],
# doubled to +2 while fertilized; ongoing crops produce on a fixed schedule and
# yield 2 instead of 1 when watered and fertilized. FERTILIZE covers day..day+2.
WINDOW = {'WHEAT': (2, 4, 6), 'CARROT': (2, 3, 4), 'MELON': (6, 12, 6)}
SCHEDULE = {'TOMATO': (8, 1, 4), 'STRAWBERRY': (10, 2, 4)}


def fertilizer_units(state, tile):
    """Extra harvestable units gained by fertilizing this plant right now."""
    crop, day = tile['crop'], state.day
    covered = tile.get('fertilized_until_day', -1)
    # FERTILIZE raises coverage to day+2; days at or below `covered` already have it.
    fresh = [d for d in range(day, day + 3) if d > covered and d <= day + state.days_left]
    if not fresh:
        return 0
    held = tile.get('yield_units', 0)
    if crop in WINDOW:
        start, last, cap = WINDOW[crop]
        planted = tile['planted_day']
        remaining = [d for d in range(day, day + 1 + int(state.days_left))
                     if start <= d - planted <= last]
        boosted = [d for d in fresh if start <= d - planted <= last]
        return min(cap, held + len(remaining) + len(boosted)) - min(cap, held + len(remaining))
    first, interval, most = SCHEDULE[crop]
    planted = tile['planted_day']
    produces = [d for d in fresh
                if (d - planted - first) >= 0 and (d - planted - first) % interval == 0
                and (d - planted - first) // interval < most]
    return min(len(produces), max(0, most - held))


def fertilizer_value(state, tile, prices, market_inventory):
    """Net value of one FERTILIZE: extra produce at forecast prices, less the
    fertilizer we give up selling. Negative means it is not worth the unit."""
    units = fertilizer_units(state, tile)
    if not units:
        return 0.
    crop = tile['crop']
    held = state.private['shed'].get(crop, 0) + sum(
        bag.get(crop, 0) for bag in state.private['inventories'])
    gain = sale_value(crop, units, market_inventory[crop] + held,
                      state.config.get('marketParams'))
    return gain - prices['FERTILIZER']
