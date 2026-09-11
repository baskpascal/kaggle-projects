"""Compose one submission bundle out of the public 0909 router and the layers that beat it.

Three public artifacts sit on the same parent and each wins against it head to head, so
the question is not which of them to copy but whether they compose:

* `yhay81/shop-router-0909` - thirteen 719-turn tapes plus the routing, weed-repair and
  sale-advance rules. Every other file here is unchanged upstream bytes.
* `aurax7/kaggriculture-shop-router-reactive-v2` - `room_guard`, which sells the surplus on
  the last turn of a day so the shed does not overflow when inventories drop into it.
* `dmitriigluzdov/kaggriculture-seven-turn-rescue-best-lb-2800` - a bounded planner that
  replaces the last seven callbacks with collect-and-sell routes it verifies against an
  extracted copy of the engine's unit semantics.

Our own contribution is the routing table (`experiments/fit_shop_table.py`) and the
composition itself. Every layer is optional and off unless asked for, because a layer that
is not measured in the combination it ships in has not been measured at all.

    python -m experiments.build_stacked_router --table <table.json> --room-guard \
        --terminal-rescue --output versions/v006
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / 'opponents' / 'public' / 'yhay_router_0909'
AURAX = ROOT / 'opponents' / 'public' / 'aurax_reactive_v2'
RESCUE = ROOT / 'opponents' / 'public' / 'gluzdov_e182'
TABLE = re.compile(r'^SHOP_PLANS = \{.*?^\}$', re.MULTILINE | re.DOTALL)
GUARD = re.compile(r'^def room_guard\(action, view, step\):\n(?:.*\n)*?(?=\ndef liquidate)',
                   re.MULTILINE)
CALL_SITE = '        advance_sales(action, view, state, tape, step)\n'
# The parent throttles its own sale-advance on every fourth turn. The town's demand race is
# resolved by order index between the two seats, so a seat that waits loses it outright, and
# seven of the seventeen pinned public artifacts carry this same clause.
ADVANCE_GUARD = ('    if next_step > LAST_STEP or next_step % 72 == 0 '
                 'or step % 4 == 0:')
RELAXED_ADVANCE = '    if next_step > LAST_STEP or next_step % 72 == 0:'
RESCUE_FILES = ('main.py', 'policy.py', 'terminal_planner.py', 'unit_model.py',
                'settings.json', 'NOTICE.txt')

DEAD_STOCK_SOURCE = '''
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
'''
DEAD_STOCK_CALL = '        dead_stock(action, view, self.schedules[state.plan], step)\n'
DEAD_STOCK_INIT = '        self.schedules = [sell_schedule(tape) for tape in self.tapes]\n'

HAND_ALIGN_SOURCE = '''
def hand_align(action, view):
    """Pad or truncate the tape's hand list to the hands this world actually has.

    A tape records the crew it was captured with. Extra entries the engine ignores,
    but a short list leaves real hands idle for the turn, and the backbone's own
    telemetry reports a mean of ten and a half hands against tapes that do not always
    address that many.
    """
    expected = max(0, len(view.positions) - 1)
    hands = list(action.get("hands") or [])
    hands.extend([["PASS"] for _ in range(max(0, expected - len(hands)))])
    action["hands"] = hands[:expected]
'''
HAND_ALIGN_CALL = '        hand_align(action, view)\n'

CLAMP_SOURCE = '''
def clamp_sells(action, view):
    """Trim SELL quantities to the stock that will actually exist, keeping the slots.

    An order for more than we hold is refused whole, so the turn's demand is lost
    rather than partly taken. Empty orders are kept rather than dropped because
    removing them would renumber the remaining ones and change the lockstep race
    against the other seat.
    """
    available = projected_shed(action, view)
    kept = []
    for order in (action.get("market") or []):
        if order and order[0] == "SELL" and len(order) >= 3 and order[1] in PRODUCTS:
            have = available.get(order[1], 0)
            quantity = max(0, min(int(order[2]), have))
            available[order[1]] = have - quantity
            kept.append(["SELL", order[1], quantity])
        else:
            kept.append(order)
            if order and order[0] == "BUY_PRODUCT" and len(order) >= 3:
                available[order[1]] = available.get(order[1], 0) + max(0, int(order[2]))
    action["market"] = kept
'''
CLAMP_CALL = '        clamp_sells(action, view)\n'

NOTICE = """
Uncle Scooge composition (2026-09-09)
------------------------------------
router_parent.py is yhay81/shop-router-0909's main.py with two changes: the
SHOP_PLANS table was refitted locally from paired mirror games, and room_guard,
plus its single call site, was copied verbatim from
aurax7/kaggriculture-shop-router-reactive-v2 (Kaggle notebook, Apache-2.0).
actions.json and LICENSE.txt are unmodified upstream bytes. Every other file is
an unmodified copy of dmitriigluzdov/kaggriculture-seven-turn-rescue-best-lb-2800.
No upstream ownership or endorsement is claimed.
"""


def render_table(table):
    body = ''.join(f'    ("{first}", "{second}"): {plan},\n'
                   for (first, second), plan in sorted(table.items()))
    return 'SHOP_PLANS = {\n' + body + '}'


def router_source(table=None, room_guard=False, note=None, relax_advance=False,
                  dead_stock=False, hand_align=False, clamp_sells=False):
    """The 0909 router, with our table and the public room guard folded in as source."""
    source = (PARENT / 'main.py').read_text()
    if table is not None:
        if not TABLE.search(source):
            raise ValueError('The parent no longer carries a SHOP_PLANS table')
        source = TABLE.sub(lambda _: render_table(table), source, count=1)
    if room_guard:
        guard = GUARD.search((AURAX / 'main.py').read_text())
        if not guard or CALL_SITE not in source:
            raise ValueError('room_guard or its call site is missing upstream')
        source = source.replace('\ndef liquidate(view):', f'\n{guard.group(0)}\ndef liquidate(view):', 1)
        source = source.replace(CALL_SITE, CALL_SITE + '        room_guard(action, view, step)\n', 1)
    if hand_align:
        if CALL_SITE not in source:
            raise ValueError('The parent no longer has the layer call site')
        source = source.replace('\ndef repair_weeds(', '\n' + HAND_ALIGN_SOURCE.strip()
                                + '\n\n\ndef repair_weeds(', 1)
        anchor = '        view = FarmView(observation)\n'
        marker = '        action = copy.deepcopy(tape[step])\n'
        source = source.replace(marker, marker + HAND_ALIGN_CALL, 1)
    if clamp_sells:
        source = source.replace('\ndef subtract_advanced_sales(', '\n' + CLAMP_SOURCE.strip()
                                + '\n\n\ndef subtract_advanced_sales(', 1)
        guard_call = '        room_guard(action, view, step)\n'
        after = guard_call if guard_call in source else CALL_SITE
        source = source.replace(after, after + CLAMP_CALL, 1)
    if dead_stock:
        anchor = '\ndef subtract_advanced_sales(action, state, step):'
        if anchor not in source or 'self.tapes = json.loads' not in source:
            raise ValueError('The parent no longer has the anchors dead_stock needs')
        source = source.replace(anchor, '\n' + DEAD_STOCK_SOURCE.strip() + '\n\n' + anchor, 1)
        source = source.replace('        self.players = {}\n',
                                DEAD_STOCK_INIT + '        self.players = {}\n', 1)
        guard_call = '        room_guard(action, view, step)\n'
        call_after = guard_call if guard_call in source else CALL_SITE
        source = source.replace(call_after, call_after + DEAD_STOCK_CALL, 1)
    if relax_advance:
        if source.count(ADVANCE_GUARD) != 1:
            raise ValueError('The parent no longer carries the advance_sales throttle')
        source = source.replace(ADVANCE_GUARD, RELAXED_ADVANCE, 1)
    if note:
        source = source.replace('SHOP_PLANS = {', f'# {note}\nSHOP_PLANS = {{', 1)
    compile(source, 'main.py', 'exec')
    return source


def build(output, *, table=None, room_guard=False, terminal_rescue=False, note=None,
          relax_advance=False, dead_stock=False, hand_align=False, clamp_sells=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    router = router_source(table, room_guard, note, relax_advance, dead_stock,
                           hand_align, clamp_sells)
    files = {'actions.json': (PARENT / 'actions.json').read_bytes(),
             'LICENSE.txt': (PARENT / 'LICENSE.txt').read_bytes()}
    if terminal_rescue:
        files['router_parent.py'] = router.encode()
        for name in RESCUE_FILES:
            files[name] = (RESCUE / name).read_bytes()
        files['NOTICE.txt'] += NOTICE.encode()
    else:
        files['main.py'] = router.encode()
    for name, payload in files.items():
        (output / name).write_bytes(payload)
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w') as handle:
        for name in sorted(files):
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(files[name]), 0o644, 0
            handle.addfile(member, io.BytesIO(files[name]))
    packed = gzip.compress(archive.getvalue(), mtime=0)
    (output / 'main.tar.gz').write_bytes(packed)
    return {'layers': {'table': table is not None, 'room_guard': room_guard,
                       'terminal_rescue': terminal_rescue,
                       'relax_advance': relax_advance, 'dead_stock': dead_stock,
                       'hand_align': hand_align, 'clamp_sells': clamp_sells},
            'members': {name: hashlib.sha256(payload).hexdigest()
                        for name, payload in sorted(files.items())},
            'archive_sha256': hashlib.sha256(packed).hexdigest(),
            'archive_bytes': len(packed)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--table', help='JSON mapping "SHOP|SHOP" to a plan index')
    parser.add_argument('--room-guard', action='store_true')
    parser.add_argument('--terminal-rescue', action='store_true')
    parser.add_argument('--hand-align', action='store_true')
    parser.add_argument('--clamp-sells', action='store_true')
    parser.add_argument('--dead-stock', action='store_true',
                        help='sell stock the rest of the route will never sell')
    parser.add_argument('--relax-advance', action='store_true',
                        help="drop the parent's every-fourth-turn sale-advance throttle")
    parser.add_argument('--note')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    table = None
    if args.table:
        table = {tuple(key.split('|')): int(plan)
                 for key, plan in json.loads(Path(args.table).read_text()).items()}
    print(json.dumps(build(args.output, table=table, room_guard=args.room_guard,
                           terminal_rescue=args.terminal_rescue, note=args.note,
                           relax_advance=args.relax_advance,
                           dead_stock=args.dead_stock, hand_align=args.hand_align,
                           clamp_sells=args.clamp_sells), indent=2))


if __name__ == '__main__':
    main()
