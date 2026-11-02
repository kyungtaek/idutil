#!/bin/sh
set -eu
# One implementation: the installed CLI owns setup, update, dry-run and uninstall.
exec "${IDUTIL_BIN:-idutil}" setup "$@"
