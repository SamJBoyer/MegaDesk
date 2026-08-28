#!/usr/bin/env bash
# Start native Redis on each Cloud Agent boot. Idempotent.
set -euo pipefail

if redis-cli ping 2>/dev/null | grep -q PONG; then
  echo "==> redis already running"
  exit 0
fi

if command -v redis-server >/dev/null 2>&1; then
  if [[ -f /etc/redis/redis.conf ]]; then
    sudo redis-server /etc/redis/redis.conf --daemonize yes
  else
    redis-server --daemonize yes --bind 127.0.0.1 --protected-mode yes
  fi
else
  echo "error: redis-server is not installed; rerun .cursor/install.sh" >&2
  exit 1
fi

for _ in $(seq 1 50); do
  if redis-cli ping 2>/dev/null | grep -q PONG; then
    echo "==> redis ready"
    exit 0
  fi
  sleep 0.1
done

echo "error: redis did not become ready" >&2
exit 1
