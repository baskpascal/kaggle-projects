"""Incremental CPU-simulation -> replay-buffer -> GPU-training pipeline for Ray.

This module is harness-only.  It never enters a submission artifact and the trained
smoke value head is not wired into the production agent.  Its purpose is to make the
CPU/GPU boundary explicit and testable before a real ML/RL objective is introduced.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import socket
import site
import subprocess
import time

import numpy as np

from .batch import make_batch, partition, run_batch

FEATURE_NAMES = ('money_100k', 'opponent_money_100k', 'seat', 'steps_719',
                 'candidate_failed', 'opponent_failed')
FEATURE_COUNT = len(FEATURE_NAMES)
ML_PYTHON_CONFIG = Path.home() / '.config' / 'kaggriculture' / 'ml-python'


def _activate_ml_environment(config=ML_PYTHON_CONFIG):
    """Expose the isolated GPU environment to this Ray process, without bloating .venv."""
    try:
        python = Path(config.read_text(encoding='utf-8').strip())
    except OSError as exc:
        raise RuntimeError('GPU environment is not configured; run '
                           'bash scripts/setup.sh --ml on this node') from exc
    if not python.is_file():
        raise RuntimeError(f'configured ML Python does not exist: {python}')
    completed = subprocess.run(
        [str(python), '-c', 'import sysconfig; print(sysconfig.get_paths()["purelib"])'],
        check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    purelib = completed.stdout.strip()
    if not purelib:
        raise RuntimeError(f'could not locate site-packages for {python}')
    site.addsitedir(purelib)
    return {'python': str(python), 'site_packages': purelib}


def _resource_record(stage, num_cpus, num_gpus):
    import ray
    context = ray.get_runtime_context()
    record = {'stage': stage, 'hostname': socket.gethostname(), 'pid': os.getpid(),
              'node_id': str(context.get_node_id()), 'num_cpus': num_cpus,
              'num_gpus': num_gpus,
              'gpu_ids': context.get_accelerator_ids().get('GPU', []),
              'cuda_visible_devices': os.environ.get('CUDA_VISIBLE_DEVICES')}
    print('[kaggriculture-resource] ' + json.dumps(record, sort_keys=True), flush=True)
    return record


def samples_from_rows(rows):
    """Turn existing match rows into a tiny value-head dataset without agent changes."""
    features, targets, origins = [], [], []
    for row in rows:
        features.append([
            float(row['money']) / 100_000,
            float(row['opponent_money']) / 100_000,
            float(row['seat']),
            float(row.get('steps') or 0) / 719,
            float(bool(row.get('failures'))),
            float(bool(row.get('opponent_failures'))),
        ])
        targets.append(float(row['score']))
        origins.append({'job_id': row.get('job_id'), 'seed': row['seed'],
                        'seat': row['seat'], 'hostname': row.get('hostname')})
    return np.asarray(features, dtype=np.float32), np.asarray(targets, dtype=np.float32), origins


class ReplayBuffer:
    """Head-owned, atomically checkpointed replay buffer; workers never write it."""
    def __init__(self, path):
        self.path = Path(path)
        if self.path.exists():
            with np.load(self.path, allow_pickle=False) as saved:
                self.features = saved['features'].astype(np.float32, copy=False)
                self.targets = saved['targets'].astype(np.float32, copy=False)
                self.origins = [json.loads(value) for value in saved['origins'].tolist()]
        else:
            self.features = np.empty((0, FEATURE_COUNT), dtype=np.float32)
            self.targets = np.empty((0,), dtype=np.float32)
            self.origins = []

    def append(self, features, targets, origins):
        features = np.asarray(features, dtype=np.float32)
        targets = np.asarray(targets, dtype=np.float32)
        if features.ndim != 2 or features.shape[1] != FEATURE_COUNT:
            raise ValueError(f'features must have shape (N, {FEATURE_COUNT})')
        if len(features) != len(targets) or len(features) != len(origins):
            raise ValueError('features, targets and origins must have equal lengths')
        self.features = np.concatenate((self.features, features))
        self.targets = np.concatenate((self.targets, targets))
        self.origins.extend(origins)
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.tmp')
        encoded = np.asarray([json.dumps(value, sort_keys=True, separators=(',', ':'))
                              for value in self.origins])
        with temporary.open('wb') as handle:
            np.savez_compressed(handle, features=self.features, targets=self.targets,
                                origins=encoded, feature_names=np.asarray(FEATURE_NAMES))
        temporary.replace(self.path)

    def split(self, eval_fraction=.25):
        if not 0 < eval_fraction < 1 or len(self.targets) < 4:
            raise ValueError('training needs at least four samples and 0 < eval_fraction < 1')
        eval_count = max(1, int(math.ceil(len(self.targets) * eval_fraction)))
        cut = len(self.targets) - eval_count
        return (self.features[:cut], self.targets[:cut],
                self.features[cut:], self.targets[cut:])


def _simulate(batch, workers, timeout):
    resources = _resource_record('cpu-simulation', workers, 0)
    result = run_batch(batch, workers=workers, timeout=timeout)
    result['ray_resources'] = resources
    return result


def _featurize(rows):
    resources = _resource_record('cpu-features', 1, 0)
    features, targets, origins = samples_from_rows(rows)
    return {'features': features, 'targets': targets, 'origins': origins,
            'resources': resources}


def evaluate_model(model, features, targets):
    weights = np.asarray(model['weight'], dtype=np.float32)
    logits = np.asarray(features, dtype=np.float32) @ weights + float(model['bias'])
    predictions = 1 / (1 + np.exp(-np.clip(logits, -30, 30)))
    targets = np.asarray(targets, dtype=np.float32)
    return {'mse': float(np.mean((predictions - targets) ** 2)),
            'accuracy': float(np.mean((predictions >= .5) == (targets >= .5))),
            'samples': len(targets)}


def _evaluate(model, features, targets):
    return {**evaluate_model(model, features, targets),
            'resources': _resource_record('cpu-evaluation', 1, 0)}


class TorchTrainer:
    """One independent trainer per GPU; intentionally no multi-GPU collectives."""
    def __init__(self, seed):
        ml_environment = _activate_ml_environment()
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError('Ray assigned a GPU but torch.cuda.is_available() is false')
        self.torch = torch
        self.device = torch.device('cuda:0')
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        self.model = torch.nn.Linear(FEATURE_COUNT, 1).to(self.device)
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=0.03)
        self.loss = torch.nn.BCEWithLogitsLoss()
        self.identity = _resource_record('gpu-trainer-init', 1, 1)
        self.identity.update(ml_environment=ml_environment,
                             torch_version=str(torch.__version__),
                             cuda_version=str(torch.version.cuda),
                             cuda_available=True, device_name=torch.cuda.get_device_name(0),
                             device_capability=list(torch.cuda.get_device_capability(0)))

    def train(self, features, targets, epochs=20, batch_size=64):
        torch, started = self.torch, time.monotonic()
        # Ray object-store arrays are read-only.  Copy before handing them to Torch so
        # future in-place transforms cannot invoke undefined behavior.
        x = torch.as_tensor(np.array(features, copy=True), dtype=torch.float32,
                            device=self.device)
        y = torch.as_tensor(np.array(targets, copy=True), dtype=torch.float32,
                            device=self.device)
        if not len(y):
            raise ValueError('trainer received an empty dataset')
        torch.cuda.reset_peak_memory_stats(self.device)
        final_loss = None
        for _ in range(epochs):
            order = torch.randperm(len(y), device=self.device)
            for start in range(0, len(y), batch_size):
                index = order[start:start + batch_size]
                prediction = self.model(x[index]).squeeze(1)
                loss = self.loss(prediction, y[index])
                self.optimizer.zero_grad(set_to_none=True)
                loss.backward()
                self.optimizer.step()
                final_loss = float(loss.detach().cpu())
        torch.cuda.synchronize(self.device)
        parameter = next(self.model.parameters())
        bias = list(self.model.parameters())[1]
        resources = _resource_record('gpu-training', 1, 1)
        return {'weight': parameter.detach().cpu().numpy()[0].tolist(),
                'bias': float(bias.detach().cpu().numpy()[0]), 'loss': final_loss,
                'epochs': epochs, 'batch_size': batch_size,
                'wall_seconds': time.monotonic() - started,
                'cuda_peak_memory_bytes': torch.cuda.max_memory_allocated(self.device),
                'trainer': self.identity, 'resources': resources}


def _node_probe():
    import importlib.util
    return {'hostname': socket.gethostname(), 'pid': os.getpid(),
            'logical_cpus': len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity')
            else os.cpu_count(), 'torch_installed': importlib.util.find_spec('torch') is not None}


def _gpu_probe():
    import importlib.util
    result = _resource_record('gpu-probe', .25, 1)
    try:
        result['ml_environment'] = _activate_ml_environment()
    except Exception as exc:
        result['torch_installed'] = False
        result['cuda_available'] = False
        result['error'] = str(exc)
        return result
    result['torch_installed'] = importlib.util.find_spec('torch') is not None
    if not result['torch_installed']:
        result['cuda_available'] = False
        result['error'] = 'install requirements-ml.lock on this GPU node'
        return result
    import torch
    result.update(torch_version=str(torch.__version__),
                  cuda_version=str(torch.version.cuda),
                  cuda_available=torch.cuda.is_available())
    if torch.cuda.is_available():
        left = torch.ones((256, 256), device='cuda')
        value = (left @ left).sum()
        torch.cuda.synchronize()
        result.update(device_name=torch.cuda.get_device_name(0),
                      device_capability=list(torch.cuda.get_device_capability(0)),
                      smoke_value=float(value.cpu()))
    return result


def cluster_inventory(ray):
    """Probe every CPU node and every GPU node with hard node affinity."""
    strategy = ray.util.scheduling_strategies.NodeAffinitySchedulingStrategy
    nodes = [node for node in ray.nodes() if node.get('Alive')]
    cpu_probe = ray.remote(num_cpus=.1, num_gpus=0, max_retries=0)(_node_probe)
    cpu_refs = [cpu_probe.options(scheduling_strategy=strategy(node['NodeID'], soft=False)).remote()
                for node in nodes]
    cpu_results = ray.get(cpu_refs)
    gpu_probe = ray.remote(num_cpus=.25, num_gpus=1, max_retries=0)(_gpu_probe)
    gpu_refs = [(node, gpu_probe.options(scheduling_strategy=strategy(
                 node['NodeID'], soft=False)).remote()) for node in nodes
                if node.get('Resources', {}).get('GPU', 0) >= 1]
    gpu_results = {node['NodeID']: ray.get(ref) for node, ref in gpu_refs}
    inventory = []
    for node, probe in zip(nodes, cpu_results):
        resources = node.get('Resources', {})
        inventory.append({**probe, 'node_id': node['NodeID'],
                          'node_address': node.get('NodeManagerAddress'),
                          'ray_cpus': resources.get('CPU', 0),
                          'ray_gpus': resources.get('GPU', 0),
                          'memory_bytes': resources.get('memory', 0),
                          'gpu_probe': gpu_results.get(node['NodeID'])})
    return sorted(inventory, key=lambda item: item['hostname'])


class HybridPipeline:
    def __init__(self, ray, *, cpus_per_simulation=1, trainers=None, timeout=-1):
        gpu_count = int(ray.cluster_resources().get('GPU', 0))
        trainers = gpu_count if trainers is None else trainers
        if cpus_per_simulation < 1 or trainers < 1:
            raise ValueError('cpus_per_simulation and trainers must be positive')
        if trainers > gpu_count:
            raise ValueError(f'requested {trainers} trainers but Ray advertises {gpu_count} GPUs')
        self.ray, self.cpus, self.timeout = ray, cpus_per_simulation, timeout
        self.simulate = ray.remote(num_cpus=cpus_per_simulation, num_gpus=0,
                                   max_retries=0)(_simulate)
        self.featurize = ray.remote(num_cpus=1, num_gpus=0, max_retries=0)(_featurize)
        self.evaluate = ray.remote(num_cpus=1, num_gpus=0, max_retries=0)(_evaluate)
        remote_trainer = ray.remote(num_cpus=1, num_gpus=1, max_restarts=0)(TorchTrainer)
        self.trainers = [remote_trainer.remote(10_000 + index) for index in range(trainers)]

    def run_cycle(self, jobs, buffer, *, simulation_batch_size=8, epochs=20,
                  train_batch_size=64, eval_fraction=.25, require_all_nodes=False):
        batches = [make_batch(group, index) for index, group in
                   enumerate(partition(jobs, simulation_batch_size))]
        nodes = [node for node in self.ray.nodes()
                 if node.get('Alive') and node.get('Resources', {}).get('CPU', 0) >= self.cpus]
        if require_all_nodes and len(batches) < len(nodes):
            raise ValueError(f'require_all_nodes needs at least {len(nodes)} simulation batches')
        strategy = self.ray.util.scheduling_strategies.NodeAffinitySchedulingStrategy
        pending, batch_metrics = [], []
        for index, batch in enumerate(batches):
            task = self.simulate
            if require_all_nodes and index < len(nodes):
                task = task.options(scheduling_strategy=strategy(nodes[index]['NodeID'], soft=False))
            pending.append(task.remote(batch, self.cpus, self.timeout))
        stage_resources, by_host = [], {}
        while pending:
            ready, pending = self.ray.wait(pending, num_returns=1)
            envelope = self.ray.get(ready[0])
            by_host[envelope['hostname']] = by_host.get(envelope['hostname'], 0) + envelope['games']
            stage_resources.append(envelope['ray_resources'])
            batch_metrics.append({key: envelope.get(key) for key in
                                  ('batch_id', 'hostname', 'games', 'wall_seconds',
                                   'cpu_utilization', 'p50_match_seconds',
                                   'p95_match_seconds')})
            samples = self.ray.get(self.featurize.remote(envelope['rows']))
            stage_resources.append(samples['resources'])
            buffer.append(samples['features'], samples['targets'], samples['origins'])
        train_x, train_y, eval_x, eval_y = buffer.split(eval_fraction)
        train_ref_x, train_ref_y = self.ray.put(train_x), self.ray.put(train_y)
        models = self.ray.get([trainer.train.remote(train_ref_x, train_ref_y, epochs,
                                                    train_batch_size)
                               for trainer in self.trainers])
        eval_ref_x, eval_ref_y = self.ray.put(eval_x), self.ray.put(eval_y)
        evaluations = self.ray.get([self.evaluate.remote(model, eval_ref_x, eval_ref_y)
                                    for model in models])
        for model, evaluation in zip(models, evaluations):
            stage_resources.extend((model['resources'], evaluation['resources']))
        best = min(range(len(models)), key=lambda index: evaluations[index]['mse'])
        return {'games': len(jobs), 'samples': len(buffer.targets), 'games_by_hostname': by_host,
                'train_samples': len(train_y), 'evaluation_samples': len(eval_y),
                'simulation_batches': batch_metrics,
                'models': models, 'evaluations': evaluations, 'best_trainer': best,
                'best_model': models[best], 'resources': stage_resources}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
