"""Discover a channel's latest uploads via its public RSS feed."""
from __future__ import annotations

from .models import Video

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


class FeedError(Exception):
    pass


def get_latest_videos(channel_name: str, channel_id: str) -> list[Video]:
    """Return the channel's most recent videos (YouTube's feed only exposes about 15)."""
    import feedparser  # imported lazily so the rest of the package works without it

    feed = feedparser.parse(FEED_URL.format(channel_id=channel_id))
    status = feed.get("status")
    if status is not None and status >= 400:
        raise FeedError(f"feed for '{channel_name}' returned HTTP {status} (is the channel ID right?)")
    if feed.bozo and not feed.entries:
        raise FeedError(f"could not read feed for '{channel_name}': {feed.bozo_exception}")

    videos: list[Video] = []
    for entry in feed.entries:
        video_id = entry.get("yt_videoid")
        link = entry.get("link")
        if not video_id or not link:
            continue
        videos.append(
            Video(video_id=video_id, title=entry.get("title", video_id), url=link, channel_name=channel_name)
        )
    return videos
