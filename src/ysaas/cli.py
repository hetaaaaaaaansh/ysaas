"""Terminal interface: `ysaas <command>` or the interactive `ysaas menu`."""
import logging
import re
import shutil
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from . import __version__
from .config import (
    CHANNEL_ID_RE,
    DEFAULTS,
    ENV_VAR,
    ConfigError,
    Settings,
    find_config,
    load_raw,
    load_settings,
    parse_settings,
    save_raw,
)
from .db import SKIPPED, UPLOADED, Database
from .notifier import notify
from .pipeline import Hooks, default_service_factory, run_download, run_upload
from .uploader import AuthError, authenticate

app = typer.Typer(
    help="Monitor YouTube channels you have permission to reuse, download their Shorts, "
    "and upload them to your own channel.",
    no_args_is_help=True,
    add_completion=False,
)
channels_app = typer.Typer(help="Manage source channels.", no_args_is_help=True)
app.add_typer(channels_app, name="channels")

console = Console()


# ---------------------------------------------------------------- helpers
def _fail(message: str) -> None:
    console.print(f"[red]Error:[/red] {escape(message)}")
    raise typer.Exit(1)


def _settings(ctx: typer.Context) -> Settings:
    path = find_config(ctx.obj["config"])
    try:
        return load_settings(path)
    except ConfigError as e:
        _fail(str(e))
        raise  # unreachable; keeps type checkers happy


