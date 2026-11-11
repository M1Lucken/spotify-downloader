"""
Tests for yt-dlp JavaScript runtime defaults and error text.
"""

from spotdl.providers.audio.base import (
    AudioProvider,
    AudioProviderError,
    format_yt_dlp_error,
)
from spotdl.utils.js_runtime import MISSING_JS_RUNTIME_HINT, user_specified_js_runtime


def test_user_js_runtime_flag_is_detected():
    assert user_specified_js_runtime("--extractor-args foo --js-runtimes deno")
    assert user_specified_js_runtime("--no-js-runtimes")
    assert not user_specified_js_runtime("--retries 5")


def test_user_js_runtime_overrides_node_default(monkeypatch, tmp_path):
    captured_options = {}

    def mock_youtube_dl(options):
        captured_options.update(options)
        return object()

    monkeypatch.setattr("spotdl.providers.audio.base.YoutubeDL", mock_youtube_dl)
    monkeypatch.setattr(
        "spotdl.providers.audio.base.get_temp_path", lambda *_: tmp_path
    )
    monkeypatch.setattr("spotdl.utils.js_runtime.node_major_version", lambda: 22)

    user_deno = tmp_path / "user-deno"
    AudioProvider(yt_dlp_args=f"--js-runtimes deno:{user_deno}")

    assert captured_options["js_runtimes"] == {"deno": {"path": str(user_deno)}}
    assert captured_options["js_runtimes"].get("node") is None


def test_format_yt_dlp_error_includes_cause_and_runtime_hint():
    cause = RuntimeError("Signature solving failed: no js runtime")
    error = RuntimeError("download failed")
    error.__cause__ = cause

    message = format_yt_dlp_error("https://example.com/watch?v=1", error, True)

    assert "Signature solving failed" in message
    assert "https://example.com/watch?v=1" in message
    assert "Node.js >= 20" in message
    assert MISSING_JS_RUNTIME_HINT in message


def test_audio_provider_error_includes_underlying_message(monkeypatch, tmp_path):
    provider = AudioProvider.__new__(AudioProvider)
    provider.js_runtime_missing = False
    cause = RuntimeError("Requested format is not available")
    failure = RuntimeError("")
    failure.__cause__ = cause
    provider.audio_handler = type(
        "Handler",
        (),
        {
            "extract_info": staticmethod(
                lambda *_args, **_kwargs: (_ for _ in ()).throw(failure)
            )
        },
    )()

    try:
        provider.get_download_metadata("https://youtu.be/abc", download=True)
    except AudioProviderError as exc:
        assert "Requested format is not available" in str(exc)
        assert "https://youtu.be/abc" in str(exc)
        assert exc.__cause__ is failure
    else:
        raise AssertionError("AudioProviderError was not raised")
