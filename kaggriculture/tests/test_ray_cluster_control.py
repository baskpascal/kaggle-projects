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
    monkeypatch.setattr(ray_cluster, 'persist_across_reboots', lambda: {})

    ray_cluster.install_service()
    ray_cluster.install_service()

    contents = unit.read_text()
    assert contents == ray_cluster.service_unit()
    assert f'WorkingDirectory={root}' in contents
    assert f'ExecStart="{python}" "{Path(ray_cluster.__file__).resolve()}" daemon' in contents
    assert calls[-3:] == [['systemctl', '--user', 'daemon-reload'],
                          ['systemctl', '--user', 'enable', ray_cluster.SERVICE],
                          ['systemctl', '--user', 'start', ray_cluster.SERVICE]]


def test_changed_controller_restarts_the_service(tmp_path, monkeypatch):
    root = tmp_path / 'checkout'
    python, ray = root / '.venv/bin/python', root / '.venv/bin/ray'
    python.parent.mkdir(parents=True)
    python.touch()
    ray.touch()
    unit = tmp_path / 'config/systemd/user/kaggriculture-ray.service'
    unit.parent.mkdir(parents=True)
    unit.write_text('old controller')
    calls = []
    monkeypatch.setattr(ray_cluster, 'ROOT', root)
    monkeypatch.setattr(ray_cluster, 'PYTHON', python)
    monkeypatch.setattr(ray_cluster, 'RAY', ray)
    monkeypatch.setattr(ray_cluster, 'UNIT', unit)
    monkeypatch.setattr(ray_cluster, 'run', lambda command, **kwargs: calls.append(command))
    monkeypatch.setattr(ray_cluster, 'persist_across_reboots', lambda: {})

    ray_cluster.install_service()

    assert calls[-1] == ['systemctl', '--user', 'restart', ray_cluster.SERVICE]


def test_raylet_health_check_matches_the_local_node_address(tmp_path):
    other = tmp_path / '100'
    other.mkdir()
    other.joinpath('comm').write_text('raylet\n')
    other.joinpath('cmdline').write_bytes(b'raylet\0--node_ip_address=100.64.0.2\0')
    local = tmp_path / '101'
    local.mkdir()
    local.joinpath('comm').write_text('raylet\n')
    local.joinpath('cmdline').write_bytes(b'raylet\0--node_ip_address=100.64.0.1\0')

    assert ray_cluster.raylet_alive('100.64.0.1', tmp_path)
    assert not ray_cluster.raylet_alive('100.64.0.3', tmp_path)


def test_supervisor_exits_when_raylet_dies_but_blocking_parent_survives(monkeypatch):
    class Process:
        returncode = None
        terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout):
            return self.returncode

    process = Process()
    moments = iter((0, 61))
    monkeypatch.setattr(ray_cluster, 'node_ip', lambda _requested: '100.64.0.1')
    monkeypatch.setattr(ray_cluster, 'ray_command', lambda _config: ['ray', 'start'])
    monkeypatch.setattr(ray_cluster.subprocess, 'Popen', lambda _command: process)
    monkeypatch.setattr(ray_cluster.time, 'monotonic', lambda: next(moments))
    monkeypatch.setattr(ray_cluster.time, 'sleep', lambda _seconds: None)
    monkeypatch.setattr(ray_cluster, 'raylet_alive', lambda _address: False)

    assert ray_cluster.supervise_ray({'node_address': 'auto'}) == 1
    assert process.terminated


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


def test_installing_the_service_also_makes_it_survive_a_logout(tmp_path, monkeypatch):
    root = tmp_path / 'checkout'
    (root / '.venv/bin').mkdir(parents=True)
    (root / '.venv/bin/python').touch()
    (root / '.venv/bin/ray').touch()
    persisted = []
    monkeypatch.setattr(ray_cluster, 'ROOT', root)
    monkeypatch.setattr(ray_cluster, 'PYTHON', root / '.venv/bin/python')
    monkeypatch.setattr(ray_cluster, 'RAY', root / '.venv/bin/ray')
    monkeypatch.setattr(ray_cluster, 'UNIT', tmp_path / 'unit/kaggriculture-ray.service')
    monkeypatch.setattr(ray_cluster, 'run', lambda command, **kwargs: None)
    monkeypatch.setattr(ray_cluster, 'persist_across_reboots',
                        lambda: persisted.append(True))

    ray_cluster.install_service()

    assert persisted == [True]


def test_linger_reports_the_manual_command_when_polkit_refuses(monkeypatch):
    monkeypatch.setenv('USER', 'lucas')
    monkeypatch.setattr(ray_cluster.shutil, 'which', lambda _name: '/usr/bin/loginctl')
    monkeypatch.setattr(ray_cluster, 'command_output', lambda _command: 'Linger=no')
    monkeypatch.setattr(ray_cluster, 'run',
                        lambda command, **kwargs: SimpleNamespace(returncode=1))

    assert ray_cluster.enable_linger() == \
        'needs one manual command: sudo loginctl enable-linger lucas'


def test_linger_already_enabled_is_not_requested_again(monkeypatch):
    monkeypatch.setenv('USER', 'lucas')
    monkeypatch.setattr(ray_cluster.shutil, 'which', lambda _name: '/usr/bin/loginctl')
    monkeypatch.setattr(ray_cluster, 'command_output', lambda _command: 'Linger=yes')
    monkeypatch.setattr(ray_cluster, 'run', lambda command, **kwargs: pytest.fail(
        'enable-linger must not run when the user already lingers'))

    assert ray_cluster.enable_linger() == 'enabled'


def test_keepalive_launcher_starts_the_distribution_hidden():
    script = ray_cluster.keepalive_script('Ubuntu', 'lucas')

    assert 'wsl.exe -d Ubuntu -u lucas' in script
    assert 'exec sleep infinity' in script
    # The trailing 0 is what keeps a console window from flashing at every logon.
    assert script.rstrip().endswith(', 0, False')


def test_keepalive_is_skipped_outside_wsl(monkeypatch):
    monkeypatch.setattr(ray_cluster, 'in_wsl', lambda: False)
    monkeypatch.setattr(ray_cluster, 'run', lambda command, **kwargs: pytest.fail(
        'no Windows tool may run on a machine that is not WSL'))

    assert ray_cluster.install_wsl_keepalive() == 'not-wsl'


def test_localized_windows_output_is_read_instead_of_raising():
    # schtasks.exe answers in the console OEM codepage; cp850 'ã' is not valid UTF-8.
    result = ray_cluster.run(['sh', '-c', r'printf "ERRO: n\306o encontrado"'],
                             check=False, capture=True)

    assert result.returncode == 0
    assert result.stdout.startswith('ERRO: n') and result.stdout.endswith('encontrado')
