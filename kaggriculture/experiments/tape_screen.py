"""Measure recorded tapes in our own arena, because a top episode is not a tape.

A tape from a daily dump is evidence that some agent played a strong game once, on
the ladder's own seed, against one specific opponent. Replayed on a fresh seed it
meets a different town: shops are sampled with replacement, so the demand a tape
was built around may simply not exist. Whether a tape transfers is therefore an
empirical question, and this module answers it the only way that counts, by
playing it.

Screening is deliberately two-stage. A wide, cheap pass over the whole library
against the strongest opponent we hold removes the tapes that cannot transfer at
all; the survivors then earn a full measurement over many seeds and every family.
Selection is by worst family rather than aggregate, which is the criterion that
separates a robust tape from one that is merely lucky against weak opposition.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics

from arena.agents import agent_hash
from arena.parallel import matches
from arena.seeds import parse_seeds
from experiments.tape_agent import build


def build_agents(tapes, directory):
    """One standalone replayer per tape, named by the tape's own digest."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for tape in tapes:
        path = directory / f"{tape['sha256'][:12]}.py"
        if not path.exists():
            provenance = (f"episode {tape.get('episode')} seat {tape.get('seat')} "
                          f"money {tape.get('money')}")
            build(tape, path, provenance)
        paths[tape['sha256']] = str(path)
    return paths


def screen(tapes, opponents, seeds, *, workers=4, agent_dir, output=None):
    """Play every tape against every opponent on both seats of every seed."""
    paths = build_agents(tapes, agent_dir)
    jobs, meta = [], []
    for tape in tapes:
        for name, opponent in opponents.items():
            for seed in seeds:
                for seat in (0, 1):
                    jobs.append(dict(candidate=paths[tape['sha256']], opponent=opponent,
                                     seed=seed, seat=seat, evidence_profile='score'))
                    meta.append((tape['sha256'], name, seed, seat))
    rows = []
    # `ordered=True` is asked for, not assumed: this pairing is positional, and
    # `arena.parallel.matches` also has a streaming mode that makes no order promise.
    for (sha, name, seed, seat), row in zip(meta, matches(jobs, workers, ordered=True)):
        rows.append({'tape': sha, 'opponent': name, 'seed': seed, 'seat': seat,
                     'score': row['score'], 'money': row['money'],
                     'opponent_money': row['opponent_money'], 'margin': row['margin'],
                     'failures': len(row['failures']),
                     'opponent_failures': len(row['opponent_failures'])})
    labels = {tape['sha256']: {key: tape.get(key) for key in
                               ('episode', 'seat', 'team', 'opponent_team', 'money')}
              for tape in tapes}
    payload = {'opponents': opponents, 'opponent_hashes': {n: agent_hash(p) for n, p in
                                                           opponents.items()},
               'diagnostic_fields': ['money', 'opponent_money', 'margin'],
               'seeds': list(seeds), 'agent_dir': str(agent_dir), 'tapes': labels, 'rows': rows}
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(payload) + '\n', encoding='utf-8')
    return payload


def rank(payload):
    """Rank by worst family first: a tape is only as good as its weakest matchup."""
    per = defaultdict(list)
    for row in payload['rows']:
        per[row['tape']].append(row)
    table = []
    for sha, rows in per.items():
        by_opponent = defaultdict(list)
        for row in rows:
            by_opponent[row['opponent']].append(row['score'])
        worst = min(statistics.mean(values) for values in by_opponent.values())
        table.append({'tape': sha, 'games': len(rows),
                      'score': statistics.mean(row['score'] for row in rows),
                      'worst_family': worst,
                      'margin_diagnostic': statistics.mean(row['margin'] for row in rows),
                      'per_opponent': {name: statistics.mean(values)
                                       for name, values in sorted(by_opponent.items())},
                      **payload['tapes'].get(sha, {})})
    table.sort(key=lambda entry: (-entry['worst_family'], -entry['score'], entry['tape']))
    return table


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', required=True)
    parser.add_argument('--opponents', required=True,
                        help='comma-separated name=path pairs')
    parser.add_argument('--seeds', default='1000:1002')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--limit', type=int, default=0, help='screen the first N tapes in digest order')
    parser.add_argument('--only', default='', help='comma-separated tape id prefixes')
    parser.add_argument('--agent-dir', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    library = json.loads(Path(args.library).read_text())
    tapes = library['tapes']
    if args.only:
        wanted = tuple(args.only.split(','))
        tapes = [t for t in tapes if t['sha256'].startswith(wanted)]
    elif args.limit:
        tapes = sorted(tapes, key=lambda tape: tape['sha256'])[:args.limit]
    opponents = dict(pair.split('=', 1) for pair in args.opponents.split(','))
    payload = screen(tapes, opponents, parse_seeds(args.seeds), workers=args.workers,
                     agent_dir=args.agent_dir, output=args.output)
    for entry in rank(payload)[:25]:
        print(f"{entry['tape'][:12]}  worst={entry['worst_family']:.3f} "
              f"score={entry['score']:.3f} margin (diagnostic)={entry['margin_diagnostic']:9.0f}  {entry.get('team')}")


if __name__ == '__main__':
    main()
