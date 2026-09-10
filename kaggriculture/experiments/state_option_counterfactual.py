"""Cheap state-conditioned macro-Option counterfactual with an automatic kill gate.

This is deliberately an experiment, not a new agent stack.  It reuses the owned planner,
the arena, full economic telemetry, recorded elite worlds and the two-host Ray transport.
Every branch replays the exact same prefix and commits to one coherent macro policy for a
bounded window.  Phase 3 is entered only when the per-state oracle beats the best fixed
Option by a material amount on win rate.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import statistics

from arena.batch import batched_runner, host_contribution, worker_budget
from arena.parallel import matches
from arena.ray_transport import DEFAULT_CPUS_PER_WORKER, require_cluster
from experiments.tape_agent import build as build_tape_agent
from experiments.top_panel import episode_streams


ROOT = Path(__file__).resolve().parents[1]
DAY_STEPS = tuple(range(0, 720, 24)) + (719,)
BASE_PARAMETERS = {'economic_planner': True, 'land_reservation': True}
OPTIONS = {
    'FOLLOW_CURRENT': {},
    # Switching economic_planner off is intentional here: its static target mode makes
    # animal_type effective and the existing planner supplies every downstream build,
    # purchase, placement, feed, care, harvest and sale consequence.
    'COW_CAPACITY': {
        'economic_planner': False, 'animal_type': 'COW', 'animal_target': 26,
    },
    'SHEEP_CAPACITY': {
        'economic_planner': False, 'animal_type': 'SHEEP', 'animal_target': 26,
    },
}
SHOP_PRODUCTS = {
    'BAKERY': ('EGG', 'WHEAT'), 'PIZZA_SHOP': ('MILK', 'TOMATO', 'WHEAT'),
    'BRUNCH_SPOT': ('EGG', 'WHEAT', 'STRAWBERRY'), 'YARN_STORE': ('WOOL',),
    'ICE_CREAM_SHOP': ('STRAWBERRY', 'MILK', 'WHEAT'), 'PET_CAFE': ('CARROT',),
    'SMOOTHIE_SHOP': ('STRAWBERRY', 'MILK'),
    'FARMERS_MARKET': ('WHEAT', 'CARROT', 'TOMATO', 'STRAWBERRY'),
}
PRODUCTS = ('WHEAT', 'CARROT', 'TOMATO', 'STRAWBERRY', 'MELON',
            'EGG', 'MILK', 'WOOL', 'FERTILIZER')

AGENT_TEMPLATE = '''from agent.planner import policy

BASE = {base!r}
OPTION = {option!r}
SWITCH = {switch}
END = {end}


def agent(observation, configuration=None):
    turns = (configuration or {{}}).get('turnsPerDay', 24)
    step = observation.get('step')
    if step is None:
        step = observation['day'] * turns + observation['hour']
    parameters = dict(BASE)
    if SWITCH <= step < END:
        parameters.update(OPTION)
    return policy(observation, configuration, parameters)
'''


def stable_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def build_agent(path, option='FOLLOW_CURRENT', switch=720, commitment_steps=0):
    """Materialise a tiny wrapper inside Ray's included working directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = AGENT_TEMPLATE.format(
        base=BASE_PARAMETERS, option=OPTIONS[option], switch=switch,
        end=min(720, switch + commitment_steps))
    path.write_text(payload, encoding='utf-8')
    return str(path.relative_to(ROOT))


def recorded_elite_worlds():
    """Tracked, reproducible binghua/Otter wins at their original seed and seat."""
    worlds = []
    for folder in ('opponents/recorded', 'opponents/recorded2950'):
        for manifest in sorted((ROOT / folder).glob('*/main.manifest.json')):
            data = json.loads(manifest.read_text(encoding='utf-8'))
            recorded = data.get('recorded', {})
            if recorded.get('team') not in ('binghua', 'Otter Vibe'):
                continue
            opponent_seat = int(recorded['seat'])
            worlds.append({
                'opponent': str(manifest.parent.relative_to(ROOT) / 'main.py'),
                'seed': int(recorded['seed']), 'seat': 1 - opponent_seat,
                'source': 'recorded_elite', 'opponent_family': recorded['team'],
                'episode': int(recorded['episode']),
            })
    return worlds