def _logger(settings: Settings) -> logging.Logger:
    logger = logging.getLogger("ysaas")
    if not logger.handlers:
        settings.log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(settings.log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def _hooks(settings: Settings, status=None) -> Hooks:
    logger = _logger(settings)

    def log(message: str) -> None:
        console.print(message, markup=False, highlight=False)
        logger.info(message)

    def progress(title: str, fraction: float) -> None:
        status.update(f"Uploading {escape(title)}: {fraction:.0%}")

    def send_notification(title: str, message: str) -> None:
        notify(title, message, settings.discord_webhook)
        logger.info("Notification: %s - %s", title, message)

    return Hooks(log=log, notify=send_notification, upload_progress=progress if status is not None else None)


# ------------------------------------------------------------ core actions
def _do_status(settings: Settings) -> None:
    db = Database(settings.db_path)
    counts = db.status_counts()
    table = Table(title="YSAAS status", show_header=False)
    table.add_column("Item")
    table.add_column("Value", justify="right")
    table.add_row("Source channels", str(len(settings.channels)))
    table.add_row("Downloaded today (UTC)", f"{db.count_downloaded_today()}/{settings.max_downloads_per_day}")
    table.add_row("Uploaded today (UTC)", f"{db.count_uploaded_today()}/{settings.max_uploads_per_day}")
    table.add_row(
        "Waiting to upload",
        f"{db.backlog_count(settings.max_attempts)} (downloads pause at {settings.backlog_pause_threshold})",
    )
    stuck = db.stuck_count(settings.max_attempts)
    table.add_row("Failed, out of retries", f"[red]{stuck}[/red]" if stuck else "0")
    table.add_row("Uploaded in total", str(counts[UPLOADED]))
    table.add_row("Skipped (not Shorts)", str(counts[SKIPPED]))
    console.print(table)
    if stuck:
        console.print("Run [bold]ysaas retry[/bold] to give failed items fresh attempts.")

    rows = db.recent(10)
    if rows:
        recent = Table(title="Recent activity")
        for col in ("Title", "Channel", "Status", "Error"):
            recent.add_column(col)
        for r in rows:
            error = (r["error"] or "")[:60]
            recent.add_row(escape(r["title"][:50]), escape(r["channel_name"]), r["status"], escape(error))
        console.print(recent)


def _print_download_report(report) -> None:
    console.print(
        f"[bold]Download:[/bold] {report.downloaded} downloaded, "
        f"{report.skipped} skipped (not Shorts), {report.failed} failed"
    )
    if report.stopped:
        console.print(f"[yellow]Note:[/yellow] {escape(report.stopped)}")


def _print_upload_report(report) -> None:
    console.print(f"[bold]Upload:[/bold] {report.uploaded} uploaded, {report.failed} failed")
    if report.stopped:
        console.print(f"[yellow]Note:[/yellow] {escape(report.stopped)}")


def _do_download(settings: Settings) -> None:
    db = Database(settings.db_path)
    hooks = _hooks(settings)
    with console.status("Checking channels and downloading..."):
        report = run_download(settings, db, hooks)
    _print_download_report(report)
    if report.downloaded or report.failed or report.stopped:
        hooks.notify("YSAAS download", f"{report.downloaded} downloaded, {report.skipped} skipped, {report.failed} failed. {report.stopped or ''}".strip())


def _do_upload(settings: Settings) -> None:
    db = Database(settings.db_path)
    status = console.status("Preparing upload...")

    def factory(s: Settings):
        status.stop()  # let the browser sign-in flow print cleanly on first run
        try:
            return default_service_factory(s)
        finally:
            status.start()

    try:
        with status:
            report = run_upload(settings, db, _hooks(settings, status), service_factory=factory)
    except AuthError as e:
        _fail(str(e))
        return
    _print_upload_report(report)
    if report.uploaded or report.failed or report.stopped:
        _hooks(settings).notify("YSAAS upload", f"{report.uploaded} uploaded, {report.failed} failed. {report.stopped or ''}".strip())


def _do_run(settings: Settings) -> None:
    _do_download(settings)
    _do_upload(settings)  # runs even if downloads paused: uploading is how the backlog drains


def _do_auth(settings: Settings) -> None:
    console.print(
        "A browser window will open. Sign in with the Google account that manages the upload channel "
        f"for [bold]{escape(settings.upload_channel or settings.target_channel)}[/bold]. The token authorizes the channel "
        "associated with that account; verify the selected account/channel before continuing."
    )
    try:
        authenticate(settings.client_secret_path, settings.token_path)
    except AuthError as e:
        _fail(str(e))
        return
    console.print(f"[green]Signed in.[/green] Token saved to {escape(str(settings.token_path))}")


def _do_retry(settings: Settings) -> None:
    count = Database(settings.db_path).reset_failed()
    console.print(f"Reset {count} failed item(s); they will be retried on the next run.")


def _do_doctor(settings: Settings) -> None:
    checks = [
        ("Settings file loads", True, ""),
        ("Source channel(s) configured", bool(settings.channels), "run: ysaas channels add NAME CHANNEL_ID"),
        ("ffmpeg installed", shutil.which("ffmpeg") is not None, "install ffmpeg (needed to merge video and audio)"),
        ("client_secret.json present", settings.client_secret_path.exists(), f"save it at {settings.client_secret_path}"),
        ("Upload channel entered", bool(settings.upload_channel), "run: ysaas setup"),
        ("Signed in to YouTube", settings.token_path.exists(), "run: ysaas auth"),
    ]
    table = Table(title="Setup check")
    table.add_column("Check")
    table.add_column("Result")
    table.add_column("Fix")
    for label, ok, hint in checks:
        table.add_row(label, "[green]OK[/green]" if ok else "[red]MISSING[/red]", "" if ok else escape(hint))
    console.print(table)


# --------------------------------------------------------------- setup and logs
def _setup_profile(ctx: typer.Context) -> None:
    """Interactive first-run setup; credentials remain in the user's ignored config folder."""
    path = find_config(ctx.obj["config"])
    try:
        raw = load_raw(path) if path.exists() else dict(DEFAULTS)
    except ConfigError as e:
        _fail(str(e))
        return

    console.print(Panel(
        "Uploads require OAuth consent from each user. A Google API key alone cannot upload videos.\n"
        "Create an OAuth client ID for a Desktop app in Google Cloud, enable YouTube Data API v3,\n"
        "and provide that downloaded client JSON below. Do not paste an API key or token here.",
        title="YouTube account setup",
    ))
    current_profile = str(raw.get("target_channel", "main"))
    profile = Prompt.ask("Upload profile name (used to keep this user's token separate)", default=current_profile).strip()
    if profile:
        raw["target_channel"] = profile
    raw["upload_channel"] = Prompt.ask(
        "Intended YouTube upload channel name or URL (reminder for OAuth sign-in)",
        default=str(raw.get("upload_channel", "")),
    ).strip()

    current_secret = path.parent / "client_secret.json"
    source = Prompt.ask(
        "Path to your OAuth Desktop app JSON (blank keeps existing file)",
        default=str(current_secret) if current_secret.exists() else "",
    ).strip().strip('"')
    if source:
        source_path = Path(source).expanduser()
        if not source_path.is_file():
            _fail(f"OAuth JSON file not found: {source_path}")
            return
        try:
            import json
            document = json.loads(source_path.read_text(encoding="utf-8"))
            if not isinstance(document, dict) or not (document.get("installed") or document.get("web")):
                raise ValueError("expected a Google OAuth client JSON with an installed or web section")
        except (OSError, ValueError) as e:
            _fail(f"Could not read OAuth client JSON: {e}")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        current_secret.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
        console.print(f"Saved OAuth client JSON to {escape(str(current_secret))} (git-ignored).")

    channels = raw.get("channels", [])
    if not isinstance(channels, list):
        channels = []
    console.print("Add source channels you own or have written permission to reuse. Leave name blank when done.")
    while True:
        name = Prompt.ask("Source channel name", default="").strip()
        if not name:
            break
        channel = Prompt.ask("Channel ID, channel URL, or @handle").strip()
        try:
            channel_id = _resolve_channel_id(channel)
        except Exception as e:
            console.print(f"[yellow]Could not resolve that channel:[/yellow] {escape(str(e))}")
            continue
        channels = [item for item in channels if str(item.get("name", "")).casefold() != name.casefold()]
        channels.append({"name": name, "channel_id": channel_id})
        console.print(f"Added {escape(name)} ({channel_id}).")
    raw["channels"] = channels

    webhook = Prompt.ask(
        "Optional Discord webhook URL for alerts (blank disables notifications)",
        default=str(raw.get("discord_webhook", "")),
    ).strip()
    raw["discord_webhook"] = webhook
    try:
        parse_settings(raw, path)
        save_raw(path, raw)
    except ConfigError as e:
        _fail(str(e))
        return
    console.print(Panel(
        f"Saved settings to {escape(str(path))}.\n\n"
        f"Upload profile: {escape(profile or current_profile)}\n"
        f"Source channels: {len(channels)}\n"
        f"OAuth client: {'ready' if current_secret.exists() else 'not set'}\n"
        f"Logs: {escape(str(path.parent.parent / 'logs' / 'ysaas.log'))}\n\n"
        "Next: run `ysaas auth`, confirm the intended Google account/channel, then run `ysaas doctor`.",
        title="Setup saved",
    ))


def _show_logs(settings: Settings) -> None:
    limit = 80
    if not settings.log_path.exists():
        console.print(f"No log file yet. It will be created at {escape(str(settings.log_path))} when a run starts.")
        return
    lines = settings.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    console.print(Panel("\n".join(escape(line) for line in lines) or "Log is empty", title=f"Recent logs ({settings.log_path})"))


# --------------------------------------------------------------- commands
def _version(value: bool) -> None:
    if value:
        console.print(f"ysaas {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    config: Optional[Path] = typer.Option(
        None, "--config", "-c", envvar=ENV_VAR, help="Path to settings.json (default: ./config/settings.json)."
    ),
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True, help="Show version and exit."),
) -> None:
    ctx.obj = {"config": config}


