#!/bin/sh
# Download a release executable; no Python, uv, or build toolchain is required.
set -eu
version=${IDUTIL_VERSION:-latest}
bin_dir=${IDUTIL_BIN_DIR:-"$HOME/.local/bin"}
assume_yes=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --version|--bin-dir)
      [ "$#" -ge 2 ] || { echo "Missing value: $1" >&2; exit 2; }
      case "$1" in --version) version=$2;; --bin-dir) bin_dir=$2;; esac
      shift 2;;
    -y|--yes) assume_yes=true; shift;;
    -h|--help)
      echo 'Usage: sh install.sh [--version v0.2.1|latest] [--bin-dir PATH] [--yes]'
      echo 'Interactive by default; --yes accepts defaults without a terminal.'
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
# Read answers independently of the script stream (curl ... | sh).
if [ "$assume_yes" = false ]; then
  if [ -t 0 ]; then
    exec 3<&0
  elif ( : </dev/tty ) 2>/dev/null; then
    exec 3</dev/tty
  else
    echo 'Interactive installation requires a terminal. Use --yes for unattended installation.' >&2
    exit 1
  fi
fi
ask() {
  printf '%s' "$1" >&2
  if ! IFS= read -r answer <&3; then
    echo 'Input closed; installation cancelled.' >&2
    exit 1
  fi
}
confirm() {
  [ "$assume_yes" = false ] || return 0
  while :; do
    ask "$1 [y/N]: "
    case "$answer" in
      y|Y|yes|YES) return 0;;
      ''|n|N|no|NO) echo 'Installation cancelled.'; exit 0;;
      *) echo 'Please enter y or n.' >&2;;
    esac
  done
}
printf '\nIdutil installer - macOS %s (%s)\n' "$major" "$arch"
if [ "$assume_yes" = false ]; then
  ask "Release version [$version]: "
  version=${answer:-$version}
  ask "Installation directory [$bin_dir]: "
  bin_dir=${answer:-$bin_dir}
fi
case "$bin_dir" in
  /*) ;;
  *) echo 'Installation directory must be an absolute path.' >&2; exit 2;;
esac
confirm "Download idutil $version for $arch to install in $bin_dir?"
repo=https://github.com/kyungtaek/idutil
echo "Resolving release $version..."
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
printf 'Downloading %s\n' "$asset"
curl --proto '=https' --tlsv1.2 -fSL --progress-bar "$base/$asset" -o "$work/$asset"
curl --proto '=https' --tlsv1.2 -fsSL "$base/SHA256SUMS" -o "$work/SHA256SUMS"
echo 'Verifying checksum, archive and executable version...'
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
[ ! -L "$bin_dir/idutil" ] || { echo "Existing symlink at $bin_dir/idutil. Uninstall the previous package manager installation or choose --bin-dir." >&2; exit 1; }
[ ! -d "$bin_dir/idutil" ] || { echo 'Target is a directory' >&2; exit 1; }
if [ -e "$bin_dir/idutil" ]; then
  confirm "Verification passed. Replace existing $bin_dir/idutil with $version?"
else
  confirm "Verification passed. Install $version to $bin_dir/idutil?"
fi
mkdir -p "$bin_dir"
staged=$(mktemp "$bin_dir/.idutil.XXXXXX")
cp "$work/idutil" "$staged"
chmod 755 "$staged"
mv -f "$staged" "$bin_dir/idutil"
staged=
printf 'Installed idutil %s (%s) to %s/idutil\n' "${version#v}" "$arch" "$bin_dir"
printf 'Ensure %s is on PATH. In each registered repository run: idutil setup --update\n' "$bin_dir"
