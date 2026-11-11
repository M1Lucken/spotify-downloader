"""
Start the bgutil HTTP PO-token server when a download needs it.

The server listens on 127.0.0.1:4416. spotdl reuses a server that is already
running. Otherwise it builds one with Node and leaves it running for later
downloads. Set SPOTDL_NO_POT_SERVER=1 to skip that. Set SPOTDL_POT_SERVER to
a checkout of bgutil-ytdlp-pot-provider (the repo or its server directory).
"""

import importlib.util
import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import List, Optional

from spotdl.utils.config import get_spotdl_path
from spotdl.utils.js_runtime import node_major_version

logger = logging.getLogger(__name__)

__all__ = [
    "POT_HOST",
    "POT_PORT",
    "POT_REF",
    "POT_REPO",
    "bgutil_http_installed",
    "pot_server_listening",
    "po_token_warning_message",
    "ensure_po_token_server",
]

POT_HOST = "127.0.0.1"
POT_PORT = 4416
POT_REF = "1.3.1"
POT_REPO = "https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git"


def bgutil_http_installed() -> bool:
    """
    Return True when the bgutil HTTP yt-dlp plugin is importable.

    ### Returns
    - True when the plugin module is on the same interpreter as spotdl.
    """

    return (
        importlib.util.find_spec("yt_dlp_plugins.extractor.getpot_bgutil_http")
        is not None
    )


def pot_server_listening(host: str = POT_HOST, port: int = POT_PORT) -> bool:
    """
    Return True when something accepts TCP connections on the PO-token port.

    ### Arguments
    - host: Interface to probe. Defaults to loopback.
    - port: TCP port. Defaults to 4416.

    ### Returns
    - True when a connection to the port succeeds.
    """

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def pot_log_path() -> Path:
    """
    Path of the PO-token server log.

    ### Returns
    - Log file under the spotdl config directory.
    """

    return get_spotdl_path() / "pot-server.log"


def po_token_warning_message() -> Optional[str]:
    """
    Explain that spotdl will start the PO-token server, when it is not up yet.

    ### Returns
    - Status text, or None when the server is already listening or auto-start is off.
    """

    if os.environ.get("SPOTDL_NO_POT_SERVER") == "1" or pot_server_listening():
        return None

    return (
        "The PO-token server is not running. spotdl will start it on "
        f"http://{POT_HOST}:{POT_PORT} when a download starts. The first run clones "
        f"bgutil-ytdlp-pot-provider {POT_REF}, then runs npm ci, npx tsc, and "
        "node build/main.js. Node.js >= 20 and git are required. "
        "Set SPOTDL_NO_POT_SERVER=1 to skip this, or SPOTDL_POT_SERVER to an "
        "existing checkout."
    )


def _normalize_server_dir(path: Path) -> Optional[Path]:
    """
    Return the server directory for a repo root, server directory, or main.js path.
    """

    if path.name == "main.js" and path.parent.name == "build":
        path = path.parent.parent

    if (path / "package.json").is_file() and (
        (path / "src").is_dir() or (path / "build").is_dir()
    ):
        return path

    server_dir = path / "server"
    if (server_dir / "package.json").is_file():
        return server_dir

    return None


def _candidate_server_dirs() -> List[Path]:
    """
    Places spotdl looks before cloning a new checkout.
    """

    candidates: List[Path] = []
    override = os.environ.get("SPOTDL_POT_SERVER")
    if override:
        candidates.append(Path(override).expanduser())

    candidates.append(get_spotdl_path() / "bgutil-ytdlp-pot-provider")
    cwd = Path.cwd()
    candidates.append(cwd / "bgutil-ytdlp-pot-provider")
    candidates.append(cwd / "local-setup" / "bgutil-ytdlp-pot-provider")
    return candidates


def prepare_pot_server_dir() -> Optional[Path]:
    """
    Find a bgutil server checkout, or clone tag 1.3.1 into the spotdl config folder.

    ### Returns
    - Server directory, or None when no checkout is available.
    """

    for candidate in _candidate_server_dirs():
        server_dir = _normalize_server_dir(candidate)
        if server_dir is not None:
            logger.info("Using PO-token server at %s", server_dir)
            return server_dir

    git = shutil.which("git")
    dest_root = get_spotdl_path() / "bgutil-ytdlp-pot-provider"
    server_dir = dest_root / "server"
    if git is None:
        logger.error(
            "git is not on PATH, so spotdl cannot download the PO-token server. "
            "Install git, or set SPOTDL_POT_SERVER to a checkout of %s.",
            POT_REPO,
        )
        return None

    if dest_root.exists():
        logger.error(
            "PO-token checkout at %s is incomplete. Remove that folder and run "
            "spotdl again, or set SPOTDL_POT_SERVER to a valid server directory.",
            dest_root,
        )
        return None

    logger.info("Cloning %s tag %s into %s", POT_REPO, POT_REF, dest_root)
    result = subprocess.run(
        [
            git,
            "clone",
            "--depth",
            "1",
            "--branch",
            POT_REF,
            POT_REPO,
            str(dest_root),
        ],
        check=False,
    )
    if result.returncode != 0 or not (server_dir / "package.json").is_file():
        logger.error("Could not clone the PO-token server from %s", POT_REPO)
        return None

    return server_dir


