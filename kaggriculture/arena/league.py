import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from .agents import agent_hash
from .batch import batched_runner, host_contribution, worker_budget
from .jobs import EVIDENCE_PROFILES, JobStore, execute, plan, single_provenance
from .parallel import stream
from .ray_transport import DEFAULT_CPUS_PER_WORKER
from .seeds import REGISTRY, SPLITS, admit_run, parse_seeds
from eval.metrics import summarize
from eval.reports import write_report


def store_path(output):
    """A sibling of the report, not a file inside it.

    `write_report` creates its directory with `exist_ok=False`, so the report can only
    be written once, and a store living inside it could not survive the interrupted run
    it exists to rescue.
    """
    return None if output is None else Path(str(output) + '.jobs.sqlite3')


def run_league(candidate, opponents, seeds, workers=4, backend='fast', output=None, split='dev',
               paired_with=(), registry_path=REGISTRY, resume=False, attempts=3, store=None,
               runner=None, run_id=None, evidence_profile='score'):
    if len(set(opponents)) != len(opponents) or not opponents or len(set(seeds)) != len(seeds) or not seeds:
        raise ValueError('Require unique, nonempty opponents and seeds')
    if evidence_profile not in EVIDENCE_PROFILES:
        raise ValueError(f'Evidence profile must be one of {", ".join(EVIDENCE_PROFILES)}')
    started = time.perf_counter()
    # Declare the whole paired batch, then burn reserved validation seeds before
    # the first callback runs. An interrupted run must not free them again.
    hashes = {name: agent_hash(name) for name in opponents}
    # A paired comparison is two legs over the same seeds. Both legs must name
    # every candidate in the batch, so the batch identity is fixed before the
    # first game and the second leg is readmitted rather than refused.
    members = sorted({agent_hash(name) for name in (candidate, *paired_with)})
    provenance = {'candidate': candidate, 'candidate_hash': agent_hash(candidate),
                  'paired_with': list(paired_with), 'batch_members': members,
                  'opponents': opponents, 'opponent_hashes': hashes, 'backend': backend,
                  'evidence_profile': evidence_profile,
                  'seeds': list(seeds), 'seats': [0, 1],
                  'requested_at': datetime.now(timezone.utc).isoformat()}
    # Admission is the head's job and happens exactly once per run, before any game.
    # A resume re-declares the same batch, which `admit_run` readmits against the
    # recorded batch id rather than burning the validation seeds a second time.
    registry = admit_run(seeds, split, provenance, path=registry_path)
    jobs = plan(candidate, opponents, seeds, backend=backend, split=split,
                evidence_profile=evidence_profile)
    target = store if store is not None else store_path(output)
    if target is None:
        target = ':memory:'
    elif not resume and Path(target).exists():
        raise ValueError(f'{target} already holds games for this run; pass resume to continue it')
    with JobStore(target, run_id=run_id) as job_store:
        remaining = len(job_store.pending(jobs))
        if resume and remaining != len(jobs):
            print(f'resuming: {len(jobs) - remaining}/{len(jobs)} games already recorded', flush=True)
        counter = {'n': len(jobs) - remaining}

        def progress(_row):
            counter['n'] += 1
            if counter['n'] % 20 == 0:
                print(f'{counter["n"]}/{len(jobs)} games; {time.perf_counter()-started:.1f}s', flush=True)

        # `stream`, not `matches`: the durable store exists to survive an interrupted run,
        # and holding finished games in RAM to keep plan order is exactly what would lose
        # them. `execute` restores the plan's order once every game is safely recorded.
        rows = execute(jobs, job_store, split, runner or stream, workers=workers,
                       attempts=attempts, on_row=progress)
    # The store may hold games from an earlier attempt; refusing a mixed set here is
    # what makes a resumed or distributed run as trustworthy as a single-process one.
    single_provenance(rows)
    summary = summarize(rows)
    metadata = {'candidate': candidate, 'candidate_hash': provenance['candidate_hash'],
                'opponents': opponents, 'opponent_hashes': hashes,
                'split': split, 'seeds': seeds, 'backend': backend, 'registry': registry,
                'evidence_profile': evidence_profile,
                'run_id': run_id,
                'created_at': datetime.now(timezone.utc).isoformat(),
                # Which machines carried this run, and how much of it each one took.
                'hosts': host_contribution(rows),
                'wall_seconds': time.perf_counter() - started}
    if output:
        write_report(output, rows, summary, metadata)
    return rows, summary, metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', default='challenger')
    parser.add_argument('--opponents', default='starter,crop,animal,diversified')
    parser.add_argument('--seeds', default='1000:1010')
    parser.add_argument('--workers', type=int)
    parser.add_argument('--leave-cpus-free', type=int, default=1)
    parser.add_argument('--batch-size', type=int,
                        help='jobs per batch; with Ray, omit for adaptive sizing')
    parser.add_argument('--ray-address', help='private Ray head address, usually ray://HOST:10001')
    parser.add_argument('--distributed', action='store_true',
                        help='require the private cluster: refuse to run if it is not up')
    parser.add_argument('--cpus-per-worker', type=int, default=DEFAULT_CPUS_PER_WORKER,
                        help='Ray CPUs and local match children assigned to each batch task')
    parser.add_argument('--backend', choices=['fast', 'official'], default='fast')
    parser.add_argument('--evidence-profile', choices=['score', 'audit', 'full'], default='score')
    parser.add_argument('--split', choices=list(SPLITS), default='dev')
    parser.add_argument('--paired-with', dest='paired_with', default='',
                        help='other candidates in the same declared validation batch')
    parser.add_argument('--resume', action='store_true',
                        help='skip games the job store already holds for this exact plan')
    parser.add_argument('--attempts', type=int, default=3,
                        help='infrastructure-fault retries; agent failures are never retried')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    args.opponents = args.opponents.split(',')
    args.paired_with = [n for n in args.paired_with.split(',') if n]
    args.seeds = parse_seeds(args.seeds)
    ray_address = args.__dict__.pop('ray_address')
    distributed = args.__dict__.pop('distributed')
    batch_size = args.__dict__.pop('batch_size')
    cpus_per_worker = args.__dict__.pop('cpus_per_worker')
    leave_cpus_free = args.__dict__.pop('leave_cpus_free')
    args.workers = args.workers or worker_budget(leave_cpus_free)
    # `--distributed` is the demand, `--ray-address` only says where to look. Neither is
    # ever inferred: without one of them this run stays on this host, and with
    # `--distributed` a missing cluster is an error rather than a quiet local run.
    if distributed:
        from .ray_transport import require_cluster
        mapper, _ = require_cluster(ray_address or 'auto',
                                    cpus_per_worker=cpus_per_worker,
                                    attempts=args.attempts)
    elif ray_address:
        from .ray_transport import connect
        mapper = connect(ray_address, cpus_per_worker=cpus_per_worker,
                         attempts=args.attempts)
    else:
        mapper = None
    if mapper is not None:
        args.runner = batched_runner(size=batch_size, map_batches=mapper,
                                     available_slots=mapper.available_slots)
    elif batch_size:
        args.runner = batched_runner(size=batch_size)
    _, summary, _ = run_league(**vars(args))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
