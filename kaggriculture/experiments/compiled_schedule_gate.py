"""Stage-1 executed-state gate for the opt-in compiled phase schedule.

This is deliberately not a competitive evaluator. It compares a single resource-policy
ablation with the committed aggregate schedule and will only say whether the structural
mechanism fired in every diagnostic world.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from arena.parallel import matches
from arena.batch import batched_runner, host_contribution, worker_budget
from arena.ray_transport import DEFAULT_CPUS_PER_WORKER
from agent.params import DEFAULTS
from agent.planner import phase_target, scheduled_phase_value
from experiments.economic_fingerprint import snapshot
from experiments.planner_milestones import parameterized_agent


BASE = {
    'economic_planner': True,
    'land_reservation': True,
    'max_quadrants': 3,
    'max_hands': 11,
    'plant_until_hour': 22,
    'animal_cap': 16,
}
ELITE_REFERENCE = {
    'quadrants': 3, 'crops_t288': '53-61', 'pasture': '11-17',
    'hands_daily_peak': '10-11',
}
CHECKPOINTS = (96, 192, 288, 480)
CREW_PHASES = ((0, 96), (96, 192), (192, 288))


def tile_at(observation, seat, position):
    x, y = position
    return observation['farms'][seat]['tiles'][y][x]


def structural_execution(replay, seat):
    """Verify direct structural commands against the following engine state."""
    turns = sorted(replay['turns'], key=lambda turn: turn['step'])
    counts = {name: {'requested': 0, 'executed': 0, 'failed': 0}
              for name in ('PLANT', 'BUILD_PASTURE')}
    failures = []
    for before, after in zip(turns, turns[1:]):
        if after['step'] != before['step'] + 1:
            continue
        actions = after.get('actions') or []
        action = actions[seat] if seat < len(actions) else {}
        farm = before['observations'][seat]['farms'][seat]
        positions = [farm['farmer'], *(farm.get('hands') or [])]
        commands = [action.get('farmer'), *(action.get('hands') or [])]
        claimed_transitions = set()
        for actor, (position, command) in enumerate(zip(positions, commands)):
            if not command or command[0] not in counts:
                continue
            operation = command[0]
            counts[operation]['requested'] += 1
            observed = tile_at(after['observations'][seat], seat, position)
            executed = ((operation == 'BUILD_PASTURE'
                         and isinstance(observed, dict)
                         and observed.get('kind') == 'PASTURE')
                        or (operation == 'PLANT'
                            and isinstance(observed, dict)
                            and observed.get('kind') == 'PLANT'
                            and observed.get('crop') == command[1]))
            transition = (operation, tuple(position))
            if executed and transition not in claimed_transitions:
                claimed_transitions.add(transition)
                counts[operation]['executed'] += 1
            else:
                counts[operation]['failed'] += 1
                failures.append({'step': before['step'], 'actor': actor,
                                 'position': position, 'command': command,
                                 'before_tile': tile_at(before['observations'][seat],
                                                        seat, position),
                                 'after_tile': observed})
    return {'operations': counts, 'failures': failures,
            'failure_count': len(failures)}


def market_execution(replay, seat):
    """Verify scheduled capacity orders from the next observed engine state."""
    turns = sorted(replay['turns'], key=lambda turn: turn['step'])
    counts = {name: {'requested': 0, 'executed': 0, 'failed': 0}
              for name in ('HIRE', 'BUY_LAND')}
    failures = []
    execution_steps = {name: [] for name in counts}
    for before, after in zip(turns, turns[1:]):
        if after['step'] != before['step'] + 1:
            continue
        actions = after.get('actions') or []
        action = actions[seat] if seat < len(actions) else {}
        orders = action.get('market') or []
        before_farm = before['observations'][seat]['farms'][seat]
        after_farm = after['observations'][seat]['farms'][seat]
        requested_hires = sum(bool(order) and order[0] == 'HIRE' for order in orders)
        requested_land = sum(bool(order) and order[0] == 'BUY_LAND' for order in orders)
        executed_hires = min(requested_hires, max(
            0, len(after_farm.get('hands') or []) - len(before_farm.get('hands') or [])))
        executed_land = min(requested_land, max(
            0, len(after_farm['unlocked_quadrants'])
            - len(before_farm['unlocked_quadrants'])))
        for operation, requested, executed in (
                ('HIRE', requested_hires, executed_hires),
                ('BUY_LAND', requested_land, executed_land)):
            counts[operation]['requested'] += requested
            counts[operation]['executed'] += executed
            counts[operation]['failed'] += requested - executed
            if executed:
                execution_steps[operation].extend([after['step']] * executed)
            if requested != executed:
                failures.append({'step': before['step'], 'operation': operation,
                                 'requested': requested, 'executed': executed})
    return {'operations': counts, 'execution_steps': execution_steps,
            'failures': failures,
            'failure_count': len(failures)}


def blocked_work(replay, seat, parameters):
    """Describe why due structural capacity did not turn into a direct action.

    This is causal triage, not a profitability estimate.  The separate opportunity-cost
    field prices only the crop shortfall at the conservative WHEAT base value already
    used by Stage 0.
    """
    turns = sorted(replay['turns'], key=lambda turn: turn['step'])
    reasons = {'no_free_tile': 0, 'crew_below_ten': 0, 'no_wheat_seed': 0,
               'travel_or_competing_work': 0}
    due_turns = 0
    for index, turn in enumerate(turns[:-1]):
        step = turn['step']
        if step >= 288:
            continue
        observation = turn['observations'][seat]
        farm = observation['farms'][seat]
        tiles = [tile for row in farm['tiles'] for tile in row if tile != 'LOCKED']
        crops = sum(isinstance(tile, dict) and tile.get('kind') == 'PLANT'
                    for tile in tiles)
        pastures = sum(isinstance(tile, dict) and tile.get('kind') == 'PASTURE'
                       for tile in tiles)
        crop_due = phase_target(step, parameters['schedule_crop_deadline'],
                                parameters['schedule_crop_target'], 24)
        pasture_due = scheduled_phase_value(step, parameters['schedule_pasture_phases'])
        if crops >= crop_due and pastures >= pasture_due:
            continue
        due_turns += 1
        after_action = turns[index + 1].get('actions') or []
        own = after_action[seat] if seat < len(after_action) else {}
        commands = [own.get('farmer'), *(own.get('hands') or [])]
        if any(command and command[0] in ('PLANT', 'BUILD_PASTURE')
               for command in commands):
            continue
        if not any(tile is None or (isinstance(tile, dict)
                                    and tile.get('kind') == 'WEED') for tile in tiles):
            reason = 'no_free_tile'
        elif len(farm.get('hands') or []) < 10:
            reason = 'crew_below_ten'
        elif crops < crop_due and observation['private']['seeds'].get('WHEAT', 0) <= 0:
            reason = 'no_wheat_seed'
        else:
            reason = 'travel_or_competing_work'
        reasons[reason] += 1
    return {'due_turns': due_turns, 'no_direct_structural_action': reasons}


def summarize_replay(replay, seat, parameters=None):
    """Read only executed observations; requested actions cannot satisfy a gate."""
    parameters = {**DEFAULTS, **(parameters or {})}
    by_step = {turn['step']: turn for turn in replay['turns']}
    phases = {str(step): snapshot(by_step[step]['observations'][seat], seat)
              for step in CHECKPOINTS}
    hand_peaks = {}
    for start, end in CREW_PHASES:
        values = [len(turn['observations'][seat]['farms'][seat].get('hands') or [])
                  for step, turn in by_step.items() if start <= step < end]
        hand_peaks[f'{start}:{end}'] = max(values, default=0)
    daily_hand_peaks = {}
    daily_crew = {}
    for day in range(12):
        rows = [(step, len(turn['observations'][seat]['farms'][seat].get('hands') or []))
                for step, turn in by_step.items()
                if day * 24 <= step < (day + 1) * 24]
        values = [value for _, value in rows]
        daily_hand_peaks[str(day)] = max(values, default=0)
        daily_crew[str(day)] = {
            'peak': max(values, default=0),
            'worker_turns': sum(values),
            'snapshots_at_ten': sum(value >= 10 for value in values),
            'first_reach_ten': next((step for step, value in rows if value >= 10), None),
        }
    requested = {'PLANT': 0, 'BUILD_PASTURE': 0}
    for step, turn in by_step.items():
        if step >= 288 or not turn.get('actions'):
            continue
        action = turn['actions'][seat] or {}
        for unit_action in [action.get('farmer')] + list(action.get('hands') or []):
            if unit_action and unit_action[0] in requested:
                requested[unit_action[0]] += 1
    gates = {
        'three_quadrants_t480': phases['480']['quadrants'] >= 3,
        'forty_crops_t288': phases['288']['crops_total'] >= 40,
        'pasture_t288': phases['288']['structures']['PASTURE'] >= 12,
        'crew_each_phase': all(value >= 10 for value in hand_peaks.values()),
        'crew_each_day_before_t288': all(value >= 10
                                          for value in daily_hand_peaks.values()),
        'zero_measured_structural_execution_failures': False,
    }
    execution = structural_execution(replay, seat)
    capacity_execution = market_execution(replay, seat)
    gates['zero_measured_structural_execution_failures'] = (
        execution['failure_count'] == 0 and capacity_execution['failure_count'] == 0)
    expected_worker_turns = sum(
        23 * scheduled_phase_value(day * 24, parameters['schedule_hands'])
        for day in range(12))
    actual_worker_turns = sum(day['worker_turns'] for day in daily_crew.values())
    gates['crew_sustained_before_t288'] = actual_worker_turns >= expected_worker_turns
    crop_shortfall = max(0, 40 - phases['288']['crops_total'])
    opportunity_cost = {
        'crop_shortfall_t288': crop_shortfall,
        'conservative_remaining_gross_ceiling': crop_shortfall * 18 * 25,
        'basis': 'shortfall * 18 remaining days * WHEAT base value 25',
    }
    return {'phases': phases, 'hand_peaks': hand_peaks,
            'daily_hand_peaks': daily_hand_peaks,
            'daily_crew': daily_crew,
            'requested_unit_actions_before_t288': requested, 'gates': gates,
            'structural_execution': execution,
            'capacity_execution': capacity_execution,
            'blocked_work': blocked_work(replay, seat, parameters),
            'opportunity_cost': opportunity_cost,
            'crew_capacity': {
                'actual_worker_turns': actual_worker_turns,
                'expected_worker_turns': expected_worker_turns,
            },
            'structural_pass': all(gates.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opponent', default='versions/v006/main.py')
    parser.add_argument('--seeds', default='1000:1010')
    parser.add_argument('--seats', default='0,1',
                        help='comma-separated seats; diagnostics may use seat 0 only')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--distributed', action='store_true',
                        help='require the configured two-host Ray cluster')
    parser.add_argument('--ray-address', default='auto')
    parser.add_argument('--cpus-per-worker', type=int, default=DEFAULT_CPUS_PER_WORKER)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    start, _, end = args.seeds.partition(':')
    seeds = list(range(int(start), int(end))) if end else [int(start)]
    seats = [int(value) for value in args.seats.split(',')]
    if not seats or any(seat not in (0, 1) for seat in seats):
        parser.error('--seats must contain only 0 and/or 1')
    output = Path(args.output)
    replay_dir = output.parent / (output.stem + '-replays')
    replay_dir.mkdir(parents=True, exist_ok=True)
    # Relative artifact paths resolve inside Ray's packaged working directory on every
    # host. An absolute head path can instead name a stale checkout on a worker.
    agents = (Path('experiments/generated/compiled-schedule-stage1')
              if args.distributed else Path(tempfile.mkdtemp(
                  prefix='compiled-schedule-agents-')))
    agents.mkdir(parents=True, exist_ok=True)
    (agents / 'compiled-v0').mkdir(exist_ok=True)
    (agents / 'hire-ablation').mkdir(exist_ok=True)
    (agents / 'market-priority').mkdir(exist_ok=True)
    (agents / 'persistent').mkdir(exist_ok=True)
    compiled_v0 = {**BASE, 'compiled_schedule': True}
    hire_ablation = {
        **compiled_v0,
        'schedule_hires_ignore_land_reserve': True,
    }
    market_priority = {
        **hire_ablation,
        'schedule_animal_deferral_horizon': 288,
    }
    persistent = {
        **market_priority,
        'schedule_persistent_assignments': True,
    }
    paths = {
        'compiled_v0': parameterized_agent(compiled_v0, agents / 'compiled-v0'),
        'hire_land_reserve_ablation': parameterized_agent(
            hire_ablation, agents / 'hire-ablation'),
        'structural_market_priority': parameterized_agent(
            market_priority, agents / 'market-priority'),
        'persistent_assignments': parameterized_agent(
            persistent, agents / 'persistent'),
    }
    jobs = []
    for variant, candidate in paths.items():
        for seed in seeds:
            for seat in seats:
                job = {'candidate': candidate, 'opponent': args.opponent,
                       'seed': seed, 'seat': seat, 'backend': 'fast',
                       'evidence_profile': 'full',
                       'replay_steps': list(range(481))}
                if args.distributed:
                    job['replay_inline'] = True
                else:
                    job['replay'] = str(
                        replay_dir / f'{variant}-{seed}-{seat}.json')
                jobs.append(job)
    mapper = None
    nodes = []
    if args.distributed:
        from arena.ray_transport import require_cluster
        mapper, nodes = require_cluster(
            args.ray_address, cpus_per_worker=args.cpus_per_worker)
        runner = batched_runner(size=None, map_batches=mapper,
                                available_slots=mapper.available_slots,
                                minimum_batch_size=args.cpus_per_worker)
        results = list(runner(jobs, worker_budget()))
    else:
        results = list(matches(jobs, workers=args.workers))
    identity = lambda value: (value['candidate'], value['opponent'],
                              value['seed'], value['seat'])
    by_identity = {identity(result): result for result in results}
    if len(by_identity) != len(jobs):
        raise OSError('runner returned missing or duplicate Stage-1 worlds')
    rows = []
    for job in jobs:
        result = by_identity[identity(job)]
        if args.distributed:
            replay = {'turns': result['replay_turns']}
            (replay_dir / f"{Path(job['candidate']).parent.name}-{job['seed']}-{job['seat']}.json").write_text(
                json.dumps(replay), encoding='utf-8')
        else:
            replay = json.loads(Path(job['replay']).read_text(encoding='utf-8'))
        variant = next(name for name, path in paths.items()
                       if path == job['candidate'])
        variant_parameters = {
            'compiled_v0': compiled_v0,
            'hire_land_reserve_ablation': hire_ablation,
            'structural_market_priority': market_priority,
            'persistent_assignments': persistent,
        }[variant]
        state = summarize_replay(replay, job['seat'], variant_parameters)
        failures = result['failures'] + result['opponent_failures']
        rows.append({
            'variant': variant,
            'seed': job['seed'], 'seat': job['seat'],
            'failures': failures, 'audit': result['audit'],
            'candidate_hash': result['candidate_hash'],
            'opponent_hash': result['opponent_hash'],
            'environment': result['environment'],
            'runtime_ms': result['runtime_ms'],
            'wall_seconds': result['wall_seconds'], **state,
            'execution_measurement_scope': [
                'PLANT', 'BUILD_PASTURE', 'HIRE', 'BUY_LAND',
            ],
        })
    by_variant = {}
    for variant in paths:
        selected = [row for row in rows if row['variant'] == variant]
        by_variant[variant] = {
            'worlds': len(selected),
            'failures': sum(bool(row['failures']) for row in selected),
            'structural_passes': sum(row['structural_pass'] and not row['failures']
                                     for row in selected),
            'minimums': {
                'quadrants_t480': min(row['phases']['480']['quadrants'] for row in selected),
                'crops_t288': min(row['phases']['288']['crops_total'] for row in selected),
                'pasture_t288': min(row['phases']['288']['structures']['PASTURE']
                                    for row in selected),
                'crew_phase_peak': min(value for row in selected
                                       for value in row['hand_peaks'].values()),
                'crew_daily_peak': min(value for row in selected
                                       for value in row['daily_hand_peaks'].values()),
                'measured_structural_failures': sum(
                    row['structural_execution']['failure_count']
                    + row['capacity_execution']['failure_count'] for row in selected),
                'worker_turns_before_t288': min(
                    sum(day['worker_turns'] for day in row['daily_crew'].values())
                    for row in selected),
                'requested_build_pasture_before_t288': min(
                    row['requested_unit_actions_before_t288']['BUILD_PASTURE']
                    for row in selected),
            },
        }
    compiled = by_variant['persistent_assignments']
    schedule_parameters = {key: DEFAULTS[key] for key in DEFAULTS
                           if key.startswith('schedule_')}
    payload = {
        'schema_version': 2, 'question': 'issue #70 compiled phase schedule Stage 1',
        'seeds': args.seeds, 'seats': seats, 'opponent': args.opponent,
        'parameters': {
            'compiled_v0': {**schedule_parameters, **compiled_v0},
            'hire_land_reserve_ablation': {
                **schedule_parameters, **hire_ablation,
            },
            'structural_market_priority': {
                **schedule_parameters, **market_priority,
            },
            'persistent_assignments': {
                **schedule_parameters, **persistent,
            },
        },
        'elite_reference': ELITE_REFERENCE, 'variants': by_variant,
        'nodes': nodes, 'hosts': host_contribution(results),
        'implementation': {
            name: hashlib.sha256((Path(__file__).resolve().parents[1] / path).read_bytes()).hexdigest()
            for name, path in {'planner': 'agent/planner.py',
                               'params': 'agent/params.py',
                               'gate': 'experiments/compiled_schedule_gate.py'}.items()
        },
        'causal_sequence': [
            'scheduled HIRE bypasses pending BUY_LAND cash reserve',
            'optional animal market slots deferred from t240 through t287',
            'per-unit crop/pasture target ownership until observed completion',
        ],
        'stage1_pass': (compiled['failures'] == 0
                        and compiled['structural_passes'] == compiled['worlds']),
        'rows': rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in payload.items() if k != 'rows'}, indent=2))
    if mapper:
        mapper.ray.shutdown()


if __name__ == '__main__':
    main()
