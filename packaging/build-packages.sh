#!/usr/bin/env bash
# Build .deb, .rpm and Arch packages into dist/ with nfpm.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${VERSION:-$(sed -nE 's/^__version__ = "(.*)"/\1/p' flatshot/__init__.py)}"
export VERSION
command -v nfpm >/dev/null || { echo "nfpm not found: https://nfpm.goreleaser.com/install/" >&2; exit 1; }

rm -rf build/stage
mkdir -p build/stage dist
cp -r flatshot build/stage/flatshot
# Native KWin capture helper (needs a C compiler and libdbus-1 headers).
cc -O2 -Wall -Wextra -o build/flatshot-kwin-grab native/flatshot-kwin-grab.c $(pkg-config --cflags --libs dbus-1)
strip build/flatshot-kwin-grab
find build/stage -name __pycache__ -prune -exec rm -rf {} +

for fmt in deb rpm archlinux; do
    nfpm package --config packaging/nfpm.yaml --packager "$fmt" --target dist/
done
ls -l dist/
