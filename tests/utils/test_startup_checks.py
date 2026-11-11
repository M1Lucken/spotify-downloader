"""
Tests for Spotify credential checks and automatic PO-token server startup.
"""

import logging
from pathlib import Path

from spotdl.utils.credentials import (
    SHARED_SPOTIFY_CLIENT_ID,
    ensure_personal_spotify_app,
    spotify_credentials_problem,
)
from spotdl.utils.po_token import (
    ensure_po_token_server,
    po_token_warning_message,
    prepare_pot_server_dir,
)
from spotdl.utils.spotify import SpotifyError


def test_shared_client_id_is_refused(monkeypatch):
    monkeypatch.setattr(
        "spotdl.utils.credentials.get_config_file",
        lambda: Path("/tmp/spotdl-config.json"),
    )
    monkeypatch.setattr(
        "spotdl.utils.credentials.get_cache_path",
        lambda: Path("/tmp/spotdl-cache"),
    )

    message = spotify_credentials_problem(SHARED_SPOTIFY_CLIENT_ID)

    assert message is not None
    assert "https://developer.spotify.com/dashboard" in message
    assert "http://127.0.0.1:9900/" in message
    assert "86400" in message

    try:
        ensure_personal_spotify_app(None)
    except SpotifyError as exc:
        assert "spotdl --generate-config" in str(exc)
    else:
        raise AssertionError("missing client id was accepted")


def test_personal_client_id_is_accepted():
    assert spotify_credentials_problem("my-own-client-id") is None


def test_prepare_pot_server_uses_existing_checkout(monkeypatch, tmp_path):
    server_dir = tmp_path / "server"
    (server_dir / "src").mkdir(parents=True)
    (server_dir / "package.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("SPOTDL_POT_SERVER", str(server_dir))
    monkeypatch.setattr(
        "spotdl.utils.po_token.get_spotdl_path", lambda: tmp_path / "config"
    )

    assert prepare_pot_server_dir() == server_dir


def test_po_token_status_describes_automatic_start(monkeypatch):
    monkeypatch.delenv("SPOTDL_NO_POT_SERVER", raising=False)
    monkeypatch.setattr("spotdl.utils.po_token.pot_server_listening", lambda: False)

    message = po_token_warning_message()

    assert message is not None
    assert "127.0.0.1:4416" in message
    assert "node build/main.js" in message
    assert "1.3.1" in message


def test_ensure_po_token_server_spawns_node(monkeypatch, tmp_path, caplog):
    server_dir = tmp_path / "server"
    (server_dir / "build").mkdir(parents=True)
    (server_dir / "build" / "main.js").write_text("/* built */\n", encoding="utf-8")
    probes = {"count": 0}

    def listening() -> bool:
        probes["count"] += 1
        return probes["count"] > 1

    spawned = {}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            spawned["cmd"] = cmd
            spawned["cwd"] = kwargs.get("cwd")

    monkeypatch.delenv("SPOTDL_NO_POT_SERVER", raising=False)
    monkeypatch.setattr("spotdl.utils.po_token.pot_server_listening", listening)
    monkeypatch.setattr("spotdl.utils.po_token.node_major_version", lambda: 22)
    monkeypatch.setattr("spotdl.utils.po_token.bgutil_http_installed", lambda: True)
    monkeypatch.setattr(
        "spotdl.utils.po_token.prepare_pot_server_dir", lambda: server_dir
    )
    monkeypatch.setattr(
        "spotdl.utils.po_token.shutil.which", lambda name: f"/usr/bin/{name}"
    )
    monkeypatch.setattr(
        "spotdl.utils.po_token.pot_log_path", lambda: tmp_path / "pot.log"
    )
    monkeypatch.setattr("spotdl.utils.po_token.subprocess.Popen", FakePopen)
    monkeypatch.setattr("spotdl.utils.po_token.time.sleep", lambda *_: None)

    with caplog.at_level(logging.INFO):
        assert ensure_po_token_server() is True

    assert spawned["cmd"][0].endswith("node")
    assert spawned["cmd"][1:] == ["build/main.js"]
    assert spawned["cwd"] == server_dir


def test_ensure_po_token_server_skips_when_opted_out(monkeypatch):
    monkeypatch.setenv("SPOTDL_NO_POT_SERVER", "1")
    monkeypatch.setattr(
        "spotdl.utils.po_token.prepare_pot_server_dir",
        lambda: (_ for _ in ()).throw(AssertionError("should not prepare a server")),
    )

    assert ensure_po_token_server() is True


def test_ensure_po_token_server_reports_missing_node(monkeypatch, caplog):
    monkeypatch.delenv("SPOTDL_NO_POT_SERVER", raising=False)
    monkeypatch.setattr("spotdl.utils.po_token.pot_server_listening", lambda: False)
    monkeypatch.setattr("spotdl.utils.po_token.node_major_version", lambda: None)
    monkeypatch.setattr("spotdl.utils.po_token.bgutil_http_installed", lambda: True)

    with caplog.at_level(logging.ERROR):
        assert ensure_po_token_server() is False

    assert "Node.js >= 20" in caplog.text
