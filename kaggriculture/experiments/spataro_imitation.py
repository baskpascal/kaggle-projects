"""Train a compact, reactive nearest-trajectory policy from SpaTaro episodes.

Unlike a tape, the generated agent chooses a donor again on every turn from the
state that actually exists.  The target is the donor's complete action: market
slots are deliberately never factorised because their ordering is strategic.
"""
import argparse
import base64
from collections import Counter
import json
from pathlib import Path
import zipfile
import zlib

RESOURCES = ('CARROT', 'EGG', 'FERTILIZER', 'MELON', 'MILK', 'STRAWBERRY',
             'TOMATO', 'WHEAT', 'WOOL')
SEEDS = ('CARROT', 'MELON', 'STRAWBERRY', 'TOMATO', 'WHEAT')
ANIMALS = ('COW', 'GOOSE', 'SHEEP')
SHOPS = ('BAKERY', 'ICE_CREAM_SHOP', 'PIZZA_SHOP', 'YARN_STORE')
MAX_HANDS = 15
KIND = {None: 0, 'LOCKED': 1, 'EMPTY': 2, 'WEED': 3, 'PLANT': 4,
        'PASTURE': 5, 'CARROT': 6, 'MELON': 7, 'STRAWBERRY': 8,
        'TOMATO': 9, 'WHEAT': 10, 'COW': 11, 'GOOSE': 12, 'SHEEP': 13}


def _tile_code(tile):
    if tile is None:
        return KIND['EMPTY']
    if tile == 'LOCKED':
        return KIND['LOCKED']
    if not isinstance(tile, dict):
        return 0
    return KIND.get(tile.get('crop') or tile.get('animal') or tile.get('kind'), 0)


