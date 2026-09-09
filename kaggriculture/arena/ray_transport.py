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
# `data/` is the downloaded Kaggle corpus: episodes, leaderboards and replay dumps that
# this checkout accumulates and that nothing inside a match ever opens. It reached 6.1 GiB
# and pushed the runtime_env package past Ray's 512 MiB ceiling, which made every
# distributed run fail at `ray.init` before a single batch was scheduled. It is excluded
# here rather than moved: the corpus belongs on disk, on the head, where the head-side
# scripts that analyse it can still read it.
DEFAULT_EXCLUDES = ['/.git/', '/.venv/', '/.tmp/', '/docs/', '/tests/', '/replays/',
                    '/data/', '/seed_registry.json',
                    '/experiments/results/', '/experiments/searches/', '**/__pycache__/']
# What a worker genuinely opens while running a match: the agent under test, the engine
# and arena around it, the opponent artifacts and the frozen versions a spec can name.
# An exclude that swallowed any of these would not fail at `ray.init`; it would fail
# deep inside a remote match, so the packing check below refuses it up front instead.
REQUIRED_ROOTS = ('agent', 'arena', 'eval', 'experiments', 'opponents', 'scripts',
                  'submission', 'versions', 'config.yaml', 'pyproject.toml')
# Ray refuses a working_dir package larger than this, and the refusal arrives as an
# opaque RuntimeEnvSetupError. Measuring first turns it into a sentence that names the
# directory that grew.
PACKAGE_LIMIT_BYTES = 512 * 1024 * 1024
DEFAULT_CPUS_PER_WORKER = 4


def _excluded(relative, excludes):
    """Match the two exclude shapes this module uses: a rooted path, and `**/name/`."""
    parts = relative.split('/')
    for pattern in excludes:
        name = pattern.strip('/')
        if pattern.startswith('**/'):
            if name[3:] in parts:
                return True
        elif relative == name or relative.startswith(name + '/'):
            return True
    return False


def package_size(working_dir=PROJECT_ROOT, excludes=DEFAULT_EXCLUDES):
    """Bytes Ray would actually ship, walking without descending into what is excluded."""
    root = Path(working_dir)
    total = 0
    stack = [root]
    while stack:
        for entry in stack.pop().iterdir():
            relative = entry.relative_to(root).as_posix()
            if _excluded(relative, excludes):
                continue
            if entry.is_symlink():
                continue
            if entry.is_dir():
                stack.append(entry)
            elif entry.is_file():
                total += entry.stat().st_size
    return total