@app.command()
def init(ctx: typer.Context) -> None:
    """Create a settings file and folders in the current directory."""
    path = find_config(ctx.obj["config"])
    if path.exists():
        console.print(f"Settings already exist at {escape(str(path))}; leaving them alone.")
    else:
        save_raw(path, DEFAULTS)
        console.print(f"Created {escape(str(path))}")
    (path.parent / "tokens").mkdir(parents=True, exist_ok=True)
    console.print(
        Panel(
            "Run `ysaas setup` to configure your OAuth client, source channels, upload profile, and alerts.\n"
            "Then run `ysaas auth` to authorize the upload channel, `ysaas doctor` to check setup,\n"
            "and `ysaas run` to process videos. Use `ysaas logs` to inspect activity.",
            title="Next steps",
        )
    )


@app.command()
def setup(ctx: typer.Context) -> None:
    """Interactively configure OAuth, source channels, upload profile and alerts."""
    _setup_profile(ctx)


@app.command()
def logs(ctx: typer.Context) -> None:
    """Show the latest log entries."""
    _show_logs(_settings(ctx))


@app.command()
def status(ctx: typer.Context) -> None:
    """Show today's progress, the upload backlog and recent activity."""
    _do_status(_settings(ctx))


@app.command()
def download(ctx: typer.Context) -> None:
    """Look for new Shorts on the source channels and download them."""
    _do_download(_settings(ctx))


@app.command()
def upload(ctx: typer.Context) -> None:
    """Upload downloaded videos to your channel."""
    _do_upload(_settings(ctx))


