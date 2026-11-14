"""No-Google-credentials tests for independent local WebUI."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

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
        status, tasks = req("GET", "/api/jobs")
        self.assertEqual(status, 200)
        self.assertTrue(isinstance(tasks["jobs"], list))
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


if __name__ == "__main__":
    unittest.main()
