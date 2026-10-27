# YSAAS

A local command-line tool for monitoring YouTube channels, downloading eligible vertical videos, and uploading them to a YouTube channel. Credit and a source link do not grant permission.

Uploads by default as **private**. This is a community project, not an official YouTube or Google product.

```text
ysaas setup                  # configuration
ysaas auth                   # authorize the upload account(channel)
ysaas channels add NAME ID   # add a permitted source channel
ysaas channels list          # list configured source channels
ysaas doctor                 # check setup
ysaas run                    # download, then upload
ysaas status                 # daily totals and backlog
ysaas logs                   # recent local log entries
ysaas retry                  # reset failed items
ysaas menu                   # interactive command menu
```
## Install

Requires Python 3.10+ and [FFmpeg](https://ffmpeg.org/) on PATH.

```bash
pip install ysaas
```
OR
```bash
pipx install ysaas
```

Then run:
```bash
ysaas setup
```

## Per-user Google setup

Each user configures their own installation and authorizes uploads to channel they own or manage. The app does not share credentials between users.

- **OAuth client JSON:** Create a Google Cloud project, enable YouTube Data API v3, create an OAuth client ID for a Desktop app, and provide that downloaded JSON to `ysaas setup`.
- **Upload channel:** Setup records the intended channel name or URL as a reminder. `ysaas auth` opens Google sign-in; use the Google account/channel that manages that destination and verify it before granting access. The profile name keeps token files separate. The Data API uploads to the channel associated with the authorized account; the reminder does not route uploads or switch channels within an account.
- **Source channels:** Add a channel ID, URL, or @handle. Only add channels whose videos you have permission to dowload and reuse.

The OAuth client JSON and user token stay in the local config directory and are excluded by `.gitignore`. Never commit them or share a token file. Revoke exposed credentials in Google Cloud.

## Requirements

Requires Python 3.10+ and [FFmpeg](https://ffmpeg.org/) on PATH.

In Google Cloud, enable YouTube Data API v3 and create an OAuth client ID with application type **Desktop app**. The first run opens a browser for consent. Consent-screen or app-verification requirements depend on how the OAuth app is configured and distributed; check Google's current guidance.

## Setup, logs, notifications

`ysaas setup` creates or edits `config/settings.json` and prompts for:

1. The user's OAuth Desktop client JSON. It is copied to the ignored `config/client_secret.json`.
2. An upload profile name for the local token file, plus the intended upload channel name or URL as a reminder.
3. Source channel labels and IDs, URLs, or @handles.
4. An optional Discord webhook URL for alerts. Leave blank to disable notifications.

An API key is deliberately not requested because it cannot authorize uploads. Do not paste an API key in place of the OAuth JSON.


Use `--config PATH` or `YSAAS_CONFIG` to select a different settings file. Logs are written to `logs/ysaas.log` with rotation. Optional Discord alerts report run summaries and stop conditions. Anyone with the webhook URL can post to that channel; keep it in the ignored local settings file. Notification failure does not stop a run.

To schedule a daily run, use your operating system's scheduler. Linux/macOS cron example: `0 9 * * * cd /path/to/ysaas && /path/to/ysaas/.venv/bin/ysaas run`.

## Settings and limitations

`config/settings.example.json` documents defaults for daily caps, retry attempts, backlog threshold, upload privacy, credit text, and deleting local files after upload. Upload privacy defaults to `private`. API quota and account limits depend on current Google policies and the user's Cloud project; check Google Cloud quota rather than relying on a fixed upload/day estimate.

YouTube RSS exposes only a limited set of recent videos, so run regularly. “Short” detection uses vertical orientation and configured maximum duration; it does not use YouTube's own Shorts classification. One setup profile authorizes one account/channel context.

Settings, OAuth client credentials, tokens, database, logs, downloads, and MP4 files are ignored by Git. Review `git status` and staged files before publishing.
