#!/usr/bin/env bash
# Start native Redis and an Xvfb display on each Cloud Agent boot. Idempotent.
# Processes stay alive for the run; exports from this script do not — DISPLAY
# is also written into the image ENV / bashrc by the Dockerfile and install.sh.
set -euo pipefail

DISPLAY="${DISPLAY:-:99}"
export DISPLAY

start_xvfb() {
  if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    echo "==> Xvfb already running on ${DISPLAY}"
    return 0
  fi
  if ! command -v Xvfb >/dev/null 2>&1; then
    echo "warning: Xvfb is not installed; canvas tests need a desktop session" >&2
    return 0
  fi
  # 1280x800 matches the off-screen viewport in Docs/integration_testing.md.
  Xvfb "$DISPLAY" -screen 0 1280x800x24 -ac +extension GLX +render -noreset \
    >/tmp/xvfb.log 2>&1 &
  for _ in $(seq 1 50); do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
      echo "==> Xvfb ready on ${DISPLAY}"
      return 0
    fi
    sleep 0.1
  done
  echo "warning: Xvfb did not become ready; see /tmp/xvfb.log" >&2
}

start_redis() {
  # Port must match DEFAULT_REDIS_PORT in megadesk_contracts (not 6379).
  local port=6380
  if redis-cli -p "$port" ping 2>/dev/null | grep -q PONG; then
    echo "==> redis already running on ${port}"
    return 0
  fi

  if ! command -v redis-server >/dev/null 2>&1; then
    echo "error: redis-server is not installed; rerun .cursor/install.sh" >&2
    exit 1
  fi

  if [[ -f /etc/redis/redis.conf ]]; then
    sudo redis-server /etc/redis/redis.conf --daemonize yes --port "$port"
  else
    redis-server --daemonize yes --bind 127.0.0.1 --port "$port" --protected-mode yes
  fi

  for _ in $(seq 1 50); do
    if redis-cli -p "$port" ping 2>/dev/null | grep -q PONG; then
      echo "==> redis ready on ${port}"
      return 0
    fi
    sleep 0.1
  done

  echo "error: redis did not become ready on ${port}" >&2
  exit 1
}

start_xvfb
start_redis
