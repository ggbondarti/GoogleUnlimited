"""Offline security checks for the independently built Mac uploader.

No Google account, Xiaomi 3 token, network access or real photos are used.
"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.verify_local_build import (  # noqa: E402
    PINNED_SOURCE_REVISION, file_sha256, source_sha256, verify
)


class LocalUploaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / "engine" / "gotohp-src"
        self.bin_dir = self.root / "engine" / "bin"
        self.src.mkdir(parents=True)
        self.bin_dir.mkdir(parents=True)
        (self.src / "UPSTREAM_REVISION").write_text(
            PINNED_SOURCE_REVISION + "\n", encoding="utf-8")
        (self.src / "sample.go").write_text(
            "package example\n", encoding="utf-8")
        self.binary = self.bin_dir / "gotohp-cli-owned-arm64"
        self.binary.write_bytes(b"INERT-TEST-BINARY")
        self.manifest = self.bin_dir / "owned-build.json"
        self.write_manifest()

    def write_manifest(self):
        self.manifest.write_text(json.dumps({
            "schema": 1,
            "origin": "https://github.com/xob0t/gotohp",
            "upstream_commit": PINNED_SOURCE_REVISION,
            "platform": "darwin/arm64",
            "source_sha256": source_sha256(self.src),
            "binary_sha256": file_sha256(self.binary)
        }), encoding="utf-8")

    def test_valid_build_manifest(self):
        ok, reason = verify(self.root)
        self.assertTrue(ok, reason)

    def test_tampered_binary_is_rejected(self):
        self.binary.write_bytes(b"TAMPERED")
        ok, reason = verify(self.root)
        self.assertFalse(ok)
        self.assertIn("SHA-256", reason)

    def test_tampered_source_requires_rebuild(self):
        (self.src / "sample.go").write_text("package altered\n", encoding="utf-8")
        ok, reason = verify(self.root)
        self.assertFalse(ok)
        self.assertIn("源码已改变", reason)

    def test_wrong_commit_or_platform_is_rejected(self):
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        data["upstream_commit"] = "wrong"
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        self.assertFalse(verify(self.root)[0])
        data["upstream_commit"] = PINNED_SOURCE_REVISION
        data["platform"] = "linux/amd64"
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        self.assertFalse(verify(self.root)[0])

    def test_symlink_binary_not_accepted(self):
        copied = self.bin_dir / "ordinary-file"
        copied.write_bytes(self.binary.read_bytes())
        self.binary.unlink()
        self.binary.symlink_to(copied)
        self.assertFalse(verify(self.root)[0])

    def test_missing_local_build_fails_closed(self):
        self.manifest.unlink()
        self.assertFalse(verify(self.root)[0])


if __name__ == "__main__":
    unittest.main()
