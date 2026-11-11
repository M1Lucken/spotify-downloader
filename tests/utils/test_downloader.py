import pytest

from spotdl.download.downloader import DownloaderError
from spotdl.utils.downloader import check_ytmusic_connection, resolve_audio_providers


def test_check_ytmusic_connection_suppresses_empty_search_log(mocker):
    ytm = mocker.Mock()
    ytm.get_results.return_value = []
    mocker.patch("spotdl.utils.downloader.YouTubeMusic", return_value=ytm)

    assert check_ytmusic_connection() is False
    ytm.get_results.assert_called_once_with("a", log_search_failures=False)


def test_failed_ytmusic_check_does_not_raise(mocker):
    mocker.patch("spotdl.utils.downloader.check_ytmusic_connection", return_value=False)

    try:
        providers = resolve_audio_providers(["youtube-music"])
    except DownloaderError as exc:  # pragma: no cover
        pytest.fail(f"failed YouTube Music check raised DownloaderError: {exc}")

    assert providers == ["youtube"]


def test_failed_ytmusic_check_uses_the_next_provider(mocker):
    mocker.patch("spotdl.utils.downloader.check_ytmusic_connection", return_value=False)

    assert resolve_audio_providers(["youtube-music", "soundcloud"]) == [
        "soundcloud",
        "youtube-music",
    ]


def test_ytmusic_check_is_skipped_for_other_providers(mocker):
    check = mocker.patch(
        "spotdl.utils.downloader.check_ytmusic_connection", return_value=False
    )

    assert resolve_audio_providers(["youtube"]) == ["youtube"]
    check.assert_not_called()
