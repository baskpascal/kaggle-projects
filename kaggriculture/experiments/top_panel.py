"""Build an opponent panel out of top-rated recorded ladder games, not published notebooks.

`docs/TOP10_STANDING.md` has one finding: the two bands that decide a paid finish were
empty, because the strongest artifact anybody publishes as a notebook sits far below the
agents that actually hold the top of the ladder. The notebooks are not the top; they are
what the top is willing to show.

The host's own daily dump is the way past that. It publishes the day's highest-rated
episodes, each one a complete 720-state recording of two live submissions, and the forum
rulings quoted in `docs/LADDER_META.md` permit building on them. This module turns that
dump into opponents under three admission rules, each of which exists to refuse something:

* **Both teams rated at or above the bar** on a dated leaderboard snapshot, not just the
  one we would replay. Kaggle matches agents of similar rating, so a pairing where both
  sides clear the bar is evidence about the game that was played, while one strong team
  beating an unrated one is evidence about nothing.
* **The replayed seat won that game.** A losing stream from a strong team is a recording
  of the day that team lost.
* **The episode reproduces exactly.** Both recorded streams are replayed at the recorded
  seed on both backends and must return the published final money to the coin. A tape that
  does not reproduce is an assertion about an episode, and `eval/panel.py` refuses those
  by design.

What this panel is not is a set of agents. A tape is open loop: it cannot re-plan when we
take the market it was going to sell into. That objection is weaker here than it looks,
because the public top of this field is itself made of fixed tapes with thin reactive
layers, but it is not zero, and the report keeps the cohort separate from the pinned
artifacts for exactly that reason.
"""
import argparse
import csv
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from arena.parallel import matches
from experiments.episode_tapes import read_episode
from experiments.tape_agent import build as build_tape_agent

SCHEMA = 1
OPENING = 144


def load_leaderboard(path):
    """`team -> rank, rating` from a downloaded public leaderboard snapshot."""
    with open(path, encoding='utf-8-sig') as handle:
        rows = list(csv.DictReader(handle))
    table = {row['TeamName']: {'rank': int(row['Rank']), 'rating': float(row['Score'])}
             for row in rows}
    for entry in table.values():
        entry['of'] = len(rows)
    return table


