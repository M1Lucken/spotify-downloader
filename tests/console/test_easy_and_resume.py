"""
Tests for playlist resume and the easy page setup copy.
"""

from pathlib import Path

from spotdl.console.download import download_songs_with_resume, log_download_summary
from spotdl.console.easy import COOKIE_HELP, PAGE, setup_status, should_close_easy
from spotdl.utils.credentials import SHARED_SPOTIFY_CLIENT_ID


class _Song:
    def __init__(self, url: str, name: str) -> None:
        self.url = url
        self.display_name = name


class _Downloader:
    def __init__(self, rounds):
        self.downloaded = {}
        self.skipped = {}
        self.misses = {}
        self.failed = {}
        self.rounds = rounds
        self.calls = 0

    def download_multiple_songs(self, songs):
        behavior = self.rounds[self.calls]
        self.calls += 1
        results = []
        for song in songs:
            kind = behavior.get(song.url, "fail")
            if kind == "ok":
                self.downloaded[song.url] = song.display_name
                self.failed.pop(song.url, None)
                results.append((song, Path("/tmp/song.mp3")))
            elif kind == "miss":
                self.misses[song.url] = f"{song.display_name}: No results found"
                results.append((song, None))
            elif kind == "skip":
                self.skipped[song.url] = song.display_name
                self.failed.pop(song.url, None)
                results.append((song, Path("/tmp/song.mp3")))
            else:
                self.failed[song.url] = song.display_name
                results.append((song, None))
        return results


def test_resume_retries_then_stops_when_a_round_makes_no_files():
    song = _Song("https://open.spotify.com/track/a", "A")
    downloader = _Downloader([{"https://open.spotify.com/track/a": "fail"}] * 3)

    download_songs_with_resume(downloader, [song], max_rounds=3)

    assert downloader.calls == 2


def test_resume_does_not_retry_lookup_misses():
    song = _Song("https://open.spotify.com/track/b", "B")
    downloader = _Downloader([{"https://open.spotify.com/track/b": "miss"}])

    download_songs_with_resume(downloader, [song], max_rounds=3)

    assert downloader.calls == 1


def test_summary_lists_skipped_and_failed(caplog):
    downloader = _Downloader([])
    downloader.skipped["s"] = "Skipped Song"
    downloader.failed["f"] = "Failed Song: YT-DLP download error"
    downloader.misses["m"] = "Missing Song: No results found"
    downloader.downloaded["d"] = "Done Song"

    with caplog.at_level("INFO"):
        log_download_summary(downloader)

    assert "Skipped Song" in caplog.text
    assert "Failed Song" in caplog.text
    assert "Missing Song" in caplog.text
    assert "1 done, 1 skipped" in caplog.text


def test_easy_page_renders_the_form():
    assert "Save songs to" in PAGE
    assert "Download" in PAGE
    assert "Quit" in PAGE
    assert "Stop helper" in PAGE
    assert "Keep open" in PAGE
    assert 'id="log"' in PAGE
    assert "320k" in PAGE


def test_easy_stays_open_during_a_download_and_closes_after_idle():
    assert (
        should_close_easy(
            running=True,
            finished_at=None,
            keep_until=0,
            last_request=0,
            now=10_000,
        )
        is False
    )
    assert (
        should_close_easy(
            running=False,
            finished_at=100,
            keep_until=0,
            last_request=100,
            now=100 + 10 * 60,
        )
        is True
    )
    assert (
        should_close_easy(
            running=False,
            finished_at=100,
            keep_until=100 + 20 * 60,
            last_request=100,
            now=100 + 10 * 60,
        )
        is False
    )
    assert (
        should_close_easy(
            running=False,
            finished_at=None,
            keep_until=0,
            last_request=0,
            now=15 * 60,
        )
        is True
    )


def test_easy_setup_explains_missing_credentials(monkeypatch):
    monkeypatch.setattr(
        "spotdl.utils.credentials.get_config_file",
        lambda: Path("/tmp/spotdl-config.json"),
    )
    monkeypatch.setattr(
        "spotdl.utils.credentials.get_cache_path",
        lambda: Path("/tmp/spotdl-cache"),
    )
    monkeypatch.setattr("spotdl.console.easy.describe_js_runtime", lambda *_: "node")
    monkeypatch.setattr("spotdl.console.easy.is_ffmpeg_installed", lambda *_: True)
    monkeypatch.setattr("spotdl.console.easy.bgutil_http_installed", lambda: True)
    monkeypatch.setattr("spotdl.console.easy.po_token_warning_message", lambda: None)

    status = setup_status(
        {"client_id": SHARED_SPOTIFY_CLIENT_ID},
        {"ffmpeg": "ffmpeg", "yt_dlp_args": None, "cookie_file": None},
    )

    text = "\n".join(status["messages"])
    assert status["credentials_ok"] is False
    assert "https://developer.spotify.com/dashboard" in text
    assert "http://127.0.0.1:9900/" in text
    assert COOKIE_HELP.split(".", 1)[0] in text
