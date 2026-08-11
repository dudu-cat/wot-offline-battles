#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUTPUT="$ROOT/scripts/client/gui/mods/offhangar/offhangar_native_seed.pyd"

docker run --rm \
	-v "$ROOT:/src" \
	-w /src \
	debian:bookworm-slim \
	/bin/sh -c '
		set -eu
		apt-get update >/dev/null
		DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
			gcc-mingw-w64-i686 binutils-mingw-w64-i686 >/dev/null
		i686-w64-mingw32-gcc -m32 -Os -Wall -Wextra -Werror \
			-fno-ident -fno-asynchronous-unwind-tables -shared -s \
			-Wl,--no-insert-timestamp \
			-o /src/scripts/client/gui/mods/offhangar/offhangar_native_seed.pyd \
			/src/native/offhangar_native_seed.c
		i686-w64-mingw32-objdump -p \
			/src/scripts/client/gui/mods/offhangar/offhangar_native_seed.pyd |
			grep -q "initoffhangar_native_seed"
	'

echo "Built $OUTPUT"
