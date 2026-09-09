"""Ray transports batches; the disposable local child still transports a match.

Ray is an optional harness dependency and is imported only by ``connect``.  The submitted
agent never imports it.  Ray's own task retries are disabled: this mapper retries only
system failures, while Python/application failures remain hard errors and agent failures
remain ordinary match rows.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
import platform
import socket

from .agents import agent_hash
from .batch import run_batch
from .engine import fingerprint
from .jobs import git_provenance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXCLUDES = ['/.git/', '/.venv/', '/.tmp/', '/docs/', '/tests/', '/replays/',
                    '/seed_registry.json',
                    '/experiments/results/', '/experiments/searches/', '**/__pycache__/']


@contextmanager
def _worker_role():
    """Mark work as remote while it and its match children are running."""
    previous = os.environ.get('ARENA_ROLE')
    os.environ['ARENA_ROLE'] = 'ray-worker'
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('ARENA_ROLE', None)
        else:
            os.environ['ARENA_ROLE'] = previous


def _environment(artifacts):
    """Evidence collected inside one specifically scheduled cluster node."""
    with _worker_role():
        return {'hostname': socket.gethostname(), 'python': platform.python_version(),
                'environment': fingerprint(), 'git': git_provenance(),
                'artifacts': {path: agent_hash(path) for path in artifacts}}


def _remote_batch(batch, workers, timeout):
    with _worker_role():
        if any(spec.get('replay') or spec.get('replay_steps') for spec in batch['specs']):
            raise ValueError('Remote jobs cannot write replay results to worker filesystems')
        return run_batch(batch, workers=workers, timeout=timeout)


class RayBatchMapper:
    def __init__(self, ray, *, cpus_per_worker=1, attempts=3, timeout=-1,
                 strict_commit=True):
        if cpus_per_worker < 1 or attempts < 1:
            raise ValueError('cpus_per_worker and attempts must be positive')
        self.ray = ray
        self.cpus_per_worker = cpus_per_worker
        self.attempts = attempts
        self.timeout = timeout
        self.strict_commit = strict_commit
        cpus = int(ray.cluster_resources().get('CPU', 0))
        self.available_slots = max(1, cpus // cpus_per_worker)
        self._task = ray.remote(num_cpus=cpus_per_worker, max_retries=0,
                                retry_exceptions=False)(_remote_batch)
        self._probe = ray.remote(num_cpus=0, max_retries=0,
                                 retry_exceptions=False)(_environment)
        self.nodes = []

    def verify_cluster(self, batches):
        artifacts = sorted({spec[field] for batch in batches for spec in batch['specs']
                            for field in ('candidate', 'opponent')})
        expected_artifacts = {path: agent_hash(path) for path in artifacts}
        expected_environment = fingerprint()
        expected_python = platform.python_version()
        expected_git = git_provenance(PROJECT_ROOT)
        try:
            strategy = self.ray.util.scheduling_strategies.NodeAffinitySchedulingStrategy
            refs = [self._probe.options(scheduling_strategy=strategy(
                    node['NodeID'], soft=False)).remote(artifacts)
                    for node in self.ray.nodes() if node.get('Alive')]
            evidence = self.ray.get(refs)
        except Exception as exc:
            raise OSError(f'Could not verify every Ray node: {exc}') from exc
        if not evidence:
            raise OSError('Ray reports no live nodes')
        for node in evidence:
            if node['artifacts'] != expected_artifacts:
                raise ValueError(f'Agent hash mismatch on {node["hostname"]}')
            if node['environment'] != expected_environment:
                raise ValueError(f'Engine fingerprint mismatch on {node["hostname"]}')
            if node['python'] != expected_python:
                raise ValueError(f'Python version mismatch on {node["hostname"]}: '
                                 f'{node["python"]} != {expected_python}')
            remote_commit = node['git'].get('git_commit')
            if (self.strict_commit and expected_git.get('git_commit') and remote_commit
                    and remote_commit != expected_git['git_commit']):
                raise ValueError(f'Git commit mismatch on {node["hostname"]}: {remote_commit}')
        self.nodes = evidence
        return evidence

    def run_on_every_node(self, batch, *, timeout=None):
        """Run the same batch once per node for exact cross-machine comparison."""
        return [(node, result) for node, result, _ in
                self._run_on_every_node(batch, timeout=timeout,
                                        workers=lambda _node: self.cpus_per_worker)]

    def local_baseline_on_every_node(self, batch, *, timeout=None, maximum_workers=None):
        """Measure the local pool on every node so the fastest host is the baseline."""
        if maximum_workers is not None and maximum_workers < 1:
            raise ValueError('maximum_workers must be positive')

        def workers(node):
            capacity = self._node_capacity(node)
            return capacity if maximum_workers is None else min(capacity, maximum_workers)

        return self._run_on_every_node(batch, timeout=timeout, workers=workers)

    @staticmethod
    def _node_capacity(node):
        return int(node.get('Resources', {}).get('CPU', 0))

    def _run_on_every_node(self, batch, *, timeout, workers):
        strategy = self.ray.util.scheduling_strategies.NodeAffinitySchedulingStrategy
        nodes = [node for node in self.ray.nodes() if node.get('Alive')]
        limit = self.timeout if timeout is None else timeout
        refs = []
        for node in nodes:
            count = workers(node)
            capacity = self._node_capacity(node)
            if capacity < 1:
                raise ValueError(f'Node {node["NodeID"]} advertises no CPUs')
            if count > capacity:
                raise ValueError(f'Node {node["NodeID"]} advertises only {capacity} CPUs; '
                                 f'cannot reserve {count}')
            task = self._task.options(num_cpus=count, scheduling_strategy=strategy(
                node['NodeID'], soft=False))
            refs.append((node, count, task.remote(self._with_source(batch), count, limit)))
        results = []
        for node, count, ref in refs:
            try:
                results.append((node, self.ray.get(ref), count))
            except self.ray.exceptions.RayTaskError as exc:
                raise exc.as_instanceof_cause() from exc
            except self.ray.exceptions.RayError as exc:
                raise OSError(f'Ray node {node.get("NodeID")} failed its probe: {exc}') from exc
        return results

    def _submit(self, batch):
        return self._task.remote(self._with_source(batch), self.cpus_per_worker, self.timeout)

    @staticmethod
    def _with_source(batch):
        return {**batch, 'source_git': git_provenance(PROJECT_ROOT)}

    def __call__(self, batches, _workers):
        batches = list(batches)
        self.verify_cluster(batches)
        queued = iter(batches)
        pending = {}

        def fill_window():
            while len(pending) < self.available_slots:
                try:
                    batch = next(queued)
                except StopIteration:
                    return
                pending[self._submit(batch)] = (batch, 1)

        # Submitting the entire run lets Ray grant leases to a slow node far ahead of
        # completion. A one-wave window keeps scheduling dynamic: whichever node frees a
        # slot first becomes eligible for the next batch.
        fill_window()
        while pending:
            ready, _ = self.ray.wait(list(pending), num_returns=1)
            ref = ready[0]
            batch, attempt = pending.pop(ref)
            try:
                result = self.ray.get(ref)
            except self.ray.exceptions.RayTaskError as exc:
                cause = exc.as_instanceof_cause()
                if isinstance(cause, OSError) and attempt < self.attempts:
                    pending[self._submit(batch)] = (batch, attempt + 1)
                    continue
                raise cause from exc
            except self.ray.exceptions.RayError as exc:
                if attempt < self.attempts:
                    pending[self._submit(batch)] = (batch, attempt + 1)
                    continue
                raise OSError(f'Ray infrastructure failed batch {batch["batch_id"]} '
                              f'after {attempt} attempts: {exc}') from exc
            fill_window()
            yield result


def connect(address='auto', *, cpus_per_worker=1, attempts=3, timeout=-1,
            working_dir=PROJECT_ROOT, strict_commit=True):
    """Connect to a private cluster and return a verified batch mapper."""
    try:
        import ray
    except ImportError as exc:  # pragma: no cover - depends on optional installation
        raise RuntimeError('Install the distributed harness extra: pip install .[distributed]') from exc
    if not ray.is_initialized():
        address = None if address in (None, 'local') else address
        runtime_env = None
        if working_dir is not None:
            runtime_env = {'working_dir': str(working_dir), 'excludes': DEFAULT_EXCLUDES,
                           'env_vars': {'PYTHONPATH': '.',
                                        'ARENA_HEAD_HOSTNAME': socket.gethostname()}}
        ray.init(address=address, runtime_env=runtime_env)
    return RayBatchMapper(ray, cpus_per_worker=cpus_per_worker, attempts=attempts,
                          timeout=timeout, strict_commit=strict_commit)
