#!/usr/bin/env bash
# No authentication, network or uploader execution. Mac-only binary selection.
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
    echo 'SKIP: only relevant for macOS'
    exit 0
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/gu_binary_test.XXXXXXXX")"
trap 'rm -rf "$TMP_TEST_DIR"' EXIT
# Shell script is sourced only to exercise its pure binary discovery function.
source "$REPO_ROOT/google-unlimited.sh"
SCRIPT_DIR="$TMP_TEST_DIR"
OS=Darwin

printf '#!/bin/sh\nexit 0\n' > "$TMP_TEST_DIR/gotohp-cli_amd64"
printf '#!/bin/sh\nexit 0\n' > "$TMP_TEST_DIR/gotohp-cli-x64.exe"
chmod +x "$TMP_TEST_DIR/gotohp-cli_amd64" "$TMP_TEST_DIR/gotohp-cli-x64.exe"

if [ -n "$(PATH=/usr/bin:/bin get_gotohp_bin)" ]; then
    echo 'FAIL: a non-Mach-O binary was selected on Mac' >&2
    exit 1
fi

cp /bin/ls "$TMP_TEST_DIR/gotohp-cli-macos-universal"
selected="$(PATH=/usr/bin:/bin get_gotohp_bin)"
if [ "$selected" != "$TMP_TEST_DIR/gotohp-cli-macos-universal" ]; then
    echo "FAIL: native macOS executable was not selected: $selected" >&2
    exit 1
fi
echo 'PASS: macOS selects compatible Mach-O and rejects non-native binaries'