def candidate_worlds(v006_start=1000, v006_seeds=20):
    worlds = recorded_elite_worlds()
    worlds.extend({
        'opponent': 'versions/v006/main.py', 'seed': seed, 'seat': seat,
        'source': 'v006_teacher', 'opponent_family': 'v006', 'episode': None,
    } for seed in range(v006_start, v006_start + v006_seeds) for seat in (0, 1))
    return worlds


def elite_loss_worlds(results_path, archive, directory, limit=50):
    """Materialise only high-information worlds where v006 lost to the elite panel."""
    payload = json.loads(Path(results_path).read_text(encoding='utf-8'))
    rows = [row for row in payload['rows'] if row['score'] == 0
            and not row.get('failures') and not row.get('opponent_failures')]
    rows.sort(key=lambda row: (row['opponent_team'] not in ('binghua', 'Otter Vibe'),
                               row['opponent_team'], row['episode'],
                               row['candidate_seat']))
    rows = rows[:limit]
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    episodes = {}
    worlds = []
    for row in rows:
        episode = int(row['episode'])
        if episode not in episodes:
            episodes[episode] = episode_streams(archive, episode)
        opponent_seat = int(row['opponent_seat'])
        tape = next(seat for seat in episodes[episode] if seat['seat'] == opponent_seat)
        path = directory / f'ep{episode}s{opponent_seat}.py'
        if not path.exists():
            build_tape_agent(tape, path,
                             f'v006 loss world episode {episode} seat {opponent_seat}')
        worlds.append({
            'opponent': str(path.relative_to(ROOT)), 'seed': int(row['seed']),
            'seat': int(row['candidate_seat']), 'source': 'v006_elite_loss',
            'opponent_family': row['opponent_team'], 'episode': episode,
        })
    return worlds


def gap_checkpoint(row, minimum_day=4, default_day=14):
    """Last day boundary before a persistent, material opponent cash acceleration."""
    ours, theirs = row.get('daily') or [], row.get('opponent_daily') or []
    if len(ours) != len(theirs) or not ours:
        return default_day * 24
    gaps = [b['cash_end'] - a['cash_end'] for a, b in zip(ours, theirs)]
    for day in range(max(2, minimum_day), len(gaps) - 2):
        acceleration = gaps[day + 2] - gaps[day - 1]
        persistent = min(gaps[day:day + 3]) > gaps[day - 1]
        if gaps[day] > 1500 and acceleration > 4000 and persistent:
            return (day - 1) * 24
    return min((len(gaps) - 3) * 24, default_day * 24)


def frame_at(row, step):
    frames = row.get('replay_turns') or []
    exact = [frame for frame in frames if frame['step'] == step]
    if len(exact) != 1:
        raise ValueError(f'expected exactly one snapshot at step {step}, got {len(exact)}')
    return exact[0]


def _tiles(farm):
    return [tile for line in farm['tiles'] for tile in line]


def _counts(farm):
    tiles = _tiles(farm)
    animals = Counter(tile.get('animal') for tile in tiles
                      if isinstance(tile, dict) and tile.get('animal'))
    structures = Counter(tile.get('kind') for tile in tiles
                         if isinstance(tile, dict) and tile.get('kind'))
    crops = Counter(tile.get('crop') for tile in tiles
                    if isinstance(tile, dict) and tile.get('crop'))
    return animals, structures, crops, tiles


