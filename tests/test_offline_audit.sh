#!/usr/bin/env bash
# Safe Mac/Linux offline census test: no Google credentials or network.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CASE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/gu_audit_test.XXXXXXXX")"
trap 'rm -rf "$CASE_DIR"' EXIT
mkdir -p "$CASE_DIR/photos/sub"
dd if=/dev/zero of="$CASE_DIR/photos/one.JPG" bs=1024 count=20 2>/dev/null
dd if=/dev/zero of="$CASE_DIR/photos/sub/two video.MP4" bs=1024 count=25 2>/dev/null
dd if=/dev/zero of="$CASE_DIR/photos/too-small.jpg" bs=1024 count=5 2>/dev/null
dd if=/dev/zero of="$CASE_DIR/photos/unsupported.txt" bs=1024 count=20 2>/dev/null
ln -s "$CASE_DIR/photos/one.JPG" "$CASE_DIR/photos/link.JPG"

output="$(bash "$REPO_ROOT/google-unlimited.sh" audit "$CASE_DIR/photos")"
printf '%s\n' "$output"
for expected in 'mode=offline_audit' 'files_scanned=4' 'media_eligible=2' 'media_bytes=46080' 'skipped=2'; do
    if ! grep -Fxq "$expected" <<< "$output"; then
        echo "FAIL: expected $expected" >&2
        exit 1
    fi
done

if bash "$REPO_ROOT/google-unlimited.sh" audit "$CASE_DIR/does-not-exist" >/dev/null 2>&1; then
    echo 'FAIL: missing directory must fail' >&2
    exit 1
fi
if [ -f "$REPO_ROOT/gotohp.config" ] || [ -f "$REPO_ROOT/gphotos.db" ]; then
    echo 'FAIL: offline audit must not create auth or upload state' >&2
    exit 1
fi
echo 'PASS: offline audit is read-only, deterministic and excludes symlinks'
