"""Build small state-based overlays around Debmalya's public v49/v50 route chassis."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


WRAPPER = '''"""Measured overlay around an exact public route chassis."""
import importlib.util
import os
from pathlib import Path

os.environ.update({environment!r})
_spec = importlib.util.spec_from_file_location('_deb_route_parent',
    Path(__file__).resolve().parent / 'parent.py')
_parent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_parent)
ROOM_GUARD = {room_guard!r}
RETURN_FROM = {return_from!r}
PRODUCTS = ('WHEAT','CARROT','TOMATO','STRAWBERRY','MELON','EGG','MILK','WOOL','FERTILIZER')

def _position(farm, index):
    return farm['farmer'] if index == 0 else farm['hands'][index - 1]

def _towards_shed(position, size):
    x, y = position
    targets = ((size//2-1,size//2-1),(size//2,size//2-1),
               (size//2-1,size//2),(size//2,size//2))
    tx, ty = min(targets, key=lambda p: abs(x-p[0])+abs(y-p[1]))
    if x < tx: return ['EAST']
    if x > tx: return ['WEST']
    if y < ty: return ['SOUTH']
    if y > ty: return ['NORTH']
    return ['DROP']

def _return_inventory(action, observation, step):
    if RETURN_FROM is None or step < RETURN_FROM:
        return
    private = observation.get('private') or {{}}
    inventories = private.get('inventories') or []
    farm = observation['farms'][int(observation['player'])]
    commands = [action.get('farmer', ['PASS'])] + list(action.get('hands') or [])
    for index, inventory in enumerate(inventories):
        if inventory and index < len(commands):
            commands[index] = _towards_shed(_position(farm, index), len(farm['tiles']))
    action['farmer'], action['hands'] = commands[0], commands[1:]

def _guard(action, observation, step):
    if not ROOM_GUARD or step % 24 != 23:
        return
    private = observation.get('private') or {{}}
    shed = private.get('shed') or {{}}
    carried = sum(sum(max(0, int(q)) for q in inv.values())
                  for inv in private.get('inventories') or [])
    sold = {{}}
    bought = 0
    for order in action.get('market') or []:
        if len(order) >= 3 and order[0] == 'SELL':
            sold[order[1]] = sold.get(order[1], 0) + max(0, int(order[2]))
        elif order and order[0] in ('BUY_PRODUCT', 'BUY_ANIMAL') and len(order) >= 3:
            bought += max(0, int(order[2]))
    projected = sum(max(0, int(q) - sold.get(item, 0)) for item, q in shed.items()) + carried + bought
    needed = max(0, projected - 99)
    prices = (observation.get('market') or {{}}).get('prices') or {{}}
    for item in sorted(PRODUCTS, key=lambda name: -int(prices.get(name, 0))):
        if needed <= 0 or len(action.get('market') or []) >= 10:
            break
        available = max(0, int(shed.get(item, 0)) - sold.get(item, 0))
        quantity = min(needed, available)
        if quantity:
            action.setdefault('market', []).append(['SELL', item, quantity])
            needed -= quantity

def agent(observation, configuration=None):
    action = _parent.agent(observation)
    step = int(observation.get('step', 0) or 0)
    _return_inventory(action, observation, step)
    _guard(action, observation, step)
    if step == 718:
        action['market'] = [['SELL', item, 1000] for item in PRODUCTS]
    return action
'''


def build(parent, output, *, room_guard=False, return_from=None, environment=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(parent, output / 'parent.py')
    environment = {str(key): str(value) for key, value in (environment or {}).items()}
    source = WRAPPER.format(room_guard=room_guard, return_from=return_from,
                            environment=environment)
    compile(source, str(output / 'main.py'), 'exec')
    (output / 'main.py').write_text(source, encoding='utf-8')
    return {'parent_sha256': hashlib.sha256(Path(parent).read_bytes()).hexdigest(),
            'main_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'room_guard': room_guard, 'return_from': return_from,
            'environment': environment}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--room-guard', action='store_true')
    parser.add_argument('--return-from', type=int)
    parser.add_argument('--environment', default='{}', help='JSON KAGG_* overrides')
    args = parser.parse_args()
    print(json.dumps(build(args.parent, args.output, room_guard=args.room_guard,
                           return_from=args.return_from,
                           environment=json.loads(args.environment)), indent=2))


if __name__ == '__main__':
    main()
