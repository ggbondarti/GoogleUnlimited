"""No-Google-credentials tests for independent local WebUI."""
import http.client
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from webui.server import Store, UserError, make_handler, ThreadingHTTPServer


class WebUITest(unittest.TestCase):
    def setUp(self):
        # A small unique source directory under current user's HOME, never MI3MountMac.
        self.temp = tempfile.TemporaryDirectory(prefix="gu_webui_test_", dir=Path.home())
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.source.mkdir()
        self.media1 = self.source / "photo A.jpg"
        self.media2 = self.source / "photo B.jpg"
        self.media1.write_bytes(b"A" * 15000)
        self.media2.write_bytes(b"A" * 15000)
        (self.source / "other.txt").write_bytes(b"B" * 20000)
        (self.source / "too-small.jpg").write_bytes(b"C" * 100)
        (self.source / ".hidden.jpg").write_bytes(b"D" * 20000)
        (self.source / "link.jpg").symlink_to(self.media1)
        self.store = Store(self.base / "state", worker=False)
        self.addCleanup(self.store.close)

    def wait_for_jobs(self, count):
        for _ in range(150):
            if len(self.store.jobs()) >= count:
                return
            time.sleep(0.02)
        self.fail("asynchronous scan did not create expected jobs")

    def test_defaults_are_safe(self):
        state = self.store.status()
        self.assertFalse(state["monitor_enabled"])
        self.assertFalse(state["upload_enabled"])
        self.assertFalse(state["auto_sync_enabled"])
        self.assertFalse(state["auto_sync_requested"])
        self.assertFalse(state["allow_real_upload"])
        self.assertFalse(state["credentials_present"])
        with self.assertRaises(UserError):
            self.store.set_upload(True)

    def test_source_card_layout_regression_guard(self):
        """Action buttons must never squeeze source metadata into a vertical sliver."""
        base = Path(__file__).resolve().parents[1] / "webui"
        js = (base / "app.js").read_text(encoding="utf-8")
        css = (base / "style.css").read_text(encoding="utf-8")
        self.assertIn('const controls = element("div", "source-controls")', js)
        self.assertIn("row.appendChild(controls)", js)
        self.assertIn("controls.appendChild(index)", js)
        self.assertIn("controls.appendChild(remove)", js)
        self.assertIn("grid-template-columns: 42px minmax(0, 1fr) auto", css)
        self.assertIn(".source-album-form", css)
        self.assertIn("source-album-settings", js)
        self.assertIn("source-quality-select", js)
        self.assertIn("/api/source/quality", js)
        self.assertIn('"sync-switch"', js)
        self.assertNotIn('"monitor-switch"', js)
        self.assertNotIn('"upload-switch"', js)
        self.assertIn("保存并确认相册", js)
        self.assertIn("仅试传 1 张照片", js)
        self.assertIn(".source-entry > .source-meta", css)
        self.assertIn("text-overflow: ellipsis", css)
        self.assertIn("@media (max-width: 1110px)", css)
        self.assertIn("grid-template-columns:repeat(2,minmax(0,1fr))", css)
        self.assertIn("engine-state", (base / "index.html").read_text(encoding="utf-8"))

    def test_folder_progress_is_compact_and_uses_api_accepted_not_cloud_verified(self):
        base = Path(__file__).resolve().parents[1] / "webui"
        js = (base / "app.js").read_text(encoding="utf-8")
        css = (base / "style.css").read_text(encoding="utf-8")
        html = (base / "index.html").read_text(encoding="utf-8")
        for marker in ("folderUploadProgress(", "folder-progress-track",
                       "folder-upload-percent", "folder.remaining", "folder.verified_count",
                       'setAttribute("role", "progressbar")', "根目录（含子目录）"):
            if marker == "folder.remaining":
                marker = "progress.remaining"
            self.assertIn(marker, js)
        self.assertIn(".folder-progress-fill", css)
        self.assertIn("未接收包括排队、失败及去重", html)
        if not shutil.which("node"):
            return  # Static structural guard still runs on Node-less test platforms.
        script = r"""
const fs = require("fs");
const assert = require("assert");
const source = fs.readFileSync(process.argv[1], "utf8");
const start = source.indexOf("function folderUploadProgress(");
const end = source.indexOf("\nasync function showFolders(", start);
assert(start >= 0 && end > start);
const calc = new Function(source.slice(start, end) + "\nreturn folderUploadProgress;")();
assert.deepStrictEqual(calc(66, 217), {
  total:217, accepted:66, remaining:151, acceptedPercent:30.4, remainingPercent:69.6
});
assert.deepStrictEqual(calc(32, 32), {
  total:32, accepted:32, remaining:0, acceptedPercent:100, remainingPercent:0
});
assert.deepStrictEqual(calc(2, 107), {
  total:107, accepted:2, remaining:105, acceptedPercent:1.9, remainingPercent:98.1
});
assert.deepStrictEqual(calc(0, 0), {
  total:0, accepted:0, remaining:0, acceptedPercent:0, remainingPercent:0
});
assert.deepStrictEqual(calc(999, 2), {
  total:2, accepted:2, remaining:0, acceptedPercent:100, remainingPercent:0
});
"""
        result = subprocess.run(["node", "-e", script, str(base / "app.js")],
                                capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_album_requires_explicit_confirmation_before_bulk_upload(self):
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        source = self.store.sources()[0]
        self.assertEqual(source["album_name"], "source")
        self.assertEqual(source["album_confirmed"], 0)
        self.assertEqual(source["upload_ready"], 0)
        self.assertIsNone(self.store._next_job())
        with self.assertRaises(UserError):
            self.store.set_album(source["id"], "\ninvalid")
        with self.assertRaises(UserError):
            self.store.set_album(source["id"], " " * 4)
        self.store.set_album(source["id"], "示例旅行相册")
        self.assertEqual(self.store.sources()[0]["album_confirmed"], 1)
        self.assertEqual(self.store.sources()[0]["album_name"],
                         "示例旅行相册")
        self.assertIsNone(self.store._next_job())
        self.store.set_source_upload(source["id"], True)
        self.assertIsNotNone(self.store._next_job())
        self.assertTrue(any(event["action"] == "album_confirmed"
                            for event in self.store.source_events(source["id"])))
        self.store.upload_enabled = True
        with self.assertRaises(UserError):
            self.store.set_album(source["id"], "Changed While Uploading")

    def test_global_sync_never_auto_uploads_a_new_unapproved_source(self):
        """New folders remain safe even when the global switch was already ON."""
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        source_id = self.store.sources()[0]["id"]
        with mock.patch.object(self.store, "_ensure_upload_ready"):
            self.store.set_auto_sync(True)
        self.assertIsNone(self.store._next_job())
        self.assertEqual(self.store.sources()[0]["upload_ready"], 0)
        # Filename defaults are only a suggestion, not an album confirmation.
        with self.assertRaises(UserError):
            self.store.set_source_upload(source_id, True)
        # Configure a source while global sync is on. No other source should block settings.
        self.store.set_album(source_id, "手工确认的相册")
        self.store.set_upload_quality(source_id, "original")
        self.assertIsNone(self.store._next_job())
        self.store.set_source_upload(source_id, True)
        self.assertIsNotNone(self.store._next_job())
        self.store.set_source_upload(source_id, False)
        self.assertIsNone(self.store._next_job())
        self.assertEqual(self.store.sources()[0]["upload_quality"], "original")
        self.store.set_auto_sync(False)
        self.assertFalse(self.store.status()["auto_sync_requested"])
        other = Store(self.base / "state", worker=False)
        try:
            self.assertFalse(other.auto_sync_requested)
            self.assertFalse(other.upload_enabled)
            self.assertEqual(other.sources()[0]["upload_ready"], 0)
            self.assertEqual({j["status"] for j in other.jobs()}, {"queued"})
        finally:
            other.close()

    def test_readding_or_restoring_source_never_restores_upload_permission(self):
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        source_id = self.store.sources()[0]["id"]
        self.store.set_album(source_id, "手工确认的相册")
        self.store.set_source_upload(source_id, True)
        self.store.add_source(str(self.source), "directory")
        self.assertEqual(self.store.sources()[0]["upload_ready"], 0)
        self.assertEqual(self.store.sources()[0]["album_name"], "手工确认的相册")
        self.assertEqual(len(self.store.jobs()), 2)
        self.store.set_source_upload(source_id, True)
        self.store.set_display(source_id, "removed")
        self.assertEqual(self.store.sources()[0]["upload_ready"], 0)
        self.store.restore_source(source_id)
        self.assertEqual(self.store.sources()[0]["upload_ready"], 0)
        self.assertEqual(self.store.sources()[0]["album_confirmed"], 1)
        self.assertEqual({j["status"] for j in self.store.jobs()}, {"queued"})

    def test_source_upload_permission_prevents_duplicate_album_creation(self):
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        sid = self.store.sources()[0]["id"]
        self.store.set_album(sid, "准备中的相册")
        with self.store.connection() as con:
            con.execute("""UPDATE jobs SET status='api_accepted',media_key='MOCK_CLOUD'
                           WHERE source_id=? AND id=(SELECT MIN(id) FROM jobs WHERE source_id=?)""",
                        (sid, sid))
        with self.assertRaises(UserError):
            self.store.set_source_upload(sid, True)
        self.assertFalse(self.store.sources()[0]["upload_ready"])
        self.assertEqual(sum(j["status"] == "api_accepted" for j in self.store.jobs()), 1)

    def test_auto_sync_requires_local_auth_and_restores_explicit_setting(self):
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        with self.assertRaises(UserError):
            self.store.set_auto_sync(True)
        self.assertFalse(self.store.auto_sync_requested)
        self.assertFalse(self.store.upload_enabled)
        # Simulate the readiness gate; no real credentials or Google requests.
        with mock.patch.object(self.store, "_ensure_upload_ready"):
            self.store.set_auto_sync(True)
        self.assertTrue(self.store.monitor_enabled)
        self.assertTrue(self.store.upload_enabled)
        self.assertTrue(self.store.status()["auto_sync_requested"])
        with mock.patch.object(Store, "_ensure_upload_ready"):
            restarted = Store(self.base / "state", allow_upload=True, worker=True)
            try:
                self.assertTrue(restarted.monitor_enabled)
                self.assertTrue(restarted.upload_enabled)
                self.assertTrue(restarted.auto_sync_requested)
                restarted.set_auto_sync(False)
                self.assertFalse(restarted.upload_enabled)
                self.assertFalse(restarted.monitor_enabled)
            finally:
                restarted.close()
        after_stop = Store(self.base / "state", allow_upload=True, worker=False)
        try:
            self.assertFalse(after_stop.auto_sync_requested)
            self.assertFalse(after_stop.monitor_enabled)
            self.assertFalse(after_stop.upload_enabled)
        finally:
            after_stop.close()

    def test_remove_and_readd_scans_new_media_but_keeps_accepted_history(self):
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        self.store.scan()
        source_id = self.store.sources()[0]["id"]
        accepted_id = next(j["id"] for j in self.store.jobs()
                           if j["path"] == str(self.media1))
        with self.store.connection() as con:
            con.execute("UPDATE jobs SET status='api_accepted' WHERE id=?", (accepted_id,))
        self.store.set_cloud_verified(accepted_id, True)
        self.store._set_source(source_id, enabled=False)
        self.store.set_display(source_id, "removed")
        self.assertIsNone(self.store._next_job())
        new_file = self.source / "photo C.jpg"
        new_file.write_bytes(b"C" * 16000)
        with mock.patch.object(self.store, "_digest", wraps=self.store._digest) as digest:
            self.store.add_source(str(self.source), "directory")
            self.wait_for_jobs(3)
            # Wait until the background scan has finished.
            with self.store.scan_lock:
                pass
            hashed_paths = [str(call.args[0]) for call in digest.call_args_list]
        self.assertNotIn(str(self.media1), hashed_paths)
        self.assertNotIn(str(self.media2), hashed_paths)
        self.assertIn(str(new_file), hashed_paths)
        jobs = {j["path"]: j for j in self.store.jobs()}
        self.assertEqual(jobs[str(self.media1)]["status"], "api_accepted")
        self.assertTrue(jobs[str(self.media1)]["cloud_verified_at"])
        self.assertEqual(jobs[str(self.media2)]["status"], "queued")
        self.assertEqual(jobs[str(new_file)]["status"], "queued")
        self.assertEqual(self.store.sources()[0]["id"], source_id)
        self.assertEqual(self.store.sources()[0]["album_confirmed"], 0)
        self.assertEqual(self.store.sources()[0]["upload_ready"], 0)

    def test_upload_quality_flag_and_history_are_isolated_per_source(self):
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        source = self.store.sources()[0]
        source_id = source["id"]
        self.store.set_album(source_id, source["album_name"])
        self.assertEqual(source["upload_quality"], "storage_saver")
        job = self.store.jobs()[0]
        with mock.patch("webui.server.subprocess.run",
                        return_value=mock.Mock(returncode=0,
                                               stdout='{"mediaKey":"TEST"}')) as run:
            ok, _, _, _ = self.store._run_upload(job)
        self.assertTrue(ok)
        self.assertIn("--saver", run.call_args.args[0])
        self.assertNotIn("--use-quota", run.call_args.args[0])
        with self.assertRaises(UserError):
            self.store.set_upload_quality(source_id, "invalid")
        self.store.set_upload_quality(source_id, "original")
        with mock.patch("webui.server.subprocess.run",
                        return_value=mock.Mock(returncode=0,
                                               stdout='{"mediaKey":"TEST"}')) as run:
            ok, _, _, _ = self.store._run_upload(job)
        self.assertTrue(ok)
        self.assertNotIn("--saver", run.call_args.args[0])
        self.assertNotIn("--use-quota", run.call_args.args[0])
        self.store.set_source_upload(source_id, True)
        self.store.upload_enabled = True
        with self.assertRaises(UserError):
            self.store.set_upload_quality(source_id, "storage_saver")
        self.store.upload_enabled = False
        with self.store.connection() as con:
            con.execute("UPDATE jobs SET status='api_accepted' WHERE id=?", (job["id"],))
        self.store.set_cloud_verified(job["id"], True)
        self.store.set_display(source_id, "removed")
        self.store.add_source(str(self.source), "directory")
        self.assertEqual(self.store.sources()[0]["upload_quality"], "original")
        accepted = next(j for j in self.store.jobs() if j["id"] == job["id"])
        self.assertEqual(accepted["status"], "api_accepted")
        self.assertTrue(accepted["cloud_verified_at"])

    def test_cli_album_flag_and_single_image_mode(self):
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        source_id = self.store.sources()[0]["id"]
        self.store.set_album(source_id, "旅程测试相册")
        job = self.store.jobs()[0]
        with mock.patch("webui.server.subprocess.run",
                        return_value=mock.Mock(returncode=0,
                                               stdout='{"mediaKey":"MOCK_MEDIA_KEY"}')) as cmd:
            ok, key, error, album_key = self.store._run_upload(job)
        self.assertTrue(ok)
        self.assertEqual(key, "MOCK_MEDIA_KEY")
        args = cmd.call_args.args[0]
        self.assertIn("--saver", args)
        self.assertEqual(args[args.index("--album") + 1], "旅程测试相册")
        self.assertNotIn("--delete", args)
        self.assertNotIn("--force", args)
        # Simulate an explicit one-photo request without using a Google account.
        self.store.allow_upload = True
        # CI runners don't contain the untracked, locally pinned gotohp binary.
        # _auth_ready() must pass with an isolated fake CLI, never the real CLI.
        self.store.cli = self.base / "fake_gotohp_cli"
        self.store.cli.write_bytes(b"FAKE_CLI_FOR_TEST_ONLY")
        self.store.credentials.write_text("FAKE_TOKEN_NOT_A_CREDENTIAL")
        self.store.credentials.chmod(0o600)
        self.store._run_upload = mock.Mock(return_value=(True, "MOCK_ONE", ""))
        with mock.patch.object(self.store, "_ensure_upload_ready"):
            result = self.store.test_one_image(source_id)
        self.assertEqual(result["status"], "api_accepted")
        self.assertEqual(result["album"], "旅程测试相册")
        self.store._run_upload.assert_called_once()
        self.assertFalse(self.store.upload_enabled)  # Never opens the batch gate.

    def test_single_cloud_album_reused_across_uploads_and_restart(self):
        """First upload creates a named album; next must use its returned ID."""
        self.media2.write_bytes(b"B" * 15000)
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        self.store.set_album(self.store.sources()[0]["id"], "source")
        album_key = "AF1Qip" + "K" * 36
        self.store.allow_upload = True
        self.store.cli = self.base / "fake_cli"
        self.store.cli.write_bytes(b"FAKE")
        self.store.credentials.write_text("NOT_A_REAL_TOKEN")
        self.store.credentials.chmod(0o600)
        responses = [
            mock.Mock(returncode=0, stdout=json.dumps({
                "results": [{"mediaKey": "MOCK_FIRST"}],
                "album": {"albumKeys": [album_key]}
            })),
            mock.Mock(returncode=0, stdout=json.dumps({
                "results": [{"mediaKey": "MOCK_SECOND"}],
                "album": {"albumKeys": [album_key]}
            })),
        ]
        with mock.patch.object(self.store, "_ensure_upload_ready"), mock.patch(
                "webui.server.subprocess.run", side_effect=responses) as runner:
            self.assertTrue(self.store.run_one(manual=True))
            self.assertEqual(self.store.sources()[0]["album_key"], album_key)
            self.assertTrue(self.store.run_one(manual=True))
        first, second = (c.args[0] for c in runner.call_args_list)
        self.assertEqual(first[first.index("--album") + 1], "source")
        self.assertEqual(second[second.index("--album") + 1], album_key)
        self.assertEqual({j["status"] for j in self.store.jobs()}, {"api_accepted"})
        reopened = Store(self.base / "state", worker=False)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.sources()[0]["album_key"], album_key)

    def test_old_accepted_source_requires_existing_album_binding(self):
        self.media2.write_bytes(b"B" * 15000)
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        source_id = self.store.sources()[0]["id"]
        first = self.store.jobs()[0]
        with self.store.connection() as con:
            con.execute("UPDATE jobs SET status='api_accepted',media_key='ALREADY_IN_CLOUD' WHERE id=?",
                        (first["id"],))
        self.assertIsNone(self.store._next_job())
        with self.assertRaises(UserError):
            self.store.bind_album(source_id, "https://other.example.com/album/bad")
        key = "AF1Qip" + "Z" * 36
        self.store.bind_album(source_id, "https://photos.google.com/album/" + key)
        self.assertEqual(self.store.sources()[0]["album_key"], key)
        self.store.set_source_upload(source_id, True)
        self.assertIsNotNone(self.store._next_job())
        with self.assertRaises(UserError):
            self.store.set_album(source_id, "different album")
        with self.assertRaises(UserError):
            self.store.bind_album(source_id, "AF1Qip" + "Y" * 36)
        self.assertEqual(self.store.sources()[0]["album_key"], key)
        self.assertEqual(len(self.store.jobs()), 2)

    def test_success_without_album_key_halts_before_next_upload(self):
        self.media2.write_bytes(b"B" * 15000)
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        self.store.set_album(self.store.sources()[0]["id"], "source")
        self.store.allow_upload = True
        self.store.cli = self.base / "fake_cli"
        self.store.cli.write_bytes(b"FAKE")
        self.store.credentials.write_text("NOT_A_REAL_TOKEN")
        self.store.credentials.chmod(0o600)
        with mock.patch.object(self.store, "_ensure_upload_ready"), mock.patch(
                "webui.server.subprocess.run",
                return_value=mock.Mock(returncode=0,
                                       stdout='{"mediaKey":"MOCK_ACCEPTED"}')):
            self.assertTrue(self.store.run_one(manual=True))
        self.assertEqual(self.store.sources()[0]["album_key"], "")
        self.assertIsNone(self.store._next_job())
        self.assertEqual(sum(j["status"] == "api_accepted" for j in self.store.jobs()), 1)

    def test_album_persists_after_hide_remove_and_readd(self):
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        source_id = self.store.sources()[0]["id"]
        self.store.set_album(source_id, "南宁武汉贵州长白山")
        self.store.set_display(source_id, "removed")
        with mock.patch.object(self.store, "request_scan") as request_scan:
            self.store.add_source(str(self.source), "directory")
            request_scan.assert_called_once()
        source = self.store.sources()[0]
        self.assertEqual(source["id"], source_id)
        self.assertEqual(source["album_name"], "南宁武汉贵州长白山")
        self.assertEqual(source["album_confirmed"], 1)
        other = Store(self.base / "state", worker=False)
        try:
            self.assertEqual(other.sources()[0]["album_name"], "南宁武汉贵州长白山")
        finally:
            other.close()

    def test_owned_engine_requires_fixed_build_path_and_fail_closed(self):
        with self.assertRaises(UserError):
            Store(self.base / "invalid", engine="unknown", worker=False)
        fake = self.base / "not-the-local-build"
        fake.write_bytes(b"FAKE_CLI")
        owned = Store(self.base / "separate-owned", worker=False, engine="owned",
                      allow_upload=True, cli=fake)
        try:
            owned.credentials.write_text("INERT-TEST-ONLY", encoding="utf-8")
            owned.credentials.chmod(0o600)
            with mock.patch("webui.server.verify_owned_engine",
                            return_value=(True, "fake verifier")):
                with self.assertRaises(UserError):
                    owned.set_upload(True)
            self.assertFalse(owned.upload_enabled)
            self.assertEqual(owned.status()["upload_engine"], "owned")
        finally:
            owned.close()

    def test_add_folder_and_detect_dedup(self):
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        tasks = self.store.jobs()
        self.assertEqual(len(tasks), 2)
        self.assertEqual({x["status"] for x in tasks}, {"queued"})
        self.store.scan()
        self.assertEqual(len(self.store.jobs()), 2)
        self.assertEqual(sum(x["bytes"] for x in self.store.jobs()), 30000)

    def test_single_file_and_edit(self):
        self.store.add_source(str(self.media1), "file")
        self.wait_for_jobs(1)
        self.assertEqual(len(self.store.jobs()), 1)
        with self.assertRaises(UserError):
            self.store.add_source(str(self.source / "other.txt"), "file")
        self.media1.write_bytes(b"Z" * 20000)
        for _ in range(100):
            self.store.scan()
            if self.store.jobs()[0]["bytes"] == 20000:
                break
            time.sleep(0.02)
        self.assertEqual(len(self.store.jobs()), 1)
        self.assertEqual(self.store.jobs()[0]["bytes"], 20000)

    def test_mock_upload_retry_and_cross_path_dedup(self):
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        self.store.set_album(self.store.sources()[0]["id"], "Mock Album")
        self.store.set_source_upload(self.store.sources()[0]["id"], True)
        self.store.allow_upload = True
        self.store.cli = self.base / "mock_cli_binary"
        self.store.cli.write_bytes(b"dummy")
        self.store.credentials.write_bytes(b"TEST-ONLY-NO-GOOGLE-TOKEN")
        self.store.credentials.chmod(0o600)
        self.store.upload_enabled = True
        calls = []
        def fake_upload(job):
            calls.append(job["path"])
            if len(calls) == 1:
                return False, "", "mock timeout"
            return True, "MOCK_MEDIA_KEY_NOT_CLOUD_VERIFIED", ""
        self.store._run_upload = fake_upload
        self.store._ensure_upload_ready = mock.Mock()  # Mock CLI has no real signed binary.
        self.assertTrue(self.store.run_one())
        self.assertEqual(len([x for x in self.store.jobs() if x["status"] == "retrying"]), 1)
        # The second identical item can be accepted independently if no success existed.
        self.assertTrue(self.store.run_one())
        states = {x["status"] for x in self.store.jobs()}
        self.assertEqual(states, {"api_accepted", "duplicate"})
        self.assertEqual(len(calls), 2)
        # Duplicate content is not uploaded again, nor counted as cloud-verified.
        self.assertFalse(self.store.run_one())

    def test_failed_job_can_retry(self):
        self.store.add_source(str(self.media1), "file")
        self.wait_for_jobs(1)
        self.store.set_album(self.store.sources()[0]["id"], "Mock Album")
        self.store.set_source_upload(self.store.sources()[0]["id"], True)
        self.store.allow_upload = True
        self.store.cli = self.base / "cli"
        self.store.cli.write_bytes(b"mock")
        self.store.credentials.write_bytes(b"fake-not-real")
        self.store.credentials.chmod(0o600)
        self.store.upload_enabled = True
        self.store._run_upload = lambda _: (False, "", "mock failure")
        self.store._ensure_upload_ready = mock.Mock()  # No real credentials or CLI.
        for attempt in range(3):
            self.assertTrue(self.store.run_one())
            with self.store.connection() as con:
                con.execute("UPDATE jobs SET retry_at=0 WHERE status='retrying'")
        job = self.store.jobs()[0]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["attempts"], 3)
        self.store.retry(job["id"])
        self.assertEqual(self.store.jobs()[0]["status"], "queued")
        self.store._run_upload = lambda _: (True, "MOCK_ACCEPTED", "")
        self.assertTrue(self.store.run_one())
        self.assertEqual(self.store.jobs()[0]["status"], "api_accepted")

    def test_pause_remove_not_delete_actual_file(self):
        self.store.add_source(str(self.media1), "file")
        self.wait_for_jobs(1)
        source_id = self.store.sources()[0]["id"]
        self.store._set_source(source_id, enabled=False)
        self.assertEqual(self.store.jobs()[0]["status"], "paused")
        self.store._set_source(source_id, enabled=True)
        self.assertEqual(self.store.jobs()[0]["status"], "queued")
        self.store._set_source(source_id, remove=True)
        self.assertTrue(self.media1.is_file())
        self.assertEqual(self.store.jobs()[0]["status"], "queued")  # UI-only removal preserves progress
        self.assertTrue(self.store.sources()[0]["archived"])
        self.assertEqual(self.store.sources()[0]["ui_visibility"], "removed")

    def test_browse_no_symlink_or_hidden(self):
        self.store.browse(str(self.source))
        listing = self.store.browse(str(self.source))
        names = {x["name"] for x in listing["files"]}
        self.assertEqual(names, {"photo A.jpg", "photo B.jpg"})
        with self.assertRaises(UserError):
            self.store.browse("/etc")

    def test_http_serves_ui_and_blocks_csrf(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def req(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            payload = json.dumps(body).encode() if body is not None else None
            hdrs = headers or {}
            if payload is not None:
                hdrs = {**hdrs, "Content-Type": "application/json"}
            conn.request(method, path, payload, hdrs)
            resp = conn.getresponse()
            data = resp.read()
            conn.close()
            if resp.getheader("Content-Type", "").startswith("application/json"):
                return resp.status, json.loads(data)
            return resp.status, data
        status, html = req("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"GoogleUnlimited", html)
        self.assertIn(b'id="storage-network"', html)
        self.assertIn(b'id="page-next"', html)
        self.assertIn(b'id="show-hidden-btn"', html)
        self.assertIn(b'<details id="index-details"', html)
        self.assertIn(b'<details id="jobs-details"', html)
        status, payload = req("GET", "/api/status")
        self.assertEqual(status, 200)
        token = payload["csrf"]
        status, _ = req("POST", "/api/monitor", {"enabled": True})
        self.assertEqual(status, 403)
        status, _ = req("POST", "/api/monitor", {"enabled": True},
                        {"X-GU-CSRF": token, "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, data = req("POST", "/api/monitor", {"enabled": True}, {"X-GU-CSRF": token})
        self.assertEqual(status, 200)
        self.assertTrue(data["monitor_enabled"])
        status, _ = req("POST", "/api/sources", {"path": str(self.media1), "kind": "file"},
                        {"X-GU-CSRF": token})
        self.assertEqual(status, 200)
        self.wait_for_jobs(1)
        source_id = self.store.sources()[0]["id"]
        status, _ = req("POST", "/api/source/quality",
                        {"id": source_id, "quality": "original"})
        self.assertEqual(status, 403)
        status, result = req("POST", "/api/source/quality",
                             {"id": source_id, "quality": "original"},
                             {"X-GU-CSRF": token})
        self.assertEqual(status, 200)
        self.assertEqual(self.store.sources()[0]["upload_quality"], "original")
        status, _ = req("POST", "/api/source/quality",
                        {"id": source_id, "quality": "unsupported"},
                        {"X-GU-CSRF": token})
        self.assertEqual(status, 400)
        status, tasks = req("GET", "/api/jobs")
        self.assertEqual(status, 200)
        self.assertTrue(isinstance(tasks["jobs"], list))
        self.assertEqual(tasks["total"], 1)
        status, page = req("GET", "/api/jobs?limit=1&offset=1")
        self.assertEqual(status, 200)
        self.assertEqual(page["jobs"], [])
        self.assertEqual(page["total"], 1)
        status, volumes = req("GET", "/api/volumes")
        self.assertEqual(status, 200)
        self.assertIn("network", volumes)
        status, _ = req("GET", "/api/folders?source_id=1")
        self.assertEqual(status, 200)
        status, only_videos = req("GET", "/api/jobs?kind=video")
        self.assertEqual(status, 200)
        self.assertEqual(only_videos["jobs"], [])
        status, _ = req("POST", "/api/share/connect",
                        {"url": "smb://username:password@remote.example/Photos"},
                        {"X-GU-CSRF": token})
        self.assertEqual(status, 400)
        status, _ = req("POST", "/api/source/restore", {"id": 99999},
                        {"X-GU-CSRF": token})
        self.assertEqual(status, 400)
        self.wait_for_jobs(1)
        job_id = self.store.jobs()[0]["id"]
        source_id = self.store.sources()[0]["id"]
        status, _ = req("POST", "/api/source/test-upload", {"id": source_id},
                        {"X-GU-CSRF": token})
        self.assertEqual(status, 400)  # Isolated credentials absent.
        status, album = req("POST", "/api/source/album",
                            {"id": source_id, "album_name": "旅行相册"},
                            {"X-GU-CSRF": token})
        self.assertEqual(status, 200)
        self.assertEqual(self.store.sources()[0]["album_name"], "旅行相册")
        self.assertEqual(self.store.sources()[0]["album_confirmed"], 1)
        status, permission = req("POST", "/api/source/upload",
                                 {"id": source_id, "enabled": True},
                                 {"X-GU-CSRF": token})
        self.assertEqual(status, 200)
        self.assertTrue(next(x for x in permission["sources"] if x["id"] == source_id)["upload_ready"])
        status, permission = req("POST", "/api/source/upload",
                                 {"id": source_id, "enabled": False},
                                 {"X-GU-CSRF": token})
        self.assertEqual(status, 200)
        self.assertFalse(next(x for x in permission["sources"] if x["id"] == source_id)["upload_ready"])
        with self.store.connection() as con:
            con.execute("UPDATE jobs SET status='api_accepted' WHERE id=?", (job_id,))
        status, _ = req("POST", "/api/cloud/verify", {"id": job_id, "verified": True},
                        {"X-GU-CSRF": token})
        self.assertEqual(status, 200)
        self.assertTrue(self.store.jobs()[0]["cloud_verified_at"])
        status, _ = req("POST", "/api/source/remove", {"id": source_id},
                        {"X-GU-CSRF": token})
        self.assertEqual(status, 200)
        self.assertTrue(self.store.sources()[0]["archived"])
        status, _ = req("POST", "/api/source/restore", {"id": source_id},
                        {"X-GU-CSRF": token})
        self.assertEqual(status, 200)
        self.assertEqual(self.store.sources()[0]["id"], source_id)
        self.assertTrue(self.store.jobs()[0]["cloud_verified_at"])
        status, _ = req("POST", "/api/source/display",
                        {"id": source_id, "mode": "hidden"}, {"X-GU-CSRF": token})
        self.assertEqual(status, 200)
        status, hidden_status = req("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(hidden_status["sources"], [])
        self.assertEqual(hidden_status["hidden_sources"], 1)
        status, hidden_jobs = req("GET", "/api/jobs")
        self.assertEqual(status, 200)
        self.assertEqual(hidden_jobs["total"], 0)
        status, all_status = req("GET", "/api/status?include_hidden=1")
        self.assertEqual(status, 200)
        self.assertEqual(all_status["sources"][0]["id"], source_id)
        status, all_jobs = req("GET", "/api/jobs?include_hidden=1")
        self.assertEqual(status, 200)
        self.assertEqual(all_jobs["total"], 1)
        status, event_result = req("GET", "/api/source/events?source_id=" + str(source_id))
        self.assertEqual(status, 200)
        self.assertIn("hidden", [event["action"] for event in event_result["events"]])
        status, _ = req("POST", "/api/source/display",
                        {"id": source_id, "mode": "removed"}, {"X-GU-CSRF": token})
        self.assertEqual(status, 200)
        self.assertTrue(self.store.sources()[0]["archived"])
        self.assertTrue(self.store.jobs()[0]["cloud_verified_at"])
        status, _ = req("POST", "/api/upload", {"enabled": True}, {"X-GU-CSRF": token})
        self.assertEqual(status, 400)  # No independent credentials / runtime gate

    def test_credentials_not_persistently_activated(self):
        self.store.add_source(str(self.media1), "file")
        # add_source starts an asynchronous scan; wait for its durable write
        # rather than racing against the newly created scanning thread.
        import time
        for _ in range(100):
            if len(self.store.jobs()) == 1:
                break
            time.sleep(0.02)
        self.assertEqual(len(self.store.jobs()), 1)
        self.store.upload_enabled = True
        other = Store(self.base / "state", worker=False)
        try:
            self.assertFalse(other.upload_enabled)
            self.assertFalse(other.monitor_enabled)
            self.assertEqual(len(other.jobs()), 1)
        finally:
            other.close()

    def test_folder_index_persists_photo_video_formats(self):
        subdir = self.source / "Nested"
        subdir.mkdir()
        video = subdir / "holiday.MP4"
        video.write_bytes(b"V" * 23000)
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(3)
        self.store.scan()
        source = self.store.sources()[0]
        self.assertEqual(source["indexed_files"], 3)
        self.assertEqual(source["images"], 2)
        self.assertEqual(source["videos"], 1)
        jobs = self.store.jobs()
        self.assertEqual([x["extension"] for x in jobs if x["media_kind"] == "video"], ["mp4"])
        roots = {x["path"]: x for x in self.store.folders(source["id"])}
        self.assertEqual(roots[str(self.source)]["file_count"], 3)
        self.assertEqual(roots[str(subdir)]["video_count"], 1)
        self.assertEqual(self.store.jobs_total(), 3)
        self.assertEqual(self.store.jobs_total(kind="video"), 1)
        self.assertEqual(len(self.store.jobs(limit=1, offset=0)), 1)
        self.assertEqual(len(self.store.jobs(limit=1, offset=1)), 1)
        self.assertEqual(len(self.store.jobs(limit=1, offset=2)), 1)
        self.assertEqual(len(self.store.jobs(limit=1, offset=3)), 0)

    def test_archive_restore_same_id_and_cloud_verification(self):
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        source_id = self.store.sources()[0]["id"]
        job_id = self.store.jobs()[0]["id"]
        with self.store.connection() as con:
            con.execute("UPDATE jobs SET status='api_accepted',media_key='MOCK_ONLY' WHERE id=?", (job_id,))
        self.store.set_cloud_verified(job_id, True)
        self.assertTrue(self.store.jobs()[0]["cloud_verified_at"])
        self.store._set_source(source_id, remove=True)
        archived = self.store.sources()[0]
        self.assertTrue(archived["archived"])
        self.assertFalse(archived["enabled"])
        self.assertEqual(archived["indexed_files"], 2)
        self.assertEqual(archived["cloud_verified"], 1)
        self.assertTrue(self.media1.exists())
        with mock.patch.object(self.store, "request_scan") as scan_requested:
            self.store.restore_source(source_id)
            scan_requested.assert_called_once()  # Incremental scan uses cached metadata.
        self.wait_for_jobs(2)
        self.assertEqual(self.store.sources()[0]["id"], source_id)
        self.assertFalse(self.store.sources()[0]["archived"])
        self.assertEqual(len(self.store.jobs()), 2)
        restored = next(x for x in self.store.jobs() if x["id"] == job_id)
        self.assertEqual(restored["status"], "api_accepted")
        self.assertEqual(restored["cloud_verify_method"], "manual")
        reopened = Store(self.base / "state", worker=False)
        try:
            self.assertEqual(reopened.sources()[0]["id"], source_id)
            self.assertEqual(reopened.folders(source_id)[0]["verified_count"], 1)
        finally:
            reopened.close()

    def test_mount_disconnect_reconnect_keeps_history(self):
        mounts = [{"path": str(self.source), "name": "FakeNetworkShare",
                   "fs_type": "smbfs", "kind": "network", "mounted": True}]
        with mock.patch.object(self.store, "_mounted_volumes", return_value=mounts):
            self.store.add_source(str(self.source), "directory")
            self.wait_for_jobs(2)
            self.assertEqual(self.store.sources()[0]["location_kind"], "network")
            self.store.scan()
        with mock.patch.object(self.store, "_mounted_volumes", return_value=[]):
            self.store.scan()
            self.assertEqual(self.store.sources()[0]["availability"], "offline")
            self.assertTrue(all(not job["file_available"] for job in self.store.jobs()))
            self.assertEqual(self.store.folders(self.store.sources()[0]["id"])[0]["missing_count"], 2)
            self.assertIsNone(self.store._next_job())
        with mock.patch.object(self.store, "_mounted_volumes", return_value=mounts):
            self.store.scan()
        self.assertEqual(self.store.sources()[0]["availability"], "online")
        self.assertEqual(len(self.store.jobs()), 2)
        self.assertTrue(all(job["file_available"] for job in self.store.jobs()))
        self.assertEqual(self.store.folders(self.store.sources()[0]["id"])[0]["missing_count"], 0)

    def test_missing_local_file_still_indexed(self):
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        self.media1.unlink()  # Only deletes test-created fake data, never user media.
        self.store.scan()
        self.assertEqual(len(self.store.jobs()), 2)
        self.assertEqual(sum(not job["file_available"] for job in self.store.jobs()), 1)
        source_id = self.store.sources()[0]["id"]
        self.assertEqual(self.store.folders(source_id)[0]["missing_count"], 1)

    def test_restart_interrupted_upload_never_auto_retries(self):
        self.store.add_source(str(self.media1), "file")
        self.wait_for_jobs(1)
        job_id = self.store.jobs()[0]["id"]
        with self.store.connection() as con:
            con.execute("UPDATE jobs SET status='uploading' WHERE id=?", (job_id,))
        restarted = Store(self.base / "state", worker=False)
        try:
            job = restarted.jobs()[0]
            self.assertEqual(job["status"], "interrupted")
            self.assertIn("云端结果未知", job["error"])
            restarted.retry(job_id)
            self.assertEqual(restarted.jobs()[0]["status"], "queued")
        finally:
            restarted.close()

    def test_network_mount_listing_and_no_password_in_webui(self):
        sample = ("//user@nas/share on /Volumes/Remote Photos (smbfs, nodev, nosuid)\n"
                  "/srv/data on /Volumes/Archive (nfs, nosuid)\n"
                  "/dev/disk1 on /Volumes/Local (apfs, local)\n")
        with mock.patch("webui.server.sys.platform", "darwin"), mock.patch(
                "webui.server.subprocess.run",
                return_value=mock.Mock(returncode=0, stdout=sample)):
            mounts = self.store._mounted_volumes()
            self.assertEqual({m["name"] for m in mounts}, {"Remote Photos", "Archive"})
        with mock.patch("webui.server.sys.platform", "darwin"), mock.patch(
                "webui.server.subprocess.Popen") as opener:
            with self.assertRaises(UserError):
                self.store.connect_share("smb://username:password@nas/share")
            opener.assert_not_called()
            result = self.store.connect_share("smb://nas/Photos")
            self.assertTrue(result["requested"])
            self.assertIn("尚未确认挂载成功", result["message"])
            opener.assert_called_once()

    def test_legacy_db_migrates_without_erasing_success(self):
        location = self.base / "legacy-state"
        location.mkdir()
        db = sqlite3.connect(location / "queue.sqlite3")
        db.executescript("""
            CREATE TABLE sources(id INTEGER PRIMARY KEY,path TEXT UNIQUE,kind TEXT,enabled INTEGER,added_at TEXT);
            CREATE TABLE jobs(id INTEGER PRIMARY KEY,path TEXT UNIQUE,name TEXT,source_id INTEGER,
                bytes INTEGER,digest TEXT,status TEXT,attempts INTEGER,retry_at REAL,error TEXT,
                media_key TEXT,duplicate_of INTEGER,updated_at TEXT);
        """)
        db.execute("INSERT INTO sources VALUES(1,?,'file',1,'old')", (str(self.media1),))
        db.execute("""INSERT INTO jobs VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (str(self.media1), "photo A.jpg", 1, 15000, "fakehash", "api_accepted",
                    0, 0, "", "OLD_API_KEY", None, "old"))
        db.commit()
        db.close()
        migrated = Store(location, worker=False)
        try:
            self.assertEqual(migrated.jobs()[0]["status"], "api_accepted")
            self.assertEqual(migrated.jobs()[0]["media_kind"], "image")
            self.assertEqual(migrated.sources()[0]["indexed_files"], 1)
            self.assertEqual(migrated.folders(1)[0]["accepted_count"], 1)
        finally:
            migrated.close()

    def test_orphaned_legacy_jobs_restore_archived_source(self):
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        source_id = self.store.sources()[0]["id"]
        job_id = self.store.jobs()[0]["id"]
        with self.store.connection() as con:
            con.execute("""UPDATE jobs SET status='api_accepted',media_key='OLD_MOCK'
                           WHERE id=?""", (job_id,))
            # Simulate old WebUI's destructive removal of a source row.
            con.execute("DELETE FROM sources WHERE id=?", (source_id,))
        self.assertEqual(self.store.sources(), [])
        reopened = Store(self.base / "state", worker=False)
        try:
            recovered = reopened.sources()[0]
            self.assertEqual(recovered["id"], source_id)
            self.assertEqual(recovered["path"], str(self.source))
            self.assertTrue(recovered["archived"])
            self.assertFalse(recovered["enabled"])
            self.assertEqual(recovered["indexed_files"], 2)
            self.assertEqual(len(reopened.jobs()), 2)
            self.assertEqual(next(j for j in reopened.jobs() if j["id"] == job_id)["status"],
                             "api_accepted")
            self.assertEqual(reopened.folders(source_id)[0]["file_count"], 2)
            self.assertFalse(reopened.upload_enabled)
            self.assertFalse(reopened.monitor_enabled)
            # Recovery of metadata alone must not requeue any previously accepted job.
        finally:
            reopened.close()

    def test_hide_shows_clean_ui_without_mutating_upload_history(self):
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        source_id = self.store.sources()[0]["id"]
        job_id = self.store.jobs()[0]["id"]
        with self.store.connection() as con:
            con.execute("""UPDATE jobs SET status='api_accepted',media_key='TEST_KEY'
                           WHERE id=?""", (job_id,))
        self.store.set_cloud_verified(job_id, True)
        snapshot = [(j["id"], j["digest"], j["status"], j["cloud_verified_at"])
                    for j in self.store.jobs()]
        self.store.set_display(source_id, "hidden")
        self.assertEqual(self.store.sources(include_hidden=False), [])
        self.assertEqual(self.store.jobs(include_hidden=False), [])
        self.assertEqual(self.store.jobs_total(include_hidden=False), 0)
        self.assertEqual(self.store.status(include_hidden=False)["indexed"]["files"], 0)
        self.assertEqual(self.store.status(include_hidden=False)["hidden_sources"], 1)
        self.assertEqual(len(self.store.jobs()), 2)
        self.assertEqual(self.store.sources()[0]["enabled"], 1)  # Hide does not stop monitoring.
        self.assertEqual(snapshot, [
            (j["id"], j["digest"], j["status"], j["cloud_verified_at"])
            for j in self.store.jobs()])
        self.assertEqual(self.store.source_events(source_id)[0]["action"], "hidden")
        self.store.set_display(source_id, "visible")
        self.assertEqual(len(self.store.sources(include_hidden=False)), 1)
        self.assertEqual(self.store.jobs_total(include_hidden=False), 2)

    def test_remove_display_and_readd_reuses_sqlite_backup_progress(self):
        self.store.add_source(str(self.source), "directory")
        self.wait_for_jobs(2)
        source_id = self.store.sources()[0]["id"]
        first = self.store.jobs()[0]
        with self.store.connection() as con:
            con.execute("""UPDATE jobs SET status='api_accepted',media_key='TEST_KEY'
                           WHERE id=?""", (first["id"],))
        self.store.set_cloud_verified(first["id"], True)
        before = [(j["id"], j["digest"], j["status"], j["cloud_verified_at"],
                   j["attempts"]) for j in self.store.jobs()]
        self.store.set_display(source_id, "removed")
        self.assertEqual(self.store.sources(include_hidden=False), [])
        self.assertEqual(self.store.jobs_total(include_hidden=False), 0)
        self.assertTrue(self.store.sources()[0]["archived"])
        self.assertFalse(self.store.sources()[0]["enabled"])
        self.assertEqual(self.store.jobs_total(), 2)
        self.assertEqual(self.store.folders(source_id)[0]["file_count"], 2)
        with mock.patch.object(self.store, "request_scan") as scan:
            self.store.add_source(str(self.source), "directory")
            scan.assert_called_once()  # Incremental scan reuses cached file hashes.
        self.assertEqual(self.store.sources()[0]["id"], source_id)
        self.assertEqual(self.store.sources()[0]["ui_visibility"], "visible")
        self.assertEqual(self.store.jobs_total(), 2)
        self.assertEqual(before, [
            (j["id"], j["digest"], j["status"], j["cloud_verified_at"],
             j["attempts"]) for j in self.store.jobs()])
        actions = [e["action"] for e in self.store.source_events(source_id)]
        self.assertIn("removed", actions)
        self.assertIn("reload", actions)
        reopened = Store(self.base / "state", worker=False)
        try:
            self.assertEqual(reopened.sources()[0]["id"], source_id)
            self.assertEqual(reopened.jobs_total(), 2)
            self.assertEqual(reopened.folders(source_id)[0]["verified_count"], 1)
            self.assertIn("removed",
                          [e["action"] for e in reopened.source_events(source_id)])
        finally:
            reopened.close()

    def test_invalid_display_action_does_not_delete_data(self):
        self.store.add_source(str(self.media1), "file")
        self.wait_for_jobs(1)
        source_id = self.store.sources()[0]["id"]
        with self.assertRaises(UserError):
            self.store.set_display(source_id, "delete_database")
        with self.assertRaises(UserError):
            self.store.set_display(999999, "removed")
        self.assertEqual(len(self.store.jobs()), 1)
        self.assertTrue(self.media1.exists())


if __name__ == "__main__":
    unittest.main()
