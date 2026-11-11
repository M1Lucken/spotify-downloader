"""
Module for functions related to downloading songs.
"""

import logging
from typing import List

from spotdl.providers.audio import YouTubeMusic

logger = logging.getLogger(__name__)

__all__ = ["check_ytmusic_connection", "resolve_audio_providers"]


def check_ytmusic_connection() -> bool:
    """
    Check if we can connect to YouTube Music API

    ### Returns
    - `True` if we can connect to YouTube Music API
    - `False` if we can't connect to YouTube Music API
    """

    # Check if we are getting results from YouTube Music
    ytm = YouTubeMusic()
    test_results = ytm.get_results("a", log_search_failures=False)
    if len(test_results) == 0:
        return False

    return True


def resolve_audio_providers(audio_providers: List[str]) -> List[str]:
    """
    Keep going when the YouTube Music preflight search is empty.

    A failed check used to abort the process. The search often returns nothing
    on networks where `--audio youtube` still works, so this warns and moves
    `youtube-music` behind the next provider (or switches to `youtube`).

    ### Arguments
    - audio_providers: Configured audio provider names, in preference order.

    ### Returns
    - Provider names to use for this run.
    """

    providers = list(audio_providers)
    if "youtube-music" not in providers:
        return providers

    if check_ytmusic_connection():
        return providers

    logger.warning(
        "YouTube Music preflight returned no results. "
        "This check is often a false block, so the download will continue. "
        "If songs fail, pass --audio youtube or use a VPN."
    )

    others = [provider for provider in providers if provider != "youtube-music"]
    if not others:
        logger.warning("Using audio provider youtube instead of youtube-music.")
        return ["youtube"]

    logger.warning("Trying %s before youtube-music.", ", ".join(others))
    return others + ["youtube-music"]
