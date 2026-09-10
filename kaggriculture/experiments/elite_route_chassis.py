"""Embed an exact-prefix elite route trie in the public reactive chassis."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import zlib

from experiments.elite_route_train import select_training_tapes, shop_histories, train


ROUTE_BLOCK = '''        if step == ROUTE_STEP:
            shops = observation["town"]["unlocked_shops"]
            state.plan = SHOP_PLANS.get(tuple(shops[:2]), 0)
        if step == FINAL_PLAN_STEP:
            state.plan = 2
'''

TRIE_BLOCK = '''        choices = ROUTES.get(str(step), {}).get(str(state.plan), {})
        shops = "|".join(observation.get("town", {}).get("unlocked_shops", ()))
        state.plan = choices.get(shops, state.plan)
'''


def build(tapes, routes, default, parent, output):
    if default not in range(len(tapes)):
        raise ValueError('default route is outside the selected tapes')
    payload = [[tape['actions'] for tape in tapes], routes, default]
    blob = base64.b85encode(zlib.compress(
        json.dumps(payload, separators=(',', ':')).encode(), 9)).decode()
    source = Path(parent).read_text(encoding='utf-8')
    source = source.replace('import copy\nimport json\n',
                            'import base64\nimport copy\nimport json\nimport zlib\n')
    marker = 'SHED_CAPACITY = 100\n'
    packed = f'TAPES, ROUTES, DEFAULT = json.loads(zlib.decompress(base64.b85decode({blob!r})))\n'
    if source.count(marker) != 1 or source.count(ROUTE_BLOCK) != 1:
        raise ValueError('parent chassis has an unexpected layout')
    source = source.replace(marker, marker + packed, 1)
    source = source.replace('        self.plan = 0\n', '        self.plan = DEFAULT\n', 1)
    loader = 'self.tapes = json.loads((Path(folder) / "actions.json").read_text())'
    if source.count(loader) != 1:
        raise ValueError('parent chassis has no unique tape loader')
    source = source.replace(loader, 'self.tapes = TAPES')
    source = source.replace(
        'if len(self.tapes) != 13 or any(len(tape) != LAST_STEP + 1 for tape in self.tapes):',
        'if not self.tapes or any(len(tape) != LAST_STEP + 1 for tape in self.tapes):')
    source = source.replace(ROUTE_BLOCK, TRIE_BLOCK)
    compile(source, str(output), 'exec')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(source, encoding='utf-8')
    return hashlib.sha256(source.encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', required=True)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--team', required=True)
    parser.add_argument('--default-sha256', required=True)
    parser.add_argument('--parent', default='opponents/public/yhay_router_0909/main.py')
    parser.add_argument('--output', required=True)
    parser.add_argument('--model', required=True)
    args = parser.parse_args()
    library = json.loads(Path(args.library).read_text())
    tapes = select_training_tapes(library, args.team)
    matches = [index for index, tape in enumerate(tapes)
               if tape['sha256'].startswith(args.default_sha256)]
    if len(matches) != 1:
        raise ValueError('--default-sha256 must identify one selected tape')
    routes, coverage = train(tapes, shop_histories(args.archive, tapes))
    digest = build(tapes, routes, matches[0], args.parent, args.output)
    model = {'schema_version': 1, 'kind': 'elite_compatible_route_reactive_chassis',
             'team': args.team, 'tapes': len(tapes), 'default': matches[0],
             'default_sha256': tapes[matches[0]]['sha256'], 'coverage': coverage,
             'artifact_sha256': digest, 'source': args.library, 'archive': args.archive,
             'parent': args.parent}
    path = Path(args.model)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(model, indent=2))


if __name__ == '__main__':
    main()