def build_pot_server(server_dir: Path) -> bool:
    """
    Install server dependencies and compile TypeScript when build/main.js is missing.

    ### Arguments
    - server_dir: bgutil server directory.

    ### Returns
    - True when build/main.js exists after the build.
    """

    if (server_dir / "build" / "main.js").is_file():
        return True

    npm = shutil.which("npm")
    npx = shutil.which("npx")
    if npm is None or npx is None:
        logger.error(
            "npm and npx are required to build the PO-token server in %s.", server_dir
        )
        return False

    logger.info(
        "Building the PO-token server in %s. The first run can take a few minutes.",
        server_dir,
    )
    if subprocess.run([npm, "ci"], cwd=server_dir, check=False).returncode != 0:
        logger.error("`npm ci` failed in %s", server_dir)
        return False
    if subprocess.run([npx, "tsc"], cwd=server_dir, check=False).returncode != 0:
        logger.error("`npx tsc` failed in %s", server_dir)
        return False

    if not (server_dir / "build" / "main.js").is_file():
        logger.error(
            "Build finished but %s is missing.", server_dir / "build" / "main.js"
        )
        return False

    return True


def spawn_pot_server(server_dir: Path) -> bool:
    """
    Launch `node build/main.js` in the background.

    ### Arguments
    - server_dir: Directory that contains build/main.js.

    ### Returns
    - True when the process was spawned.
    """

    node = shutil.which("node")
    if node is None:
        logger.error("node is not on PATH.")
        return False

    log_path = pot_log_path()
    logger.info("Starting PO-token server. Log: %s", log_path)
    log_file = open(log_path, "ab")  # pylint: disable=consider-using-with
    try:
        subprocess.Popen(  # pylint: disable=consider-using-with
            [node, "build/main.js"],
            cwd=server_dir,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        logger.error("Could not start the PO-token server: %s", exc)
        return False
    finally:
        log_file.close()

    return True


def wait_for_pot_server(timeout: float = 20.0) -> bool:
    """
    Wait until port 4416 accepts connections.

    ### Arguments
    - timeout: Seconds to wait.

    ### Returns
    - True when the port opens in time.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pot_server_listening():
            return True
        time.sleep(0.3)

    return False


def ensure_po_token_server() -> bool:
    """
    Make sure a PO-token HTTP server is listening before YouTube downloads start.

    ### Returns
    - False when spotdl tried to start the server and it did not come up.
      True when the server is already up, was started, or auto-start is disabled.
    """

    if os.environ.get("SPOTDL_NO_POT_SERVER") == "1":
        logger.info(
            "SPOTDL_NO_POT_SERVER=1, so spotdl will not start a PO-token server."
        )
        return True

    if pot_server_listening():
        logger.info("PO-token server already listening on %s:%s", POT_HOST, POT_PORT)
        return True

    if not bgutil_http_installed():
        logger.warning(
            "bgutil-ytdlp-pot-provider is not installed for this Python. "
            "The server can still be started, but yt-dlp will not request tokens until "
            "you install it with the same interpreter: "
            "python -m pip install 'bgutil-ytdlp-pot-provider==%s'",
            POT_REF,
        )

    major = node_major_version()
    if major is None or major < 20:
        logger.error(
            "Cannot start the PO-token server. Install Node.js >= 20 "
            "(https://nodejs.org/) and run the download again. "
            "spotdl also passes that Node to yt-dlp for signature solving."
        )
        return False

    server_dir = prepare_pot_server_dir()
    if server_dir is None:
        return False
    if not build_pot_server(server_dir):
        return False
    if not spawn_pot_server(server_dir):
        return False
    if wait_for_pot_server():
        logger.info("PO-token server is listening on http://%s:%s", POT_HOST, POT_PORT)
        return True

    logger.error(
        "PO-token server did not open %s:%s. See %s",
        POT_HOST,
        POT_PORT,
        pot_log_path(),
    )
    return False
