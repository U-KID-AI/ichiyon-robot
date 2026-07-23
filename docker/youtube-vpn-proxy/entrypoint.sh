#!/bin/sh
set -eu

OVPN_CONFIG="${OPENVPN_CONFIG:-/vpn/client.ovpn}"
TUN_MTU="${OPENVPN_TUN_MTU:-1400}"
MSSFIX="${OPENVPN_MSSFIX:-1360}"
OPENVPN_LOG="${OPENVPN_LOG:-/tmp/openvpn.log}"

if [ ! -r "$OVPN_CONFIG" ]; then
  echo "[ERROR] OpenVPN config is not readable: $OVPN_CONFIG" >&2
  exit 1
fi

cleanup() {
  if [ -f /tmp/openvpn.pid ]; then
    kill "$(cat /tmp/openvpn.pid)" 2>/dev/null || true
  fi
  if [ -f /tmp/tinyproxy.pid ]; then
    kill "$(cat /tmp/tinyproxy.pid)" 2>/dev/null || true
  fi
}
trap cleanup INT TERM EXIT

openvpn \
  --config "$OVPN_CONFIG" \
  --tun-mtu "$TUN_MTU" \
  --mssfix "$MSSFIX" \
  --writepid /tmp/openvpn.pid \
  --log "$OPENVPN_LOG" \
  --daemon

for _ in $(seq 1 60); do
  if ip link show tun0 >/dev/null 2>&1; then
    echo "[INFO] OpenVPN tun0 is ready"
    break
  fi
  sleep 1
done

if ! ip link show tun0 >/dev/null 2>&1; then
  echo "[ERROR] OpenVPN tun0 did not become ready" >&2
  tail -n 30 "$OPENVPN_LOG" >&2 || true
  exit 1
fi

tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf &
echo "$!" > /tmp/tinyproxy.pid
wait "$(cat /tmp/tinyproxy.pid)"
