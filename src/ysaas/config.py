"""Settings loading and validation."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CHANNEL_ID_RE = re.compile(r"^UC[\w-]{22}$")
TARGET_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
PRIVACY_VALUES = ("private", "unlisted", "public")
ENV_VAR = "YSAAS_CONFIG"

DEFAULTS: dict[str, Any] = {
    "download_folder": "downloads",
    "quality": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    "max_duration_seconds": 180,
    "max_downloads_per_day": 5,
    "max_uploads_per_day": 5,
    "backlog_pause_threshold": 20,
    "max_attempts": 3,
    "upload_privacy": "private",
    "credit_sources": True,
    "delete_after_upload": True,
    "target_channel": "main",
    "upload_channel": "",
    "discord_webhook": "",
    "channels": [],
}


class ConfigError(Exception):
    """Raised when the settings file is missing or invalid."""


@dataclass(frozen=True)
class Channel:
    name: str
    channel_id: str


@dataclass(frozen=True)
class Settings:
    root: Path
    config_path: Path
    download_folder: Path
    quality: str
    max_duration_seconds: int
    max_downloads_per_day: int
    max_uploads_per_day: int
    backlog_pause_threshold: int
    max_attempts: int
    upload_privacy: str
    credit_sources: bool
    delete_after_upload: bool
    target_channel: str
    upload_channel: str
    discord_webhook: str | None
    channels: tuple[Channel, ...]

    @property
    def db_path(self) -> Path:
        return self.root / "data" / "ysaas.db"

    @property
    def log_path(self) -> Path:
        return self.root / "logs" / "ysaas.log"

    @property
    def client_secret_path(self) -> Path:
        return self.config_path.parent / "client_secret.json"

    @property
    def token_path(self) -> Path:
        return self.config_path.parent / "tokens" / f"{self.target_channel}.json"


def find_config(explicit: Path | None = None) -> Path:
    """--config flag, then $YSAAS_CONFIG, then ./config/settings.json."""
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser().resolve()
    return (Path.cwd() / "config" / "settings.json").resolve()


def load_raw(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Settings file not found: {path}. Run `ysaas init` first.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object.")
    return data


def save_raw(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _positive_int(data: dict[str, Any], key: str) -> int:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"'{key}' must be a whole number of 1 or more (got {value!r}).")
    return value


def _bool(data: dict[str, Any], key: str) -> bool:
    value = data[key]
    if not isinstance(value, bool):
        raise ConfigError(f"'{key}' must be true or false (got {value!r}).")
    return value


def parse_settings(raw: dict[str, Any], config_path: Path) -> Settings:
    data = {**DEFAULTS, **raw}
    root = config_path.parent.parent

    privacy = data["upload_privacy"]
    if privacy not in PRIVACY_VALUES:
        raise ConfigError(f"'upload_privacy' must be one of {PRIVACY_VALUES} (got {privacy!r}).")

    target = data["target_channel"]
    if not isinstance(target, str) or not TARGET_NAME_RE.match(target):
        raise ConfigError("'target_channel' may only contain letters, digits, '_' and '-'.")
    upload_channel = data.get("upload_channel", "")
    if not isinstance(upload_channel, str):
        raise ConfigError("'upload_channel' must be a channel name, URL, or empty.")
    upload_channel = upload_channel.strip()

    webhook = data.get("discord_webhook", "")
    if not isinstance(webhook, str):
        raise ConfigError("'discord_webhook' must be a URL string or empty.")
    webhook = webhook.strip() or None
    if webhook and not webhook.startswith("https://discord.com/api/webhooks/"):
        raise ConfigError("'discord_webhook' must be a Discord HTTPS webhook URL, or empty.")

    quality = data["quality"]
    if not isinstance(quality, str) or not quality.strip():
        raise ConfigError("'quality' must be a non-empty yt-dlp format string.")

    folder = data["download_folder"]
    if not isinstance(folder, str) or not folder.strip():
        raise ConfigError("'download_folder' must be a non-empty path.")
    download_folder = Path(folder).expanduser()
    if not download_folder.is_absolute():
        download_folder = root / download_folder

    raw_channels = data["channels"]
    if not isinstance(raw_channels, list):
        raise ConfigError("'channels' must be a list.")
    channels: list[Channel] = []
    seen: set[str] = set()
    for i, item in enumerate(raw_channels, start=1):
        if not isinstance(item, dict) or not item.get("name") or not item.get("channel_id"):
            raise ConfigError(f"channels[{i}] needs both 'name' and 'channel_id'.")
        name, cid = str(item["name"]).strip(), str(item["channel_id"]).strip()
        if not CHANNEL_ID_RE.match(cid):
            raise ConfigError(
                f"channels[{i}] ('{name}') has an invalid channel_id {cid!r}. "
                "It should start with 'UC' and be 24 characters long."
            )
        if name.lower() in seen:
            raise ConfigError(f"Duplicate channel name '{name}'.")
        seen.add(name.lower())
        channels.append(Channel(name=name, channel_id=cid))

    return Settings(
        root=root,
        config_path=config_path,
        download_folder=download_folder,
        quality=quality,
        max_duration_seconds=_positive_int(data, "max_duration_seconds"),
        max_downloads_per_day=_positive_int(data, "max_downloads_per_day"),
        max_uploads_per_day=_positive_int(data, "max_uploads_per_day"),
        backlog_pause_threshold=_positive_int(data, "backlog_pause_threshold"),
        max_attempts=_positive_int(data, "max_attempts"),
        upload_privacy=privacy,
        credit_sources=_bool(data, "credit_sources"),
        delete_after_upload=_bool(data, "delete_after_upload"),
        target_channel=target,
        upload_channel=upload_channel,
        discord_webhook=webhook,
        channels=tuple(channels),
    )


def load_settings(path: Path) -> Settings:
    return parse_settings(load_raw(path), path)
