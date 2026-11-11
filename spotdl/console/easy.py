"""
One-page local UI for spotdl download.
"""

import logging
import re
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    po_token_warning_message,
)
from spotdl.utils.search import get_simple_songs
from spotdl.utils.spotify import SpotifyClient, SpotifyError

__all__ = ["easy", "create_app", "setup_status"]

logger = logging.getLogger(__name__)

EASY_PORT = 8810
BITRATE_RE = re.compile(r"^(auto|disable|\d+k)$")
COOKIE_HELP = (
    "YouTube cookies are not set. In a private window, sign in to YouTube, open only "
    "https://www.youtube.com/robots.txt, then export youtube.com cookies as a Netscape "
    "cookies.txt. Save that file somewhere outside this git repo and paste its path below. "
    "A throwaway Google account is safer than a personal one; a personal account can be "
    "restricted. Do not commit the cookie file. "
    "Steps: https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies "
    "and https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
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
  :root { color-scheme: light dark; }
  body {
    font-family: ui-sans-serif, system-ui, sans-serif;
    margin: 0 auto;
    max-width: 42rem;
    padding: 1.25rem;
    line-height: 1.45;
  }
  h1 { font-size: 1.4rem; margin: 0 0 0.25rem; }
  p.lead { margin-top: 0; }
  label { display: block; font-weight: 600; margin-top: 0.8rem; }
  input, select, button {
    font: inherit;
    width: 100%;
    box-sizing: border-box;
    margin-top: 0.25rem;
  }
  input, select { padding: 0.4rem 0.5rem; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
  button {
    margin-top: 1rem;
    padding: 0.55rem 0.8rem;
    font-weight: 650;
    cursor: pointer;
  }
  button:disabled { cursor: not-allowed; opacity: 0.55; }
  .banner {
    border: 1px solid #b45309;
    background: #fff7ed;
    color: #431407;
    padding: 0.75rem;
    margin: 0.75rem 0;
    white-space: pre-wrap;
  }
  @media (prefers-color-scheme: dark) {
    .banner { background: #431407; color: #ffedd5; border-color: #c2410c; }
  }
  table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
  th, td { text-align: left; padding: 0.3rem 0.2rem; border-bottom: 1px solid #8884; vertical-align: top; }
  #log {
    height: 12rem;
    overflow: auto;
    white-space: pre-wrap;
    font-family: ui-monospace, monospace;
    font-size: 0.82rem;
    border: 1px solid #8884;
    padding: 0.5rem;
    margin-top: 0.75rem;
  }
  .status-done { color: #15803d; }
  .status-error, .status-no-match { color: #b91c1c; }
  .status-skipped { color: #a16207; }
</style>
</head>
<body>
  <h1>spotdl</h1>
  <p class="lead">Download a Spotify song, playlist, album, or artist. This page stays on this computer.</p>
  <div id="setup"></div>
  <form id="form">
    <label for="output_dir">Save songs to</label>
    <input id="output_dir" name="output_dir" required>
    <label for="url">Spotify or YouTube Music link</label>
    <input id="url" name="url" placeholder="https://open.spotify.com/playlist/..." required>
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
    <label for="cookie_file">YouTube cookies.txt</label>
    <input id="cookie_file" name="cookie_file" placeholder="/path/to/cookies.txt">
    <button id="start" type="submit">Start</button>
  </form>
  <table>
    <thead><tr><th>Song</th><th>Status</th></tr></thead>
    <tbody id="songs"></tbody>
  </table>
  <div id="log" aria-live="polite"></div>
<script>
const outputDir = document.getElementById("output_dir");
const cookieFile = document.getElementById("cookie_file");
const setup = document.getElementById("setup");
const songs = document.getElementById("songs");
const log = document.getElementById("log");
const start = document.getElementById("start");
const form = document.getElementById("form");

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
  start.disabled = data.running || !data.credentials_ok || !data.ffmpeg_ok || !data.js_runtime || !cookieFile.value.trim();
  songs.replaceChildren();
  for (const song of data.songs) {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    const state = document.createElement("td");
    name.textContent = song.name;
    const label = song.detail && song.status === "error" ? song.status + ": " + song.detail : song.status;
    state.textContent = label;
    state.className = "status-" + song.status.replace(" ", "-");
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


def _run_download(
    job: DownloadJob,
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
    default_output = output_dir or str(Path.cwd())

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE

    @app.get("/api/status")
    def status() -> Dict[str, Any]:
        payload = setup_status(spotify_settings, downloader_settings)
        payload.update(job.snapshot())
        payload["output_dir"] = default_output
        payload["cookie_file"] = downloader_settings.get("cookie_file") or ""
        return payload

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
        cookie_path = Path(body.cookie_file).expanduser()
        if not body.cookie_file or not cookie_path.is_file():
            raise HTTPException(status_code=400, detail=COOKIE_HELP)
        url = body.url.strip()
        if not url.startswith(("https://", "http://")):
            raise HTTPException(
                status_code=400,
                detail="Paste a full https link to a Spotify track, playlist, album, or artist.",
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
        body.cookie_file = str(cookie_path)
        threading.Thread(
            target=_run_download,
            args=(job, spotify_settings, downloader_settings, body),
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
    url = f"http://{host}:{port}/"
    logger.info("Easy UI at %s", url)
    webbrowser.open(url)
    server.run()
