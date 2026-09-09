from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import ray_cluster


def test_auto_address_prefers_linux_tailscale(monkeypatch):
    monkeypatch.setattr(ray_cluster, 'tailscale_ip', lambda: '100.64.0.10')
    monkeypatch.setattr(ray_cluster, 'route_ip', lambda: '172.22.1.2')
    assert ray_cluster.node_ip() == '100.64.0.10'


def test_auto_address_refuses_wsl_nat(monkeypatch):
    monkeypatch.setattr(ray_cluster, 'tailscale_ip', lambda: None)
    monkeypatch.setattr(ray_cluster, 'route_ip', lambda: '172.22.1.2')
    monkeypatch.setattr(ray_cluster, 'is_wsl_nat', lambda address: True)
    with pytest.raises(SystemExit, match='WSL NAT'):
        ray_cluster.node_ip()


def test_explicit_address_must_be_private(monkeypatch):
    monkeypatch.setattr(ray_cluster.socket, 'gethostbyname', lambda value: '8.8.8.8')
    with pytest.raises(SystemExit, match='not a private'):
        ray_cluster.node_ip('public.example')


def test_head_and_worker_commands_are_deterministic(monkeypatch):
    monkeypatch.setattr(ray_cluster, 'node_ip', lambda requested: '100.64.0.10')
    monkeypatch.setattr(ray_cluster, 'available_cpus', lambda leave, requested=None: 14)
    head = ray_cluster.ray_command({'role': 'head', 'node_address': 'auto',
                                    'port': 6379, 'leave_cpus_free': 2})
    worker = ray_cluster.ray_command({'role': 'worker', 'node_address': 'auto',
                                      'head_host': 'pc-a.example.ts.net', 'port': 6379,
                                      'leave_cpus_free': 1})
    assert '--head' in head and '--include-dashboard=false' in head
    assert '--num-cpus=14' in head and '--node-ip-address=100.64.0.10' in head
    assert '--address=pc-a.example.ts.net:6379' in worker


def test_install_service_is_idempotent_and_uses_this_checkout(tmp_path, monkeypatch):
    root = tmp_path / 'checkout'
    python, ray = root / '.venv/bin/python', root / '.venv/bin/ray'
    python.parent.mkdir(parents=True)
    python.touch()
    ray.touch()
    unit = tmp_path / 'config/systemd/user/kaggriculture-ray.service'
    calls = []
    monkeypatch.setattr(ray_cluster, 'ROOT', root)
    monkeypatch.setattr(ray_cluster, 'PYTHON', python)
    monkeypatch.setattr(ray_cluster, 'RAY', ray)
    monkeypatch.setattr(ray_cluster, 'UNIT', unit)
    monkeypatch.setattr(ray_cluster, 'run', lambda command, **kwargs: calls.append(command))

    ray_cluster.install_service()
    ray_cluster.install_service()

    contents = unit.read_text()
    assert contents == ray_cluster.service_unit()
    assert f'WorkingDirectory={root}' in contents
    assert f'ExecStart="{python}" "{Path(ray_cluster.__file__).resolve()}" daemon' in contents
    assert calls[-3:] == [['systemctl', '--user', 'daemon-reload'],
                          ['systemctl', '--user', 'enable', ray_cluster.SERVICE],
                          ['systemctl', '--user', 'start', ray_cluster.SERVICE]]


def test_configure_persists_role_then_enables_service(tmp_path, monkeypatch):
    config = tmp_path / 'ray-cluster.json'
    enabled = []
    monkeypatch.setattr(ray_cluster, 'CONFIG', config)
    monkeypatch.setattr(ray_cluster, 'node_ip', lambda requested: '100.64.0.10')
    monkeypatch.setattr(ray_cluster, 'tailscale_dns_name',
                        lambda: 'pc-a.example.ts.net')
    monkeypatch.setattr(ray_cluster, 'install_service',
                        lambda **options: enabled.append(options))
    monkeypatch.setattr(ray_cluster, 'status_data', lambda value: {'role': value['role']})
    args = SimpleNamespace(node_address='auto', port=6379, leave_cpus_free=1,
                           num_cpus=None)

    ray_cluster.configure('head', args)

    assert ray_cluster.read_config()['role'] == 'head'
    assert ray_cluster.read_config()['advertised_head'] == 'pc-a.example.ts.net'
    assert enabled == [{'restart': True}]


def test_corrupt_or_incomplete_machine_config_is_refused(tmp_path, monkeypatch):
    config = tmp_path / 'ray-cluster.json'
    config.write_text('{"schema_version": 1, "role": "worker", "port": 6379, '
                      '"leave_cpus_free": 1, "node_address": "auto"}')
    monkeypatch.setattr(ray_cluster, 'CONFIG', config)
    with pytest.raises(SystemExit, match='no head host'):
        ray_cluster.read_config()


def test_explicit_cpu_capacity_overrides_the_reserve(monkeypatch):
    monkeypatch.setattr(ray_cluster.os, 'sched_getaffinity', lambda _pid: set(range(16)))
    assert ray_cluster.available_cpus(2, requested=9) == 9
    with pytest.raises(SystemExit, match='exceeds'):
        ray_cluster.available_cpus(1, requested=17)


def test_explicit_cpu_capacity_is_persisted_and_advertised(tmp_path, monkeypatch):
    config = tmp_path / 'ray-cluster.json'
    monkeypatch.setattr(ray_cluster, 'CONFIG', config)
    monkeypatch.setattr(ray_cluster, 'node_ip', lambda requested: '100.64.0.10')
    monkeypatch.setattr(ray_cluster, 'available_cpus',
                        lambda leave, requested=None: requested or 15)
    monkeypatch.setattr(ray_cluster, 'install_service', lambda **_options: None)
    monkeypatch.setattr(ray_cluster, 'status_data', lambda value: value)
    args = SimpleNamespace(node_address='auto', port=6379, leave_cpus_free=1,
                           num_cpus=8)

    ray_cluster.configure('head', args)

    saved = ray_cluster.read_config()
    assert saved['num_cpus'] == 8
    assert '--num-cpus=8' in ray_cluster.ray_command(saved)


@pytest.mark.parametrize(('value', 'expected'), [
    ('pc-a', ('pc-a', 6379)),
    ('pc-a:6380', ('pc-a', 6380)),
])
def test_worker_head_address_accepts_hostname_and_optional_port(value, expected):
    assert ray_cluster.normalize_head(value, 6379) == expected