def state_features(row, switch):
    """Compact observable checkpoint features; no orders, seed or private opponent data."""
    frame = frame_at(row, switch)
    obs = frame['observations'][row['seat']]
    me, opponent = obs['farms'][row['seat']], obs['farms'][1 - row['seat']]
    animals, structures, crops, tiles = _counts(me)
    other_animals, other_structures, other_crops, _ = _counts(opponent)
    private = obs['private']
    held = Counter(private['shed'])
    for inventory in private['inventories']:
        held.update(inventory)
    shops = tuple(obs['town']['unlocked_shops'])
    demand = Counter()
    for shop in shops:
        demand.update(SHOP_PRODUCTS.get(shop, ()))
    prior_step = max(0, switch - 24)
    prior = frame_at(row, prior_step)['observations'][row['seat']]['market']
    market = obs['market']
    result = {
        'turn': switch, 'day': switch // 24, 'phase': min(2, switch // 240),
        'cash': me['money'], 'land': len(me['unlocked_quadrants']),
        'hands': 1 + len(me['hands']), 'free_tiles': sum(tile is None for tile in tiles),
        'shed_used': sum(private['shed'].values()), 'feed_wheat': held['WHEAT'],
        'cows': animals['COW'], 'sheep': animals['SHEEP'], 'geese': animals['GOOSE'],
        'pastures': structures['PASTURE'], 'coops': structures['COOP'],
        'crops': sum(crops.values()), 'shops': len(shops),
        'milk_demand_shops': demand['MILK'], 'wool_demand_shops': demand['WOOL'],
        'opponent_cash': opponent['money'],
        'opponent_land': len(opponent['unlocked_quadrants']),
        'opponent_cows': other_animals['COW'], 'opponent_sheep': other_animals['SHEEP'],
        'opponent_geese': other_animals['GOOSE'],
        'opponent_pastures': other_structures['PASTURE'],
        'opponent_crops': sum(other_crops.values()),
    }
    for item in PRODUCTS:
        key = item.lower()
        result[f'{key}_price'] = market['prices'][item]
        result[f'{key}_inventory'] = market['inventory'][item]
        result[f'{key}_price_change_1d'] = market['prices'][item] - prior['prices'][item]
        result[f'{key}_inventory_change_1d'] = (
            market['inventory'][item] - prior['inventory'][item])
    return result


def snapshot_digest(row, step):
    # Actions at this turn have not executed yet. This is the counterfactual fork boundary.
    return stable_digest(frame_at(row, step)['observations'])


def select_diverse_states(rows, limit=50):
    """Greedy max-min selection, with losses first and numeric feature normalization."""
    if limit < 1:
        raise ValueError('state limit must be positive')
    eligible = sorted(rows, key=lambda row: (row['score'] != 0, row['opponent_family'],
                                             row['seed'], row['seat']))
    pool = eligible[:max(limit, sum(row['score'] == 0 for row in eligible))]
    if len(pool) <= limit:
        return pool
    keys = sorted(pool[0]['state_features'])
    spans = {}
    for key in keys:
        values = [float(row['state_features'][key]) for row in pool]
        spans[key] = (min(values), max(values))

    def vector(row):
        result = []
        for key in keys:
            low, high = spans[key]
            result.append((float(row['state_features'][key]) - low) / (high - low)
                          if high > low else 0.)
        return result

    vectors = [vector(row) for row in pool]
    chosen = []
    # Seed coverage with one state from each opponent family.
    for family in sorted({row['opponent_family'] for row in pool}):
        chosen.append(next(i for i, row in enumerate(pool)
                           if row['opponent_family'] == family))
    chosen = list(dict.fromkeys(chosen))[:limit]
    while len(chosen) < limit:
        remaining = [i for i in range(len(pool)) if i not in chosen]
        def distance(index):
            return min(sum((a - b) ** 2 for a, b in zip(vectors[index], vectors[other]))
                       for other in chosen)
        chosen.append(max(remaining, key=lambda i: (distance(i), -i)))
    return [pool[i] for i in chosen]


def _sum_counter(days, field, start_day, end_day):
    total = Counter()
    for day in days or []:
        if start_day <= day['day'] < end_day:
            total.update(day.get(field, {}))
    return total


def mechanism(row, switch, end):
    """What actually executed during the commitment, derived from engine telemetry/state."""
    start_day, end_day = switch // 24, math.ceil(end / 24)
    buys = _sum_counter(row.get('daily'), 'buys', start_day, end_day)
    actions = _sum_counter(row.get('daily'), 'unit_actions', start_day, end_day)
    expenditure = _sum_counter(row.get('daily'), 'expenditure', start_day, end_day)
    before = frame_at(row, switch)['observations'][row['seat']]['farms'][row['seat']]
    after = frame_at(row, end)['observations'][row['seat']]['farms'][row['seat']]
    old_animals, old_structures, _, _ = _counts(before)
    new_animals, new_structures, _, _ = _counts(after)
    effective_harvests = sum(day.get('harvests', 0) for day in row.get('daily') or []
                             if start_day <= day['day'] < end_day)
    effective_plantings = sum(day.get('plantings', 0) for day in row.get('daily') or []
                              if start_day <= day['day'] < end_day)
    return {
        'executed_buys': dict(buys), 'attempted_unit_actions': {
            key: actions[key] for key in ('BUILD_PASTURE', 'PLACE', 'FEED', 'CARE', 'HARVEST')
        },
        'effective_harvests': effective_harvests,
        'effective_plantings': effective_plantings,
        'herd_delta': {name: new_animals[name] - old_animals[name]
                       for name in ('COW', 'SHEEP', 'GOOSE')},
        'structure_delta': {name: new_structures[name] - old_structures[name]
                            for name in ('PASTURE', 'COOP')},
        'transition_cost': sum(expenditure.values()),
        'transition_expenditure': dict(expenditure),
    }


