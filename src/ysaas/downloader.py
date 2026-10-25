"""Download videos with yt-dlp."""
from __future__ import annotations

import re
from pathlib import Path

from .models import Video


class DownloadError(Exception):
    pass


def slugify(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name).strip("._") or "channel"


def download_video(video: Video, download_folder: Path, quality: str) -> Path:
    """Download one video to <download_folder>/<channel>/<video_id>.mp4 and return the path.

    Files are named by video ID, so they are unique and filesystem-safe; the
    human title lives in the database. Merging to mp4 needs ffmpeg installed.
    """
    import yt_dlp

    folder = Path(download_folder) / slugify(video.channel_name)
    folder.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": quality,
        "outtmpl": str(folder / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video.url, download=True)
    except Exception as e:
        raise DownloadError(str(e)) from e

    for item in (info or {}).get("requested_downloads") or []:
        path = item.get("filepath")
        if path and Path(path).exists():
            return Path(path)
    fallback = folder / f"{video.video_id}.mp4"
    if fallback.exists():
        return fallback
    raise DownloadError("download finished but the output file was not found (is ffmpeg installed?)")
