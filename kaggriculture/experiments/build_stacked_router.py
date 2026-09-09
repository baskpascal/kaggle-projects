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
RESCUE_FILES = ('main.py', 'policy.py', 'terminal_planner.py', 'unit_model.py',
                'settings.json', 'NOTICE.txt')
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


def router_source(table=None, room_guard=False, note=None):
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
    if note:
        source = source.replace('SHOP_PLANS = {', f'# {note}\nSHOP_PLANS = {{', 1)
    compile(source, 'main.py', 'exec')
    return source


def build(output, *, table=None, room_guard=False, terminal_rescue=False, note=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    router = router_source(table, room_guard, note)
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
                       'terminal_rescue': terminal_rescue},
            'members': {name: hashlib.sha256(payload).hexdigest()
                        for name, payload in sorted(files.items())},
            'archive_sha256': hashlib.sha256(packed).hexdigest(),
            'archive_bytes': len(packed)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--table', help='JSON mapping "SHOP|SHOP" to a plan index')
    parser.add_argument('--room-guard', action='store_true')
    parser.add_argument('--terminal-rescue', action='store_true')
    parser.add_argument('--note')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    table = None
    if args.table:
        table = {tuple(key.split('|')): int(plan)
                 for key, plan in json.loads(Path(args.table).read_text()).items()}
    print(json.dumps(build(args.output, table=table, room_guard=args.room_guard,
                           terminal_rescue=args.terminal_rescue, note=args.note), indent=2))


if __name__ == '__main__':
    main()