def option_summary(states):
    names = tuple(OPTIONS)
    fixed_options = names[1:]
    fixed = {name: statistics.mean(state['results'][name]['score'] for state in states)
             for name in names}
    fixed_margin = {name: statistics.mean(state['results'][name]['margin'] for state in states)
                    for name in names}
    # Win/loss is the tournament objective. Coin margin is diagnostic and must never
    # break a policy-selection tie (the same rule pinned by test_selection_policy.py).
    best_fixed = max(fixed_options, key=lambda name: (fixed[name], -names.index(name)))
    best_overall = max(names, key=lambda name: (fixed[name], -names.index(name)))
    oracle_choices = []
    unique_winners = set()
    tie_sets = Counter()
    for state in states:
        best_score = max(state['results'][name]['score'] for name in names)
        winners = tuple(name for name in names if state['results'][name]['score'] == best_score)
        oracle_choices.append(winners[0])
        tie_sets['|'.join(winners)] += 1
        if len(winners) == 1:
            unique_winners.add(winners[0])
    oracle_wr = statistics.mean(state['results'][name]['score']
                                for state, name in zip(states, oracle_choices))
    strict_beats = sum(max(state['results'][name]['score'] for name in names[1:]) >
                       state['results']['FOLLOW_CURRENT']['score'] for state in states)
    mechanism_real = {}
    for option, animal in (('COW_CAPACITY', 'COW'), ('SHEEP_CAPACITY', 'SHEEP')):
        mechanism_real[option] = sum(
            state['results'][option].get('executed_buys', {}).get(
                f'BUY_ANIMAL:{animal}', 0) > 0
            and state['results'][option].get('herd_delta', {}).get(animal, 0) > 0
            for state in states)
    return {
        'fixed_win_rate': fixed, 'fixed_mean_margin': fixed_margin,
        'best_fixed_option': best_fixed, 'best_fixed_win_rate': fixed[best_fixed],
        'current_win_rate': fixed['FOLLOW_CURRENT'],
        'best_overall_fixed': best_overall,
        'oracle_win_rate': oracle_wr,
        'oracle_gap': oracle_wr - fixed[best_overall],
        'oracle_choices': dict(Counter(oracle_choices)),
        'oracle_tie_sets': dict(tie_sets),
        'states_an_option_strictly_beats_current': strict_beats,
        'different_unique_oracle_options': sorted(unique_winners),
        'mechanism_real_states': mechanism_real,
    }


def oracle_gate_failures(summary, minimum_oracle_gap=.10,
                         minimum_adaptive_states=5):
    """Return stable, inspectable reasons for killing the direction after Phase 2."""
    failures = []
    if summary['oracle_gap'] < minimum_oracle_gap:
        failures.append('oracle_gap')
    if summary['states_an_option_strictly_beats_current'] < minimum_adaptive_states:
        failures.append('adaptive_states')
    if len(summary['different_unique_oracle_options']) < 2:
        failures.append('state_heterogeneity')
    if any(count < 1 for count in summary.get('mechanism_real_states', {}).values()):
        failures.append('mechanism_not_executed')
    return failures


