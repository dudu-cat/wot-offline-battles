#!/usr/bin/env sh
set -eu

PORT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$PORT_ROOT/../.." && pwd)

docker run --rm --platform linux/amd64 \
  -e "OFFLINE_LAN_RELEASE_HOST=${OFFLINE_LAN_RELEASE_HOST:-127.0.0.1}" \
  -e "OFFLINE_LAN_RELEASE_PORT=${OFFLINE_LAN_RELEASE_PORT:-28782}" \
  -v "$PROJECT_ROOT:/work" \
  -w /work/ports/0.9.22 \
  python:2.7.18 \
  python build_wotmod.py
