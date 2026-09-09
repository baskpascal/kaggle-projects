"""Run the incremental two-PC CPU/GPU Ray training scaffold."""
from __future__ import annotations

import argparse
import atexit
from pathlib import Path
import time

from arena.hybrid import HybridPipeline, ReplayBuffer, cluster_inventory, write_json
from arena.jobs import plan
from arena.ray_transport import require_cluster


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--address', default='auto')
    parser.add_argument('--candidate', default='versions/v006/main.py')
    parser.add_argument('--opponent', default='opponents/public/thomas_t95/main.py')
    parser.add_argument('--seed', type=int, default=900000)
    parser.add_argument('--seeds-per-cycle', type=int, default=32)
    parser.add_argument('--cycles', type=int, default=1)
    parser.add_argument('--cpus-per-simulation', type=int, default=1)
    parser.add_argument('--simulation-batch-size', type=int, default=2)
    parser.add_argument('--trainers', type=int,
                        help='independent trainers; default is one per Ray GPU')
    parser.add_argument('--epochs', type=int, default=20)
    parser.add_argument('--train-batch-size', type=int, default=64)
    parser.add_argument('--timeout', type=float, default=-1)
    parser.add_argument('--buffer', default='experiments/results/hybrid-replay.npz')
    parser.add_argument('--output', default='experiments/results/hybrid-training.json')
    parser.add_argument('--require-all-nodes', action='store_true',
                        help='pin the first CPU batch to every node; useful for smoke tests')
    args = parser.parse_args()
    if args.cycles < 1 or args.seeds_per_cycle < 1:
        parser.error('--cycles and --seeds-per-cycle must be positive')

    mapper, topology = require_cluster(args.address,
                                       cpus_per_worker=args.cpus_per_simulation,
                                       strict_commit=False)
    ray = mapper.ray
    atexit.register(ray.shutdown)
    inventory = cluster_inventory(ray)
    gpu_nodes = [node for node in inventory if node['ray_gpus']]
    broken = [node['hostname'] for node in gpu_nodes
              if not node['gpu_probe'] or not node['gpu_probe'].get('cuda_available')]
    if not gpu_nodes:
        raise SystemExit('Ray advertises no GPU; configure the GPU worker with --num-gpus=1')
    if broken:
        raise SystemExit('PyTorch CUDA probe failed on: ' + ', '.join(broken) +
                         '. Run bash scripts/setup.sh --distributed --ml on those nodes.')

    pipeline = HybridPipeline(ray, cpus_per_simulation=args.cpus_per_simulation,
                              trainers=args.trainers, timeout=args.timeout)
    buffer = ReplayBuffer(args.buffer)
    report = {'schema_version': 1, 'status': 'running', 'inventory': inventory,
              'topology': topology, 'candidate': args.candidate,
              'opponent': args.opponent, 'buffer': str(Path(args.buffer)), 'cycles': []}
    write_json(args.output, report)
    started = time.monotonic()
    for cycle in range(args.cycles):
        first = args.seed + cycle * args.seeds_per_cycle
        jobs = plan(args.candidate, [args.opponent],
                    range(first, first + args.seeds_per_cycle), split='diagnostic')
        result = pipeline.run_cycle(
            jobs, buffer, simulation_batch_size=args.simulation_batch_size,
            epochs=args.epochs, train_batch_size=args.train_batch_size,
            require_all_nodes=args.require_all_nodes)
        report['cycles'].append({'cycle': cycle, 'seed_start': first, **result})
        write_json(args.output, report)
    report.update(status='complete', wall_seconds=time.monotonic() - started,
                  final_samples=len(buffer.targets))
    write_json(args.output, report)
    print(Path(args.output).read_text(encoding='utf-8'))


if __name__ == '__main__':
    main()
