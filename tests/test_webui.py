"""No-Google-credentials tests for independent local WebUI."""
import http.client
import json
import os
from pathlib import Path
import sqlite3
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
        self.assertFalse(state["allow_real_upload"])
        self.assertFalse(state["credentials_present"])
        with self.assertRaises(UserError):
            self.store.set_upload(True)

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
        self.store.allow_upload = True
        self.store.cli = self.base / "cli"
        self.store.cli.write_bytes(b"mock")
        self.store.credentials.write_bytes(b"fake-not-real")
        self.store.credentials.chmod(0o600)
        self.store.upload_enabled = True
        self.store._run_upload = lambda _: (False, "", "mock failure")
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
        self.assertEqual(self.store.jobs()[0]["status"], "paused")

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
            scan_requested.assert_not_called()  # Loading 80+ GB history does not hash files.
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


if __name__ == "__main__":
    unittest.main()
