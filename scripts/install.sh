#!/bin/sh
# Download a release executable; no Python, uv, or build toolchain is required.
set -eu
version=${IDUTIL_VERSION:-latest}
bin_dir=${IDUTIL_BIN_DIR:-"$HOME/.local/bin"}
while [ "$#" -gt 0 ]; do
  case "$1" in
    --version|--bin-dir)
      [ "$#" -ge 2 ] || { echo "Missing value: $1" >&2; exit 2; }
      case "$1" in --version) version=$2;; --bin-dir) bin_dir=$2;; esac
      shift 2;;
    -h|--help)
      echo 'Usage: sh install.sh [--version v0.2.0|latest] [--bin-dir PATH]'
      exit 0;;
    *) echo "Unknown option: $1" >&2; exit 2;;
  esac
done
[ "$(uname -s)" = Darwin ] || { echo 'idutil release binaries require macOS.' >&2; exit 1; }
major=$(sw_vers -productVersion | cut -d . -f 1)
[ "$major" -ge 15 ] || { echo 'These binaries require macOS 15 or later.' >&2; exit 1; }
arch=$(uname -m)
# Prefer the native Apple Silicon executable even from a Rosetta terminal.
if [ "$arch" = x86_64 ] && [ "$(sysctl -in sysctl.proc_translated 2>/dev/null || true)" = 1 ]; then arch=arm64; fi
case "$arch" in arm64|x86_64) ;; *) echo "Unsupported architecture: $arch" >&2; exit 1;; esac
repo=https://github.com/kyungtaek/idutil
if [ "$version" = latest ]; then
  resolved=$(curl --proto '=https' --tlsv1.2 -fsSL -o /dev/null -w '%{url_effective}' "$repo/releases/latest")
  version=${resolved##*/}
fi
case "$version" in v[0-9]*) ;; *) echo "Invalid release version: $version" >&2; exit 2;; esac
case "$version" in *[!a-zA-Z0-9._-]*) echo 'Invalid release version characters' >&2; exit 2;; esac
asset="idutil-$version-macos-$arch.tar.gz"
work=$(mktemp -d "${TMPDIR:-/tmp}/idutil-install.XXXXXX")
staged=
cleanup() { rm -rf "$work"; if [ -n "$staged" ]; then rm -f "$staged"; fi; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
base="$repo/releases/download/$version"
curl --proto '=https' --tlsv1.2 -fsSL "$base/$asset" -o "$work/$asset"
curl --proto '=https' --tlsv1.2 -fsSL "$base/SHA256SUMS" -o "$work/SHA256SUMS"
expected=$(awk -v file="$asset" '$2 == file { print $1 }' "$work/SHA256SUMS")
[ "${#expected}" -eq 64 ] || { echo 'Missing or ambiguous SHA-256 entry' >&2; exit 1; }
actual=$(shasum -a 256 "$work/$asset" | awk '{print $1}')
[ "$actual" = "$expected" ] || { echo 'SHA-256 mismatch; installation cancelled.' >&2; exit 1; }
# Only a single plain executable is permitted in this release format.
[ "$(tar -tzf "$work/$asset")" = idutil ] || { echo 'Unexpected archive contents' >&2; exit 1; }
tar -xzf "$work/$asset" -C "$work"
[ -f "$work/idutil" ] && [ ! -L "$work/idutil" ] || { echo 'Invalid executable' >&2; exit 1; }
chmod 755 "$work/idutil"
[ "$("$work/idutil" --version)" = "idutil ${version#v}" ] || { echo 'Executable version mismatch' >&2; exit 1; }
mkdir -p "$bin_dir"
[ ! -L "$bin_dir/idutil" ] || { echo "Existing symlink at $bin_dir/idutil. Uninstall the previous package manager installation or choose --bin-dir." >&2; exit 1; }
[ ! -d "$bin_dir/idutil" ] || { echo 'Target is a directory' >&2; exit 1; }
staged=$(mktemp "$bin_dir/.idutil.XXXXXX")
cp "$work/idutil" "$staged"
chmod 755 "$staged"
mv -f "$staged" "$bin_dir/idutil"
staged=
printf 'Installed idutil %s (%s) to %s/idutil\n' "${version#v}" "$arch" "$bin_dir"
printf 'Ensure %s is on PATH. In each registered repository run: idutil setup --update\n' "$bin_dir"
