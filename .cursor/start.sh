#!/usr/bin/env bash
# Per-boot Redis for MegaDesk IPC (DB 0 ephemeral / DB 1 persistent).
# Native redis-server on localhost:6379; Docker is not required.
set -euo pipefail

if command -v redis-cli >/dev/null 2>&1 && redis-cli ping >/dev/null 2>&1; then
  exit 0
fi

if command -v service >/dev/null 2>&1; then
  sudo service redis-server start >/dev/null 2>&1 || true
fi

if ! command -v redis-cli >/dev/null 2>&1 || ! redis-cli ping >/dev/null 2>&1; then
  if ! command -v redis-server >/dev/null 2>&1; then
    echo "error: redis-server is not installed; run bash .cursor/install.sh" >&2
    exit 1
  fi
  redis-server --daemonize yes --bind 127.0.0.1 --port 6379 --protected-mode yes
fi

for _ in $(seq 1 50); do
  if redis-cli ping >/dev/null 2>&1; then
    exit 0
  fi
  sleep 0.1
done

echo "error: redis did not become ready on localhost:6379" >&2
exit 1
