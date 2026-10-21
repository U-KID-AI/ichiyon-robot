#!/bin/sh
# Official 2026-10-21 security release. SHA256 values from nodejs.org/dist.
# The Docker Hub 22.23.4 images were not yet published at audit time.
set -eu
case "$(dpkg --print-architecture)" in
  amd64) arch=x64; checksum=1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce ;;
  arm64) arch=arm64; checksum=6287aa46b6c375d816e63ae66d024fbbfd70404825aabdfdf898a970871d2f4b ;;
  armhf) arch=armv7l; checksum=c9057c6a3b8fbb9651ed115c1e96d4a7281b7b9c9ca89fa05865c98ed7ffd437 ;;
  ppc64el) arch=ppc64le; checksum=e4732f45e0cad787d6ba70c4eb6db45dbdb3f286f67dee429dbc6b4ff36eac43 ;;
  s390x) arch=s390x; checksum=07ae074fc218fc42e24c939df0dc0209443fb08eb5b2ed6d26b1bf6757781fff ;;
  *) echo 'Unsupported Node.js architecture' >&2; exit 1 ;;
esac
archive="node-v22.23.4-linux-${arch}.tar.xz"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  "https://nodejs.org/dist/v22.23.4/${archive}" -o "$work/$archive"
printf '%s  %s\n' "$checksum" "$work/$archive" | sha256sum --check --status
tar -xJf "$work/$archive" -C "$work" "node-v22.23.4-linux-${arch}/bin/node"
install -m 0755 "$work/node-v22.23.4-linux-${arch}/bin/node" /usr/local/bin/node
test "$(node --version)" = v22.23.4