def split_states(states, holdout_fraction=.3):
    """Deterministic grouped split; mirrored seats never cross the boundary."""
    if not 0 < holdout_fraction < 1:
        raise ValueError('holdout fraction must be between zero and one')
    groups = {}
    for state in states:
        # Two substitutions from one recorded episode can use different tape paths and
        # seats. The episode, not the generated opponent filename, is the world boundary.
        key = (('episode', state['episode']) if state.get('episode') is not None else
               ('synthetic', state['opponent'], state['seed']))
        groups.setdefault(key, []).append(state)
    if len(groups) < 2:
        raise ValueError('strict holdout needs at least two distinct worlds')
    ordered = sorted(groups, key=lambda key: stable_digest(list(key)))
    holdout_keys = {key for key in ordered
                    if int(stable_digest(list(key))[:8], 16) / 0xffffffff
                    < holdout_fraction}
    if not holdout_keys or len(holdout_keys) == len(ordered):
        count = max(1, min(len(ordered) - 1,
                           round(len(ordered) * holdout_fraction)))
        holdout_keys = set(ordered[:count])
    train = [state for key in ordered if key not in holdout_keys for state in groups[key]]
    holdout = [state for key in ordered if key in holdout_keys for state in groups[key]]
    return train, holdout


def fit_tiny_value_model(train, ridge=1.):
    """Option-specific ridge values; tiny, deterministic and deliberately not RL."""
    import numpy as np
    feature_names = sorted(train[0]['state_features'])
    matrix = np.array([[float(state['state_features'][key]) for key in feature_names]
                       for state in train], dtype=float)
    mean, scale = matrix.mean(axis=0), matrix.std(axis=0)
    scale[scale == 0] = 1.
    x = np.column_stack([np.ones(len(matrix)), (matrix - mean) / scale])
    weights = {}
    penalty = np.eye(x.shape[1]) * ridge
    penalty[0, 0] = 0
    for option in OPTIONS:
        y = np.array([state['results'][option]['score'] for state in train], dtype=float)
        weights[option] = np.linalg.solve(x.T @ x + penalty, x.T @ y).tolist()
    return {'kind': 'option_ridge_value_v1', 'feature_names': feature_names,
            'mean': mean.tolist(), 'scale': scale.tolist(), 'weights': weights}


def model_choice(model, features):
    values = [1.] + [(float(features[key]) - mean) / scale for key, mean, scale in zip(
        model['feature_names'], model['mean'], model['scale'])]
    scores = {option: sum(a * b for a, b in zip(weights, values))
              for option, weights in model['weights'].items()}
    names = tuple(OPTIONS)
    return max(names, key=lambda option: (scores[option], -names.index(option)))


def evaluate_selector(states, choose):
    choices = [choose(state) for state in states]
    return {'win_rate': statistics.mean(state['results'][option]['score']
                                        for state, option in zip(states, choices)),
            'mean_margin': statistics.mean(state['results'][option]['margin']
                                           for state, option in zip(states, choices)),
            'choices': dict(Counter(choices))}


def _jobs(worlds, candidate, replay_steps):
    return [{**world, 'candidate': candidate, 'backend': 'fast',
             'evidence_profile': 'full', 'replay_inline': True,
             'replay_steps': sorted(set(replay_steps))}
            for world in worlds]


def run_jobs(jobs, runner, workers):
    rows = list(runner(jobs, workers))
    if len(rows) != len(jobs):
        raise OSError(f'runner returned {len(rows)} of {len(jobs)} jobs')
    failed = [row for row in rows if row.get('failures') or row.get('opponent_failures')]
    if failed:
        sample = failed[0]
        raise ValueError(f'{len(failed)} match(es) had callback failures; first: '
                         f'{sample.get("failures") or sample.get("opponent_failures")}')
    def identity(value):
        return (value['candidate'], value['opponent'], value['seed'], value['seat'])
    by_identity = {identity(row): row for row in rows}
    if len(by_identity) != len(rows):
        raise ValueError('runner returned duplicate counterfactual identities')
    try:
        return [by_identity[identity(job)] for job in jobs]
    except KeyError as exc:
        raise OSError(f'runner substituted a counterfactual job: {exc}') from exc


def local_runner(jobs, workers):
    yield from matches(jobs, workers=workers, ordered=False)


