#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
[ "$(uname -s)" = Darwin ] || { echo 'Build on macOS' >&2; exit 1; }
python_bin=${IDUTIL_BUILD_PYTHON:-python3}
version=$("$python_bin" -c 'from idutil import __version__; print(__version__)')
arch=$(uname -m)
case "$arch" in arm64|x86_64) ;; *) echo "Unsupported architecture: $arch" >&2; exit 1;; esac
"$python_bin" -m PyInstaller --noconfirm --clean --onefile --name idutil \
  --distpath dist/binary --workpath build/pyinstaller --specpath build \
  --collect-data idutil packaging/entry.py
[ "$(dist/binary/idutil --version)" = "idutil $version" ]
codesign --verify --strict dist/binary/idutil
mkdir -p dist/release
tar -czf "dist/release/idutil-v$version-macos-$arch.tar.gz" -C dist/binary idutil
shasum -a 256 "dist/release/idutil-v$version-macos-$arch.tar.gz"
