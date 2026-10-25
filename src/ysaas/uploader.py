"""Upload to YouTube through the Data API (OAuth, resumable uploads)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CHUNK_SIZE = 8 * 1024 * 1024  # must be a multiple of 256 KB
TITLE_LIMIT = 100


class AuthError(Exception):
    pass


class QuotaExceeded(Exception):
    """The API quota or YouTube's daily upload limit is used up."""


def authenticate(client_secret: Path, token_path: Path) -> Any:
    """Return valid credentials, refreshing or running the browser consent flow as needed."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except ValueError:
            creds = None

    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds, token_path)
            return creds
        except RefreshError:
            creds = None

    if not client_secret.exists():
        raise AuthError(
            f"Missing {client_secret}. Download an OAuth 'Desktop app' client from the "
            "Google Cloud console and save it there (see the README)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds, token_path)
    return creds


def _save_token(creds: Any, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(token_path, 0o600)
    except OSError:
        pass


def build_service(creds: Any) -> Any:
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def make_title(title: str, suffix: str = " #Shorts") -> str:
    title = title.strip()
    if "#shorts" in title.lower():
        return title[:TITLE_LIMIT]
    return title[: TITLE_LIMIT - len(suffix)].rstrip() + suffix


def make_description(source_url: str, channel_name: str, credit: bool) -> str:
    if not credit:
        return "#Shorts"
    return f"Original video by {channel_name}: {source_url}\n\n#Shorts"


def _is_quota_error(err: Any) -> bool:
    status = getattr(getattr(err, "resp", None), "status", None)
    content = getattr(err, "content", b"") or b""
    return status == 403 and (b"quotaExceeded" in content or b"uploadLimitExceeded" in content)


def upload_video(
    service: Any,
    path: Path,
    title: str,
    description: str,
    privacy: str,
    on_progress: Callable[[float], None] | None = None,
) -> str:
    """Upload one file and return the new YouTube video ID."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {"title": title, "description": description, "categoryId": "24"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=CHUNK_SIZE, resumable=True)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    try:
        while response is None:
            status, response = request.next_chunk(num_retries=3)
            if status and on_progress:
                on_progress(status.progress())
    except HttpError as e:
        if _is_quota_error(e):
            raise QuotaExceeded(str(e)) from e
        raise
    return response["id"]
