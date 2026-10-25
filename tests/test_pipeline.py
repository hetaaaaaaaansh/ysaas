import unittest
from pathlib import Path

from ysaas.db import Database
from ysaas.downloader import DownloadError
from ysaas.pipeline import Hooks, run_download, run_upload
from ysaas.uploader import QuotaExceeded
from helpers import LONG_INFO, SHORT_INFO, make_settings, make_video, tmpdir


class DownloadPipelineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tmpdir()
        self.tmp = Path(self._tmp.name)
        self.db = Database(self.tmp / "x.db")
        self.logs = []
        self.notes = []
        self.hooks = Hooks(log=self.logs.append, notify=lambda t, m: self.notes.append((t, m)))

    def tearDown(self):
        self._tmp.cleanup()

    def _download(self, video, folder, quality):
        p = folder / f"{video.video_id}.mp4"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        return p

    def test_downloads_shorts_skips_long_and_respects_cap(self):
        s = make_settings(self.tmp, max_downloads_per_day=2)
        vids = [make_video(i) for i in range(1, 6)]
        infos = {"vid1": LONG_INFO, "vid2": SHORT_INFO, "vid3": SHORT_INFO, "vid4": SHORT_INFO, "vid5": SHORT_INFO}
        r = run_download(
            s, self.db, self.hooks,
            fetch=lambda n, c: vids,
            probe=lambda url: infos[url.rsplit("/", 1)[1]],
            download=self._download,
        )
        self.assertEqual((r.downloaded, r.skipped, r.failed), (2, 1, 0))
        self.assertIn("cap", r.stopped)
        self.assertTrue(self.notes)
        # vid4/vid5 were never touched, so they are picked up tomorrow
        self.assertIsNone(self.db.get("vid4"))

    def test_skipped_videos_are_not_reprobed(self):
        s = make_settings(self.tmp)
        calls = []
        def probe(url):
            calls.append(url)
            return LONG_INFO
        kwargs = dict(fetch=lambda n, c: [make_video(1)], probe=probe, download=self._download)
        run_download(s, self.db, self.hooks, **kwargs)
        run_download(s, self.db, self.hooks, **kwargs)
        self.assertEqual(len(calls), 1)

    def test_failed_download_is_retried_then_gives_up(self):
        s = make_settings(self.tmp, max_attempts=2)
        def bad(video, folder, quality):
            raise DownloadError("nope")
        kwargs = dict(fetch=lambda n, c: [make_video(1)], probe=lambda u: SHORT_INFO, download=bad)
        for _ in range(3):
            run_download(s, self.db, self.hooks, **kwargs)
        self.assertEqual(self.db.get("vid1")["download_attempts"], 2)

    def test_probe_error_is_not_recorded(self):
        s = make_settings(self.tmp)
        def probe(url):
            raise RuntimeError("network")
        r = run_download(s, self.db, self.hooks, fetch=lambda n, c: [make_video(1)], probe=probe, download=self._download)
        self.assertEqual(r.downloaded, 0)
        self.assertIsNone(self.db.get("vid1"))

    def test_feed_error_does_not_stop_other_channels(self):
        s = make_settings(self.tmp, channels=[
            {"name": "Bad", "channel_id": "UC" + "b" * 22},
            {"name": "Alpha", "channel_id": "UC" + "a" * 22},
        ])
        def fetch(name, cid):
            if name == "Bad":
                raise RuntimeError("feed down")
            return [make_video(1, name)]
        r = run_download(s, self.db, self.hooks, fetch=fetch, probe=lambda u: SHORT_INFO, download=self._download)
        self.assertEqual(r.downloaded, 1)

    def test_backlog_pauses_downloads(self):
        s = make_settings(self.tmp, backlog_pause_threshold=2)
        for n in (10, 11):
            self.db.record_downloaded(make_video(n), Path(f"/tmp/{n}.mp4"))
        r = run_download(s, self.db, self.hooks, fetch=lambda n, c: [make_video(1)],
                         probe=lambda u: SHORT_INFO, download=self._download)
        self.assertEqual(r.downloaded, 0)
        self.assertIn("backlog", r.stopped)

    def test_no_channels(self):
        s = make_settings(self.tmp, channels=[])
        r = run_download(s, self.db, self.hooks)
        self.assertIn("no source channels", r.stopped)


class UploadPipelineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tmpdir()
        self.tmp = Path(self._tmp.name)
        self.db = Database(self.tmp / "x.db")
        self.hooks = Hooks(log=lambda m: None)

    def tearDown(self):
        self._tmp.cleanup()

    def _stage(self, n, create=True):
        v = make_video(n)
        p = self.tmp / f"{n}.mp4"
        if create:
            p.write_bytes(b"x")
        self.db.record_downloaded(v, p)
        return v, p

    def test_uploads_and_deletes_file(self):
        s = make_settings(self.tmp)
        v, p = self._stage(1)
        seen = {}
        def upload(service, path, title, description, privacy, progress):
            seen.update(title=title, description=description, privacy=privacy)
            return "ytID"
        r = run_upload(s, self.db, self.hooks, service_factory=lambda s: object(), upload=upload)
        self.assertEqual(r.uploaded, 1)
        self.assertFalse(p.exists())
        self.assertEqual(seen["title"], "Title 1 #Shorts")
        self.assertEqual(seen["privacy"], "private")
        self.assertIn("https://youtu.be/vid1", seen["description"])
        self.assertEqual(self.db.get("vid1")["uploaded_video_id"], "ytID")

    def test_keep_file_when_configured(self):
        s = make_settings(self.tmp, delete_after_upload=False)
        v, p = self._stage(1)
        run_upload(s, self.db, self.hooks, service_factory=lambda s: object(), upload=lambda *a: "id")
        self.assertTrue(p.exists())

    def test_daily_cap_counts_previous_uploads(self):
        s = make_settings(self.tmp, max_uploads_per_day=2)
        for n in (1, 2, 3):
            self._stage(n)
        up = lambda *a: "id"
        run_upload(s, self.db, self.hooks, service_factory=lambda s: object(), upload=up)
        r2 = run_upload(s, self.db, self.hooks, service_factory=lambda s: object(), upload=up)
        self.assertEqual(r2.uploaded, 0)
        self.assertIn("cap", r2.stopped)
        self.assertEqual(self.db.count_uploaded_today(), 2)

    def test_quota_stops_without_burning_attempts(self):
        s = make_settings(self.tmp)
        self._stage(1)
        self._stage(2)
        def upload(*a):
            raise QuotaExceeded("q")
        r = run_upload(s, self.db, self.hooks, service_factory=lambda s: object(), upload=upload)
        self.assertEqual(r.uploaded, 0)
        self.assertIn("quota", r.stopped)
        self.assertEqual(self.db.get("vid1")["upload_attempts"], 0)
        self.assertEqual(self.db.backlog_count(3), 2)

    def test_failure_counts_attempt_and_continues(self):
        s = make_settings(self.tmp)
        self._stage(1)
        self._stage(2)
        def upload(service, path, *a):
            if path.name == "1.mp4":
                raise RuntimeError("boom")
            return "ok"
        r = run_upload(s, self.db, self.hooks, service_factory=lambda s: object(), upload=upload)
        self.assertEqual((r.uploaded, r.failed), (1, 1))
        self.assertEqual(self.db.get("vid1")["upload_attempts"], 1)

    def test_missing_file_is_recorded(self):
        s = make_settings(self.tmp)
        self._stage(1, create=False)
        r = run_upload(s, self.db, self.hooks, service_factory=lambda s: object(), upload=lambda *a: "id")
        self.assertEqual(r.failed, 1)
        self.assertIn("file missing", self.db.get("vid1")["error"])

    def test_nothing_waiting_does_not_authenticate(self):
        s = make_settings(self.tmp)
        def explode(_):
            raise AssertionError("should not authenticate with nothing to upload")
        r = run_upload(s, self.db, self.hooks, service_factory=explode)
        self.assertIn("nothing waiting", r.stopped)


if __name__ == "__main__":
    unittest.main()
