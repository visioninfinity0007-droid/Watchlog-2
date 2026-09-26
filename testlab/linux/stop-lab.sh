#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
docker compose down
if ip link show watchlog-lab-shim >/dev/null 2>&1; then
  sudo ip link delete watchlog-lab-shim
fi
echo "WatchLog lab stopped."
