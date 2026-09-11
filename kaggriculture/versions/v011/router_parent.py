"""Shop plans with small, observation-based repairs. Python standard library only.

The 13 complete action tapes live in actions.json. This file contains every rule:
choose a plan after two shops, delay weed-blocked work within the current day,
bring some planned sales forward one turn, and liquidate on the final turn.

Sell timing and shed projection follow aurax7's public Reactive Router:
https://www.kaggle.com/code/aurax7/kaggriculture-reactive-router
The shop-pair routes and worker-local, same-day queues were developed here.
"""

import copy
import json
from collections import deque
from pathlib import Path

TURNS_PER_DAY = 24
ROUTE_STEP = 144
FINAL_PLAN_STEP = 648
LAST_STEP = 718
SHED_CAPACITY = 100
MAX_ORDERS = 10
PRODUCTS = (
    "WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON",
    "EGG", "MILK", "WOOL", "FERTILIZER",
)
WEED_BLOCKED_WORK = {"PLANT", "BUILD_COOP", "BUILD_PASTURE"}
ANIMALS = {"GOOSE", "COW", "SHEEP"}

# Keys are the first two shops in their observed order; values index actions.json.
# All other pairs keep plan 0. Plan 1 is the previous yarn-market continuation.
# Plans 3..12 are the ten distinct continuations selected in the latest search.
# v011: v008 plus reserve_sales and sells-first ordering
SHOP_PLANS = {
    ("BAKERY", "YARN_STORE"): 11,
    ("BRUNCH_SPOT", "YARN_STORE"): 3,
    ("FARMERS_MARKET", "YARN_STORE"): 3,
    ("ICE_CREAM_SHOP", "YARN_STORE"): 6,
    ("PET_CAFE", "YARN_STORE"): 5,
    ("PIZZA_SHOP", "YARN_STORE"): 3,
    ("SMOOTHIE_SHOP", "YARN_STORE"): 8,
    ("YARN_STORE", "BAKERY"): 11,
    ("YARN_STORE", "BRUNCH_SPOT"): 9,
    ("YARN_STORE", "FARMERS_MARKET"): 3,
    ("YARN_STORE", "ICE_CREAM_SHOP"): 3,
    ("YARN_STORE", "PET_CAFE"): 7,
    ("YARN_STORE", "PIZZA_SHOP"): 3,
    ("YARN_STORE", "SMOOTHIE_SHOP"): 11,
    ("YARN_STORE", "YARN_STORE"): 12,
}


class FarmView:
    """Only the current own farm, private inventory, and public prices."""

    def __init__(self, observation):
        farm = observation["farms"][observation["player"]]
        private = observation["private"]
        self.tiles = farm["tiles"]
        self.positions = [farm["farmer"], *farm["hands"]]
        self.inventories = private["inventories"]
        self.shed = {item: max(0, int(qty)) for item, qty in private["shed"].items()}
        self.prices = observation["market"]["prices"]

    def inventory(self, worker):
        return self.inventories[worker] if worker < len(self.inventories) else {}

    def beside_shed(self, position):
        center = len(self.tiles) // 2
        return position[0] in (center - 1, center) and position[1] in (center - 1, center)


class DayState:
    """Per-player memory; queues expire at dawn and sales expire next turn."""

    def __init__(self):
        self.plan = 0
        self.last_step = -1
        self.day = -1
        self.queues = {}
        self.sale_due_step = -1
        self.advanced_sales = {}


def repair_weeds(action, view, state, step):
    """Insert DIG without consuming the blocked action; shift only this worker."""
    day = step // TURNS_PER_DAY
    if day != state.day:
        state.day = day
        state.queues.clear()  # Unfinished work never spills into tomorrow.

    workers = [action.get("farmer") or ["PASS"], *(action.get("hands") or [])]
    for worker in range(min(len(workers), len(view.positions))):
        queue = state.queues.setdefault(worker, deque())
        queue.append(list(workers[worker]))
        x, y = view.positions[worker]
        tile = view.tiles[y][x]
        blocked = (queue[0][0] in WEED_BLOCKED_WORK
                   and isinstance(tile, dict) and tile.get("kind") == "WEED")
        workers[worker] = ["DIG"] if blocked else queue.popleft()
    action["farmer"], action["hands"] = workers[0], workers[1:]


