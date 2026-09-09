"""Record action tapes from strong donors, because the meta is imitation.

The public top of this competition does not compute a strategy at runtime: it
replays recorded 719-step action streams and switches between them on public
state. The strongest artifact we measured carries five such tapes, two of which
are near-duplicates, and branches at only two of its ten decision blocks.

Episode replays cannot be scraped here — the competition denies `episodes.get`
even to an authenticated account — so tapes are generated locally instead. That
is the better source anyway: we choose the seeds, we get both seats, we know the
opponent, and every tape carries the outcome it actually produced.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from arena.agents import agent_hash
from arena.parallel import matches
from arena.seeds import parse_seeds

SCHEMA = 1


def tape_digest(actions):
    """Identity of a tape is its action stream, so duplicates cannot inflate a library."""
    return hashlib.sha256(json.dumps(actions, sort_keys=True, separators=(',', ':'))
                          .encode()).hexdigest()


def extract(replay, seat):
    """Take the donor's own action stream, indexed by turn, from a lab replay."""
    turns = replay['turns']
    if len(turns) != 719:
        raise ValueError(f'Expected 719 recorded turns, found {len(turns)}')
    actions = []
    for expected, turn in enumerate(turns, start=1):
        if turn['step'] != expected:
            raise ValueError(f"Replay is not contiguous at step {turn['step']}")
        actions.append(turn['actions'][seat])
    return actions


def harvest(donors, opponents, seeds, output, *, workers=4, min_score=1.,
            replay_dir=None):
    """Run every donor against every opponent on both seats and keep the winners.

    A tape is only evidence of good play if the game it came from was good, so
    the outcome travels with it and the filter is explicit rather than implied.
    """
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    seeds = list(seeds)
    if not donors or not opponents or not seeds:
        raise ValueError('Require donors, opponents and seeds')
    scratch = Path(replay_dir) if replay_dir else output.parent / (output.name + '.replays')
    scratch.mkdir(parents=True, exist_ok=True)
    jobs, meta = [], []
    for donor in donors:
        for opponent in opponents:
            for seed in seeds:
                for seat in (0, 1):
                    path = scratch / f'{Path(donor).stem}-{Path(opponent).stem}-{seed}-{seat}.json'
                    jobs.append(dict(candidate=donor, opponent=opponent, seed=seed, seat=seat,
                                     replay=str(path), evidence_profile='full'))
                    meta.append((donor, opponent, seed, seat, path))
    library, seen, stats = [], {}, Counter()
    # Positional pairing, so the ordered mode is requested explicitly rather than relied on.
    for (donor, opponent, seed, seat, path), row in zip(meta, matches(jobs, workers,
                                                                     ordered=True)):
        stats['games'] += 1
        if row['failures'] or row['opponent_failures']:
            stats['failed'] += 1
            path.unlink(missing_ok=True)
            continue
        if row['score'] < min_score:
            stats['rejected_weak'] += 1
            path.unlink(missing_ok=True)
            continue
        actions = extract(json.loads(path.read_text()), seat)
        digest = tape_digest(actions)
        path.unlink(missing_ok=True)
        if digest in seen:
            stats['duplicates'] += 1
            seen[digest]['sources'].append({'donor': donor, 'opponent': opponent,
                                            'seed': seed, 'seat': seat})
            continue
        entry = {'id': digest[:16], 'sha256': digest, 'donor': donor,
                 'donor_hash': agent_hash(donor), 'opponent': opponent, 'seed': seed,
                 'seat': seat, 'score': row['score'], 'money': row['money'],
                 'opponent_money': row['opponent_money'], 'margin': row['margin'],
                 'sources': [{'donor': donor, 'opponent': opponent, 'seed': seed, 'seat': seat}],
                 'actions': actions}
        seen[digest] = entry
        library.append(entry)
        stats['kept'] += 1
    library.sort(key=lambda tape: (-tape['score'], tape['sha256']))
    payload = {'schema_version': SCHEMA,
               'created_at': datetime.now(timezone.utc).isoformat(),
               'donors': {name: agent_hash(name) for name in donors},
               'opponents': {name: agent_hash(name) for name in opponents},
               'seeds': seeds, 'filter': {'min_score': min_score},
               'diagnostic_fields': ['money', 'opponent_money', 'margin'],
               'selection_rule': 'score descending, then action digest',
               'stats': dict(stats), 'tapes': library}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload) + '\n', encoding='utf-8')
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--donors', required=True, help='comma-separated agent paths')
    parser.add_argument('--opponents', required=True)
    parser.add_argument('--seeds', default='1000:1010')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--min-score', type=float, default=1.)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    payload = harvest(args.donors.split(','), args.opponents.split(','),
                      parse_seeds(args.seeds), args.output, workers=args.workers,
                      min_score=args.min_score)
    print(json.dumps({'stats': payload['stats'],
                      'tapes': len(payload['tapes']),
                      'first_margin_diagnostic': payload['tapes'][0]['margin'] if payload['tapes'] else None},
                     indent=2))


if __name__ == '__main__':
    main()
