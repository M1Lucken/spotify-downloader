"""
One-page local UI for spotdl download.
"""

import logging
import re
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from uvicorn import Config, Server

from spotdl.console.download import download_songs_with_resume, prevent_sleep
from spotdl.download.downloader import Downloader
from spotdl.utils.config import get_config_file
from spotdl.utils.credentials import spotify_credentials_problem
from spotdl.utils.downloader import resolve_audio_providers
from spotdl.utils.ffmpeg import is_ffmpeg_installed
from spotdl.utils.js_runtime import MISSING_JS_RUNTIME_HINT, describe_js_runtime
from spotdl.utils.po_token import (
    bgutil_http_installed,
    ensure_po_token_server,
    helper_status,
    po_token_warning_message,
    stop_po_token_server,
)
from spotdl.utils.search import get_simple_songs, sanitize_spotify_url
from spotdl.utils.spotify import SpotifyClient, SpotifyError

__all__ = ["easy", "create_app", "setup_status", "should_close_easy"]

logger = logging.getLogger(__name__)

EASY_PORT = 8810
EASY_IDLE_SECONDS = 10 * 60
EASY_ABANDONED_SECONDS = 15 * 60
BITRATE_RE = re.compile(r"^(auto|disable|\d+k)$")
COOKIE_HELP = (
    "YouTube cookies are optional until a song says you must sign in. "
    "If that happens: open a private window, sign in to YouTube, open only "
    "https://www.youtube.com/robots.txt, and export the cookies as cookies.txt. "
    "Save that file as the cookies.txt inside your spotdl settings folder "
    "(the same folder as config.json). A throwaway Google account is safer. "
    "Do not commit the file."
)
FFMPEG_HELP = (
    "FFmpeg is not installed. Run `spotdl --download-ffmpeg`, or install ffmpeg "
    "and make sure the `ffmpeg` command is on PATH (`brew install ffmpeg` on macOS)."
)
PLUGIN_HELP = (
    "The PO-token plugin is not installed for this Python. YouTube often refuses "
    "a playlist without it. Install it with the same interpreter you use for spotdl:\n"
    "python -m pip install 'bgutil-ytdlp-pot-provider==1.3.1'\n"
    "Then start the server on 127.0.0.1:4416 (see docs/local-setup.md)."
)

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>spotdl</title>
<style>
  :root {
    color-scheme: light;
    --desktop: #d8d8de;
    --window: rgba(246, 246, 248, 0.92);
    --titlebar: rgba(236, 236, 240, 0.9);
    --ink: #1d1d1f;
    --muted: #6e6e73;
    --line: rgba(0, 0, 0, 0.08);
    --field: #fff;
    --accent: #0071e3;
    --accent-press: #0062c4;
    --shadow: 0 30px 70px rgba(0, 0, 0, 0.18);
    --banner: #fff8eb;
    --banner-ink: #6b4e16;
    --ok: #1d7a3a;
    --bad: #c41818;
    --skip: #9a6700;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      color-scheme: dark;
      --desktop: #1c1c1e;
      --window: rgba(44, 44, 46, 0.92);
      --titlebar: rgba(58, 58, 60, 0.92);
      --ink: #f5f5f7;
      --muted: #a1a1a6;
      --line: rgba(255, 255, 255, 0.1);
      --field: #1c1c1e;
      --accent: #0a84ff;
      --accent-press: #409cff;
      --shadow: 0 30px 70px rgba(0, 0, 0, 0.45);
      --banner: rgba(255, 214, 10, 0.12);
      --banner-ink: #ffe08a;
      --ok: #30d158;
      --bad: #ff453a;
      --skip: #ffd60a;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    display: grid;
    place-items: center;
    padding: 32px 16px;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", sans-serif;
    color: var(--ink);
    background:
      radial-gradient(1200px 500px at 50% -10%, rgba(255,255,255,0.45), transparent 60%),
      var(--desktop);
  }
  .window {
    width: min(640px, 100%);
    border-radius: 18px;
    overflow: hidden;
    background: var(--window);
    backdrop-filter: blur(24px);
    box-shadow: var(--shadow);
    border: 1px solid rgba(255, 255, 255, 0.35);
  }
  header {
    display: flex;
    gap: 14px;
    align-items: center;
    padding: 14px 18px 12px;
    background: var(--titlebar);
    border-bottom: 1px solid var(--line);
  }
  .lights { display: flex; gap: 6px; }
  .light {
    width: 12px; height: 12px; border-radius: 50%; display: block; padding: 0;
  }
  button.light { border: 0; cursor: pointer; min-width: 0; }
  button.light:disabled { opacity: 1; }
  .light.close { background: #ff5f57; }
  .light.close:hover { background: #e0443e; }
  .light.minimize { background: #febc2e; }
  .light.zoom { background: #28c840; }
  h1 { font-size: 15px; font-weight: 650; margin: 0; letter-spacing: -0.02em; }
  .lead { margin: 1px 0 0; color: var(--muted); font-size: 12px; }
  .body { padding: 18px; }
  label {
    display: block;
    margin: 14px 0 6px;
    font-size: 12px;
    font-weight: 600;
    color: var(--muted);
  }
  input, select {
    width: 100%;
    font: inherit;
    font-size: 14px;
    color: var(--ink);
    background: var(--field);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 9px 12px;
    outline: none;
  }
  input:focus, select:focus { box-shadow: 0 0 0 4px rgba(0, 113, 227, 0.18); }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .actions { display: flex; justify-content: flex-end; align-items: center; gap: 8px; margin-top: 16px; }
  .note { margin: 14px 0 0; color: var(--muted); font-size: 12px; line-height: 1.4; min-height: 1.4em; }
  button {
    font: inherit;
    font-size: 14px;
    font-weight: 600;
    color: white;
    background: var(--accent);
    border: 0;
    border-radius: 10px;
    padding: 9px 18px;
    min-width: 128px;
    cursor: pointer;
  }
  button.secondary {
    color: var(--ink);
    background: transparent;
    border: 1px solid var(--line);
    min-width: 0;
  }
  button:hover { background: var(--accent-press); }
  button.secondary:hover { background: rgba(120, 120, 128, 0.12); }
  button:disabled { opacity: 0.45; cursor: default; }
  .lights button.light {
    min-width: 12px;
    width: 12px;
    height: 12px;
    padding: 0;
  }
  .banner {
    background: var(--banner);
    color: var(--banner-ink);
    border-radius: 12px;
    padding: 12px 14px;
    margin-bottom: 10px;
    font-size: 13px;
    line-height: 1.45;
    white-space: pre-wrap;
  }
  h2 {
    margin: 22px 0 8px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--muted);
  }
  table { width: 100%; border-collapse: collapse; }
  th { display: none; }
  td { padding: 8px 0; border-bottom: 1px solid var(--line); vertical-align: middle; font-size: 14px; }
  td:last-child { text-align: right; white-space: nowrap; }
  .pill {
    display: inline-block;
    border-radius: 999px;
    padding: 2px 8px;
    font-size: 12px;
    font-weight: 600;
    background: rgba(120, 120, 128, 0.16);
  }
  .status-done { color: var(--ok); background: color-mix(in srgb, var(--ok) 16%, transparent); }
  .status-error, .status-no-match { color: var(--bad); background: color-mix(in srgb, var(--bad) 14%, transparent); }
  .status-skipped { color: var(--skip); background: color-mix(in srgb, var(--skip) 16%, transparent); }
  #log {
    height: 9.5rem;
    overflow: auto;
    margin: 0;
    padding: 12px;
    border-radius: 12px;
    background: var(--field);
    border: 1px solid var(--line);
    color: var(--muted);
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
    font-size: 12px;
    line-height: 1.45;
    white-space: pre-wrap;
  }
</style>
</head>
<body>
  <main class="window">
    <header>
      <div class="lights">
        <button type="button" id="quit" class="light close" title="Quit" aria-label="Quit"></button>
        <i class="light minimize"></i>
        <i class="light zoom"></i>
      </div>
      <div>
        <h1>spotdl</h1>
        <p class="lead">Paste a Spotify link. The extra ?si= text is ignored. Songs stay on this Mac.</p>
      </div>
    </header>
    <div class="body">
      <div id="setup"></div>
      <form id="form">
        <label for="output_dir">Save songs to</label>
        <input id="output_dir" name="output_dir" required>
        <label for="url">Spotify link</label>
        <input id="url" name="url" placeholder="https://open.spotify.com/album/..." required>
        <div class="row">
          <div>
            <label for="bitrate">Bitrate</label>
            <select id="bitrate" name="bitrate">
              <option value="128k">128k</option>
              <option value="192k">192k</option>
              <option value="256k">256k</option>
              <option value="320k" selected>320k</option>
            </select>
          </div>
          <div>
            <label for="threads">Threads</label>
            <input id="threads" name="threads" type="number" min="1" max="16" value="1">
          </div>
        </div>
        <label for="cookie_file">YouTube cookies, if you have them</label>
        <input id="cookie_file" name="cookie_file" placeholder="Leave blank unless YouTube asks you to sign in">
        <p class="note" id="helper-note"></p>
        <div class="actions">
          <button type="button" id="keep-open" class="secondary" hidden>Keep open</button>
          <button type="button" id="stop-helper" class="secondary" hidden>Stop helper</button>
          <button type="button" id="quit-text" class="secondary">Quit</button>
          <button id="start" type="submit">Download</button>
        </div>
      </form>
      <h2>Songs</h2>
      <table>
        <thead><tr><th>Song</th><th>Status</th></tr></thead>
        <tbody id="songs"></tbody>
      </table>
      <h2>Log</h2>
      <div id="log" aria-live="polite"></div>
    </div>
  </main>
<script>
const outputDir = document.getElementById("output_dir");
const cookieFile = document.getElementById("cookie_file");
const setup = document.getElementById("setup");
const songs = document.getElementById("songs");
const log = document.getElementById("log");
const start = document.getElementById("start");
const form = document.getElementById("form");
const helperNote = document.getElementById("helper-note");
const stopHelper = document.getElementById("stop-helper");
const keepOpen = document.getElementById("keep-open");

function banner(text) {
  const box = document.createElement("div");
  box.className = "banner";
  box.textContent = text;
  return box;
}

async function refresh() {
  const response = await fetch("/api/status");
  const data = await response.json();
  if (!outputDir.value) outputDir.value = data.output_dir;
  if (!cookieFile.value && data.cookie_file) cookieFile.value = data.cookie_file;
  setup.replaceChildren();
  for (const message of data.messages) setup.appendChild(banner(message));
  start.disabled = data.running || !data.credentials_ok || !data.ffmpeg_ok || !data.js_runtime;
  stopHelper.hidden = !data.helper_owned;
  keepOpen.hidden = data.closing_in == null;
  if (data.closing_in != null) {
    const minutes = Math.max(1, Math.ceil(data.closing_in / 60));
    helperNote.textContent = "This window closes in " + minutes + " minute" + (minutes === 1 ? "" : "s") + ". The background helper closes with it.";
  } else if (data.helper_owned) {
    const minutes = Math.round((data.helper_idle_seconds || 300) / 60);
    helperNote.textContent = "A background helper is running. It closes on its own " + minutes + " minutes after spotdl exits. Stop helper closes it now.";
  } else if (data.helper_running) {
    helperNote.textContent = "A helper is already running on this Mac. This window will leave it alone.";
  } else {
    helperNote.textContent = "";
  }
  songs.replaceChildren();
  for (const song of data.songs) {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    const state = document.createElement("td");
    const pill = document.createElement("span");
    name.textContent = song.name;
    pill.textContent = song.status;
    pill.className = "pill status-" + song.status.replace(" ", "-");
    pill.title = song.detail || "";
    state.appendChild(pill);
    row.append(name, state);
    songs.appendChild(row);
  }
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  log.textContent = data.log.join("\\n");
  if (atBottom) log.scrollTop = log.scrollHeight;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  start.disabled = true;
  const body = {
    url: document.getElementById("url").value,
    output_dir: outputDir.value,
    bitrate: document.getElementById("bitrate").value,
    threads: Number(document.getElementById("threads").value || 1),
    cookie_file: cookieFile.value
  };
  const response = await fetch("/api/download", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const message = payload.detail || "Could not start the download.";
    setup.appendChild(banner(typeof message === "string" ? message : JSON.stringify(message)));
  }
  refresh();
});

async function quit() {
  await fetch("/api/quit", {method: "POST"});
  helperNote.textContent = "Closed. You can close this tab.";
  start.disabled = true;
}

document.getElementById("quit").addEventListener("click", quit);
document.getElementById("quit-text").addEventListener("click", quit);
stopHelper.addEventListener("click", async () => {
  stopHelper.disabled = true;
  await fetch("/api/helper/stop", {method: "POST"});
  stopHelper.disabled = false;
  refresh();
});
keepOpen.addEventListener("click", async () => {
  await fetch("/api/keep-open", {method: "POST"});
  refresh();
});

refresh();
setInterval(refresh, 1000);
</script>
</body>
</html>
"""


class DownloadRequest(BaseModel):
    """
    Fields posted by the easy page.
    """

    url: str
    output_dir: str = ""
    bitrate: str = "320k"
    threads: int = Field(default=1, ge=1, le=16)
    cookie_file: str = ""


class DownloadJob:
    """
    One download at a time, with log lines and per-song status for the page.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.log_lines: List[str] = []
        self.songs: Dict[str, Dict[str, str]] = {}
        self.order: List[str] = []

    def add_log(self, line: str) -> None:
        """
        Append a log line, keeping the last 400.
        """

        with self.lock:
            self.log_lines.append(line)
            del self.log_lines[:-400]

    def note(self, name: str, status: str, detail: str) -> None:
        """
        Record the latest status for a song.
        """

        with self.lock:
            if name not in self.songs:
                self.order.append(name)
            self.songs[name] = {"name": name, "status": status, "detail": detail}

    def snapshot(self) -> Dict[str, Any]:
        """
        Copy the current job for the status endpoint.
        """

        with self.lock:
            return {
                "running": self.running,
                "log": list(self.log_lines),
                "songs": [
                    dict(self.songs[name]) for name in self.order if name in self.songs
                ],
            }


class _JobLogHandler(logging.Handler):
    """
    Send spotdl log records to the easy page.
    """

    def __init__(self, job: DownloadJob) -> None:
        super().__init__()
        self.job = job

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.job.add_log(self.format(record))
        except Exception:
            return


def setup_status(
    spotify_settings: Dict[str, Any], downloader_settings: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Plain-language blockers for the easy page.

    ### Arguments
    - spotify_settings: Resolved Spotify settings.
    - downloader_settings: Resolved downloader settings.

    ### Returns
    - Status fields the page renders. Credentials and cookie contents are not included.
    """

    problem = spotify_credentials_problem(spotify_settings.get("client_id"))
    runtime = describe_js_runtime(downloader_settings.get("yt_dlp_args"))
    ffmpeg_ok = is_ffmpeg_installed(downloader_settings.get("ffmpeg") or "ffmpeg")
    po_message = po_token_warning_message()
    cookie_file = downloader_settings.get("cookie_file") or ""
    messages: List[str] = []
    if problem:
        messages.append(problem)
    if not cookie_file or not Path(cookie_file).is_file():
        messages.append(COOKIE_HELP)
    if runtime is None:
        messages.append(MISSING_JS_RUNTIME_HINT)
    if not bgutil_http_installed():
        messages.append(PLUGIN_HELP)
    if po_message:
        messages.append(po_message)
    if not ffmpeg_ok:
        messages.append(FFMPEG_HELP)

    return {
        "credentials_ok": problem is None,
        "js_runtime": runtime,
        "ffmpeg_ok": ffmpeg_ok,
        "messages": messages,
        "config_path": str(get_config_file()),
    }


def _init_spotify(spotify_settings: Dict[str, Any]) -> None:
    """
    Initialize Spotify once per process.
    """

    try:
        SpotifyClient.init(**spotify_settings)
    except SpotifyError as exc:
        if "already been initialized" not in str(exc):
            raise


class EasySession:
    """
    Timers for closing the easy page after a download.
    """

    def __init__(self) -> None:
        now = time.monotonic()
        self.finished_at: Optional[float] = None
        self.keep_until = 0.0
        self.last_request = now
        self.on_quit: Optional[Callable[[], None]] = None


def should_close_easy(
    *,
    running: bool,
    finished_at: Optional[float],
    keep_until: float,
    last_request: float,
    now: float,
    idle_seconds: float = EASY_IDLE_SECONDS,
    abandoned_seconds: float = EASY_ABANDONED_SECONDS,
) -> bool:
    """
    Decide whether the easy page should exit.

    ### Arguments
    - running: A download is in progress.
    - finished_at: When the last download finished, or None.
    - keep_until: Deadline set by Keep open.
    - last_request: When the browser last asked for status.
    - now: Current monotonic time.
    - idle_seconds: How long to stay open after a download.
    - abandoned_seconds: How long to stay open with the tab closed.

    ### Returns
    - True when the page and the helper it started should close.
    """

    if running:
        return False
    if now - last_request >= abandoned_seconds:
        return True
    if finished_at is None:
        return False
    return now >= max(finished_at + idle_seconds, keep_until)


def closing_in_seconds(
    session: EasySession, running: bool, now: float
) -> Optional[int]:
    """
    Seconds until the page closes itself after a download.

    ### Arguments
    - session: Page timers.
    - running: A download is in progress.
    - now: Current monotonic time.

    ### Returns
    - Remaining seconds, or None when no close is scheduled.
    """

    if running or session.finished_at is None:
        return None
    remaining = max(session.finished_at + EASY_IDLE_SECONDS, session.keep_until) - now
    if remaining <= 0:
        return 0
    return int(remaining)


def _watch_easy_server(server: Server, job: DownloadJob, session: EasySession) -> None:
    """
    Close the page after it has been idle, and stop the helper spotdl started.
    """

    while not server.should_exit:
        time.sleep(5)
        if server.should_exit:
            return
        now = time.monotonic()
        with job.lock:
            running = job.running
        if should_close_easy(
            running=running,
            finished_at=session.finished_at,
            keep_until=session.keep_until,
            last_request=session.last_request,
            now=now,
        ):
            stop_po_token_server()
            server.should_exit = True
            return


def _run_download(
    job: DownloadJob,
    session: EasySession,
    spotify_settings: Dict[str, Any],
    downloader_settings: Dict[str, Any],
    request: DownloadRequest,
) -> None:
    """
    Run the same download path as the CLI.
    """

    handler = _JobLogHandler(job)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    root = logging.getLogger("spotdl")
    root.addHandler(handler)
    try:
        prevent_sleep()
        settings = dict(downloader_settings)
        output_dir = Path(request.output_dir).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        settings["output"] = str(output_dir / "{artists} - {title}.{output-ext}")
        settings["bitrate"] = request.bitrate
        settings["threads"] = request.threads
        settings["overwrite"] = "skip"
        settings["simple_tui"] = True
        if request.cookie_file:
            settings["cookie_file"] = request.cookie_file
        settings["audio_providers"] = resolve_audio_providers(
            list(settings["audio_providers"])
        )
        if not ensure_po_token_server():
            job.add_log(
                "The PO-token server did not start. YouTube downloads often fail "
                "without it. See the log above."
            )

        _init_spotify(spotify_settings)
        songs = get_simple_songs(
            [request.url.strip()],
            use_ytm_data=settings["ytm_data"],
            playlist_numbering=settings["playlist_numbering"],
            albums_to_ignore=settings["ignore_albums"],
            album_type=settings["album_type"],
            playlist_retain_track_cover=settings["playlist_retain_track_cover"],
        )
        if not songs:
            job.add_log("No songs found for that link.")
            return

        downloader = Downloader(settings)
        downloader.status_callback = job.note
        download_songs_with_resume(downloader, songs)
    except Exception as exc:
        logger.error("%s", exc)
        job.add_log(f"Error: {exc}")
    finally:
        root.removeHandler(handler)
        with job.lock:
            job.running = False
        session.finished_at = time.monotonic()


def create_app(
    spotify_settings: Dict[str, Any],
    downloader_settings: Dict[str, Any],
    output_dir: Optional[str] = None,
) -> FastAPI:
    """
    Build the localhost easy app.

    ### Arguments
    - spotify_settings: Resolved Spotify settings.
    - downloader_settings: Resolved downloader settings.
    - output_dir: Default folder shown in the form.

    ### Returns
    - FastAPI application.
    """

    app = FastAPI(title="spotdl easy", docs_url=None, redoc_url=None, openapi_url=None)
    job = DownloadJob()
    session = EasySession()
    app.state.job = job
    app.state.session = session
    default_output = output_dir or str(Path.cwd())

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE

    @app.get("/api/status")
    def status() -> Dict[str, Any]:
        session.last_request = time.monotonic()
        payload = setup_status(spotify_settings, downloader_settings)
        payload.update(job.snapshot())
        payload.update(helper_status())
        payload["closing_in"] = closing_in_seconds(
            session, bool(payload["running"]), time.monotonic()
        )
        payload["output_dir"] = default_output
        payload["cookie_file"] = downloader_settings.get("cookie_file") or ""
        return payload

    @app.post("/api/helper/stop")
    def stop_helper() -> Dict[str, bool]:
        return {"ok": stop_po_token_server()}

    @app.post("/api/keep-open")
    def keep_open() -> Dict[str, bool]:
        session.keep_until = time.monotonic() + EASY_IDLE_SECONDS
        session.last_request = time.monotonic()
        return {"ok": True}

    @app.post("/api/quit")
    def quit_app() -> Dict[str, bool]:
        stop_po_token_server()
        if session.on_quit is not None:
            session.on_quit()
        return {"ok": True}

    @app.post("/api/download")
    def start(body: DownloadRequest) -> Dict[str, bool]:
        problem = spotify_credentials_problem(spotify_settings.get("client_id"))
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        if not is_ffmpeg_installed(downloader_settings.get("ffmpeg") or "ffmpeg"):
            raise HTTPException(status_code=400, detail=FFMPEG_HELP)
        if describe_js_runtime(downloader_settings.get("yt_dlp_args")) is None:
            raise HTTPException(status_code=400, detail=MISSING_JS_RUNTIME_HINT)
        if not BITRATE_RE.match(body.bitrate):
            raise HTTPException(
                status_code=400, detail="Bitrate must look like 320k, auto, or disable."
            )
        cookie_file = ""
        if body.cookie_file.strip():
            cookie_path = Path(body.cookie_file).expanduser()
            if not cookie_path.is_file():
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not find that cookie file: {cookie_path}",
                )
            cookie_file = str(cookie_path)
        url = sanitize_spotify_url(body.url.strip())
        if not url.startswith(("https://", "http://")):
            raise HTTPException(
                status_code=400,
                detail="Paste a full link, starting with https://",
            )
        with job.lock:
            if job.running:
                raise HTTPException(
                    status_code=409, detail="A download is already running."
                )
            job.running = True
            job.songs.clear()
            job.order.clear()
            job.log_lines.clear()
        body.cookie_file = cookie_file
        body.url = url
        threading.Thread(
            target=_run_download,
            args=(job, session, spotify_settings, downloader_settings, body),
            daemon=True,
        ).start()
        return {"ok": True}

    return app


def easy(
    spotify_settings: Dict[str, Any],
    downloader_settings: Dict[str, Any],
    web_settings: Dict[str, Any],
) -> None:
    """
    Serve the easy page on localhost and open it in a browser.

    ### Arguments
    - spotify_settings: Resolved Spotify settings.
    - downloader_settings: Resolved downloader settings.
    - web_settings: Web settings. `--port` selects the port; the host is always loopback.
    """

    host = "127.0.0.1"
    requested_host = web_settings.get("host") or host
    if requested_host not in {host, "localhost", "::1"}:
        logger.warning(
            "spotdl easy only listens on %s (ignoring %s).", host, requested_host
        )

    port = int(web_settings["port"]) if "--port" in sys.argv else EASY_PORT
    app = create_app(spotify_settings, downloader_settings)
    config = Config(app=app, host=host, port=port, log_level="info")
    server = Server(config)

    def on_quit() -> None:
        def _later() -> None:
            time.sleep(0.2)
            server.should_exit = True

        threading.Thread(target=_later, daemon=True).start()

    app.state.session.on_quit = on_quit
    threading.Thread(
        target=_watch_easy_server,
        args=(server, app.state.job, app.state.session),
        daemon=True,
    ).start()
    url = f"http://{host}:{port}/"
    logger.info(
        "Easy UI at %s. It closes itself %s minutes after a download, "
        "or when you press Quit.",
        url,
        EASY_IDLE_SECONDS // 60,
    )
    webbrowser.open(url)
    server.run()