def projected_shed(action, view):
    """Estimate stock after this turn's nearby PICKUP, DROP and PLACE actions.

    Preserve worker and inventory order: limited shed capacity can make it matter.
    This is the qualified lightweight estimate, not a full game simulation.
    """
    stock = {item: view.shed.get(item, 0) for item in PRODUCTS}
    stock.update(view.shed)
    total = sum(stock.values())
    workers = [action.get("farmer") or ["PASS"], *(action.get("hands") or [])]
    for worker in range(min(len(workers), len(view.positions))):
        if not view.beside_shed(view.positions[worker]):
            continue
        work = workers[worker]
        operation = work[0] if work else "PASS"
        inventory = view.inventory(worker)
        if operation == "PICKUP" and len(work) >= 2 and work[1] in stock:
            quantity = max(0, int(work[2]) if len(work) >= 3 else 1)
            taken = min(stock[work[1]], quantity)
            stock[work[1]] -= taken
            total -= taken
        elif operation == "DROP":
            for item, held in inventory.items():
                added = min(max(0, int(held)), max(0, SHED_CAPACITY - total))
                if added > 0:
                    stock[item] = stock.get(item, 0) + added
                    total += added
        elif operation == "PLACE" and len(work) >= 2 and work[1] not in ANIMALS:
            item = work[1]
            quantity = max(0, int(work[2]) if len(work) >= 3 else 1)
            added = min(quantity, max(0, int(inventory.get(item, 0))),
                        max(0, SHED_CAPACITY - total))
            if added > 0:
                stock[item] = stock.get(item, 0) + added
                total += added
    return stock


def future_sells(schedule, item, step):
    """Units of `item` the rest of this tape still plans to sell, from `step` on."""
    column = schedule.get(item)
    if not column or step >= len(column):
        return 0
    return column[step]


def sell_schedule(tape):
    """Suffix sums of each product's planned SELL quantity, indexed by step.

    Built once per tape at load time: the dead-stock layer asks "does the route ever
    sell this again" on every turn, and walking seven hundred remaining steps each time
    would cost more than the layer returns.
    """
    schedule = {item: [0] * (len(tape) + 1) for item in PRODUCTS}
    for step in range(len(tape) - 1, -1, -1):
        planned = {}
        for order in (tape[step].get("market") or []):
            if order and order[0] == "SELL" and len(order) >= 3 and order[1] in PRODUCTS:
                planned[order[1]] = planned.get(order[1], 0) + max(0, int(order[2]))
        for item in PRODUCTS:
            schedule[item][step] = schedule[item][step + 1] + planned.get(item, 0)
    return schedule


def dead_stock(action, view, schedule, step):
    """Sell stock the rest of the route will never sell, while it still has a price.

    Measured on the 67 archived ladder worlds, this backbone's shed peaks at the
    hundred-unit capacity in every one of them and sits at or above ninety-five from
    day twenty-five, so late production has nowhere to go. Stock beyond everything the
    remaining tape still plans to sell is dead inventory occupying that room. On the
    last day nothing is scheduled any more, so all of it is dead.

    Orders are appended after the planned ones, highest value first, so the existing
    market slots keep their order and the turn's lockstep race is unchanged.
    """
    projected = projected_shed(action, view)
    planned = {}
    for order in (action.get("market") or []):
        if order and order[0] == "SELL" and len(order) >= 3 and order[1] in PRODUCTS:
            planned[order[1]] = planned.get(order[1], 0) + max(0, int(order[2]))
    day = step // TURNS_PER_DAY
    extra = []
    for item in PRODUCTS:
        have = projected.get(item, 0) - planned.get(item, 0)
        if have <= 0:
            continue
        surplus = have if day >= 29 else have - future_sells(schedule, item, step + 1)
        if surplus > 0 and int(view.prices.get(item, 0)) > 1:
            extra.append(["SELL", item, surplus])
    extra.sort(key=lambda order: -int(view.prices.get(order[1], 0)) * order[2])
    action["market"] = (action.get("market") or []) + extra


SALE_HORIZON = 2
ADVANCE_START = 288


