"""Decide whether a video counts as a Short."""
from __future__ import annotations

from typing import Any


def probe_video(url: str) -> dict[str, Any]:
    """Fetch metadata without downloading."""
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _dimensions(info: dict[str, Any]) -> tuple[int, int] | None:
    width, height = info.get("width"), info.get("height")
    if width and height:
        return width, height
    best: tuple[int, int] | None = None
    for fmt in info.get("formats") or []:
        w, h = fmt.get("width"), fmt.get("height")
        if w and h and (best is None or h > best[1]):
            best = (w, h)
    return best


def is_short(info: dict[str, Any], max_duration_seconds: int = 180) -> bool:
    """Vertical and no longer than max_duration_seconds.

    Missing or null duration/dimensions mean "not a Short": we'd rather skip a
    video than upload the wrong thing.
    """
    duration = info.get("duration")
    dims = _dimensions(info)
    if not duration or not dims:
        return False
    width, height = dims
    return height > width and duration <= max_duration_seconds
