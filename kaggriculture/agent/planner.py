from .economy import (ANIMALS, CROPS, crop_context, fertilizer_value, price,
                      score_crops)
from .params import DEFAULTS
from .market import projected_shed, sale_orders
from .routing import distance, move_towards, nearest_shed, shed_tiles
from .state import State


def policy(observation, configuration=None, parameters=None):
    p = {**DEFAULTS, **(parameters or {})}
    s = State(observation, configuration or {})
    risk = s.risk_posture(p['risk_window_days'], p['risk_min_buffer'],
                          p['risk_buffer_fraction'])
    tiles = s.tiles()
    prices = observation['market']['prices']
    inventories = s.private['inventories']
    shed = dict(s.private['shed'])
    seeds = dict(s.private['seeds'])
    context = crop_context(s, p)
    scores = score_crops(context, p)
    best_crop = max(scores, key=scores.get) if scores else None
    animals = [(x, y, t) for x, y, t in tiles if isinstance(t, dict) and 'animal' in t]
    structures = [(x, y, t) for x, y, t in tiles if isinstance(t, dict)
                  and t.get('kind') in ('COOP', 'PASTURE')]
    animal_type = p['animal_type']
    animal_cost, structure, _, _, _ = ANIMALS[animal_type]
    want_animals = p['animal_target'] if s.days_left > 10 else len(animals)
    jobs = []
    plantable = []
    fertilize_targets = 0
    for x, y, t in tiles:
        pos = (x, y)
        if t is None:
            plantable.append(pos)
            continue
        if t.get('kind') == 'WEED':
            if best_crop and s.hour < p['plant_until_hour']:
                jobs.append((pos, ['DIG'], 14., None))
        elif t.get('kind') == 'PLANT':
            crop = t['crop']
            _, first, peak, _, _ = CROPS[crop]
            age = s.day - t['planted_day']
            value = prices[crop] * t.get('yield_units', 0)
            ongoing = crop in ('TOMATO', 'STRAWBERRY')
            # Leave enough time for explicit final-day delivery and sale.
            endgame = s.turns_left <= s.turns_per_day
            ripe = age >= first and t.get('yield_units', 0) > 0
            harvest = ripe and (ongoing or age >= peak or endgame)
            needs_water = not t['watered_today'] and (
                p['water_daily'] or t['consecutive_unwatered'] >= 1
                or (not ongoing and age >= (peak + 1) // 2))
            if harvest and not ongoing and not endgame and needs_water:
                jobs.append((pos, ['WATER'], 120 + value * .1, None))
            elif harvest:
                jobs.append((pos, ['HARVEST'], 90 + value * .3, None))
            elif needs_water and s.turns_left > s.turns_per_day - s.hour:
                urgent = t['consecutive_unwatered'] >= 1
                jobs.append((pos, ['WATER'], 65 + 75 * urgent + s.hour * 3, None))
            if p['fertilize']:
                # Compete economically like any other job: the extra produce is
                # priced at the forecast inventory and charged the fertilizer we
                # give up selling, so a losing application never gets queued.
                net = fertilizer_value(s, t, prices, observation['market']['inventory'])
                if net > 0:
                    fertilize_targets += 1
                    jobs.append((pos, ['FERTILIZE'], 40 + net * .3, 'FERTILIZER'))
        elif 'animal' in t:
            if not t['fed_today'] and s.days_left > 1:
                jobs.append((pos, ['FEED'], 160 + 50 * t['consecutive_unfed'], 'WHEAT'))
            if t.get('yield_units', 0):
                product = ANIMALS[t['animal']][2]
                jobs.append((pos, ['HARVEST'], 75 + prices[product] * t['yield_units'] * .3, None))
            if t.get('fertilizer_available'):
                jobs.append((pos, ['COLLECT_FERTILIZER'], 30 + prices['FERTILIZER'] * .3, None))
            if t['fed_today'] and not t['cared_today'] and s.days_left > 2:
                jobs.append((pos, ['CARE'], 45., None))
        elif t.get('kind') == structure and len(animals) < want_animals:
            jobs.append((pos, ['PLACE', animal_type], 130., animal_type))

    # Structure target allocation and crop tasks prefer land near the shed.
    plantable.sort(key=lambda pos: (distance(pos, nearest_shed(pos, s.size)), pos))
    to_build = max(0, want_animals - len(structures))
    planned = {}
    for pos in plantable:
        if to_build and s.hour < 15:
            jobs.append((pos, ['BUILD_' + structure], 65., None))
            to_build -= 1
        elif s.hour < p['plant_until_hour']:
            # Re-score after every tile we commit: without this the planner rates
            # the second, third and fourth planting of a crop as if the earlier
            # ones did not exist. This corrects the marginal forecast; it does not
            # force variety, so a crop that stays best keeps being chosen.
            current = score_crops(context, p, planned) if p['planned_feedback'] else scores
            crop = max(current, key=current.get) if current else None
            if crop and current[crop] > 0:
                jobs.append((pos, ['PLANT', crop], 20 + min(40, current[crop]), None))
                planned[crop] = planned.get(crop, 0) + 1

    used = set()
    unit_actions = []
    planned_crops = {}
    for index, position in enumerate(s.positions):
        inv = inventories[index]
        target_shed = nearest_shed(position, s.size)
        home_distance = distance(position, target_shed)
        total_inv = sum(inv.values())
        # Explicitly deliver before the season stops; final night's auto-drop is too late.
        must_drop = total_inv and (s.turns_left <= home_distance + 4 or total_inv >= 15)
        if must_drop:
            unit_actions.append(['DROP'] if home_distance == 0 else move_towards(position, target_shed))
            continue
        ranked = []
        for j, (target, action, value, required) in enumerate(jobs):
            if j in used:
                continue
            travel = distance(position, target)
            if action[0] == 'PLANT':
                if seeds.get(action[1], 0) <= 0:
                    continue
                if s.hour + travel + 2 >= s.turns_per_day:
                    continue
            if action[0] in ('HARVEST', 'COLLECT_FERTILIZER'):
                delivery = distance(target, nearest_shed(target, s.size))
                if travel + delivery + 2 >= s.turns_left:
                    continue
            if travel >= s.turns_per_day - s.hour:
                continue
            if required and not inv.get(required, 0):
                if not shed.get(required, 0):
                    continue
                travel = home_distance + distance(target_shed, target) + 1
            score = value / (1 + travel * .65)
            if not travel:
                # Commitment: a job the unit is already standing on pays this
                # turn, while any job at distance d pays nothing for d turns.
                # The travel discount alone under-prices that, so a unit can
                # walk away from work it could finish now. 1.0 disables.
                score *= p['continuity_completion']
            ranked.append((score, -j, j))
        if not ranked:
            unit_actions.append(['DROP'] if total_inv and home_distance == 0 else ['PASS'])
            continue
        _, _, chosen = max(ranked)
        target, action, _, required = jobs[chosen]
        used.add(chosen)
        if required and not inv.get(required, 0):
            if home_distance:
                result = move_towards(position, target_shed)
            else:
                amount = min(shed[required], 4 if required == 'WHEAT' else 1)
                result = ['PICKUP', required, amount]
                shed[required] -= amount
        elif distance(position, target):
            result = move_towards(position, target)
        else:
            result = list(action)
            if action[0] == 'PLANT':
                # Reserve the shared seed pool before the official atomic check.
                seeds[action[1]] -= 1
                planned_crops[action[1]] = planned_crops.get(action[1], 0) + 1
        unit_actions.append(result)

    cash = s.me['money']
    reserve_wheat = len(animals) * 2 if s.days_left > 1 else 0
    keep_fertilizer = fertilize_targets if p['fertilize'] and s.days_left > 1 else 0
    orders = sale_orders(s, p, projected_shed(s, unit_actions), reserve_wheat,
                         keep_fertilizer)
    limit = s.config.get('maxMarketOrdersPerTurn', 10)
    effective_reserve = 0 if risk == 'behind' else p['cash_reserve']
    def buy(order, cost):
        nonlocal cash
        if len(orders) < limit and cost <= max(0, cash - effective_reserve):
            orders.append(order)
            cash -= cost
            return True
        return False

    busy = len(jobs)
    desired_hands = min(p['max_hands'], max(0, (busy + 2) // 3))
    if s.hour < 4 and s.turns_left > 12:
        a, b = 1, 1
        for n in range(desired_hands):
            cost = a * s.config.get('farmHandCostMult', 1)
            if n >= s.me['hires_today']:
                buy(['HIRE'], cost)
            a, b = b, a + b
    if risk != 'ahead':
        # Buy seed for the mix we actually planned; otherwise the planner commits
        # to tiles it has no seed for and the PLANT jobs are filtered out again.
        wanted = planned if p['planned_feedback'] else (
            {best_crop: min(12, len(plantable))} if best_crop else {})
        budget = min(12, len(plantable))
        for crop, count in sorted(wanted.items(), key=lambda kv: -kv[1]):
            count = min(count, budget)
            needed = max(0, count - seeds.get(crop, 0))
            if needed and buy(['BUY_SEED', crop, needed], needed * CROPS[crop][0]):
                budget -= count
    if fertilize_targets:
        stock = s.private['shed'].get('FERTILIZER', 0) + sum(
            i.get('FERTILIZER', 0) for i in inventories)
        count = min(fertilize_targets - stock, 4)
        if count > 0:
            inv = observation['market']['inventory']['FERTILIZER']
            cost = sum(price('FERTILIZER', inv - k - 1, s.config.get('marketParams'))
                       for k in range(count))
            buy(['BUY_PRODUCT', 'FERTILIZER', count], cost)
    supply_animals = sum(i.get(animal_type, 0) for i in inventories) + s.private['shed'].get(animal_type, 0)
    if len(animals) + supply_animals < want_animals:
        buy(['BUY_ANIMAL', animal_type, 1], animal_cost)
    wheat_total = s.private['shed'].get('WHEAT', 0) + sum(i.get('WHEAT', 0) for i in inventories)
    if wheat_total < reserve_wheat:
        count = reserve_wheat - wheat_total
        inv = observation['market']['inventory']['WHEAT']
        cost = sum(price('WHEAT', inv - k - 1, s.config.get('marketParams')) for k in range(count))
        buy(['BUY_PRODUCT', 'WHEAT', count], cost)
    quadrants = len(s.me['unlocked_quadrants'])
    occupied = sum(t is not None for _, _, t in tiles)
    if (quadrants < p['max_quadrants'] and s.day >= p['expand_day'] and s.days_left > 10
            and occupied >= len(tiles) * .75):
        buy(['BUY_LAND'], (1000, 2000, 4000)[quadrants - 1])
    return {'farmer': unit_actions[0], 'hands': unit_actions[1:], 'market': orders[:limit]}
