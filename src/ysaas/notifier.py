"""Optional Discord webhook notifications; local file logging is handled by the CLI."""
from __future__ import annotations

import json
import urllib.error
import urllib.request


def notify(title: str, message: str, webhook: str | None = None) -> None:
    """Send an optional Discord message. A webhook URL is a secret."""
    if not webhook:
        return
    payload = json.dumps({"content": f"**{title}**\n{message}"}).encode("utf-8")
    request = urllib.request.Request(
        webhook, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            response.read(1)
    except (OSError, urllib.error.URLError):
        # Notification failures must not interrupt downloads or uploads.
        return
