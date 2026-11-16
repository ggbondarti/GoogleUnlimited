"""Regression tests for manual incremental scans and live upload telemetry."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from webui.server import Store


class ManualIncrementalSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gu_incremental_test_", dir=Path.home())
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.source.mkdir()
        self.photo = self.source / "first.jpg"
        self.photo.write_bytes(b"A" * 16000)
        self.store = Store(self.base / "state", worker=False)
        self.addCleanup(self.store.close)
        self.store.add_source(str(self.source), "directory")
        self.wait_for_scan()
        self.source_id = self.store.sources()[0]["id"]

    def wait_for_scan(self):
        for _ in range(300):
            if not self.store.scan_thread_running and not self.store.scan_running:
                return
            time.sleep(0.01)
        self.fail("scan did not finish")

    def test_initial_scan_requires_explicit_confirmation(self):
        jobs = self.store.jobs(source_id=self.source_id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["status"], "pending_review")
        self.assertIsNone(self.store._next_job())
        self.store.set_album(self.source_id, "Test Album")
        result = self.store.confirm_scan(self.source_id, [jobs[0]["id"]])
        self.assertEqual(result["confirmed_count"], 1)
        self.assertEqual(self.store.jobs(source_id=self.source_id)[0]["status"], "queued")

    def test_incremental_scan_only_adds_new_file_to_review(self):
        original = self.store.jobs(source_id=self.source_id)[0]
        self.store.set_album(self.source_id, "Test Album")
        self.store.confirm_scan(self.source_id, [original["id"]])
        with self.store.connection() as con:
            con.execute("UPDATE jobs SET status='api_accepted',media_key='OLD_MEDIA' WHERE id=?",
                        (original["id"],))
        new_file = self.source / "second.mp4"
        new_file.write_bytes(b"B" * 24000)
        report = self.store.scan(source_id=self.source_id)
        self.assertEqual(report["new"], 1)
        jobs = {j["path"]: j for j in self.store.jobs(source_id=self.source_id)}
        self.assertEqual(jobs[str(self.photo)]["status"], "api_accepted")
        self.assertEqual(jobs[str(new_file)]["status"], "pending_review")
        self.assertIsNone(self.store._next_job())
        self.store.confirm_scan(self.source_id, [jobs[str(new_file)]["id"]])
        self.assertEqual(self.store.jobs(source_id=self.source_id)[0]["status"], "queued")

    def test_partial_confirmation_queues_only_selected_new_files(self):
        original = self.store.jobs(source_id=self.source_id)[0]
        self.store.set_album(self.source_id, "Test Album")
        self.store.confirm_scan(self.source_id, [original["id"]])
        first = self.source / "second.jpg"
        second = self.source / "third.mp4"
        first.write_bytes(b"B" * 12000)
        second.write_bytes(b"C" * 24000)
        self.store.scan(source_id=self.source_id)
        jobs = {job["path"]: job for job in self.store.jobs(source_id=self.source_id)}
        result = self.store.confirm_scan(self.source_id, [jobs[str(first)]["id"]])
        self.assertEqual(result["confirmed_count"], 1)
        self.assertEqual(jobs[str(first)]["status"], "pending_review")
        refreshed = {job["path"]: job for job in self.store.jobs(source_id=self.source_id)}
        self.assertEqual(refreshed[str(first)]["status"], "queued")
        self.assertEqual(refreshed[str(second)]["status"], "pending_review")
        self.assertIsNone(self.store._next_job())  # No upload permission was granted.

    def test_confirmation_rejects_files_changed_after_preview(self):
        job = self.store.jobs(source_id=self.source_id)[0]
        self.store.set_album(self.source_id, "Test Album")
        self.photo.write_bytes(b"changed after scan" * 1200)
        with self.assertRaisesRegex(Exception, "重新执行增量扫描"):
            self.store.confirm_scan(self.source_id, [job["id"]])
        self.assertEqual(self.store.jobs(source_id=self.source_id)[0]["status"], "pending_review")

    def test_scan_report_counts_locally_missing_files_without_deleting_history(self):
        self.photo.unlink()
        report = self.store.scan(source_id=self.source_id)
        self.assertEqual(report["missing"], 1)
        self.assertEqual(report["unavailable"], 0)
        job = self.store.jobs(source_id=self.source_id)[0]
        self.assertFalse(job["file_available"])
        self.assertEqual(job["status"], "pending_review")

    def test_changed_file_keeps_previous_uploaded_version_in_history(self):
        job = self.store.jobs(source_id=self.source_id)[0]
        self.store.set_album(self.source_id, "Test Album")
        self.store.confirm_scan(self.source_id, [job["id"]])
        with self.store.connection() as con:
            con.execute("UPDATE jobs SET status='api_accepted',media_key='OLD_MEDIA',cloud_verified_at='manual' WHERE id=?",
                        (job["id"],))
        self.photo.write_bytes(b"Z" * 18000)
        report = self.store.scan(source_id=self.source_id)
        self.assertEqual(report["changed"], 1)
        current = self.store.jobs(source_id=self.source_id)[0]
        self.assertEqual(current["status"], "pending_review")
        self.assertEqual(current["change_kind"], "changed")
        with self.store.connection() as con:
            history = con.execute("SELECT status,media_key,cloud_verified_at FROM job_history WHERE job_id=?",
                                  (job["id"],)).fetchone()
        self.assertEqual(history["status"], "api_accepted")
        self.assertEqual(history["media_key"], "OLD_MEDIA")
        self.assertEqual(history["cloud_verified_at"], "manual")

    def test_sync_toggles_do_not_trigger_directory_scans(self):
        with mock.patch.object(self.store, "request_scan") as request_scan:
            self.store.set_monitor(True)
            with mock.patch.object(self.store, "_ensure_upload_ready"):
                self.store.set_auto_sync(True)
            self.store._set_source(self.source_id, enabled=True)
            request_scan.assert_not_called()

    def test_worker_does_not_periodically_scan(self):
        self.store.monitor_enabled = True
        with mock.patch.object(self.store, "request_scan") as request_scan:
            worker = threading.Thread(target=self.store._worker_loop)
            worker.start()
            time.sleep(0.06)
            self.store.stop_event.set()
            self.store.wake_event.set()
            worker.join(timeout=2)
            request_scan.assert_not_called()

    def test_progress_snapshot_uses_real_byte_events_and_last_completed(self):
        job = self.store.jobs(source_id=self.source_id)[0]
        self.store._begin_progress(job)
        self.store._consume_cli_progress(job["id"], {
            "event": "thread_status", "status": "uploading",
            "file_name": "first.jpg", "bytes_uploaded": 100,
            "bytes_total": 16000, "attempt": 1
        })
        time.sleep(0.02)
        self.store._consume_cli_progress(job["id"], {
            "event": "thread_status", "status": "uploading",
            "file_name": "first.jpg", "bytes_uploaded": 2100,
            "bytes_total": 16000, "attempt": 1
        })
        snapshot = self.store.progress_snapshot()
        self.assertEqual(snapshot["current"]["file_name"], "first.jpg")
        self.assertEqual(snapshot["current"]["bytes_uploaded"], 2100)
        self.assertGreater(snapshot["current"]["speed_bps"], 0)
        self.assertEqual(snapshot["current"]["percent"], 13)
        self.assertIsNone(snapshot["queue"]["eta_seconds"])
        self.assertEqual(snapshot["queue"]["total"], 1)
        self.assertEqual(snapshot["queue"]["pending_review"], 1)

    def test_queue_eta_and_last_completed_use_real_completed_samples(self):
        self.store.set_album(self.source_id, "Test Album")
        for name, payload in (("second.jpg", b"B" * 12000),
                              ("third.jpg", b"C" * 13000),
                              ("fourth.mp4", b"D" * 24000)):
            (self.source / name).write_bytes(payload)
        self.store.scan(source_id=self.source_id)
        pending = self.store.jobs(source_id=self.source_id)
        self.store.confirm_scan(self.source_id, [job["id"] for job in pending])
        ordered = sorted(self.store.jobs(source_id=self.source_id), key=lambda job: job["id"])
        with self.store.connection() as con:
            for job, duration, completed in zip(
                    ordered[:3], (10.0, 20.0, 30.0),
                    ("2026-11-16T10:01:00", "2026-11-16T10:02:00", "2026-11-16T10:03:00")):
                con.execute("""UPDATE jobs SET status='api_accepted',media_key='MEDIA',
                               duration_seconds=?,completed_at=?,updated_at=? WHERE id=?""",
                            (duration, completed, completed, job["id"]))
        snapshot = self.store.progress_snapshot()
        self.assertEqual(snapshot["queue"]["done"], 3)
        self.assertEqual(snapshot["queue"]["total"], 4)
        self.assertEqual(snapshot["queue"]["percent"], 75)
        self.assertEqual(snapshot["queue"]["average_sample_count"], 3)
        self.assertEqual(snapshot["queue"]["average_file_seconds"], 20.0)
        self.assertEqual(snapshot["queue"]["eta_seconds"], 20.0)
        self.assertEqual(snapshot["last_completed"]["file_name"], ordered[2]["name"])

    def test_source_and_quality_changes_do_not_authorize_pending_files(self):
        job = self.store.jobs(source_id=self.source_id)[0]
        self.store.set_album(self.source_id, "Test Album")
        self.store.set_upload_quality(self.source_id, "storage_saver")
        self.store.set_source_upload(self.source_id, True)
        self.store.upload_enabled = True
        self.assertEqual(self.store.jobs(source_id=self.source_id)[0]["status"], "pending_review")
        self.assertIsNone(self.store._next_job())


if __name__ == "__main__":
    unittest.main()
