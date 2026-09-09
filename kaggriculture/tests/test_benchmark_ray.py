import json

import pytest

from scripts.benchmark_ray import load_verification, write_report


def proof():
    return {
        'schema_version': 3,
        'paired_seats': True,
        'bit_exact': True,
        'money_binary64_exact': True,
        'deadline_passed_on_every_node': True,
        'worker_recovery_passed': True,
        'candidate': 'candidate.py',
        'opponent': 'opponent.py',
        'hostnames': ['pc-a', 'pc-b'],
        'driver_git': {'git_commit': 'abc123', 'git_dirty': False},
        'nodes': [
            {'hostname': host, 'python': '3.12.0', 'environment': {'engine': 'same'},
             'artifacts': {'candidate.py': 'candidate-hash',
                           'opponent.py': 'opponent-hash'}} for host in ('pc-a', 'pc-b')
        ],
        'jobs_per_node': 400,
        'seed_pairs': 200,
        'determinism_nodes': [
            {'hostname': host, 'relevant_result_sha256': 'results',
             'money_binary64_sha256': 'money'} for host in ('pc-a', 'pc-b')
        ],
        'worker_recovery_nodes': [
            {'hostname': host, 'attempts': 2} for host in ('pc-a', 'pc-b')
        ],
    }


def write(tmp_path, value):
    path = tmp_path / 'verification.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    return path


def load(path, **changes):
    arguments = {'candidate': 'candidate.py', 'opponent': 'opponent.py',
                 'environments': proof()['nodes'],
                 'current_git': {'git_commit': 'abc123', 'git_dirty': False}}
    arguments.update(changes)
    return load_verification(path, **arguments)


def test_benchmark_binds_the_exact_verification_artifact(tmp_path):
    path = write(tmp_path, proof())
    evidence = load(path)
    assert evidence['git_commit'] == 'abc123'
    assert evidence['hostnames'] == ['pc-a', 'pc-b']
    assert evidence['jobs_per_node'] == 400
    assert len(evidence['sha256']) == 64


def test_benchmark_accepts_integer_clean_flags_from_git_provenance(tmp_path):
    value = proof()
    value['driver_git']['git_dirty'] = 0
    evidence = load(
        write(tmp_path, value),
        current_git={'git_commit': 'abc123', 'git_dirty': 0},
    )
    assert evidence['git_commit'] == 'abc123'


def test_benchmark_checkpoint_replaces_the_previous_report_atomically(tmp_path):
    path = tmp_path / 'nested' / 'benchmark.json'
    write_report(path, {'status': 'running', 'workloads': [32]})
    write_report(path, {'status': 'complete', 'workloads': [32, 256]})
    assert json.loads(path.read_text()) == {
        'status': 'complete', 'workloads': [32, 256]}
    assert not path.with_name(path.name + '.tmp').exists()


@pytest.mark.parametrize('mutation, message', [
    (lambda value: value.update(money_binary64_exact=False), 'release gate'),
    (lambda value: value.update(candidate='other.py'), 'benchmark agents'),
    (lambda value: value.update(hostnames=['pc-a']), 'current cluster'),
    (lambda value: value.update(seed_pairs=1, jobs_per_node=2), '200 seeds'),
    (lambda value: value['driver_git'].update(git_commit='old'), 'git commit'),
    (lambda value: value['driver_git'].update(git_dirty=True), 'git commit'),
    (lambda value: value['nodes'][1]['artifacts'].update(
        **{'candidate.py': 'changed'}), 'artifacts changed'),
    (lambda value: value['nodes'][1].update(python='3.13.0'), 'python changed'),
    (lambda value: value['worker_recovery_nodes'].pop(), 'every current hostname'),
    (lambda value: value['determinism_nodes'][1].update(
        money_binary64_sha256='different'), 'digests'),
])
def test_stale_or_incomplete_verification_is_a_hard_failure(tmp_path, mutation, message):
    value = proof()
    mutation(value)
    with pytest.raises(SystemExit, match=message):
        load(write(tmp_path, value))
