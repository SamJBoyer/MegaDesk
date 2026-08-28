#!/usr/bin/env bash
# Idempotent Cloud Agent bootstrap: Miniconda, MEGADESK env, editable MegaDesk
# packages, and a native Redis server (no Docker). Processes started here are
# not expected to survive; Redis is started by .cursor/start.sh on each boot.
set -euo pipefail

CONDA_ROOT="${HOME}/miniconda3"
ENV_NAME="MEGADESK"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  redis-server \
  build-essential \
  curl \
  ca-certificates \
  libgl1 \
  libglib2.0-0 \
  libx11-6 \
  libxext6 \
  libxrender1 \
  libxi6 \
  portaudio19-dev \
  pkg-config

if [[ ! -x "${CONDA_ROOT}/bin/conda" ]]; then
  installer="/tmp/miniforge.sh"
  curl -fsSL -o "${installer}" \
    "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-$(uname -s)-$(uname -m).sh"
  bash "${installer}" -b -p "${CONDA_ROOT}"
  rm -f "${installer}"
fi

# shellcheck disable=SC1091
source "${CONDA_ROOT}/etc/profile.d/conda.sh"

if [[ ! -x "${CONDA_ROOT}/envs/${ENV_NAME}/bin/python" ]]; then
  conda create -y -n "${ENV_NAME}" python=3.13
fi

conda activate "${ENV_NAME}"

python -m pip install -U pip
python scripts/refresh_contracts.py
python scripts/refresh_nodes.py
python -m pip install -r requirements-dev.txt

# Login/interactive shells get `conda` and MEGADESK without extra PATH hacks.
# refresh_nodes.py already looks in ~/miniconda3/envs/MEGADESK.
"${CONDA_ROOT}/bin/conda" init bash >/dev/null
bashrc="${HOME}/.bashrc"
marker="# MegaDesk conda (cloud environment)"
if [[ -f "${bashrc}" ]] && ! grep -qF "${marker}" "${bashrc}"; then
  cat >> "${bashrc}" <<EOF

${marker}
if [ -x "\${HOME}/miniconda3/bin/conda" ]; then
  eval "\$("\${HOME}/miniconda3/bin/conda" shell.bash hook)"
  conda activate ${ENV_NAME} >/dev/null 2>&1 || true
fi
EOF
fi

sudo tee /etc/profile.d/megadesk-conda.sh >/dev/null <<'EOF'
if [ -n "${HOME:-}" ] && [ -x "${HOME}/miniconda3/bin/conda" ]; then
  # shellcheck disable=SC1091
  . "${HOME}/miniconda3/etc/profile.d/conda.sh"
  conda activate MEGADESK >/dev/null 2>&1 || true
fi
EOF
