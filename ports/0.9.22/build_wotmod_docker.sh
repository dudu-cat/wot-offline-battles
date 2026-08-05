#!/usr/bin/env sh
set -eu

PORT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

docker run --rm --platform linux/amd64 \
  -v "$PORT_ROOT:/work" \
  -w /work \
  python:2.7.18 \
  python build_wotmod.py
