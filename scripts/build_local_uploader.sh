#!/usr/bin/env bash
# Build pinned CLI on Mac locally, without reading account tokens or photos.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="$ROOT/engine/gotohp-src"
BIN_DIR="$ROOT/engine/bin"
EXPECTED_REVISION="68ad61fc844a060ce0e09c0b0524dd799c14170c"
if [[ "$(cat "$SOURCE/UPSTREAM_REVISION")" != "$EXPECTED_REVISION" ]]; then
  echo "Unrecognized upstream revision; refusing build" >&2; exit 2
fi
if [[ "$(uname -s)/$(uname -m)" != "Darwin/arm64" ]]; then
  echo "This builder is for the Mac M2 (Darwin/arm64) only" >&2; exit 2
fi
if [[ ! -f "$SOURCE/LICENSE" ]] || ! grep -q 'Copyright (c) 2024 xob0t' "$SOURCE/LICENSE"; then
  echo "Upstream MIT notice missing" >&2; exit 2
fi
mkdir -p "$BIN_DIR"
chmod 700 "$BIN_DIR"
TMP="$BIN_DIR/.gotohp-owned-$$"
trap 'rm -f "$TMP"' EXIT
(
  cd "$SOURCE"
  export CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 GOTOOLCHAIN=auto GOMAXPROCS=4
  go build -p 2 -tags cli -trimpath -buildvcs=false -ldflags="-w -s" -o "$TMP" .
)
chmod 700 "$TMP"
mv "$TMP" "$BIN_DIR/gotohp-cli-owned-arm64"
python3 - "$SOURCE" "$BIN_DIR" "$EXPECTED_REVISION" <<'PY'
from pathlib import Path
import sys,hashlib,json,os,datetime,subprocess
src=Path(sys.argv[1]); out=Path(sys.argv[2]); rev=sys.argv[3]
files=sorted(p for p in src.rglob("*") if p.is_file())
h=hashlib.sha256()
for p in files:
    rel=p.relative_to(src).as_posix().encode("utf-8")
    h.update(len(rel).to_bytes(4,"big")+rel)
    payload=p.read_bytes()
    h.update(len(payload).to_bytes(8,"big")+payload)
binary=out/"gotohp-cli-owned-arm64"
manifest={
    "schema":1,"origin":"https://github.com/xob0t/gotohp",
    "upstream_commit":rev,"license":"MIT",
    "source_sha256":h.hexdigest(),
    "binary_sha256":hashlib.sha256(binary.read_bytes()).hexdigest(),
    "go_version":subprocess.run(["go","version"],cwd=src,capture_output=True,text=True,env={**os.environ,"GOTOOLCHAIN":"auto"},timeout=20).stdout.strip(),
    "platform":"darwin/arm64",
    "built_at_utc":datetime.datetime.now(datetime.timezone.utc).isoformat()
}
target=out/"owned-build.json"
target.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
os.chmod(target,0o600)
print("Source pinned:",rev)
print("Local binary:",binary)
print("SHA-256:",manifest["binary_sha256"])
print("Build manifest:",target)
PY
"$BIN_DIR/gotohp-cli-owned-arm64" --version
