from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Video:
    video_id: str
    title: str
    url: str
    channel_name: str
