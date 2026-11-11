"""
Reject the historical shared Spotify client id.
"""

from typing import Optional

from spotdl.utils.config import get_cache_path, get_config_file
from spotdl.utils.spotify import SpotifyError

__all__ = [
    "SHARED_SPOTIFY_CLIENT_ID",
    "spotify_credentials_problem",
    "ensure_personal_spotify_app",
]

SHARED_SPOTIFY_CLIENT_ID = "5f573c9620494bae87890c0f08a60293"


def spotify_credentials_problem(client_id: Optional[str]) -> Optional[str]:
    """
    Explain how to replace the shared Spotify app, when that app is still configured.

    ### Arguments
    - client_id: Resolved Spotify client id.

    ### Returns
    - Instructions, or None when the client id is the user's own app.
    """

    if client_id and client_id != SHARED_SPOTIFY_CLIENT_ID:
        return None

    config_path = get_config_file()
    cache_path = get_cache_path()
    return (
        "Spotify rate-limits the old shared client id immediately "
        "(Retry will occur after: 86400). Create your own app at "
        "https://developer.spotify.com/dashboard (Web API) and set the redirect URI to "
        "http://127.0.0.1:9900/. Run `spotdl --generate-config`, then set client_id and "
        f"client_secret in {config_path}. Never commit those values. After changing "
        f"credentials, delete {cache_path} or the old token is reused. If a playlist "
        "returns 403 or 429, run again with --user-auth. See docs/local-setup.md."
    )


def ensure_personal_spotify_app(client_id: Optional[str]) -> None:
    """
    Stop startup when config still uses the shared Spotify client id.

    ### Arguments
    - client_id: Resolved Spotify client id.

    ### Errors
    - SpotifyError: The shared or missing client id cannot be used.
    """

    message = spotify_credentials_problem(client_id)
    if message:
        raise SpotifyError(message)
