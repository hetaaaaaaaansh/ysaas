"""SQLite state: one row per video, tracked from download through upload."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .models import Video

SKIPPED = "skipped"
DOWNLOADED = "downloaded"
DOWNLOAD_FAILED = "download_failed"
UPLOADED = "uploaded"
UPLOAD_FAILED = "upload_failed"
ALL_STATUSES = (SKIPPED, DOWNLOADED, DOWNLOAD_FAILED, UPLOAD_FAILED, UPLOADED)

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id          TEXT PRIMARY KEY,
    channel_name      TEXT NOT NULL,
    title             TEXT NOT NULL,
    source_url        TEXT NOT NULL,
    status            TEXT NOT NULL,
    file_path         TEXT,
    download_attempts INTEGER NOT NULL DEFAULT 0,
    upload_attempts   INTEGER NOT NULL DEFAULT 0,
    error             TEXT,
    uploaded_video_id TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    downloaded_at     TEXT,
    uploaded_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
"""

# Videos that are on disk waiting for upload.
_WAITING = (DOWNLOADED, UPLOAD_FAILED)


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---- reads -----------------------------------------------------------
    def get(self, video_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)).fetchone()

    def should_process(self, video_id: str, max_attempts: int) -> bool:
        """New videos, and failed downloads that still have attempts left."""
        row = self.get(video_id)
        if row is None:
            return True
        return row["status"] == DOWNLOAD_FAILED and row["download_attempts"] < max_attempts

    def pending_uploads(self, limit: int, max_attempts: int) -> list[sqlite3.Row]:
        if limit <= 0:
            return []
        marks = ",".join("?" for _ in _WAITING)
        with self._connect() as conn:
            return conn.execute(
                f"""SELECT * FROM videos
                    WHERE status IN ({marks}) AND upload_attempts < ?
                    ORDER BY downloaded_at, rowid LIMIT ?""",
                (*_WAITING, max_attempts, limit),
            ).fetchall()

    def backlog_count(self, max_attempts: int) -> int:
        marks = ",".join("?" for _ in _WAITING)
        with self._connect() as conn:
            return conn.execute(
                f"SELECT COUNT(*) FROM videos WHERE status IN ({marks}) AND upload_attempts < ?",
                (*_WAITING, max_attempts),
            ).fetchone()[0]

    def count_downloaded_today(self) -> int:
        """Counts by UTC date."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM videos WHERE date(downloaded_at) = date('now')"
            ).fetchone()[0]

    def count_uploaded_today(self) -> int:
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM videos WHERE status = ? AND date(uploaded_at) = date('now')",
                (UPLOADED,),
            ).fetchone()[0]

    def status_counts(self) -> dict[str, int]:
        counts = {s: 0 for s in ALL_STATUSES}
        with self._connect() as conn:
            for row in conn.execute("SELECT status, COUNT(*) AS n FROM videos GROUP BY status"):
                counts[row["status"]] = row["n"]
        return counts

    def stuck_count(self, max_attempts: int) -> int:
        """Failed items that ran out of attempts and need `ysaas retry`."""
        with self._connect() as conn:
            return conn.execute(
                """SELECT COUNT(*) FROM videos
                   WHERE (status = ? AND download_attempts >= ?)
                      OR (status = ? AND upload_attempts >= ?)""",
                (DOWNLOAD_FAILED, max_attempts, UPLOAD_FAILED, max_attempts),
            ).fetchone()[0]

    def recent(self, limit: int = 10) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                """SELECT * FROM videos
                   WHERE status != ?
                   ORDER BY COALESCE(uploaded_at, downloaded_at, created_at) DESC, rowid DESC
                   LIMIT ?""",
                (SKIPPED, limit),
            ).fetchall()

    # ---- writes ----------------------------------------------------------
    def record_skipped(self, video: Video) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO videos (video_id, channel_name, title, source_url, status)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(video_id) DO UPDATE SET status = excluded.status""",
                (video.video_id, video.channel_name, video.title, video.url, SKIPPED),
            )

    def record_downloaded(self, video: Video, file_path: Path) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO videos (video_id, channel_name, title, source_url, status,
                                       file_path, download_attempts, downloaded_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, datetime('now'))
                   ON CONFLICT(video_id) DO UPDATE SET
                       status = excluded.status,
                       file_path = excluded.file_path,
                       download_attempts = download_attempts + 1,
                       downloaded_at = excluded.downloaded_at,
                       error = NULL""",
                (video.video_id, video.channel_name, video.title, video.url, DOWNLOADED, str(file_path)),
            )

    def record_download_failed(self, video: Video, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO videos (video_id, channel_name, title, source_url, status,
                                       download_attempts, error)
                   VALUES (?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(video_id) DO UPDATE SET
                       status = excluded.status,
                       download_attempts = download_attempts + 1,
                       error = excluded.error""",
                (video.video_id, video.channel_name, video.title, video.url, DOWNLOAD_FAILED, error),
            )

    def record_uploaded(self, video_id: str, youtube_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE videos SET status = ?, uploaded_video_id = ?, error = NULL,
                       upload_attempts = upload_attempts + 1, uploaded_at = datetime('now')
                   WHERE video_id = ?""",
                (UPLOADED, youtube_id, video_id),
            )

    def record_upload_failed(self, video_id: str, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE videos SET status = ?, error = ?, upload_attempts = upload_attempts + 1
                   WHERE video_id = ?""",
                (UPLOAD_FAILED, error, video_id),
            )

    def reset_failed(self) -> int:
        """Give every failed item a fresh set of attempts. Returns how many were reset."""
        with self._connect() as conn:
            a = conn.execute(
                "UPDATE videos SET download_attempts = 0 WHERE status = ?", (DOWNLOAD_FAILED,)
            ).rowcount
            b = conn.execute(
                "UPDATE videos SET upload_attempts = 0 WHERE status = ?", (UPLOAD_FAILED,)
            ).rowcount
            return a + b
