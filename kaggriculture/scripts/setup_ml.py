"""Install PyTorch in a GPU-only environment and register it for Ray trainers."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT = Path.home() / '.local' / 'share' / 'kaggriculture' / 'ml-venv'
CONFIG = Path.home() / '.config' / 'kaggriculture' / 'ml-python'


def main():
    venv.EnvBuilder(with_pip=True, upgrade_deps=False).create(ENVIRONMENT)
    python = ENVIRONMENT / 'bin' / 'python'
    subprocess.run([str(python), '-m', 'pip', 'install',
                    '-r', str(ROOT / 'requirements-ml.lock')], check=True)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG.with_name(CONFIG.name + '.tmp')
    temporary.write_text(str(python.resolve()) + '\n', encoding='utf-8')
    os.chmod(temporary, 0o600)
    temporary.replace(CONFIG)
    subprocess.run([
        str(python), '-c',
        'import torch; '
        'assert torch.cuda.is_available(), "PyTorch cannot use CUDA on this node"; '
        'x=torch.ones((256,256), device="cuda"); '
        'value=float((x@x).sum().cpu()); '
        'print({"torch": str(torch.__version__), "cuda": str(torch.version.cuda), '
        '"device": torch.cuda.get_device_name(0), "smoke_value": value})'
    ], check=True)


if __name__ == '__main__':
    sys.exit(main())