def run(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    agents_dir = ROOT / args.agents_dir
    baseline_agent = build_agent(agents_dir / 'follow-current.py')

    # Ray snapshots working_dir at connect time. Materialise every possible day-boundary
    # wrapper and every selected replay opponent before requiring the cluster.
    paths = {(option, switch): build_agent(
        agents_dir / f'{option.lower()}-{switch}.py', option, switch,
        args.commitment_days * 24)
        for switch in range(0, 720, 24) for option in OPTIONS}
    if bool(args.elite_results) != bool(args.elite_archive):
        raise ValueError('--elite-results and --elite-archive must be supplied together')
    if args.elite_results and args.elite_archive:
        worlds = elite_loss_worlds(args.elite_results, args.elite_archive,
                                   agents_dir / 'elite-opponents', args.elite_losses)
        worlds.extend(candidate_worlds(args.v006_start, args.v006_seeds)[
            len(recorded_elite_worlds()):])
    else:
        worlds = candidate_worlds(args.v006_start, args.v006_seeds)

    mapper = None
    if args.local:
        runner, nodes = local_runner, []
    else:
        mapper, nodes = require_cluster(args.ray_address,
                                        cpus_per_worker=args.cpus_per_worker)
        runner = batched_runner(size=None, map_batches=mapper,
                                available_slots=mapper.available_slots,
                                minimum_batch_size=args.cpus_per_worker)

    baseline_rows = run_jobs(_jobs(worlds, baseline_agent, DAY_STEPS), runner, args.workers)
    screened = []
    by_world = {(world['opponent'], world['seed'], world['seat']): world
                for world in worlds}
    for row in baseline_rows:
        world = by_world[(row['opponent'], row['seed'], row['seat'])]
        switch = gap_checkpoint(row)
        screened.append({**world, 'score': row['score'], 'money': row['money'],
                         'margin': row['margin'], 'switch': switch,
                         'state_features': state_features(row, switch),
                         'prefix_digest': snapshot_digest(row, switch)})
    selected = select_diverse_states(screened, args.states)

    jobs = []
    identities = []
    for index, state in enumerate(selected):
        end = min(719, state['switch'] + args.commitment_days * 24)
        for option in OPTIONS:
            jobs.extend(_jobs([state], paths[(option, state['switch'])],
                              (state['switch'], end)))
            identities.append((index, option, end))
    option_rows = run_jobs(jobs, runner, args.workers)

    records = [{**state, 'results': {}} for state in selected]
    for row, (index, option, end) in zip(option_rows, identities):
        state = records[index]
        digest = snapshot_digest(row, state['switch'])
        if digest != state['prefix_digest']:
            raise ValueError(f'counterfactual prefix mismatch for state {index}, {option}')
        executed = mechanism(row, state['switch'], end)
        state['results'][option] = {
            'score': row['score'], 'outcome': ('win' if row['score'] == 1 else
                'loss' if row['score'] == 0 else 'tie'),
            'terminal_money': row['money'], 'opponent_money': row['opponent_money'],
            'margin': row['margin'], **executed,
        }
    for state in records:
        current_cost = state['results']['FOLLOW_CURRENT']['transition_cost']
        for result in state['results'].values():
            result['incremental_transition_cost'] = result['transition_cost'] - current_cost
        best_score = max(result['score'] for result in state['results'].values())
        state['chosen_option'] = next(option for option in OPTIONS
                                      if state['results'][option]['score'] == best_score)

    summary = option_summary(records)
    report = {
        'schema_version': 1, 'hypothesis': 'state_conditioned_hierarchical_option_selector',
        'base_parameters': BASE_PARAMETERS, 'options': OPTIONS,
        'commitment_days': args.commitment_days, 'candidate_worlds': len(worlds),
        'selected_states': len(records),
        'selection': {'losses': sum(state['score'] == 0 for state in records),
                      'sources': dict(Counter(state['source'] for state in records)),
                      'opponents': dict(Counter(state['opponent_family'] for state in records))},
        'phase_2': summary, 'nodes': nodes,
        'hosts': host_contribution([*baseline_rows, *option_rows]),
        'option_hosts': host_contribution(option_rows),
        'inputs': ({
            'elite_results': {'path': str(Path(args.elite_results).resolve()),
                              'sha256': file_digest(args.elite_results)},
            'elite_archive': {'path': str(Path(args.elite_archive).resolve()),
                              'sha256': file_digest(args.elite_archive)},
        } if args.elite_results else {'tracked_recorded_opponents': True}),
        'limitations': [
            'Recorded elite opponents are open-loop tapes; their score is a screening '
            'counterfactual, not live-agent win rate.',
            'The reactive v006 control replays the full prefix because v006 is stateful.',
        ],
        'states': records,
    }
    gate_failures = oracle_gate_failures(
        summary, args.minimum_oracle_gap, args.minimum_adaptive_states)
    if gate_failures:
        report['decision'] = {
            'stage': 'rejected_phase_2', 'integrate': False,
            'reason': 'oracle gap, state heterogeneity, or strict improvements below gate',
            'failures': gate_failures,
        }
    else:
        train, holdout = split_states(records, args.holdout_fraction)
        model = fit_tiny_value_model(train)
        # The holdout measures the fixed Option selected on training data. Letting the
        # holdout select its own comparator would leak labels into the Phase 3 baseline.
        fixed = option_summary(train)['best_fixed_option']
        current_eval = evaluate_selector(holdout, lambda _: 'FOLLOW_CURRENT')
        fixed_eval = evaluate_selector(holdout, lambda _: fixed)
        learned_eval = evaluate_selector(holdout,
                                         lambda state: model_choice(model, state['state_features']))
        oracle_eval = evaluate_selector(holdout, lambda state: next(
            option for option in OPTIONS if state['results'][option]['score'] ==
            max(result['score'] for result in state['results'].values())))
        reference_wr = max(current_eval['win_rate'], fixed_eval['win_rate'])
        possible = oracle_eval['win_rate'] - reference_wr
        captured = ((learned_eval['win_rate'] - reference_wr) / possible
                    if possible > 0 else 0.)
        report['phase_3'] = {
            'train_worlds': len(train), 'holdout_worlds': len(holdout), 'model': model,
            'current_policy': current_eval, 'best_fixed_option': fixed_eval,
            'learned_selector': learned_eval, 'oracle_selector': oracle_eval,
            'fraction_oracle_gap_closed': captured,
        }
        passed = (captured >= args.minimum_gap_fraction
                  and learned_eval['win_rate'] > current_eval['win_rate']
                  and learned_eval['win_rate'] > fixed_eval['win_rate'])
        report['decision'] = {
            'stage': 'validated_phase_3' if passed else 'rejected_phase_3',
            'integrate': passed,
            'reason': ('holdout selector clears the scientific gate' if passed else
                       'learned selector did not reproduce enough oracle gain on holdout'),
        }
    report['report_sha256'] = stable_digest(report)
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if mapper:
        mapper.ray.shutdown()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--output', default='experiments/results/state-option-counterfactual')
    parser.add_argument('--agents-dir', default='experiments/generated/state-option-counterfactual')
    parser.add_argument('--states', type=int, default=50)
    parser.add_argument('--v006-start', type=int, default=1000)
    parser.add_argument('--v006-seeds', type=int, default=20)
    parser.add_argument('--elite-results',
                        help='top-panel v006 result JSON; its actual loss rows seed Phase 1')
    parser.add_argument('--elite-archive',
                        help='episode archive named by --elite-results, used to build loss tapes')
    parser.add_argument('--elite-losses', type=int, default=50)
    parser.add_argument('--commitment-days', type=int, default=8)
    parser.add_argument('--minimum-oracle-gap', type=float, default=.10)
    parser.add_argument('--minimum-adaptive-states', type=int, default=5)
    parser.add_argument('--holdout-fraction', type=float, default=.30)
    parser.add_argument('--minimum-gap-fraction', type=float, default=.40)
    parser.add_argument('--workers', type=int, default=worker_budget())
    parser.add_argument('--cpus-per-worker', type=int, default=DEFAULT_CPUS_PER_WORKER)
    parser.add_argument('--ray-address', default='auto')
    parser.add_argument('--local', action='store_true',
                        help='development fallback; scientific run requires the default two-host Ray gate')
    args = parser.parse_args()
    if args.states < 1 or args.commitment_days < 1:
        parser.error('states and commitment-days must be positive')
    report = run(args)
    print(json.dumps({'decision': report['decision'], 'phase_2': report['phase_2'],
                      'phase_3': report.get('phase_3'), 'hosts': report['hosts']}, indent=2))


if __name__ == '__main__':
    main()
