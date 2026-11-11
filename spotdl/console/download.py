"""
Download module for the console.
"""

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List

from spotdl.download.downloader import Downloader
from spotdl.types.song import Song
from spotdl.utils.search import get_simple_songs

__all__ = [
    "download",
    "download_songs_with_resume",
    "log_download_summary",
    "normalize_output",
]

logger = logging.getLogger(__name__)

MAX_RESUME_ROUNDS = 3
_sleep_inhibited = False


def normalize_output(output: str) -> str:
    """
    Treat a plain folder path as the directory songs are saved into.

    A template such as `{artists} - {title}.{output-ext}` is left unchanged.
    `~/Music/villager` becomes `~/Music/villager/{artists} - {title}.{output-ext}`.

    ### Arguments
    - output: The `--output` value.

    ### Returns
    - A file-name template.
    """

    if not output or "{" in output:
        return output

    path = Path(output).expanduser()
    if path.suffix.lower() in {
        ".mp3",
        ".m4a",
        ".flac",
        ".opus",
        ".ogg",
        ".wav",
        ".lrc",
    }:
        return output

    path.mkdir(parents=True, exist_ok=True)
    return str(path / "{artists} - {title}.{output-ext}")


def prevent_sleep() -> None:
    """
    On macOS, keep the machine awake until this process exits.
    """

    global _sleep_inhibited  # pylint: disable=global-statement

    if _sleep_inhibited or sys.platform != "darwin":
        return

    caffeinate = shutil.which("caffeinate")
    if caffeinate is None:
        return

    subprocess.Popen(  # pylint: disable=consider-using-with
        [caffeinate, "-i", "-w", str(os.getpid())],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _sleep_inhibited = True
    logger.info("Keeping the Mac awake with caffeinate until this process exits.")


def log_download_summary(downloader: Downloader) -> None:
    """
    Log which tracks finished, were skipped, had no match, or failed.

    ### Arguments
    - downloader: Downloader that recorded per-song status.
    """

    logger.info(
        "Download summary: %d done, %d skipped (already on disk), "
        "%d no match, %d failed. Re-run the same command to resume; "
        "--overwrite skip (the default) leaves existing files in place.",
        len(downloader.downloaded),
        len(downloader.skipped),
        len(downloader.misses),
        len(downloader.failed),
    )
    if downloader.skipped:
        logger.info("Skipped: %s", "; ".join(downloader.skipped.values()))
    if downloader.misses:
        logger.warning(
            "No match (not a crash): %s", "; ".join(downloader.misses.values())
        )
    if downloader.failed:
        logger.error("Failed: %s", "; ".join(downloader.failed.values()))


def _failed_songs(downloader: Downloader, results: List[tuple]) -> List[Song]:
    """
    Songs that still need another attempt. Lookup misses are not included.
    """

    failed: List[Song] = []
    for song, path in results:
        if path is not None:
            continue
        key = song.url or song.display_name
        if key in downloader.misses:
            continue
        failed.append(song)

    return failed


def download_songs_with_resume(
    downloader: Downloader,
    songs: List[Song],
    max_rounds: int = MAX_RESUME_ROUNDS,
) -> None:
    """
    Download songs, then retry failures until a round saves nothing new.

    Files already on disk are skipped when overwrite is `skip`. A song with
    no search match is listed once and is not retried.

    ### Arguments
    - downloader: Downloader to use.
    - songs: Songs to download.
    - max_rounds: Maximum passes, including the first.
    """

    pending = list(songs)
    for round_number in range(1, max_rounds + 1):
        done_before = len(downloader.downloaded)
        results = downloader.download_multiple_songs(pending)
        failed = _failed_songs(downloader, results)
        new_files = len(downloader.downloaded) - done_before
        if not failed:
            break
        if round_number > 1 and new_files == 0:
            logger.warning(
                "Resume stopped: round %d made no new files. %d song(s) still failed.",
                round_number,
                len(failed),
            )
            break
        if round_number == max_rounds:
            break
        logger.info(
            "Retrying %d failed song(s) (round %d of %d). "
            "Files already on disk are skipped.",
            len(failed),
            round_number + 1,
            max_rounds,
        )
        pending = failed

    log_download_summary(downloader)


def download(
    query: List[str],
    downloader: Downloader,
) -> None:
    """
    Find songs with the provided audio provider and save them to the disk.

    Re-running the same command resumes a playlist: the default overwrite mode
    skips files already on disk. The summary lists failed tracks separately
    from skipped ones. Songs with no search match are listed and are not a crash.

    ### Arguments
    - query: list of strings to search for.
    """

    prevent_sleep()
    downloader.settings["output"] = normalize_output(downloader.settings["output"])

    # Parse the query
    songs = get_simple_songs(
        query,
        use_ytm_data=downloader.settings["ytm_data"],
        playlist_numbering=downloader.settings["playlist_numbering"],
        albums_to_ignore=downloader.settings["ignore_albums"],
        album_type=downloader.settings["album_type"],
        playlist_retain_track_cover=downloader.settings["playlist_retain_track_cover"],
    )

    download_songs_with_resume(downloader, songs)
