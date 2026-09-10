from .economy import (ANIMALS, CROPS, WATER_BONUS_FROM, arbitrage_opportunities,
                      care_priority, crop_context, fertilizer_value, livestock_targets,
                      price, sale_value, score_crops)
from .params import DEFAULTS
from .market import projected_shed, sale_orders, schedule_market_orders
from .routing import distance, move_towards, nearest_shed, shed_tiles
from .state import State

# Optional, off by default: the planner records why units had nothing to do, so the
# question "what profitable work was available but not executed" can be answered from
# state rather than by counting PASS. Written only when `diagnostics` is enabled.
DIAGNOSTIC = {}


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
    animal_counts = {name: sum(t['animal'] == name for _, _, t in animals)
                     for name in ANIMALS}
    animal_plan = livestock_targets(s, p)
    animal_supply = {
        name: s.private['shed'].get(name, 0) + sum(i.get(name, 0) for i in inventories)
        for name in ANIMALS
    }
    available_to_place = dict(animal_supply)
    placement_need = {name: max(0, animal_plan[name] - animal_counts[name])
                      for name in ANIMALS}
    structure_counts = {
        kind: sum(t.get('kind') == kind for _, _, t in structures)
        for kind in ('COOP', 'PASTURE')
    }
    structure_need = {
        'COOP': max(0, animal_plan['GOOSE'] - structure_counts['COOP']),
        'PASTURE': max(0, animal_plan['COW'] + animal_plan['SHEEP']
                       - structure_counts['PASTURE']),
    }
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
            # O4: watering inside the bonus window raises `yield_units` on the spot, so
            # water-then-harvest beats harvest-now even in the endgame. But the extra
            # unit is worth far less than the whole harvest, and delaying the HARVEST by
            # a turn can push the delivery past the end of the season -- measured, not
            # assumed: with a bare `turns_left > 1` guard, 8 of 30 episodes lost money,
            # the worst by $220. So the water only happens when there is still room to
            # water, harvest, carry the crop to a shed and drop it.
            to_shed = distance(pos, nearest_shed(pos, s.size))
            last_day_pays = (not ongoing and age >= WATER_BONUS_FROM[crop]
                             and s.turns_left > to_shed + 3)
            needs_water = not t['watered_today'] and (
                p['water_daily'] or t['consecutive_unwatered'] >= 1
                # Bonus window keys off max_yield_day, not the planned harvest day.
                or (not ongoing and age >= WATER_BONUS_FROM[crop]))
            # `not endgame` used to send every ripe plant straight to HARVEST on the
            # last day, throwing away the unit one more watering would have added.
            water_first = not endgame or (p['last_day_water'] and last_day_pays)
            if harvest and not ongoing and water_first and needs_water:
                jobs.append((pos, ['WATER'], 120 + value * .1, None))
            elif harvest:
                jobs.append((pos, ['HARVEST'], 90 + value * .3, None))
            elif needs_water and (s.turns_left > s.turns_per_day - s.hour
                                  or (p['last_day_water'] and last_day_pays
                                      and age >= first)):
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
                # O3: a flat 45 valued a wool unit and an egg unit the same. `care_priority`
                # prices the unit the CARE actually banks, and returns None when the cap
                # or the end of the season means it banks nothing.
                worth = (care_priority(t, prices, s.day, s.days_left)
                         if p['care_pricing'] else 45.)
                if worth is not None:
                    jobs.append((pos, ['CARE'], worth, None))
        elif t.get('kind') in ('COOP', 'PASTURE') and 'animal' not in t:
            compatible = (('GOOSE',) if t['kind'] == 'COOP' else ('COW', 'SHEEP'))
            choices = [name for name in compatible if placement_need[name] > 0]
            if choices:
                animal_type = max(choices, key=lambda name: (
                    available_to_place[name] > 0, prices[ANIMALS[name][2]],
                    placement_need[name], name))
                jobs.append((pos, ['PLACE', animal_type], 130., animal_type))
                placement_need[animal_type] -= 1
                available_to_place[animal_type] = max(
                    0, available_to_place[animal_type] - 1)

    # Structure target allocation and crop tasks prefer land near the shed.
    plantable.sort(key=lambda pos: (distance(pos, nearest_shed(pos, s.size)), pos))
    build_kinds = [kind for kind in ('PASTURE', 'COOP')
                   for _ in range(structure_need[kind])]
    planned = {}
    # Market attack. The engine's price is shallow below the neutral inventory and collapses
    # above it, so a market the opponent depends on and that sits just under neutral is worth
    # pushing over: measured here, v006's strawberry line realises 45,479 at inventory 9,806
    # and 7,370 once 200 more units are in. Our own crop score cannot see this, because it
    # prices our revenue and not the damage, so `attack_tiles` reserves tiles for it outright.
    attack_crop = None
    if p['attack_tiles']:
        opponent_tiles = {}
        for row in s.opponent['tiles']:
            for tile in row:
                if isinstance(tile, dict) and tile.get('crop') in CROPS:
                    opponent_tiles[tile['crop']] = opponent_tiles.get(tile['crop'], 0) + 1
        market = observation['market']
        leverage = {}
        for crop, count in opponent_tiles.items():
            headroom = p['neutral_inventory'] - market['inventory'].get(crop, 0)
            if headroom <= 0 or headroom > p['attack_headroom']:
                continue
            leverage[crop] = count * market['prices'].get(crop, 0)
        if leverage:
            attack_crop = max(leverage, key=leverage.get)
    attack_budget = p['attack_tiles'] if attack_crop else 0
    for pos in plantable:
        # Before `attack_until_day` the reservation outranks construction: the target crop
        # needs ten days from planting to first harvest, so a tile committed after about day
        # eight never reaches the market, and the opening is otherwise spent on pasture.
        early = s.day < p['attack_until_day']
        if attack_budget and s.hour < p['plant_until_hour'] and (early or not build_kinds):
            jobs.append((pos, ['PLANT', attack_crop], 60., None))
            planned[attack_crop] = planned.get(attack_crop, 0) + 1
            attack_budget -= 1
            continue
        if build_kinds and s.hour < 15:
            jobs.append((pos, ['BUILD_' + build_kinds.pop(0)], 65., None))
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

    if p.get('diagnostics'):
        DIAGNOSTIC['turns'] = DIAGNOSTIC.get('turns', 0) + 1
        DIAGNOSTIC['jobs_generated'] = DIAGNOSTIC.get('jobs_generated', 0) + len(jobs)
        DIAGNOSTIC['units'] = DIAGNOSTIC.get('units', 0) + len(s.positions)
        DIAGNOSTIC['plantable_tiles'] = DIAGNOSTIC.get('plantable_tiles', 0) + len(plantable)
        for _, action, _, _ in jobs:
            k = 'job_' + str(action[0])
            DIAGNOSTIC[k] = DIAGNOSTIC.get(k, 0) + 1
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
            if p.get('diagnostics'):
                DIAGNOSTIC['idle_units'] = DIAGNOSTIC.get('idle_units', 0) + 1
                key = ('no_job_generated' if not jobs else
                       'all_jobs_taken' if len(used) >= len(jobs) else
                       'jobs_unreachable_or_gated')
                DIAGNOSTIC[key] = DIAGNOSTIC.get(key, 0) + 1
            # 79% of idleness is a unit that has work available and cannot reach any of
            # it before the day ends, so standing still guarantees the same refusal
            # tomorrow. Walking toward the nearest unclaimed job converts a dead slot
            # into position, which is what makes that job reachable next morning.
            if p['reposition_idle'] and not (total_inv and home_distance == 0):
                remaining = [jobs[j] for j in range(len(jobs)) if j not in used]
                if remaining:
                    target = min(remaining, key=lambda job: (distance(position, job[0]),
                                                             job[0]))[0]
                    if distance(position, target):
                        unit_actions.append(move_towards(position, target))
                        continue
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
    # Two days of feed per animal is bought every turn the store dips below it, and the
    # telemetry says most of those units are still sitting there seventy-two turns later.
    # `feed_days_of_cover` makes the buffer a parameter so an ablation can ask whether the
    # second day is paying for itself; 2 is the historical behaviour.
    reserve_wheat = (len(animals) * p['feed_days_of_cover'] if s.days_left > 1 else 0)
    keep_fertilizer = fertilize_targets if p['fertilize'] and s.days_left > 1 else 0
    limit = s.config.get('maxMarketOrdersPerTurn', 10)
    sales = sale_orders(s, p, projected_shed(s, unit_actions), reserve_wheat,
                        keep_fertilizer, max_orders=limit)
    # SELL settles before later list positions, so part of this turn's conservative sale
    # estimate is spendable by the planner now. The historical baseline keeps the fraction
    # at zero; planner-v1 candidates opt in.
    financed = 0.
    for _, item, count in sales:
        financed += sale_value(item, count, observation['market']['inventory'][item],
                               s.config.get('marketParams'))
    cash += financed * p['sale_financing_fraction']
    essential_orders = []
    optional_orders = []
    reserve_slots = p['market_slot_reservation']
    effective_reserve = 0 if risk == 'behind' else p['cash_reserve']
    quadrants = len(s.me['unlocked_quadrants'])
    occupied = sum(t is not None for _, _, t in tiles)
    land_due = (p['land_reservation'] and quadrants < p['max_quadrants']
                and s.day >= p['expand_day']
                and s.days_left > 10 and occupied >= len(tiles) * .75)
    land_cost = (1000, 2000, 4000)[quadrants - 1] if land_due else 0
    # `land_cost` does two separate jobs: it is the price of the quadrant, and it is the
    # floor every other order has to clear while the expansion is pending. Splitting them
    # is what lets an ablation ask which of the two is paying.
    held_reserve = land_cost if p['land_reserve_holds'] else 0

    def buy(order, cost, essential=False, required_reserve=None):
        nonlocal cash
        accepted = len(essential_orders) + len(optional_orders)
        slots_available = (accepted < limit if reserve_slots else
                           len(sales) + accepted < limit)
        reserve = (max(effective_reserve, held_reserve) if required_reserve is None
                   else required_reserve)
        if slots_available and cost <= max(0, cash - reserve):
            (essential_orders if essential else optional_orders).append(order)
            cash -= cost
            return True
        return False

    purchase_need = {
        name: max(0, animal_plan[name] - animal_counts[name] - animal_supply[name])
        for name in ANIMALS
    }
    busy = len(jobs)
    # HIRE is cheap but occupies one of only ten market positions. During herd setup,
    # leave one position per animal chain so nine hires cannot postpone every asset buy.
    animal_order_slots = sum(count > 0 for count in purchase_need.values())
    hire_order_cap = max(0, limit - animal_order_slots)
    desired_hands = min(p['max_hands'], hire_order_cap, max(0, (busy + 2) // 3))
    # Reserving the market slot is not the same as reserving the money. Hires are bought
    # before the herd, so a full opening crew can spend exactly the capital the animal
    # chain needs and postpone it by days. The guard is a condition on capital, not a
    # turn number: hire only while the herd this turn would still be affordable after.
    herd_cost = sum(ANIMALS[name][0] * min(2, count)
                    for name, count in purchase_need.items() if count)
    hire_reserve = herd_cost if p['hire_capital_guard'] else None
    if s.hour < 4 and s.turns_left > 12:
        a, b = 1, 1
        for n in range(desired_hands):
            cost = a * s.config.get('farmHandCostMult', 1)
            if n >= s.me['hires_today']:
                buy(['HIRE'], cost, essential=True, required_reserve=hire_reserve)
            a, b = b, a + b
    # Existing livestock survives before the herd expands. Buying feed ahead of animal
    # inventory also prevents a new placement from turning the next morning into a rescue.
    wheat_total = s.private['shed'].get('WHEAT', 0) + sum(
        i.get('WHEAT', 0) for i in inventories)
    # Seed is ordered after feed, so a turn that tops the feed buffer up can leave a
    # plantable tile without seed until the next one. `seed_priority` reserves the cost of
    # the realisable seed deficit - capacity we could plant now, minus seed already held -
    # against the feed order. It is a deficit, not a target: no deficit, no reserve.
    seed_deficit_cost = 0.
    if p['seed_priority'] and risk != 'ahead':
        held_seed = sum(seeds.values())
        deficit = max(0, min(len(plantable), 12) - held_seed)
        if deficit and best_crop:
            seed_deficit_cost = deficit * CROPS[best_crop][0]
    if wheat_total < reserve_wheat:
        count = reserve_wheat - wheat_total
        inv = observation['market']['inventory']['WHEAT']
        cost = sum(price('WHEAT', inv - k - 1, s.config.get('marketParams'))
                   for k in range(count))
        buy(['BUY_PRODUCT', 'WHEAT', count], cost, essential=True,
            required_reserve=max(held_reserve, seed_deficit_cost)
            if seed_deficit_cost else None)

    # Land is the binding production asset once the starting quadrant fills. Preserve
    # its price across turns and schedule it ahead of herd expansion and seed orders.
    if land_due and p['land_purchase']:
        buy(['BUY_LAND'], land_cost, essential=True, required_reserve=0)

    # One order per species, up to two animals. Fixed unit costs mean the local cash
    # accounting is exact apart from the deliberately discounted sale proceeds above.
    for animal_type in sorted(ANIMALS, key=lambda name: (
            -prices[ANIMALS[name][2]], -purchase_need[name], name)):
        count = min(2, purchase_need[animal_type])
        if count:
            cost = ANIMALS[animal_type][0] * count
            buy(['BUY_ANIMAL', animal_type, count], cost,
                essential=p['economic_planner'])
    if risk != 'ahead':
        # Buy seed for the mix we actually planned; otherwise the planner commits
        # to tiles it has no seed for and the PLANT jobs are filtered out again.
        wanted = planned if p['planned_feedback'] else (
            {best_crop: min(12, len(plantable))} if best_crop else {})
        # The attack allocation commits tiles to a crop our own scorer did not choose, so
        # without this its PLANT jobs are generated and then refused for want of seed.
        if attack_crop:
            wanted = dict(wanted)
            wanted[attack_crop] = max(wanted.get(attack_crop, 0),
                                      min(p['attack_tiles'], len(plantable)))
        budget = min(12, len(plantable))
        for crop, count in sorted(wanted.items(), key=lambda kv: -kv[1]):
            count = min(count, budget)
            needed = max(0, count - seeds.get(crop, 0))
            if needed and buy(['BUY_SEED', crop, needed], needed * CROPS[crop][0],
                              essential=True):
                budget -= count
    # Once livestock exists it generates one fertilizer per animal per day for free.
    # Buying more while that renewable pipeline is active created large shed overflows
    # and, in the terminal liquidation, sold purchased units back near the price floor.
    if fertilize_targets and not animals:
        stock = s.private['shed'].get('FERTILIZER', 0) + sum(
            i.get('FERTILIZER', 0) for i in inventories)
        count = min(fertilize_targets - stock, 4)
        if count > 0:
            inv = observation['market']['inventory']['FERTILIZER']
            cost = sum(price('FERTILIZER', inv - k - 1, s.config.get('marketParams'))
                       for k in range(count))
            buy(['BUY_PRODUCT', 'FERTILIZER', count], cost, essential=True)
    projected = projected_shed(s, unit_actions)
    sold_units = sum(order[2] for order in sales)
    stored_after_sales = max(0, sum(projected.values()) - sold_units)
    trade_room = max(0, min(
        int(p['arbitrage_storage_limit']) - stored_after_sales,
        s.config.get('shedCapacity', 100) - stored_after_sales))
    trade_budget = max(0, cash - effective_reserve)
    for opportunity in arbitrage_opportunities(s, trade_room, trade_budget, p):
        if buy(['BUY_PRODUCT', opportunity['item'], opportunity['count']],
               opportunity['cost']):
            trade_room -= opportunity['count']
            trade_budget -= opportunity['cost']
        if trade_room <= 0 or trade_budget <= 0:
            break
    orders = schedule_market_orders(sales, essential_orders, optional_orders,
                                    limit, reserve=reserve_slots)
    return {'farmer': unit_actions[0], 'hands': unit_actions[1:], 'market': orders}