def opening_digest(actions):
    """Identity of a plan's opening, so one team cannot fill the panel with one plan."""
    payload = json.dumps(actions[:OPENING], sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def admissible(tapes, leaderboard, min_rating):
    """Winning streams from pairings where both teams cleared the bar."""
    rows, refused = [], defaultdict(int)
    for tape in tapes:
        mine = leaderboard.get(tape['team'])
        theirs = leaderboard.get(tape['opponent_team'])
        if mine is None or theirs is None:
            refused['team not on the leaderboard snapshot'] += 1
        elif mine['rating'] < min_rating or theirs['rating'] < min_rating:
            refused['a team in the pairing is below the bar'] += 1
        elif not (tape['money'] or 0) > (tape['opponent_money'] or 0):
            refused['the replayed seat did not win that game'] += 1
        else:
            rows.append({**{k: v for k, v in tape.items() if k != 'actions'},
                         'rating': mine['rating'], 'rank': mine['rank'], 'of': mine['of'],
                         'opponent_rating': theirs['rating'], 'opponent_rank': theirs['rank'],
                         'margin': tape['money'] - tape['opponent_money'],
                         'opening_sha256': opening_digest(tape['actions'])})
            continue
    return rows, dict(refused)


def select(rows, per_team):
    """The widest-margin games per team, at most one per distinct opening."""
    chosen, seen = [], defaultdict(set)
    for row in sorted(rows, key=lambda row: -row['margin']):
        team = row['team']
        if len(seen[team]) >= per_team or row['opening_sha256'] in seen[team]:
            continue
        seen[team].add(row['opening_sha256'])
        chosen.append(row)
    return sorted(chosen, key=lambda row: (row['rank'], -row['margin']))


def episode_streams(archive, episode):
    """Both seats of one episode, straight from the dump the tape came from."""
    import zipfile
    with zipfile.ZipFile(archive) as source:
        members = [name for name in source.namelist() if name.endswith(f'{episode}.json')]
    if len(members) != 1:
        raise ValueError(f'Episode {episode} is not uniquely present in {archive}')
    result = read_episode((str(archive), members[0]))
    if 'error' in result:
        raise ValueError(f'Episode {episode}: {result["error"]}')
    return result['seats']


def reproduce(archive, episodes, directory, *, workers=8):
    """Replay each episode's own streams at its own seed; the published money is the answer."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    jobs, meta = [], []
    for episode in episodes:
        seats = episode_streams(archive, episode)
        paths = []
        for seat in seats:
            path = directory / f'{episode}-seat{seat["seat"]}.py'
            if not path.exists():
                build_tape_agent(seat, path, f'episode {episode} seat {seat["seat"]}')
            paths.append(str(path))
        for backend in ('fast', 'official'):
            jobs.append(dict(candidate=paths[0], opponent=paths[1], seed=seats[0]['seed'],
                             seat=0, backend=backend, evidence_profile='score'))
            meta.append((episode, backend, [seats[0]['money'], seats[1]['money']]))
    proofs = defaultdict(dict)
    for (episode, backend, expected), row in zip(meta, matches(jobs, workers, ordered=True)):
        played = [row['money'], row['opponent_money']]
        proofs[episode].update(expected=expected, episode=episode, seed=row['seed'])
        proofs[episode][backend] = played
        proofs[episode][f'{backend}_failures'] = [row['failures'], row['opponent_failures']]
    for episode, proof in proofs.items():
        proof['exact'] = (proof['fast'] == proof['expected'] == proof['official']
                          and not any(proof['fast_failures'] + proof['official_failures']))
        proof['verified_at'] = datetime.now(timezone.utc).isoformat()
    return dict(proofs)


def emit(rows, tapes, proofs, directory, *, source, snapshot, min_rating):
    """One replayer per admitted tape, with the provenance that admits it beside it."""
    directory = Path(directory)
    by_digest = {tape['sha256']: tape for tape in tapes}
    admitted = []
    for row in rows:
        proof = proofs.get(row['episode'], {})
        if not proof.get('exact'):
            continue
        pin = f"ep{row['episode']}s{row['seat']}"
        folder = directory / pin
        folder.mkdir(parents=True, exist_ok=True)
        provenance = (f"episode {row['episode']} seat {row['seat']}, team {row['team']}, "
                      f"money {row['money']} to {row['opponent_money']}")
        build_tape_agent(by_digest[row['sha256']], folder / 'main.py', provenance)
        source_bytes = (folder / 'main.py').read_bytes()
        manifest = {
            'id': pin, 'family': f"recorded::{row['team']}", 'kind': 'recorded_episode',
            'path': str((folder / 'main.py').relative_to(Path.cwd())) if folder.is_absolute()
                    else str(folder / 'main.py'),
            'sha256': hashlib.sha256(source_bytes).hexdigest(),
            'tape_sha256': row['sha256'], 'opening_sha256': row['opening_sha256'],
            'engine': {'kaggle_environments': row['engine']},
            'recorded': {'episode': row['episode'], 'seed': row['seed'], 'seat': row['seat'],
                         'team': row['team'], 'opponent_team': row['opponent_team'],
                         'money': row['money'], 'opponent_money': row['opponent_money']},
            'reconstruction': {'episode': str(row['episode']), 'source_dataset': source,
                               'reproduction': {'episode': str(row['episode']),
                                                'digest': row['sha256'],
                                                'verified_at': proof['verified_at'],
                                                'expected': proof['expected'],
                                                'fast': proof['fast'],
                                                'official': proof['official']}},
            'observed_rating': {'rating': row['rating'], 'rank': row['rank'], 'of': row['of'],
                                'kind': 'episode_team_bound', 'observed_at': snapshot['observed_at'],
                                'source': snapshot['leaderboard'],
                                'note': ('the team rating on this snapshot bounds the recorded '
                                         'submission from above; admission also required the '
                                         f'opposing team at or above {min_rating}, which is a '
                                         'statement about the pairing that was actually played')},
            'opponent_observed_rating': {'rating': row['opponent_rating'],
                                         'rank': row['opponent_rank']},
            'usage': 'benchmark opponent only; open-loop replay, not the live agent',
        }
        (folder / 'main.manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        admitted.append(manifest)
    return admitted


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--library', required=True, help='tape library from episode_tapes')
    parser.add_argument('--archive', required=True, help='the daily dump the library came from')
    parser.add_argument('--leaderboard', required=True, help='public leaderboard snapshot CSV')
    parser.add_argument('--observed-at', required=True, help='date of that snapshot')
    parser.add_argument('--min-rating', type=float, default=2800.)
    parser.add_argument('--per-team', type=int, default=4)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--agents', default='opponents/recorded')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    library = json.loads(Path(args.library).read_text())
    leaderboard = load_leaderboard(args.leaderboard)
    rows, refused = admissible(library['tapes'], leaderboard, args.min_rating)
    chosen = select(rows, args.per_team)
    proofs = reproduce(args.archive, sorted({row['episode'] for row in chosen}),
                       Path(args.agents) / '.proof', workers=args.workers)
    snapshot = {'leaderboard': Path(args.leaderboard).name, 'observed_at': args.observed_at}
    admitted = emit(chosen, library['tapes'], proofs, args.agents,
                    source=library['source'], snapshot=snapshot, min_rating=args.min_rating)
    report = {'schema_version': SCHEMA, 'created_at': datetime.now(timezone.utc).isoformat(),
              'library': args.library, 'archive': args.archive, 'snapshot': snapshot,
              'min_rating': args.min_rating, 'per_team': args.per_team,
              'tapes_considered': len(library['tapes']), 'admissible': len(rows),
              'refused': refused, 'selected': len(chosen),
              'reproduced_exactly': sum(1 for proof in proofs.values() if proof['exact']),
              'admitted': [{'id': entry['id'], 'team': entry['recorded']['team'],
                            'rank': entry['observed_rating']['rank'],
                            'rating': entry['observed_rating']['rating'],
                            'episode': entry['recorded']['episode'],
                            'money': entry['recorded']['money'],
                            'opponent_money': entry['recorded']['opponent_money'],
                            'path': entry['path']} for entry in admitted]}
    Path(args.output).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'admitted'}, indent=2))
    print(f'{len(admitted)} recorded opponents written under {args.agents}')


if __name__ == '__main__':
    main()
