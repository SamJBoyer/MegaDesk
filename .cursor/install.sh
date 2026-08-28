#!/usr/bin/env bash
# Idempotent Cloud Agent bootstrap: Miniforge, MEGADESK env, native Redis.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CONDA_ROOT="${HOME}/miniconda3"
ENV_NAME="MEGADESK"

export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends redis-server libportaudio2

if [[ ! -x "${CONDA_ROOT}/bin/conda" ]]; then
  installer="/tmp/miniforge.sh"
  curl -fsSL -o "$installer" \
    "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh"
  bash "$installer" -b -p "$CONDA_ROOT"
  rm -f "$installer"
fi

# shellcheck source=/dev/null
source "${CONDA_ROOT}/etc/profile.d/conda.sh"
conda config --set auto_activate false
conda init bash >/dev/null

if [[ ! -x "${CONDA_ROOT}/envs/${ENV_NAME}/bin/python" ]]; then
  conda create -y -n "$ENV_NAME" python=3.13
fi

conda activate "$ENV_NAME"

if ! grep -q "conda activate ${ENV_NAME}" "${HOME}/.bashrc" 2>/dev/null; then
  printf '\n# MegaDesk Cloud Agent\nconda activate %s 2>/dev/null || true\n' "$ENV_NAME" >> "${HOME}/.bashrc"
fi

python "${ROOT}/scripts/refresh_contracts.py"
python "${ROOT}/scripts/refresh_nodes.py"
python -m pip install -r "${ROOT}/requirements-dev.txt"

echo "==> cloud install complete ($(python -V) via ${ENV_NAME})"
