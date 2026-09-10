"""Where a paired result comes from, split by the world regime the backbone routes on.

An aggregate win rate cannot distinguish a tie-break that applies everywhere from a real
weakness concentrated in one kind of world. v006 selects its plan at step 144 from the first
two unlocked shops, so that pair is the natural regime label: it is what the backbone itself
conditions on, and any weakness that concentrates inside one regime is a decision boundary
rather than noise.

The shop pair is read from the engine by replaying each seed to step 144 with a probe agent,
so the label comes from the world and not from either agent's behaviour.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PROBE = '''def agent(observation, configuration=None):
    return {'farmer': ['PASS'], 'hands': [], 'market': []}
'''
ROUTE_STEP = 144


def shop_pair(seed, probe, cache):
    """The first two unlocked shops at the step the backbone routes on."""
    if seed in cache:
        return cache[seed]
    with tempfile.NamedTemporaryFile('r', suffix='.json', delete=False) as handle:
        path = handle.name
    result = subprocess.run(
        [sys.executable, '-m', 'arena.match', '--agent', probe, '--opponent', probe,
         '--seed', str(seed), '--seat', '0', '--replay', path,
         '--evidence-profile', 'full'],
        cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-200:])
    replay = json.loads(Path(path).read_text(encoding='utf-8'))
    Path(path).unlink(missing_ok=True)
    frame = next((turn for turn in replay['turns'] if turn['step'] >= ROUTE_STEP), None)
    shops = tuple((frame['observations'][0]['town']['unlocked_shops'] or ())[:2]) if frame else ()
    cache[seed] = shops
    return shops


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, help='an arena.league output directory')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    rows = [json.loads(line) for line in
            (Path(args.run) / 'matches.jsonl').read_text(encoding='utf-8').splitlines()
            if line.strip()]
    workdir = Path(tempfile.mkdtemp(prefix='regime-'))
    probe = workdir / 'probe.py'
    probe.write_text(PROBE, encoding='utf-8')

    cache, by_regime, by_shop = {}, defaultdict(list), defaultdict(list)
    for row in rows:
        shops = shop_pair(row['seed'], str(probe), cache)
        by_regime[shops].append(row)
        for shop in shops:
            by_shop[shop].append(row)

    def block(batch):
        scores = [r['score'] for r in batch]
        margins = [r['margin'] for r in batch]
        return {'games': len(batch), 'score': round(statistics.mean(scores), 3),
                'median_margin': round(statistics.median(margins), 1),
                'seeds': len({r['seed'] for r in batch})}

    payload = {
        'schema_version': 1, 'run': args.run,
        'candidate_hash': rows[0]['candidate_hash'],
        'opponent_hash': rows[0]['opponent_hash'],
        'overall': block(rows),
        'by_regime': sorted(
            ({'shops': list(shops), **block(batch)} for shops, batch in by_regime.items()),
            key=lambda entry: entry['score']),
        'by_shop_present': sorted(
            ({'shop': shop, **block(batch)} for shop, batch in by_shop.items()),
            key=lambda entry: entry['score']),
    }
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in payload.items() if k != 'by_regime'}, indent=2)[:1800])


if __name__ == '__main__':
    main()
