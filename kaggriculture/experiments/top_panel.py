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
from experiments.corpus_ingest import file_digest
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


def horizon_digest(actions, turns):
    payload = json.dumps(actions[:turns], sort_keys=True, separators=(',', ':'))
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
                         'opening_sha256': opening_digest(tape['actions']),
                         'lineage_h24': horizon_digest(tape['actions'], 24),
                         'lineage_h48': horizon_digest(tape['actions'], 48),
                         'lineage_h136': horizon_digest(tape['actions'], 136),
                         'stream_full_hash': horizon_digest(tape['actions'], 719)})
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
        proofs[episode][f'{backend}_environment'] = row.get('environment')
        proofs[episode][f'{backend}_failures'] = [row['failures'], row['opponent_failures']]
    for episode, proof in proofs.items():
        proof['exact'] = (proof['fast'] == proof['expected'] == proof['official']
                          and not any(proof['fast_failures'] + proof['official_failures']))
        proof['verified_at'] = datetime.now(timezone.utc).isoformat()
    return dict(proofs)


def _winner(money):
    return 0 if money[0] > money[1] else 1 if money[1] > money[0] else None


def reproduction_record(row, proof, artifact_sha256, dataset_revision):
    seat = row['seat']
    expected_pair, actual_pair = proof['expected'], proof['official']
    expected = {'winner': _winner(expected_pair), 'our_money': expected_pair[seat],
                'opponent_money': expected_pair[1 - seat]}
    actual = {'winner': _winner(actual_pair), 'our_money': actual_pair[seat],
              'opponent_money': actual_pair[1 - seat]}
    return {'dataset_revision': dataset_revision, 'episode_id': str(row['episode']),
            'seat': seat, 'engine_version': row['engine'],
            'engine_fingerprint': proof['official_environment'],
            'agent_sha256': artifact_sha256, 'stream_sha256': row['sha256'],
            'expected': expected, 'actual': actual,
            'fast_actual': {'winner': _winner(proof['fast']),
                            'our_money': proof['fast'][seat],
                            'opponent_money': proof['fast'][1 - seat]},
            'verified_at': proof['verified_at']}


def emit(rows, tapes, proofs, directory, *, source, dataset_revision, snapshot, min_rating):
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
        artifact_sha256 = hashlib.sha256(source_bytes).hexdigest()
        reproduction = reproduction_record(row, proof, artifact_sha256, dataset_revision)
        manifest = {
            'id': pin, 'family': f"recorded::{row['team']}", 'kind': 'recorded_episode',
            'path': str((folder / 'main.py').relative_to(Path.cwd())) if folder.is_absolute()
                    else str(folder / 'main.py'),
            'sha256': artifact_sha256,
            'tape_sha256': row['sha256'], 'opening_sha256': row['opening_sha256'],
            'lineage_h24': row['lineage_h24'], 'lineage_h48': row['lineage_h48'],
            'lineage_h136': row['lineage_h136'],
            'stream_full_hash': row['stream_full_hash'],
            'engine': {'kaggle_environments': row['engine']},
            'recorded': {'episode': row['episode'], 'seed': row['seed'], 'seat': row['seat'],
                         'team': row['team'], 'opponent_team': row['opponent_team'],
                         'money': row['money'], 'opponent_money': row['opponent_money']},
            'reconstruction': {'episode': str(row['episode']), 'source_dataset': source,
                               'reproduction': reproduction},
            'observed_rating': {'rating': row['rating'], 'rank': row['rank'], 'of': row['of'],
                                'kind': 'episode_reconstruction',
                                'observed_at': snapshot['observed_at'],
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


def panel_observations(admitted, snapshot, source, dataset_revision):
    return {'schema_version': SCHEMA,
            'source': {'dataset': source, 'dataset_revision': dataset_revision,
                       'leaderboard': snapshot['leaderboard']},
            'opponents': {entry['id']: {
                'kind': 'episode_reconstruction',
                'observed_at': entry['observed_rating']['observed_at'],
                'source': entry['observed_rating']['source'],
                'rank': entry['observed_rating']['rank'],
                'of': entry['observed_rating']['of'],
                'rating': entry['observed_rating']['rating'],
                'episode': str(entry['recorded']['episode']),
                'seat': entry['recorded']['seat'],
                'lineage_h24': entry['lineage_h24'],
                'lineage_h48': entry['lineage_h48'],
                'lineage_h136': entry['lineage_h136'],
                'stream_full_hash': entry['stream_full_hash']}
                for entry in admitted}}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--library', required=True, help='tape library from episode_tapes')
    parser.add_argument('--archive', required=True, help='the daily dump the library came from')
    parser.add_argument('--dataset-revision',
                        help='pinned source revision; defaults to the archive SHA-256')
    parser.add_argument('--leaderboard', required=True, help='public leaderboard snapshot CSV')
    parser.add_argument('--observed-at', required=True, help='date of that snapshot')
    parser.add_argument('--min-rating', type=float, default=2800.)
    parser.add_argument('--per-team', type=int, default=4)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--agents', default='opponents/recorded')
    parser.add_argument('--output', required=True)
    parser.add_argument('--observations-output',
                        help='derived panel observations for the emitted opponents')
    args = parser.parse_args()

    library = json.loads(Path(args.library).read_text())
    leaderboard = load_leaderboard(args.leaderboard)
    rows, refused = admissible(library['tapes'], leaderboard, args.min_rating)
    chosen = select(rows, args.per_team)
    proofs = reproduce(args.archive, sorted({row['episode'] for row in chosen}),
                       Path(args.agents) / '.proof', workers=args.workers)
    snapshot = {'leaderboard': Path(args.leaderboard).name, 'observed_at': args.observed_at}
    dataset_revision = args.dataset_revision or file_digest(args.archive)
    admitted = emit(chosen, library['tapes'], proofs, args.agents,
                    source=library['source'], dataset_revision=dataset_revision,
                    snapshot=snapshot, min_rating=args.min_rating)
    observations = panel_observations(admitted, snapshot, library['source'], dataset_revision)
    if args.observations_output:
        Path(args.observations_output).write_text(
            json.dumps(observations, indent=2, sort_keys=True) + '\n')
    report = {'schema_version': SCHEMA, 'created_at': datetime.now(timezone.utc).isoformat(),
              'library': args.library, 'archive': args.archive, 'snapshot': snapshot,
              'dataset_revision': dataset_revision,
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