def _at(tiles, position):
    try:
        return tiles[int(position[0])][int(position[1])]
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def features(observation):
    """Small integer signature, ordered from decisive to contextual fields."""
    player = int(observation.get('player', 0))
    farms = observation['farms']
    own, foe = farms[player], farms[1 - player]
    private = observation.get('private') or {}
    market = observation.get('market') or {}
    prices, stock = market.get('prices') or {}, market.get('inventory') or {}
    shops = Counter((observation.get('town') or {}).get('unlocked_shops') or [])
    vector = [
        len(own.get('hands') or []), len(foe.get('hands') or []),
        int((own.get('money') or 0) // 100), int((foe.get('money') or 0) // 100),
        int(own.get('hires_today') or 0), int(foe.get('hires_today') or 0),
        len(own.get('unlocked_quadrants') or []), len(foe.get('unlocked_quadrants') or []),
    ]
    vector += [int(prices.get(key, 0) // 5) for key in RESOURCES]
    vector += [int(stock.get(key, 0) // 20) for key in RESOURCES]
    vector += [shops[key] for key in SHOPS]
    shed, seeds = private.get('shed') or {}, private.get('seeds') or {}
    vector += [int(shed.get(key, 0)) for key in RESOURCES]
    vector += [int(seeds.get(key, 0)) for key in SEEDS]

    # Worker order matters because the action list is indexed by worker.  Local
    # tile type lets a donor distinguish e.g. FEED from WATER at one position.
    for farm in (own, foe):
        workers = [farm.get('farmer') or [-1, -1]] + list(farm.get('hands') or [])
        tiles = farm.get('tiles') or []
        for index in range(MAX_HANDS + 1):
            if index < len(workers):
                pos = workers[index]
                vector += [int(pos[0]), int(pos[1]), _tile_code(_at(tiles, pos))]
            else:
                vector += [-1, -1, 0]

    # Aggregate farm composition prevents identical worker layouts on radically
    # different farms from looking close without storing both 10x10 grids.
    for farm in (own, foe):
        counts = Counter()
        yields = Counter()
        for row in farm.get('tiles') or []:
            for tile in row:
                code = _tile_code(tile)
                counts[code] += 1
                if isinstance(tile, dict):
                    label = tile.get('crop') or tile.get('animal') or tile.get('kind')
                    yields[label] += int(tile.get('yield_units') or 0)
        vector += [counts[i] for i in range(1, 14)]
        vector += [yields[key] for key in SEEDS + ANIMALS]

    # Contents carried by each hand determine whether DROP/FEED/FERTILIZE is legal.
    inventories = private.get('inventories') or []
    for index in range(MAX_HANDS):
        bag = inventories[index] if index < len(inventories) else {}
        vector += [int((bag or {}).get(key, 0)) for key in RESOURCES]
    return vector


def _episode_member(archive, episode):
    suffix = f'{episode}.json'
    matches = [name for name in archive.namelist() if name.endswith(suffix)]
    if len(matches) != 1:
        raise ValueError(f'episode {episode}: expected one archive member, got {len(matches)}')
    return matches[0]


def rows(library_path, archive_path, *, team='SpaTaro', winners_only=False,
         excluded=()):
    library = json.loads(Path(library_path).read_text())
    excluded = {int(value) for value in excluded}
    selected = [t for t in library['tapes'] if t.get('team') == team
                and int(t['episode']) not in excluded
                and (not winners_only or (t.get('money') or 0) > (t.get('opponent_money') or 0))]
    if not selected:
        raise ValueError('selection produced no training episodes')
    by_turn = [[] for _ in range(719)]
    with zipfile.ZipFile(archive_path) as archive:
        for tape in selected:
            episode = json.loads(archive.read(_episode_member(archive, tape['episode'])))
            seat = int(tape['seat'])
            for turn in range(719):
                observation = episode['steps'][turn][seat]['observation']
                action = episode['steps'][turn + 1][seat]['action']
                by_turn[turn].append([features(observation), action])
    return selected, by_turn


TEMPLATE = '''"""Reactive whole-action imitation model trained from {count} {team} episodes."""
import base64, json, zlib

MODEL = {blobs!r}
RESOURCES = ('CARROT','EGG','FERTILIZER','MELON','MILK','STRAWBERRY','TOMATO','WHEAT','WOOL')
SEEDS = ('CARROT','MELON','STRAWBERRY','TOMATO','WHEAT')
ANIMALS = ('COW','GOOSE','SHEEP')
SHOPS = ('BAKERY','ICE_CREAM_SHOP','PIZZA_SHOP','YARN_STORE')
MAX_HANDS = 15
KIND = {{None:0,'LOCKED':1,'EMPTY':2,'WEED':3,'PLANT':4,'PASTURE':5,'CARROT':6,'MELON':7,'STRAWBERRY':8,'TOMATO':9,'WHEAT':10,'COW':11,'GOOSE':12,'SHEEP':13}}

def tc(x):
    if x is None:return 2
    if x=='LOCKED':return 1
    if not isinstance(x,dict):return 0
    return KIND.get(x.get('crop') or x.get('animal') or x.get('kind'),0)
def at(ts,p):
    try:return ts[int(p[0])][int(p[1])]
    except:return None
def feat(o):
    p=int(o.get('player',0)); fs=o['farms']; own,foe=fs[p],fs[1-p]; pr=o.get('private') or {{}}; m=o.get('market') or {{}}; ps=m.get('prices') or {{}}; st=m.get('inventory') or {{}}
    sh={{k:0 for k in SHOPS}}
    for k in (o.get('town') or {{}}).get('unlocked_shops') or []:sh[k]=sh.get(k,0)+1
    v=[len(own.get('hands') or []),len(foe.get('hands') or []),int((own.get('money') or 0)//100),int((foe.get('money') or 0)//100),int(own.get('hires_today') or 0),int(foe.get('hires_today') or 0),len(own.get('unlocked_quadrants') or []),len(foe.get('unlocked_quadrants') or [])]
    v += [int(ps.get(k,0)//5) for k in RESOURCES]+[int(st.get(k,0)//20) for k in RESOURCES]+[sh[k] for k in SHOPS]
    sd,se=pr.get('shed') or {{}},pr.get('seeds') or {{}}
    v += [int(sd.get(k,0)) for k in RESOURCES]+[int(se.get(k,0)) for k in SEEDS]
    for f in (own,foe):
        ws=[f.get('farmer') or [-1,-1]]+list(f.get('hands') or []); ts=f.get('tiles') or []
        for i in range(MAX_HANDS+1):
            if i<len(ws):q=ws[i];v += [int(q[0]),int(q[1]),tc(at(ts,q))]
            else:v += [-1,-1,0]
    for f in (own,foe):
        cs=[0]*14; ys={{k:0 for k in SEEDS+ANIMALS}}
        for row in f.get('tiles') or []:
            for x in row:
                c=tc(x);cs[c]+=1
                if isinstance(x,dict):
                    k=x.get('crop') or x.get('animal') or x.get('kind')
                    if k in ys:ys[k]+=int(x.get('yield_units') or 0)
        v += cs[1:14]+[ys[k] for k in SEEDS+ANIMALS]
    ins=pr.get('inventories') or []
    for i in range(MAX_HANDS):
        b=ins[i] if i<len(ins) else {{}};v += [int((b or {{}}).get(k,0)) for k in RESOURCES]
    return v
def agent(o,configuration=None):
    t=o.get('step');t=int(t) if isinstance(t,int) else int(o['day'])*24+int(o['hour'])
    if not 0<=t<len(MODEL):return {{'farmer':['PASS'],'hands':[],'market':[]}}
    choices=json.loads(zlib.decompress(base64.b85decode(MODEL[t])))
    x=feat(o); best=None; bd=None
    for y,a in choices:
        # First fields and worker triples dominate; L1 is robust to money outliers.
        d=40*abs(x[0]-y[0])+20*abs(x[1]-y[1])
        for i,(u,v) in enumerate(zip(x[2:],y[2:]),2):
            w=6 if 39<=i<135 else (3 if i<39 else 1)
            d += w*abs(u-v)
        if bd is None or d<bd:bd=d;best=a
    nh=len((o['farms'][int(o.get('player',0))].get('hands') or []))
    hs=[list(a) if isinstance(a,list) and a else ['PASS'] for a in best.get('hands',[])[:nh]]
    hs += [['PASS'] for _ in range(nh-len(hs))]
    return {{'farmer':list(best.get('farmer') or ['PASS']),'hands':hs,'market':[list(a) for a in best.get('market',[])[:10]]}}
'''


def build(library, archive, output, *, team='SpaTaro', winners_only=False, excluded=()):
    selected, model = rows(library, archive, team=team, winners_only=winners_only,
                           excluded=excluded)
    packed = json.dumps(model, separators=(',', ':')).encode()
    blobs = [base64.b85encode(zlib.compress(
        json.dumps(turn, separators=(',', ':')).encode(), 9)).decode() for turn in model]
    source = TEMPLATE.format(count=len(selected), team=team, blobs=blobs)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(source, encoding='utf-8')
    return {'episodes': len(selected), 'examples': sum(map(len, model)),
            'raw_bytes': len(packed), 'source_bytes': len(source.encode()),
            'excluded': sorted(map(int, excluded)), 'output': str(output)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', required=True)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--team', default='SpaTaro')
    parser.add_argument('--winners-only', action='store_true')
    parser.add_argument('--exclude-episode', action='append', default=[])
    args = parser.parse_args()
    print(json.dumps(build(args.library, args.archive, args.output, team=args.team,
                           winners_only=args.winners_only,
                           excluded=args.exclude_episode), indent=2))


if __name__ == '__main__':
    main()