def reserve_sales(action, view, state, tape, step):
    """Advance the next `SALE_HORIZON` turns of planned sales, with a debt ledger.

    The inherited `advance_sales` brings one turn forward and runs before the worker
    repairs, so it projects the shed from commands that are still going to change. This
    runs after them, reaches two turns instead of one, and records what it has already
    sold against each future step so the same units are never sold twice.

    Ported from flexonafft/kaggriculture-most-powerfull-route and the identical layer in
    guruprasaathas111/kaggriculture-structured-economic-policy-v2, both forks of the same
    yhay81 backbone we run, whose teams sit above ours on the ladder.
    """
    horizon = SALE_HORIZON if step >= ROUTE_STEP else 1
    end = min(LAST_STEP, step + horizon, (step // 72 + 1) * 72 - 1)
    if end <= step:
        return
    market = action["market"]
    stock = projected_shed(action, view)
    blocked = {order[1] for order in market
               if len(order) > 1 and order[0] in ("SELL", "BUY_PRODUCT")}
    blocked.update(command[1] for queue in state.queues.values() for command in queue
                   if len(command) > 1 and command[0] == "PICKUP")
    commands = [action.get("farmer") or ["PASS"], *(action.get("hands") or [])]
    blocked.update(command[1] for command in commands
                   if len(command) > 1 and command[0] == "PICKUP")
    # An animal PLACE can fall back into the shed and the projection does not model it,
    # so the turn is left alone rather than advanced against a stock that may not exist.
    if any(len(command) > 1 and command[0] == "PLACE" and command[1] in ANIMALS
           and view.inventory(index).get(command[1], 0) > 0
           for index, command in enumerate(commands)):
        return
    debts = getattr(state, "sale_window_debts", {})
    for item in PRODUCTS:
        if item in ("WHEAT", "FERTILIZER") or item in blocked:
            continue
        if int(view.prices.get(item, 0)) < 2:
            continue
        available = max(0, int(stock.get(item, 0)))
        if not available or len(market) >= MAX_ORDERS:
            continue
        reservations = []
        for due_step in range(step + 1, end + 1):
            future = tape[due_step]
            work = [future.get("farmer") or ["PASS"], *(future.get("hands") or [])]
            if any(len(c) > 1 and c[0] == "PICKUP" and c[1] == item for c in work):
                break
            if any(len(o) > 1 and o[0] == "BUY_PRODUCT" and o[1] == item
                   for o in (future.get("market") or [])):
                break
            planned = sum(max(0, int(o[2])) for o in (future.get("market") or [])
                          if len(o) >= 3 and o[0] == "SELL" and o[1] == item)
            remaining = max(0, planned - debts.get(due_step, {}).get(item, 0))
            amount = min(available, remaining)
            if amount:
                reservations.append((due_step, amount))
                available -= amount
            if not available:
                break
        quantity = sum(amount for _, amount in reservations)
        if quantity:
            market.append(["SELL", item, quantity])
            for due_step, amount in reservations:
                debt = debts.setdefault(due_step, {})
                debt[item] = debt.get(item, 0) + amount
    state.sale_window_debts = debts


def sales_first(action):
    """Move SELL orders ahead of non-SELL ones, and drop the empty orders.

    The town resolves its demand race by order index between the two seats, so a sell
    that sits behind a purchase in the same turn can lose stock it would have cleared.
    An order is never moved ahead of a purchase of the same item, which would break the
    wheat wash that funds it.
    """
    original = (action.get("market") or [])[:MAX_ORDERS]
    orders = [list(order) for order in original
              if order and (order[0] in ("HIRE", "BUY_LAND")
                            or (len(order) >= 3 and int(order[2]) > 0))]
    for index in range(len(orders)):
        if orders[index][0] != "SELL":
            continue
        cursor = index
        while cursor > 0:
            previous = orders[cursor - 1]
            if previous[0] == "SELL":
                break
            if previous[0] in ("BUY_PRODUCT", "BUY_ANIMAL") and previous[1] == orders[cursor][1]:
                break
            orders[cursor - 1], orders[cursor] = orders[cursor], orders[cursor - 1]
            cursor -= 1
    action["market"] = orders


def subtract_advanced_sales(action, state, step):
    """Remove quantities already requested one turn early, retaining order slots."""
    if state.sale_due_step == step:
        remaining = dict(state.advanced_sales)
        for order in action["market"]:
            if order and order[0] == "SELL" and len(order) >= 3:
                item = order[1]
                removed = min(max(0, int(order[2])), remaining.get(item, 0))
                if removed > 0:
                    order[2] = int(order[2]) - removed
                    remaining[item] -= removed
    state.advanced_sales = {}
    state.sale_due_step = -1


def advance_sales(action, view, state, tape, step):
    """Bring eligible sales from our next planned action forward by one turn."""
    next_step = step + 1
    if next_step > LAST_STEP or next_step % 72 == 0:
        return
    planned = {}
    for order in tape[next_step].get("market") or []:
        if order and order[0] == "SELL" and len(order) >= 3 and order[1] in PRODUCTS:
            item = order[1]
            planned[item] = planned.get(item, 0) + max(0, int(order[2]))
    already_selling = {order[1] for order in action["market"]
                       if order and order[0] == "SELL" and len(order) > 1}
    stock = projected_shed(action, view)
    for item in PRODUCTS:
        if item in ("WHEAT", "FERTILIZER") or item in already_selling:
            continue
        quantity = min(stock.get(item, 0), planned.get(item, 0))
        if quantity <= 0 or int(view.prices.get(item, 0)) < 2:
            continue
        if len(action["market"]) >= MAX_ORDERS:
            break
        action["market"].append(["SELL", item, quantity])
        state.advanced_sales[item] = quantity
    if state.advanced_sales:
        state.sale_due_step = next_step


def room_guard(action, view, step):
    if step % TURNS_PER_DAY != TURNS_PER_DAY - 1:
        return
    carried = sum(max(0, int(n)) for inv in view.inventories for n in inv.values())
    shed_total = sum(view.shed.values())
    total = shed_total + carried
    if total <= 99:
        return
    needed = total - 99
    planned_sells = {}
    for o in action.get("market", []):
        if o and o[0] == "SELL" and len(o) >= 3:
            planned_sells[o[1]] = planned_sells.get(o[1], 0) + max(0, int(o[2]))
    
    priority = sorted(PRODUCTS, key=lambda it: -int(view.prices.get(it, 0)))
    for item in priority:
        avail = max(0, view.shed.get(item, 0) - planned_sells.get(item, 0))
        qty = min(needed, avail)
        if qty <= 0:
            continue
        if len(action["market"]) >= MAX_ORDERS:
            break
        action["market"].append(["SELL", item, qty])
        planned_sells[item] = planned_sells.get(item, 0) + qty
        needed -= qty
        if needed <= 0:
            break

def liquidate(view):
    """On the last turn, drop reachable inventory and sell the projected shed."""
    workers = [["DROP"] if view.beside_shed(pos) and view.inventory(worker) else ["PASS"]
               for worker, pos in enumerate(view.positions)]
    action = {"farmer": workers[0], "hands": workers[1:], "market": []}
    stock = projected_shed(action, view)
    action["market"] = [["SELL", item, stock[item]] for item in PRODUCTS if stock[item] > 0]
    action["market"].sort(key=lambda order: -int(view.prices.get(order[1], 0)) * order[2])
    return action


class Policy:
    def __init__(self, folder):
        self.tapes = json.loads((Path(folder) / "actions.json").read_text())
        if len(self.tapes) != 13 or any(len(tape) != LAST_STEP + 1 for tape in self.tapes):
            raise ValueError("Expected 13 complete, 719-turn action tapes")
        self.schedules = [sell_schedule(tape) for tape in self.tapes]
        self.players = {}

    def act(self, observation):
        step, player = int(observation["step"]), int(observation["player"])
        state = self.players.get(player)
        if state is None or step <= state.last_step:
            state = self.players[player] = DayState()
        state.last_step = step

        if step == ROUTE_STEP:
            shops = observation["town"]["unlocked_shops"]
            state.plan = SHOP_PLANS.get(tuple(shops[:2]), 0)
        if step == FINAL_PLAN_STEP:
            state.plan = 2

        view = FarmView(observation)
        tape = self.tapes[state.plan]
        action = copy.deepcopy(tape[step])
        repair_weeds(action, view, state, step)
        subtract_advanced_sales(action, state, step)
        advance_sales(action, view, state, tape, step)
        room_guard(action, view, step)
        dead_stock(action, view, self.schedules[state.plan], step)
        action["market"] = action["market"][:MAX_ORDERS]
        if ADVANCE_START <= step < LAST_STEP:
            reserve_sales(action, view, state, tape, step)
        if step >= ROUTE_STEP:
            sales_first(action)
        return liquidate(view) if step == LAST_STEP else action


_POLICY = None


def agent(observation, configuration=None):
    global _POLICY
    if _POLICY is None:
        # Kaggle's source loader omits __file__, but retains the code filename.
        folder = Path(agent.__code__.co_filename).resolve().parent
        _POLICY = Policy(folder)
    return _POLICY.act(observation)
