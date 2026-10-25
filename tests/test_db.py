import unittest
from pathlib import Path

from ysaas import db as dbm
from ysaas.db import Database
from helpers import make_video, tmpdir


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tmpdir()
        self.db = Database(Path(self._tmp.name) / "nested" / "x.db")  # parent dir is created

    def tearDown(self):
        self._tmp.cleanup()

    def test_lifecycle(self):
        v = make_video(1)
        self.assertTrue(self.db.should_process(v.video_id, 3))
        self.db.record_downloaded(v, Path("/tmp/a.mp4"))
        self.assertFalse(self.db.should_process(v.video_id, 3))
        self.assertEqual(self.db.backlog_count(3), 1)
        self.assertEqual(self.db.count_downloaded_today(), 1)
        self.db.record_uploaded(v.video_id, "yt123")
        self.assertEqual(self.db.backlog_count(3), 0)
        self.assertEqual(self.db.count_uploaded_today(), 1)
        self.assertEqual(self.db.get(v.video_id)["uploaded_video_id"], "yt123")

    def test_failed_download_retries_until_limit(self):
        v = make_video(2)
        for attempt in range(1, 4):
            self.assertTrue(self.db.should_process(v.video_id, 3))
            self.db.record_download_failed(v, f"boom {attempt}")
        self.assertFalse(self.db.should_process(v.video_id, 3))
        self.assertEqual(self.db.stuck_count(3), 1)
        self.assertEqual(self.db.reset_failed(), 1)
        self.assertTrue(self.db.should_process(v.video_id, 3))

    def test_skipped_is_remembered_and_hidden_from_recent(self):
        v = make_video(3)
        self.db.record_skipped(v)
        self.assertFalse(self.db.should_process(v.video_id, 3))
        self.assertEqual(self.db.recent(), [])
        self.assertEqual(self.db.status_counts()[dbm.SKIPPED], 1)

    def test_upload_failure_attempts(self):
        v = make_video(4)
        self.db.record_downloaded(v, Path("/tmp/b.mp4"))
        for _ in range(3):
            self.assertEqual(len(self.db.pending_uploads(10, 3)), 1)
            self.db.record_upload_failed(v.video_id, "nope")
        self.assertEqual(self.db.pending_uploads(10, 3), [])
        self.assertEqual(self.db.backlog_count(3), 0)
        self.assertEqual(self.db.stuck_count(3), 1)

    def test_pending_order_and_limit(self):
        for n in (1, 2, 3):
            self.db.record_downloaded(make_video(n), Path(f"/tmp/{n}.mp4"))
        rows = self.db.pending_uploads(2, 3)
        self.assertEqual([r["video_id"] for r in rows], ["vid1", "vid2"])
        self.assertEqual(self.db.pending_uploads(0, 3), [])


if __name__ == "__main__":
    unittest.main()
