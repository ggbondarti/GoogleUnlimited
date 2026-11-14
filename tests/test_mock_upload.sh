#!/usr/bin/env bash
# Isolated integration test: upload engine and network are entirely stubbed.
# Validates SQLite state transitions and retry, not Google cloud completion.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/gu_mock_upload.XXXXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

# Sourcing only defines functions; the upstream dispatcher has an execution guard.
source "$REPO_ROOT/google-unlimited.sh"
SCRIPT_DIR="$TEST_ROOT"
DB="$TEST_ROOT/mock_upload.sqlite"
mkdir -p "$TEST_ROOT/photos"
MEDIA="$TEST_ROOT/photos/test-media.jpg"
dd if=/dev/zero of="$MEDIA" bs=1024 count=20 2>/dev/null

init_db
# Skip filesystem stabilization delay; this test isolates upload state only.
wait_for_file_ready() { return 0; }

# A mock Google API response that explicitly fails, even with a mediaKey.
run_gotohp() {
    printf '%s\n' '{"success":true,"mediaKey":"FAKE_NOT_UPLOADED"}'
    return 23
}
invoke_upload "$MEDIA"

uploads="$(sqlite3 "$DB" 'SELECT COUNT(*) FROM uploads;')"
retries="$(sqlite3 "$DB" 'SELECT COUNT(*) FROM retry_queue;')"
if [ "$uploads" != 0 ] || [ "$retries" != 1 ]; then
    echo "FAIL: failed CLI command incorrectly marked completed (uploads=$uploads retries=$retries)" >&2
    exit 1
fi

# A second stubbed upload reports successful API acceptance.
run_gotohp() {
    printf '%s\n' '{"success":true,"mediaKey":"MOCK_ACCEPTED_ONLY"}'
    return 0
}
invoke_upload "$MEDIA"
uploads="$(sqlite3 "$DB" 'SELECT COUNT(*) FROM uploads;')"
retries="$(sqlite3 "$DB" 'SELECT COUNT(*) FROM retry_queue;')"
media_key="$(sqlite3 "$DB" 'SELECT media_key FROM uploads LIMIT 1;')"
if [ "$uploads" != 1 ] || [ "$retries" != 0 ] || [ "$media_key" != 'MOCK_ACCEPTED_ONLY' ]; then
    echo "FAIL: success did not clear retries (uploads=$uploads retries=$retries)" >&2
    exit 1
fi

invoke_upload "$MEDIA"
uploads="$(sqlite3 "$DB" 'SELECT COUNT(*) FROM uploads;')"
if [ "$uploads" != 1 ]; then
    echo "FAIL: duplicate invocation created extra uploads (uploads=$uploads)" >&2
    exit 1
fi

if [ -e "$REPO_ROOT/gotohp.config" ] || [ -e "$REPO_ROOT/gphotos.db" ]; then
    echo 'FAIL: test unexpectedly touched repository auth/database files' >&2
    exit 1
fi
echo 'PASS: mock failure -> retry -> API accepted -> dedup, without credentials or network'
