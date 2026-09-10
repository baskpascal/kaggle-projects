"""Stage-1 executed-state gate for the opt-in compiled phase schedule.

This is deliberately not a competitive evaluator. It compares the compiled body with
the frozen reactive sheep-max control, but will only say whether the structural mechanism
fired in every diagnostic world.
"""
import argparse
import json
from pathlib import Path
import tempfile

from arena.parallel import matches
from agent.params import DEFAULTS
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


def summarize_replay(replay, seat):
    """Read only executed observations; requested actions cannot satisfy a gate."""
    by_step = {turn['step']: turn for turn in replay['turns']}
    phases = {str(step): snapshot(by_step[step]['observations'][seat], seat)
              for step in CHECKPOINTS}
    hand_peaks = {}
    for start, end in CREW_PHASES:
        values = [len(turn['observations'][seat]['farms'][seat].get('hands') or [])
                  for step, turn in by_step.items() if start <= step < end]
        hand_peaks[f'{start}:{end}'] = max(values, default=0)
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
    }
    return {'phases': phases, 'hand_peaks': hand_peaks,
            'requested_unit_actions_before_t288': requested, 'gates': gates,
            'structural_pass': all(gates.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opponent', default='versions/v006/main.py')
    parser.add_argument('--seeds', default='1000:1010')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    start, _, end = args.seeds.partition(':')
    seeds = list(range(int(start), int(end))) if end else [int(start)]
    output = Path(args.output)
    replay_dir = output.parent / (output.stem + '-replays')
    replay_dir.mkdir(parents=True, exist_ok=True)
    agents = Path(tempfile.mkdtemp(prefix='compiled-schedule-agents-'))
    (agents / 'control').mkdir()
    (agents / 'compiled').mkdir()
    paths = {
        'reactive_control': parameterized_agent(BASE, agents / 'control'),
        'compiled_schedule': parameterized_agent({**BASE, 'compiled_schedule': True},
                                                  agents / 'compiled'),
    }
    jobs = []
    for variant, candidate in paths.items():
        for seed in seeds:
            for seat in (0, 1):
                jobs.append({'candidate': candidate, 'opponent': args.opponent,
                             'seed': seed, 'seat': seat, 'backend': 'fast',
                             'evidence_profile': 'full',
                             'replay': str(replay_dir / f'{variant}-{seed}-{seat}.json'),
                             'replay_steps': list(range(481))})
    rows = []
    for job, result in zip(jobs, matches(jobs, workers=args.workers)):
        replay = json.loads(Path(job['replay']).read_text(encoding='utf-8'))
        state = summarize_replay(replay, job['seat'])
        failures = result['failures'] + result['opponent_failures']
        rows.append({
            'variant': next(name for name, path in paths.items()
                            if path == job['candidate']),
            'seed': job['seed'], 'seat': job['seat'],
            'failures': failures, 'audit': result['audit'], **state,
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
                'requested_build_pasture_before_t288': min(
                    row['requested_unit_actions_before_t288']['BUILD_PASTURE']
                    for row in selected),
            },
        }
    compiled = by_variant['compiled_schedule']
    schedule_parameters = {key: DEFAULTS[key] for key in DEFAULTS
                           if key.startswith('schedule_')}
    payload = {
        'schema_version': 1, 'question': 'issue #70 compiled phase schedule Stage 1',
        'seeds': args.seeds, 'seats': [0, 1], 'opponent': args.opponent,
        'parameters': {'control': BASE,
                       'compiled': {**BASE, 'compiled_schedule': True,
                                    **schedule_parameters}},
        'elite_reference': ELITE_REFERENCE, 'variants': by_variant,
        'stage1_pass': (compiled['failures'] == 0
                        and compiled['structural_passes'] == compiled['worlds']),
        'rows': rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in payload.items() if k != 'rows'}, indent=2))


if __name__ == '__main__':
    main()
