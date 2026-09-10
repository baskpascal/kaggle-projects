#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps kaggle-environments==1.32.7
for extra in "$@"; do
  case "$extra" in
    --distributed) .venv/bin/python -m pip install -r requirements-ray.lock ;;
    --ml) .venv/bin/python scripts/setup_ml.py ;;
    *) echo "unknown setup option: $extra" >&2; exit 2 ;;
  esac
done
.venv/bin/python -c 'from arena.engine import fingerprint; print(fingerprint())'