@app.command()
def run(ctx: typer.Context) -> None:
    """Download, then upload. This is the command to schedule with cron."""
    _do_run(_settings(ctx))


@app.command()
def auth(ctx: typer.Context) -> None:
    """Sign in to the YouTube channel you upload to."""
    _do_auth(_settings(ctx))


@app.command()
def retry(ctx: typer.Context) -> None:
    """Give failed downloads and uploads a fresh set of attempts."""
    _do_retry(_settings(ctx))


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Check that everything needed is installed and configured."""
    _do_doctor(_settings(ctx))


@app.command()
def menu(ctx: typer.Context) -> None:
    """Interactive menu."""
    settings = _settings(ctx)
    actions = {
        "1": ("Show status", _do_status),
        "2": ("Download new Shorts", _do_download),
        "3": ("Upload waiting videos", _do_upload),
        "4": ("Download, then upload", _do_run),
        "5": ("Sign in to YouTube", _do_auth),
        "6": ("Check setup", _do_doctor),
        "7": ("Retry failed items", _do_retry),
        "8": ("View logs", _show_logs),
        "9": ("Setup / edit profile", lambda _settings: _setup_profile(ctx)),
    }
    body = "\n".join(f"[bold]{key}[/bold]  {label}" for key, (label, _) in actions.items())
    while True:
        console.print(Panel(body + "\n[bold]q[/bold]  Quit", title=f"YSAAS {__version__}"))
        choice = Prompt.ask("Choose", choices=[*actions, "q"], default="1")
        if choice == "q":
            break
        actions[choice][1](settings)
        console.print()
        if choice == "9":
            break


# --------------------------------------------------------------- channels
def _resolve_channel_id(text: str) -> str:
    """Accept a channel ID, a /channel/UC... URL, or an @handle / handle URL."""
    text = text.strip()
    if CHANNEL_ID_RE.match(text):
        return text
    match = re.search(r"youtube\.com/channel/(UC[\w-]{22})", text)
    if match:
        return match.group(1)

    import yt_dlp

    url = text if text.startswith("http") else f"https://www.youtube.com/{text if text.startswith('@') else '@' + text}"
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True, "playlistend": 1}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    for key in ("channel_id", "uploader_id"):
        value = (info or {}).get(key)
        if value and CHANNEL_ID_RE.match(value):
            return value
    raise ValueError("could not find a channel ID")


@channels_app.command("list")
def channels_list(ctx: typer.Context) -> None:
    """List source channels."""
    settings = _settings(ctx)
    if not settings.channels:
        console.print("No source channels yet. Add one with: ysaas channels add NAME CHANNEL_ID")
        return
    table = Table(title="Source channels")
    table.add_column("Name")
    table.add_column("Channel ID")
    for channel in settings.channels:
        table.add_row(escape(channel.name), channel.channel_id)
    console.print(table)


@channels_app.command("add")
def channels_add(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="A label for this channel, e.g. 'MyOtherChannel'."),
    channel: str = typer.Argument(..., help="Channel ID (UC...), channel URL, or @handle."),
) -> None:
    """Add a source channel. Only add channels you own or have permission to reuse."""
    path = find_config(ctx.obj["config"])
    try:
        channel_id = _resolve_channel_id(channel)
    except Exception:
        _fail(
            "Could not work out the channel ID from that. Paste the ID itself instead "
            "(it starts with UC and is 24 characters long)."
        )
        return
    try:
        raw = load_raw(path)
        raw["channels"] = [*raw.get("channels", []), {"name": name.strip(), "channel_id": channel_id}]
        parse_settings(raw, path)  # validates, including duplicate names
        save_raw(path, raw)
    except ConfigError as e:
        _fail(str(e))
        return
    console.print(f"Added [bold]{escape(name)}[/bold] ({channel_id}).")


@channels_app.command("remove")
def channels_remove(ctx: typer.Context, name: str = typer.Argument(..., help="Name of the channel to remove.")) -> None:
    """Remove a source channel."""
    path = find_config(ctx.obj["config"])
    try:
        raw = load_raw(path)
    except ConfigError as e:
        _fail(str(e))
        return
    before = raw.get("channels", [])
    after = [c for c in before if str(c.get("name", "")).lower() != name.strip().lower()]
    if len(after) == len(before):
        _fail(f"No channel named '{name}'.")
        return
    raw["channels"] = after
    save_raw(path, raw)
    console.print(f"Removed {escape(name)}.")
