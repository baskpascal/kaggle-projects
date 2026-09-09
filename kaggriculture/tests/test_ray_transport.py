import os
from types import SimpleNamespace

import pytest

from arena.batch import make_batch
from arena.ray_transport import RayBatchMapper, _remote_batch


class RayError(Exception):
    pass


class RayTaskError(RayError):
    def __init__(self, cause):
        self.cause = cause

    def as_instanceof_cause(self):
        return self.cause


class Ref:
    def __init__(self, value=None, error=None):
        self.value, self.error = value, error


class Remote:
    def __init__(self, function, ray, options):
        self.function, self.ray, self.options_seen = function, ray, options

    def options(self, **options):
        return Remote(self.function, self.ray, {**self.options_seen, **options})

    def remote(self, *args):
        try:
            return Ref(value=self.function(*args))
        except Exception as exc:
            return Ref(error=RayTaskError(exc))


class FakeRay:
    exceptions = SimpleNamespace(RayError=RayError, RayTaskError=RayTaskError)
    util = SimpleNamespace(scheduling_strategies=SimpleNamespace(
        NodeAffinitySchedulingStrategy=lambda node_id, soft: (node_id, soft)))

    def __init__(self):
        self.remote_options = []
        self.wait_sizes = []

    def cluster_resources(self):
        return {'CPU': 4}

    def nodes(self):
        return [{'Alive': True, 'NodeID': 'node-a', 'Resources': {'CPU': 4}}]

    def remote(self, **options):
        self.remote_options.append(options)
        return lambda function: Remote(function, self, options)

    def get(self, value):
        if isinstance(value, list):
            return [self.get(item) for item in value]
        if value.error:
            raise value.error
        return value.value

    def wait(self, refs, num_returns=1):
        self.wait_sizes.append(len(refs))
        return refs[:num_returns], refs[num_returns:]


class SequenceTask:
    def __init__(self, refs):
        self.refs = iter(refs)
        self.calls = 0

    def remote(self, *args):
        self.calls += 1
        return next(self.refs)


class RecordingTask:
    def __init__(self):
        self.options_seen = []

    def options(self, **options):
        self.options_seen.append(options)
        return self

    def remote(self, batch, workers, timeout):
        return Ref(value={'workers': workers, 'timeout': timeout, 'batch': batch})


class CountingTask:
    def __init__(self):
        self.calls = 0

    def remote(self, *_args):
        self.calls += 1
        return Ref(value={'call': self.calls})


def test_mapper_disables_ray_retries_and_counts_cluster_slots():
    ray = FakeRay()
    mapper = RayBatchMapper(ray, cpus_per_worker=2)
    assert mapper.available_slots == 2
    assert ray.remote_options == [
        {'num_cpus': 2, 'max_retries': 0, 'retry_exceptions': False},
        {'num_cpus': 0, 'max_retries': 0, 'retry_exceptions': False},
    ]


def test_mapper_counts_schedulable_slots_per_node_without_cross_node_fragmentation():
    ray = FakeRay()
    ray.nodes = lambda: [
        {'Alive': True, 'NodeID': 'node-a', 'Resources': {'CPU': 15}},
        {'Alive': True, 'NodeID': 'node-b', 'Resources': {'CPU': 11}},
    ]
    mapper = RayBatchMapper(ray, cpus_per_worker=4)
    assert mapper.node_slots == {'node-a': 3, 'node-b': 2}
    assert mapper.available_slots == 5


def test_local_baseline_reserves_and_uses_each_nodes_full_capacity():
    ray = FakeRay()
    mapper = RayBatchMapper(ray, cpus_per_worker=1)
    task = RecordingTask()
    mapper._task = task
    batch = make_batch([{'candidate': 'pass', 'opponent': 'pass', 'seed': 1,
                         'seat': 0, 'backend': 'fast'}])

    [(node, result, workers)] = mapper.local_baseline_on_every_node(batch, timeout=9)

    assert node['NodeID'] == 'node-a'
    assert workers == result['workers'] == 4
    assert result['timeout'] == 9
    assert task.options_seen[0]['num_cpus'] == 4
    assert task.options_seen[0]['scheduling_strategy'] == ('node-a', False)


def test_local_baseline_can_target_one_selected_node():
    ray = FakeRay()
    mapper = RayBatchMapper(ray, cpus_per_worker=1)
    task = RecordingTask()
    mapper._task = task
    batch = make_batch([{'candidate': 'pass', 'opponent': 'pass', 'seed': 1,
                         'seat': 0, 'backend': 'fast'}])

    node, result, workers = mapper.local_baseline_on_node(batch, 'node-a')

    assert node['NodeID'] == 'node-a'
    assert result['workers'] == workers == 4
    assert task.options_seen[0]['scheduling_strategy'] == ('node-a', False)


def test_mapper_retries_a_system_failure_but_not_application_failure(monkeypatch):
    batch = make_batch([{'candidate': 'pass', 'opponent': 'pass', 'seed': 1,
                         'seat': 0, 'backend': 'fast'}])
    result = {'batch_id': batch['batch_id']}
    ray = FakeRay()
    mapper = RayBatchMapper(ray, attempts=2)
    monkeypatch.setattr(mapper, 'verify_cluster', lambda batches: [])
    task = SequenceTask([Ref(error=RayError('node died')), Ref(value=result)])
    mapper._task = task
    assert list(mapper([batch], 1)) == [result]
    assert task.calls == 2

    mapper._task = SequenceTask([Ref(error=RayTaskError(ValueError('bad batch')))])
    with pytest.raises(ValueError, match='bad batch'):
        list(mapper([batch], 1))
    assert mapper._task.calls == 1


def test_mapper_keeps_only_one_cluster_wave_in_flight(monkeypatch):
    ray = FakeRay()
    mapper = RayBatchMapper(ray)
    monkeypatch.setattr(mapper, 'verify_cluster', lambda batches: [])
    mapper._task = CountingTask()
    batches = [make_batch([{'candidate': 'pass', 'opponent': 'pass', 'seed': seed,
                            'seat': 0, 'backend': 'fast'}]) for seed in range(10)]

    assert len(list(mapper(batches, 1))) == 10
    assert mapper._task.calls == 10
    assert ray.wait_sizes[0] == mapper.available_slots
    assert max(ray.wait_sizes) == mapper.available_slots


def test_remote_jobs_cannot_leave_replay_results_on_worker_filesystems(monkeypatch):
    batch = make_batch([{'candidate': 'pass', 'opponent': 'pass', 'seed': 1,
                         'seat': 0, 'backend': 'fast', 'replay': '/shared/result.json'}])
    monkeypatch.delenv('ARENA_ROLE', raising=False)
    with pytest.raises(ValueError, match='cannot write replay'):
        _remote_batch(batch, 1, -1)
    assert 'ARENA_ROLE' not in os.environ


def test_remote_batch_marks_match_children_without_leaking_role(monkeypatch):
    batch = make_batch([{'candidate': 'pass', 'opponent': 'pass', 'seed': 1,
                         'seat': 0, 'backend': 'fast'}])
    observed = []

    def run_batch(spec, workers, timeout):
        observed.append((os.environ.get('ARENA_ROLE'), spec, workers, timeout))
        return {'complete': True}

    monkeypatch.delenv('ARENA_ROLE', raising=False)
    monkeypatch.setattr('arena.ray_transport.run_batch', run_batch)
    assert _remote_batch(batch, 2, 3) == {'complete': True}
    assert observed == [('ray-worker', batch, 2, 3)]
    assert 'ARENA_ROLE' not in os.environ
