from pathlib import Path

from spotdl.console.download import normalize_output
from spotdl.utils.search import sanitize_spotify_url


def test_sanitize_spotify_url_drops_share_query():
    raw = "https://open.spotify.com/album/7tWh96JdQB9AJo5SE0QiFc?si=eSX59cgFQheAmgQ5sMeyFA"
    assert (
        sanitize_spotify_url(raw)
        == "https://open.spotify.com/album/7tWh96JdQB9AJo5SE0QiFc"
    )


def test_sanitize_spotify_url_drops_locale_prefix():
    raw = "https://open.spotify.com/intl-de/track/abc?si=1#section"
    assert sanitize_spotify_url(raw) == "https://open.spotify.com/track/abc"


def test_sanitize_spotify_url_leaves_youtube_queries():
    raw = "https://music.youtube.com/watch?v=dQw4w9WgXcQ"
    assert sanitize_spotify_url(raw) == raw


def test_normalize_output_uses_a_folder(tmp_path: Path):
    folder = tmp_path / "villager"
    assert normalize_output(str(folder)) == str(
        folder / "{artists} - {title}.{output-ext}"
    )
    assert folder.is_dir()


def test_normalize_output_keeps_templates():
    template = "{artists} - {title}.{output-ext}"
    assert normalize_output(template) == template
