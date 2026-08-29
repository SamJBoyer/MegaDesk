#!/usr/bin/env bash
# Idempotent Cloud Agent bootstrap: Miniforge, MEGADESK env, native Redis,
# PortAudio, and the X11/OpenGL stack Dear PyGui needs.
#
# On the baked `.cursor/Dockerfile` image this is a no-op for system packages
# and conda; it only editable-installs this checkout. On a stock Ubuntu VM it
# still does the full host-env setup.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_NAME="MEGADESK"

resolve_conda_root() {
  if [[ -n "${CONDA_ROOT:-}" && -x "${CONDA_ROOT}/bin/conda" ]]; then
    printf '%s\n' "$CONDA_ROOT"
    return 0
  fi
  local candidate
  for candidate in \
      "${HOME}/miniconda3" \
      /opt/conda \
      "${HOME}/anaconda3" \
      /usr/local/anaconda3
  do
    if [[ -x "${candidate}/bin/conda" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  printf '%s\n' "${HOME}/miniconda3"
}

conda_root_exists() {
  [[ -x "$(resolve_conda_root)/bin/conda" ]]
}

megadesk_python() {
  local prefix candidate
  if [[ -n "${CONDA_PREFIX:-}" && "$(basename "${CONDA_PREFIX}")" == "${ENV_NAME}" ]]; then
    candidate="${CONDA_PREFIX}/bin/python"
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi
  for prefix in \
      "$(resolve_conda_root)/envs/${ENV_NAME}" \
      "${HOME}/miniconda3/envs/${ENV_NAME}" \
      /opt/conda/envs/${ENV_NAME} \
      "${HOME}/anaconda3/envs/${ENV_NAME}"
  do
    candidate="${prefix}/bin/python"
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

apt_missing() {
  local pkg
  for pkg in "$@"; do
    if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed'; then
      printf '%s\n' "$pkg"
    fi
  done
}

export DEBIAN_FRONTEND=noninteractive

mapfile -t missing < <(apt_missing \
  redis-server \
  libportaudio2 \
  xvfb \
  x11-utils \
  libgl1 \
  libglib2.0-0 \
  libx11-6 \
  libxext6 \
  libxrender1 \
  libxi6 \
  libxrandr2 \
  libxinerama1 \
  libxcursor1 \
)

if ((${#missing[@]})); then
  sudo apt-get update -qq
  sudo apt-get install -y --no-install-recommends "${missing[@]}"
fi

CONDA_ROOT="$(resolve_conda_root)"

if ! conda_root_exists; then
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

if ! megadesk_python >/dev/null; then
  conda create -y -n "$ENV_NAME" python=3.13
fi

conda activate "$ENV_NAME"

if ! grep -q "conda activate ${ENV_NAME}" "${HOME}/.bashrc" 2>/dev/null; then
  printf '\n# MegaDesk Cloud Agent\nconda activate %s 2>/dev/null || true\n' "$ENV_NAME" >> "${HOME}/.bashrc"
fi
if ! grep -q 'DISPLAY=' "${HOME}/.bashrc" 2>/dev/null; then
  printf 'export DISPLAY="${DISPLAY:-:99}"\n' >> "${HOME}/.bashrc"
fi

python "${ROOT}/scripts/refresh_contracts.py"
python "${ROOT}/scripts/refresh_nodes.py"
python -m pip install -r "${ROOT}/requirements-dev.txt"

echo "==> cloud install complete ($(python -V) via ${ENV_NAME})"
