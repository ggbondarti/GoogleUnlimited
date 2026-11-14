#!/usr/bin/env python3
"""Verify the Mac M2 source-built uploader without opening any credentials.

Standalone standard-library tool; the WebUI calls verify() before enabling
uploads. Build manifest is intentionally outside Git and contains no secrets.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

PINNED_SOURCE_REVISION = "68ad61fc844a060ce0e09c0b0524dd799c14170c"
BINARY_NAME = "gotohp-cli-owned-arm64"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_sha256(source: Path) -> str:
    hasher = hashlib.sha256()
    for item in sorted(p for p in source.rglob("*") if p.is_file()):
        relative = item.relative_to(source).as_posix().encode("utf-8")
        hasher.update(len(relative).to_bytes(4, "big") + relative)
        data = item.read_bytes()
        hasher.update(len(data).to_bytes(8, "big") + data)
    return hasher.hexdigest()


def verify(project_root: Path) -> tuple[bool, str]:
    root = project_root.resolve()
    source = root / "engine" / "gotohp-src"
    artifact_dir = root / "engine" / "bin"
    binary = artifact_dir / BINARY_NAME
    manifest = artifact_dir / "owned-build.json"
    if not source.is_dir() or not binary.is_file() or not manifest.is_file():
        return False, "尚未编译本地上传引擎"
    if binary.is_symlink() or manifest.is_symlink():
        return False, "上传引擎文件不允许为符号链接"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if (data.get("schema") != 1 or
                data.get("upstream_commit") != PINNED_SOURCE_REVISION or
                data.get("platform") != "darwin/arm64" or
                (source / "UPSTREAM_REVISION").read_text(encoding="utf-8").strip()
                != PINNED_SOURCE_REVISION):
            return False, "上传引擎来源或平台记录不匹配"
        if source_sha256(source) != data.get("source_sha256"):
            return False, "源码已改变，必须重新编译"
        if file_sha256(binary) != data.get("binary_sha256"):
            return False, "编译程序 SHA-256 校验失败"
    except (OSError, ValueError, TypeError, KeyError):
        return False, "上传引擎校验失败"
    return True, "本机源码、二进制和构建清单一致"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    ok, reason = verify(root)
    print(reason)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