def check_package(working_dir=PROJECT_ROOT, excludes=DEFAULT_EXCLUDES,
                  required=REQUIRED_ROOTS, limit=PACKAGE_LIMIT_BYTES):
    """Refuse to ship a package that is too large, or one missing what a match needs."""
    root = Path(working_dir)
    for name in required:
        if not (root / name).exists():
            continue
        if _excluded(name, excludes):
            raise ValueError(f'{name!r} is excluded from the Ray package, but a remote '
                             f'match opens it; remove it from the exclude list')
    size = package_size(root, excludes)
    if size > limit:
        largest = sorted(((package_size(entry, excludes), entry.name)
                          for entry in root.iterdir() if entry.is_dir()
                          and not _excluded(entry.name, excludes)), reverse=True)[:3]
        biggest = ', '.join(f'{name} {value / 1048576:.0f}MiB' for value, name in largest)
        raise OSError(f'The Ray working_dir package is {size / 1048576:.1f}MiB, over the '
                      f'{limit / 1048576:.0f}MiB limit. Largest included: {biggest}. '
                      f'Exclude what a remote match does not open; do not move a dataset '
                      f'into the package.')
    return size


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
        self.node_slots = {
            node['NodeID']: self._node_capacity(node) // cpus_per_worker
            for node in ray.nodes() if node.get('Alive')
        }
        self.available_slots = sum(self.node_slots.values())
        if self.available_slots < 1:
            raise ValueError(f'No live Ray node can reserve {cpus_per_worker} CPUs')
        self._task = ray.remote(num_cpus=cpus_per_worker, max_retries=0,
                                retry_exceptions=False)(_remote_batch)
        self._probe = ray.remote(num_cpus=0, max_retries=0,
                                 retry_exceptions=False)(_environment)
        self.nodes = []

    def describe_nodes(self):
        """Ask every live node who it is, so a run can record where it actually ran."""
        strategy = self.ray.util.scheduling_strategies.NodeAffinitySchedulingStrategy
        alive = [node for node in self.ray.nodes() if node.get('Alive')]
        try:
            refs = [self._probe.options(scheduling_strategy=strategy(
                    node['NodeID'], soft=False)).remote(())
                    for node in alive]
            evidence = self.ray.get(refs)
        except Exception as exc:
            raise OSError(f'Could not reach every Ray node: {exc}') from exc
        return sorted(({'node_id': node['NodeID'], 'hostname': probe['hostname'],
                        'slots': self.node_slots.get(node['NodeID'], 0)}
                       for node, probe in zip(alive, evidence)),
                      key=lambda entry: entry['hostname'])

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

    def local_baseline_on_node(self, batch, node_id, *, timeout=None,
                               maximum_workers=None):
        """Measure one previously selected baseline host without repeating every host."""
        if maximum_workers is not None and maximum_workers < 1:
            raise ValueError('maximum_workers must be positive')
        matches = [node for node in self.ray.nodes()
                   if node.get('Alive') and node['NodeID'] == node_id]
        if not matches:
            raise OSError(f'Ray baseline node is no longer alive: {node_id}')

        def workers(node):
            capacity = self._node_capacity(node)
            return capacity if maximum_workers is None else min(capacity, maximum_workers)

        return self._run_on_nodes(batch, matches, timeout=timeout, workers=workers)[0]

    @staticmethod
    def _node_capacity(node):
        return int(node.get('Resources', {}).get('CPU', 0))

    def _run_on_every_node(self, batch, *, timeout, workers):
        nodes = [node for node in self.ray.nodes() if node.get('Alive')]
        return self._run_on_nodes(batch, nodes, timeout=timeout, workers=workers)

    def _run_on_nodes(self, batch, nodes, *, timeout, workers):
        strategy = self.ray.util.scheduling_strategies.NodeAffinitySchedulingStrategy
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
            # Measured before `ray.init`, so an oversized checkout is named here instead
            # of surfacing as an opaque RuntimeEnvSetupError from inside Ray.
            size = check_package(working_dir)
            print(f'ray working_dir package: {size / 1048576:.1f}MiB', flush=True)
            runtime_env = {'working_dir': str(working_dir), 'excludes': DEFAULT_EXCLUDES,
                           'env_vars': {'PYTHONPATH': '.',
                                        'ARENA_HEAD_HOSTNAME': socket.gethostname()}}
        ray.init(address=address, runtime_env=runtime_env)
    return RayBatchMapper(ray, cpus_per_worker=cpus_per_worker, attempts=attempts,
                          timeout=timeout, strict_commit=strict_commit)


def require_cluster(address='auto', *, minimum_hosts=2, **options):
    """Connect only to a real multi-machine cluster, and fail loudly when it is missing.

    ``connect`` is deliberately permissive: it will happily return a mapper backed by this
    host alone, which is the same thing as not distributing at all. An operator who asked
    for the cluster must never silently get a local run instead -- the numbers would be
    honest but they would not be the run that was requested, and nothing downstream would
    say so. So this refuses anything that is not at least ``minimum_hosts`` distinct
    hostnames, and returns the mapper together with the nodes it verified.
    """
    if minimum_hosts < 1:
        raise ValueError('minimum_hosts must be positive')
    mapper = connect(address, **options)
    nodes = mapper.describe_nodes()
    hosts = sorted({node['hostname'] for node in nodes})
    if len(hosts) < minimum_hosts:
        raise OSError(
            f'--distributed needs at least {minimum_hosts} machines in the Ray cluster; '
            f'only {len(hosts)} answered ({", ".join(hosts) or "none"}). Start the other '
            f'node (scripts/ray_cluster.py ensure) or drop the flag to run locally.')
    return mapper, nodes
