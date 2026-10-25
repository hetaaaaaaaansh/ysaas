import json
import tempfile
from pathlib import Path

from ysaas.config import parse_settings
from ysaas.models import Video

GOOD_ID = "UC" + "a" * 22


def make_settings(tmp: Path, **overrides):
    config_dir = tmp / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    raw = {"channels": [{"name": "Alpha", "channel_id": GOOD_ID}], **overrides}
    path = config_dir / "settings.json"
    path.write_text(json.dumps(raw))
    return parse_settings(raw, path)


def make_video(n: int, channel: str = "Alpha") -> Video:
    return Video(video_id=f"vid{n}", title=f"Title {n}", url=f"https://youtu.be/vid{n}", channel_name=channel)


SHORT_INFO = {"duration": 30, "width": 1080, "height": 1920}
LONG_INFO = {"duration": 600, "width": 1920, "height": 1080}


def tmpdir():
    return tempfile.TemporaryDirectory()
