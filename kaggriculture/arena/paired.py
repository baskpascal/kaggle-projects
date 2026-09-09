"""Run both declared comparison legs and emit frozen wins-only evidence in one command."""
import argparse
import ast
from datetime import date
import json
from pathlib import Path
import time

from .agents import VARIANTS, agent_hash
from .engine import fingerprint
from .league import run_league
from .ray_transport import DEFAULT_CPUS_PER_WORKER
from . import runspec
from .seeds import REGISTRY, SPLITS, parse_seeds, validate_seeds
from eval.comparison import build_comparison, digest, load_families
from eval.ladder import cohort, load_ratings, opponent_id, STRONG_CUT, MAX_RATING_AGE_DAYS
from eval.submit_gate import decide
from eval.pairing import paired_rows


def check_spec(spec):
    base = spec
    while '::' in base:
        wrapper, base = base.split('::', 1)
        if wrapper not in ('clock', 'xliq'):
            raise ValueError(f'Unknown adapter: {wrapper}')
    if base not in (*VARIANTS, 'champion', 'challenger', 'starter', 'pass', 'random'):
        path = Path(base)
        if not path.is_file():
            raise ValueError(f'Unknown agent: {spec}')
        ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    return agent_hash(spec)


def run_pair(baseline, candidate, opponents, seeds, output, *, workers=4, backend='fast',
             split='dev', min_blocks=100, strong_only=False, ratings=None, families=None,
             mirrors=(), cut=STRONG_CUT, max_age_days=MAX_RATING_AGE_DAYS,
             registry_path=REGISTRY, incumbent=None, runner=None, panel=None,
             deadline_seconds=None, attempts=3, distribution=None):
    started = time.perf_counter()
    seeds, opponents = list(seeds), list(opponents)
    if (workers < 1 or backend not in ('fast', 'official') or min_blocks < 2
            or len(seeds) < min_blocks or not opponents or len(opponents) != len(set(opponents))):
        raise ValueError('Require positive workers, valid backend, unique opponents and enough seed blocks')
    if len({opponent_id(name) for name in opponents}) != len(opponents):
        raise ValueError('Ambiguous opponent IDs')
    today = date.today()
    ratings = json.loads(json.dumps(load_ratings() if ratings is None else ratings))
    families = dict(load_families() if families is None else families)
    if strong_only:
        opponents = [name for name in opponents if cohort(name, ratings, cut=cut,
                     max_age_days=max_age_days, today=today) == 'strong']
        if not opponents:
            raise ValueError('No current strong opponents in the requested panel')
    if not set(mirrors) <= {opponent_id(name) for name in opponents}:
        raise ValueError('Declared mirror must belong to the selected panel')
    hashes = {name: check_spec(name) for name in (baseline, candidate, *opponents)}
    environment = fingerprint()
    # Read-only prechecks precede any consumption. The league admits both hashes
    # atomically before its first callback and readmits the second leg.
    registry_before = validate_seeds(seeds, split, path=registry_path)
    # Everything the spec checks is read-only and happens here, before `run_league` reaches
    # `admit_run` and burns reserved seeds. A run refused after admission has already spent
    # the evidence it needed to learn it was misconfigured.
    spec = runspec.build(
        baseline=(baseline, hashes[baseline]), candidate=(candidate, hashes[candidate]),
        opponents={name: {'hash': hashes[name], 'lineage': families.get(opponent_id(name),
                                                                       opponent_id(name))}
                   for name in opponents},
        seeds=seeds, split=split, registry=registry_before, backend=backend,
        environment=environment, panel=panel, min_blocks=min_blocks, cut=cut,
        max_rating_age_days=max_age_days, deadline_seconds=deadline_seconds,
        attempts=attempts, distribution=distribution, registry_path=registry_path)
    target = Path(output)
    target.mkdir(parents=True, exist_ok=False)
    plan = dict(baseline=baseline, candidate=candidate, opponents=opponents, seeds=seeds,
                hashes=hashes, backend=backend, environment=environment, split=split,
                ratings=ratings, ratings_sha256=digest(ratings), families=families,
                as_of=today.isoformat(), cut=cut, max_rating_age_days=max_age_days,
                registry_before=registry_before, strong_only=strong_only,
                run_id=spec['run_id'])
    (target / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    (target / 'spec.json').write_text(json.dumps(spec, indent=2) + '\n')
    results, timings = [], {}
    try:
        for label, agent, other in [('baseline', baseline, candidate), ('candidate', candidate, baseline)]:
            if {name: check_spec(name) for name in hashes} != hashes or fingerprint() != environment:
                raise ValueError('Agent or environment changed after the paired batch was declared')
            leg_started = time.perf_counter()
            rows, _, _ = run_league(agent, opponents, seeds, workers=workers, backend=backend,
                                   output=target / label, split=split, paired_with=(other,),
                                   registry_path=registry_path, runner=runner,
                                   attempts=attempts, run_id=spec['run_id'])
            timings[label] = time.perf_counter() - leg_started
            if len(rows) != len(seeds) * len(opponents) * 2:
                raise ValueError('Incomplete comparison leg')
            for row in rows:
                if (row['candidate_hash'] != hashes[agent]
                        or row['opponent_hash'] != hashes.get(row['opponent'])
                        or row['environment'] != environment or row['backend'] != backend
                        or row['seed'] not in seeds or row['seat'] not in (0, 1)):
                    raise ValueError('Match evidence differs from the declared batch')
            paired_rows(rows, rows)
            results.append(rows)
        comparison = build_comparison(*results, ratings=ratings, families=families, mirrors=mirrors,
                                      today=today, cut=cut, max_age_days=max_age_days, split=split)
        comparison['execution'] = dict(wall_seconds=time.perf_counter() - started,
                                       leg_wall_seconds=timings, split=split,
                                       strong_only=strong_only, plan_sha256=digest(plan))
        # Stamped on the result, not only kept beside it: this is what lets the manifest,
        # and `eval/dossier.py` above it, refuse a comparison from another experiment
        # instead of reading it as if it belonged.
        comparison['run_id'] = spec['run_id']
        # The gate refuses to read a comparison as a release decision unless the
        # baseline leg is the declared incumbent, so the declaration is checked here
        # against the hash the batch actually froze rather than after the fact.
        gate = decide(comparison, incumbent)
        gate['run_id'] = spec['run_id']
        (target / 'comparison.json').write_text(json.dumps(comparison, indent=2) + '\n')
        (target / 'gate.json').write_text(json.dumps(gate, indent=2) + '\n')
        evidence = {'spec': target / 'spec.json', 'comparison': target / 'comparison.json',
                    'gate': target / 'gate.json'}
        for label in ('baseline', 'candidate'):
            store = Path(str(target / label) + '.jobs.sqlite3')
            if store.exists():
                evidence[f'{label}_jobs'] = store
        (target / 'manifest.json').write_text(
            json.dumps(runspec.manifest(spec, evidence), indent=2, default=str) + '\n')
        (target / 'report.md').write_text(gate['verdict'] + '\n\n' +
            '\n'.join('- ' + reason for reason in gate['reasons']) + '\n\n' +
            f"Wall time: {comparison['execution']['wall_seconds']:.2f}s.\n")
        return comparison, gate
    except Exception as exc:
        (target / 'failure.json').write_text(json.dumps(dict(error=str(exc),
            completed_legs=len(results), wall_seconds=time.perf_counter() - started), indent=2) + '\n')
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--opponents', required=True)
    parser.add_argument('--seeds', default='1000:1100')
    parser.add_argument('--split', choices=SPLITS, default='dev')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--ray-address')
    parser.add_argument('--cpus-per-worker', type=int, default=DEFAULT_CPUS_PER_WORKER)
    parser.add_argument('--batch-size', type=int,
                        help='omit for adaptive sizing over the Ray cluster')
    parser.add_argument('--backend', choices=('fast', 'official'), default='fast')
    parser.add_argument('--min-blocks', type=int, default=100, help='lower only for development or smoke runs')
    parser.add_argument('--strong-only', action='store_true')
    parser.add_argument('--cut', type=float, default=STRONG_CUT)
    parser.add_argument('--max-rating-age-days', dest='max_age_days', type=int, default=MAX_RATING_AGE_DAYS)
    parser.add_argument('--mirrors', default='')
    parser.add_argument('--incumbent-id', dest='incumbent_id',
                        help='id of the agent holding our active submission slot; without it '
                             'the run is a measurement and cannot authorize a release')
    parser.add_argument('--displaces', help='which active submission a release would destroy')
    parser.add_argument('--attempts', type=int, default=3,
                        help='infrastructure-fault retries; part of the run spec because '
                             'retry policy changes what a failure means')
    parser.add_argument('--deadline-seconds', dest='deadline_seconds', type=float,
                        help='per-match deadline; material, because a deadline changes results')
    parser.add_argument('--panel', dest='panel_snapshot',
                        help='a filed eval.panel revision this run is measured against')
    parser.add_argument('--output', required=True)
    args = vars(parser.parse_args())
    panel_snapshot = args.pop('panel_snapshot')
    if panel_snapshot:
        from eval.panel import load as load_panel
        args['panel'] = load_panel(panel_snapshot)
    args['seeds'] = parse_seeds(args['seeds'])
    args['opponents'] = args['opponents'].split(',')
    args['mirrors'] = [name for name in args['mirrors'].split(',') if name]
    incumbent_id, displaces = args.pop('incumbent_id'), args.pop('displaces')
    ray_address = args.pop('ray_address')
    cpus_per_worker = args.pop('cpus_per_worker')
    batch_size = args.pop('batch_size')
    if ray_address:
        import atexit
        from .batch import batched_runner
        from .ray_transport import connect
        mapper = connect(ray_address, cpus_per_worker=cpus_per_worker)
        atexit.register(mapper.ray.shutdown)
        args['runner'] = batched_runner(size=batch_size, map_batches=mapper,
                                        available_slots=mapper.available_slots)
    args['distribution'] = {'topology': 'ray' if ray_address else 'local',
                            'workers': args['workers'], 'cpus_per_worker': cpus_per_worker,
                            'batch_size': batch_size}
    # The hash is not taken from the operator: it is the baseline this run will freeze,
    # so a declaration naming the wrong agent is caught by check_spec, not by trust.
    args['incumbent'] = (None if not incumbent_id else
                         {'id': incumbent_id, 'hash': check_spec(args['baseline']),
                          'displaces': displaces or ''})
    comparison, gate = run_pair(**args)
    print(json.dumps(dict(**gate, wall_seconds=comparison['execution']['wall_seconds']), indent=2))


if __name__ == '__main__':
    main()
