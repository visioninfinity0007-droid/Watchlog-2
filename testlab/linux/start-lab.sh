#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARENT="${LAB_PARENT_IFACE:-eth1}"
VM_IP="${LAB_VM_IP:-10.77.0.2/24}"
SHIM="${LAB_SHIM_IFACE:-watchlog-lab-shim}"
SHIM_IP="${LAB_SHIM_IP:-10.77.0.254/32}"
ROUTE="${LAB_CONTAINER_ROUTE:-10.77.0.0/24}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install Docker Engine + Compose plugin first." >&2
  exit 2
fi

sudo ip link set "$PARENT" up
if ! ip -4 addr show dev "$PARENT" | grep -q "10.77.0.2"; then
  sudo ip addr add "$VM_IP" dev "$PARENT"
fi

if ip link show "$SHIM" >/dev/null 2>&1; then
  sudo ip link delete "$SHIM"
fi
sudo ip link add "$SHIM" link "$PARENT" type macvlan mode bridge
sudo ip addr add "$SHIM_IP" dev "$SHIM"
sudo ip link set "$SHIM" up
sudo ip route replace "$ROUTE" dev "$SHIM" src "${SHIM_IP%/*}"

cd "$HERE"
if [[ ! -f .env ]]; then
  cp .env.example .env
fi

docker compose build
docker compose up -d

echo
echo "WatchLog virtual CCTV lab started."
echo "  Dahua NVR:      10.77.0.20  admin / WatchLog123!"
echo "  Hikvision NVR:  10.77.0.21  admin / WatchLog123!"
echo "  ONVIF camera 1: 10.77.0.31"
echo "  ONVIF camera 2: 10.77.0.32"
echo "  Router decoy:   10.77.0.50"
echo
echo "Run testlab/windows/Test-Lab.ps1 from the Windows installer-test PC."
