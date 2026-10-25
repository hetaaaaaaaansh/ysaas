"""The download and upload workflows. Pure logic: no printing, no CLI."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .db import Database
from .downloader import download_video
from .filters import is_short, probe_video
from .models import Video
from .monitor import get_latest_videos
from .uploader import (
    QuotaExceeded,
    authenticate,
    build_service,
    make_description,
    make_title,
    upload_video,
)


def _noop_notify(title: str, message: str) -> None:
    return None


@dataclass
class Hooks:
    """How the pipeline talks to whatever front end is running it."""

    log: Callable[[str], None] = print
    notify: Callable[[str, str], None] = _noop_notify
    upload_progress: Callable[[str, float], None] | None = None


@dataclass
class DownloadReport:
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    stopped: str | None = None


@dataclass
class UploadReport:
    uploaded: int = 0
    failed: int = 0
    stopped: str | None = None


def run_download(
    settings: Settings,
    db: Database,
    hooks: Hooks | None = None,
    *,
    fetch: Callable[[str, str], list[Video]] = get_latest_videos,
    probe: Callable[[str], dict[str, Any]] = probe_video,
    download: Callable[[Video, Path, str], Path] = download_video,
) -> DownloadReport:
    hooks = hooks or Hooks()
    report = DownloadReport()

    if not settings.channels:
        report.stopped = "no source channels configured (use `ysaas channels add`)"
        return report

    backlog = db.backlog_count(settings.max_attempts)
    if backlog >= settings.backlog_pause_threshold:
        report.stopped = f"backlog too high ({backlog} videos waiting to upload)"
        hooks.notify("YSAAS", f"Downloads paused: {backlog} videos waiting to upload")
        return report

    cap = settings.max_downloads_per_day
    done_today = db.count_downloaded_today()
    if done_today >= cap:
        report.stopped = f"daily download cap already reached ({done_today}/{cap})"
        return report

    for channel in settings.channels:
        if done_today >= cap:
            break
        try:
            videos = fetch(channel.name, channel.channel_id)
        except Exception as e:
            hooks.log(f"[{channel.name}] could not fetch feed: {e}")
            continue

        for video in videos:
            if done_today >= cap:
                break
            if not db.should_process(video.video_id, settings.max_attempts):
                continue
            try:
                info = probe(video.url)
            except Exception as e:
                hooks.log(f"[{channel.name}] could not inspect '{video.title}': {e}")
                continue  # not recorded, so it is retried next run
            if not is_short(info, settings.max_duration_seconds):
                db.record_skipped(video)
                report.skipped += 1
                continue
            try:
                path = download(video, settings.download_folder, settings.quality)
            except Exception as e:
                db.record_download_failed(video, str(e))
                report.failed += 1
                hooks.log(f"[{channel.name}] download failed for '{video.title}': {e}")
                continue
            db.record_downloaded(video, path)
            done_today += 1
            report.downloaded += 1
            hooks.log(f"[{channel.name}] downloaded: {video.title}")

    if done_today >= cap:
        report.stopped = f"daily download cap reached ({done_today}/{cap})"
        hooks.notify("YSAAS", f"Daily download limit reached ({done_today}/{cap})")
    return report


def default_service_factory(settings: Settings) -> Any:
    creds = authenticate(settings.client_secret_path, settings.token_path)
    return build_service(creds)


def run_upload(
    settings: Settings,
    db: Database,
    hooks: Hooks | None = None,
    *,
    service_factory: Callable[[Settings], Any] = default_service_factory,
    upload: Callable[..., str] = upload_video,
) -> UploadReport:
    hooks = hooks or Hooks()
    report = UploadReport()

    remaining = settings.max_uploads_per_day - db.count_uploaded_today()
    if remaining <= 0:
        report.stopped = f"daily upload cap reached ({settings.max_uploads_per_day})"
        return report

    rows = db.pending_uploads(remaining, settings.max_attempts)
    if not rows:
        report.stopped = "nothing waiting to upload"
        return report

    service = service_factory(settings)

    for row in rows:
        video_id = row["video_id"]
        title = make_title(row["title"])
        path = Path(row["file_path"] or "")
        if not path.is_file():
            db.record_upload_failed(video_id, f"file missing: {path}")
            report.failed += 1
            hooks.log(f"file missing for '{row['title']}': {path}")
            continue

        description = make_description(row["source_url"], row["channel_name"], settings.credit_sources)
        progress = None
        if hooks.upload_progress is not None:
            callback = hooks.upload_progress

            def progress(fraction: float, _title: str = title, _cb=callback) -> None:
                _cb(_title, fraction)

        try:
            youtube_id = upload(service, path, title, description, settings.upload_privacy, progress)
        except QuotaExceeded:
            report.stopped = "YouTube quota or daily upload limit reached; try again tomorrow"
            hooks.notify("YSAAS", "Upload stopped: YouTube quota reached")
            break
        except Exception as e:
            db.record_upload_failed(video_id, str(e))
            report.failed += 1
            hooks.log(f"upload failed for '{row['title']}': {e}")
            continue

        db.record_uploaded(video_id, youtube_id)
        report.uploaded += 1
        hooks.log(f"uploaded: {title} -> https://youtu.be/{youtube_id}")
        if settings.delete_after_upload:
            path.unlink(missing_ok=True)

    return report
