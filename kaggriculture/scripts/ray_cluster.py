#!/usr/bin/env python3
"""Configure and keep the private two-PC Ray cluster connected without a dashboard."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / '.venv/bin/python'
RAY = ROOT / '.venv/bin/ray'
SETUP = ROOT / 'scripts/setup.sh'
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / \
    'kaggriculture/ray-cluster.json'
UNIT = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / \
    'systemd/user/kaggriculture-ray.service'
SERVICE = 'kaggriculture-ray.service'


def run(command, *, check=True, capture=False, timeout=None):
    return subprocess.run(command, check=check, text=True, timeout=timeout,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def command_output(command):
    try:
        return run(command, check=False, capture=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def tailscale_ip():
    executable = shutil.which('tailscale')
    if not executable:
        return None
    values = command_output([executable, 'ip', '-4']).splitlines()
    return values[0].strip() if values else None


def tailscale_dns_name():
    executable = shutil.which('tailscale')
    if not executable:
        return None
    try:
        status = json.loads(command_output([executable, 'status', '--json']))
    except json.JSONDecodeError:
        return None
    return status.get('Self', {}).get('DNSName', '').rstrip('.') or None


def route_ip():
    output = command_output(['ip', '-4', 'route', 'get', '1.1.1.1'])
    fields = output.split()
    return fields[fields.index('src') + 1] if 'src' in fields else None


def is_wsl_nat(address):
    if 'microsoft' not in os.uname().release.lower():
        return False
    try:
        return ipaddress.ip_address(address) in ipaddress.ip_network('172.16.0.0/12')
    except ValueError:
        return False


def node_ip(requested='auto'):
    if requested != 'auto':
        try:
            address = socket.gethostbyname(requested)
        except socket.gaierror as exc:
            raise SystemExit(f'Cannot resolve node address {requested}: {exc}') from exc
        parsed = ipaddress.ip_address(address)
        tailscale = parsed in ipaddress.ip_network('100.64.0.0/10')
        if (parsed.is_loopback or parsed.is_unspecified or parsed.is_multicast
                or not (parsed.is_private or tailscale)):
            raise SystemExit(f'Node address {address} is not a private LAN/Tailscale address')
        return address
    address = tailscale_ip() or route_ip()
    if not address:
        raise SystemExit('No private node address found')
    if is_wsl_nat(address):
        raise SystemExit(
            f'Auto-detected {address}, which is WSL NAT and is normally unreachable from '
            'another PC. Install/connect Tailscale inside WSL or enable WSL mirrored '
            'networking, then run configure again.')
    return address


def normalize_head(value, port):
    if '://' in value:
        raise SystemExit('--head is HOST or HOST:PORT, without a URL scheme')
    if value.count(':') == 1:
        host, raw_port = value.rsplit(':', 1)
        try:
            parsed_port = int(raw_port)
        except ValueError as exc:
            raise SystemExit(f'Invalid head port: {raw_port}') from exc
        if parsed_port not in range(1, 65536):
            raise SystemExit(f'Invalid head port: {raw_port}')
        return host, parsed_port
    return value, port


def available_cpus(leave_free, requested=None):
    count = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') \
        else (os.cpu_count() or 1)
    if requested is not None:
        if type(requested) is not int or requested < 1:
            raise SystemExit('--num-cpus must be a positive integer')
        if requested > count:
            raise SystemExit(f'--num-cpus={requested} exceeds the {count} CPUs available '
                             'to this process')
        return requested
    return max(1, count - leave_free)


def validate_config(value):
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise SystemExit('Ray cluster config has an unsupported schema')
    if value.get('role') not in ('head', 'worker'):
        raise SystemExit('Ray cluster role must be head or worker')
    port, leave = value.get('port'), value.get('leave_cpus_free')
    if type(port) is not int or port not in range(1, 65536):
        raise SystemExit('Ray cluster config has an invalid port')
    if type(leave) is not int or leave < 0:
        raise SystemExit('Ray cluster config has an invalid CPU reserve')
    requested = value.get('num_cpus')
    if requested is not None and (type(requested) is not int or requested < 1):
        raise SystemExit('Ray cluster config has an invalid explicit CPU capacity')
    if not isinstance(value.get('node_address'), str) or not value['node_address']:
        raise SystemExit('Ray cluster config has no node address')
    if value['role'] == 'worker' and not value.get('head_host'):
        raise SystemExit('Ray worker config has no head host')
    return value


def read_config():
    try:
        value = json.loads(CONFIG.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise SystemExit(f'Cluster is not configured. Run {sys.argv[0]} configure-head '
                         'on PC A or configure-worker on PC B.') from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f'Invalid cluster config {CONFIG}: {exc}') from exc
    return validate_config(value)


def write_config(value):
    validate_config(value)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temporary.chmod(0o600)
    temporary.replace(CONFIG)


def unit_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def unit_path(value):
    """Escape a path for scalar systemd directives such as WorkingDirectory."""
    safe = '/._-:abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    return ''.join(character if character in safe else
                   ''.join(f'\\x{byte:02x}' for byte in character.encode('utf-8'))
                   for character in str(value)).replace('%', '%%')


def service_unit():
    return f'''[Unit]
Description=Kaggriculture private Ray node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={unit_path(ROOT)}
ExecStart={unit_quote(PYTHON)} {unit_quote(Path(__file__).resolve())} daemon
ExecStop={unit_quote(RAY)} stop --force
Restart=always
RestartSec=15
TimeoutStopSec=30
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
'''


def install_service(*, restart=False):
    if not PYTHON.exists() or not RAY.exists():
        run(['bash', str(SETUP), '--distributed'])
    UNIT.parent.mkdir(parents=True, exist_ok=True)
    UNIT.write_text(service_unit(), encoding='utf-8')
    run(['systemctl', '--user', 'daemon-reload'])
    run(['systemctl', '--user', 'enable', SERVICE])
    run(['systemctl', '--user', 'restart' if restart else 'start', SERVICE])


def configure(role, args):
    address = node_ip(args.node_address)
    config = {'schema_version': 1, 'role': role, 'node_address': args.node_address,
              'resolved_node_ip': address, 'port': args.port,
              'leave_cpus_free': args.leave_cpus_free}
    if args.num_cpus is not None:
        # Validate against this machine before persisting a service that would restart
        # forever with an impossible resource declaration.
        available_cpus(args.leave_cpus_free, args.num_cpus)
        config['num_cpus'] = args.num_cpus
    if role == 'worker':
        host, port = normalize_head(args.head, args.port)
        config.update(head_host=host, port=port)
    else:
        config['advertised_head'] = tailscale_dns_name() or address
    write_config(config)
    install_service(restart=True)
    print(json.dumps(status_data(config), indent=2))
    if role == 'head':
        print('\nNo PC B, execute uma vez:')
        print(f'  python3 scripts/ray_cluster.py configure-worker '
              f'--head {config["advertised_head"]}:{args.port}')


def ray_command(config):
    address = node_ip(config.get('node_address', 'auto'))
    cpus = available_cpus(config.get('leave_cpus_free', 1), config.get('num_cpus'))
    common = [str(RAY), 'start', '--block', f'--node-ip-address={address}',
              f'--num-cpus={cpus}']
    if config['role'] == 'head':
        return common + ['--head', f'--port={config["port"]}',
                         '--include-dashboard=false']
    return common + [f'--address={config["head_host"]}:{config["port"]}']


def daemon():
    os.execv(str(RAY), ray_command(read_config()))


def service_state():
    result = command_output(['systemctl', '--user', 'is-active', SERVICE])
    return result or 'unknown'


def tcp_reachable(host, port):
    try:
        with socket.create_connection((host, port), timeout=.5):
            return True
    except OSError:
        return False


def status_data(config=None):
    config = config or read_config()
    if config['role'] == 'head':
        try:
            host = node_ip(config.get('node_address', 'auto'))
        except SystemExit:
            host = config.get('resolved_node_ip')
    else:
        host = config['head_host']
    return {'configured': True, 'role': config['role'], 'config': str(CONFIG),
            'advertised_cpus': available_cpus(config.get('leave_cpus_free', 1),
                                              config.get('num_cpus')),
            'capacity_policy': ('explicit' if config.get('num_cpus') is not None
                                else 'available-minus-reserve'),
            'service': service_state(), 'head': f'{host}:{config["port"]}',
            'peer_head': f'{config.get("advertised_head", host)}:{config["port"]}',
            'head_reachable': bool(host and tcp_reachable(host, config['port']))}


def ensure():
    config = read_config()
    install_service()
    print(json.dumps(status_data(config), indent=2))


def doctor():
    route = route_ip()
    tailnet = tailscale_ip()
    windows_tailscale = Path('/mnt/c/Program Files/Tailscale/tailscale.exe').exists()
    data = {'wsl': 'microsoft' in os.uname().release.lower(),
            'linux_tailscale_ip': tailnet, 'route_ip': route,
            'route_is_wsl_nat': bool(route and is_wsl_nat(route)),
            'windows_tailscale_installed': windows_tailscale,
            'automatic_address_usable': bool(tailnet or
                                             (route and not is_wsl_nat(route)))}
    data['next_step'] = ('run configure-head/configure-worker'
                         if data['automatic_address_usable'] else
                         'connect Tailscale inside this WSL or enable WSL mirrored, '
                         'then run doctor again')
    print(json.dumps(data, indent=2))
    return 0 if data['automatic_address_usable'] else 2


def stop():
    run(['systemctl', '--user', 'disable', '--now', SERVICE], check=False)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest='command', required=True)
    for name in ('configure-head', 'configure-worker'):
        command = commands.add_parser(name)
        command.add_argument('--node-address', default='auto',
                             help='private/Tailscale address; auto refuses WSL NAT')
        command.add_argument('--port', type=int, default=6379)
        command.add_argument('--leave-cpus-free', type=int, default=1)
        command.add_argument('--num-cpus', type=int,
                             help='explicit effective Ray slots for this machine; '
                                  'overrides automatic CPUs minus reserve')
        if name == 'configure-worker':
            command.add_argument('--head', required=True, help='head HOST or HOST:PORT')
    commands.add_parser('ensure')
    commands.add_parser('status')
    commands.add_parser('doctor')
    commands.add_parser('daemon')
    commands.add_parser('stop')
    return result


def main():
    args = parser().parse_args()
    if getattr(args, 'port', 6379) not in range(1, 65536):
        raise SystemExit('Port must be between 1 and 65535')
    if getattr(args, 'leave_cpus_free', 1) < 0:
        raise SystemExit('--leave-cpus-free cannot be negative')
    if getattr(args, 'num_cpus', None) is not None and args.num_cpus < 1:
        raise SystemExit('--num-cpus must be positive')
    if args.command == 'configure-head':
        configure('head', args)
    elif args.command == 'configure-worker':
        configure('worker', args)
    elif args.command == 'ensure':
        ensure()
    elif args.command == 'status':
        print(json.dumps(status_data(), indent=2))
    elif args.command == 'doctor':
        raise SystemExit(doctor())
    elif args.command == 'daemon':
        daemon()
    elif args.command == 'stop':
        stop()


if __name__ == '__main__':
    main()
