"""
Start the bgutil HTTP PO-token server when a download needs it.

The server listens on 127.0.0.1:4416. spotdl reuses a server that is already
running. Otherwise it builds one with Node. A server spotdl started closes
itself a few minutes after the last spotdl process exits. A server the user
started (Docker, for example) is left alone. Set SPOTDL_NO_POT_SERVER=1 to
skip auto-start. Set SPOTDL_POT_SERVER to a checkout of
bgutil-ytdlp-pot-provider (the repo or its server directory).
"""

import atexit
import importlib.util
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from spotdl.utils.config import get_spotdl_path
from spotdl.utils.js_runtime import node_major_version

logger = logging.getLogger(__name__)

__all__ = [
    "POT_HOST",
    "POT_PORT",
    "POT_REF",
    "POT_REPO",
    "POT_IDLE_SECONDS",
    "bgutil_http_installed",
    "pot_server_listening",
    "po_token_warning_message",
    "ensure_po_token_server",
    "helper_status",
    "read_owned_pid",
    "register_po_token_client",
    "should_stop_idle_server",
    "stop_po_token_server",
]

POT_HOST = "127.0.0.1"
POT_PORT = 4416
POT_REF = "1.3.1"
POT_REPO = "https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git"
POT_IDLE_SECONDS = 300

_WATCHDOG = """
import os
import signal
import sys
import time
from pathlib import Path

server_pid = int(sys.argv[1])
pid_path = Path(sys.argv[2])
clients = Path(sys.argv[3])
watchdog_path = Path(sys.argv[4])
idle = float(sys.argv[5])
last_busy = time.time()


def alive(pid):
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def living_clients():
    count = 0
    if not clients.is_dir():
        return 0
    for marker in list(clients.iterdir()):
        try:
            client_pid = int(marker.name)
        except ValueError:
            marker.unlink(missing_ok=True)
            continue
        if alive(client_pid):
            count += 1
        else:
            marker.unlink(missing_ok=True)
    return count


while alive(server_pid):
    time.sleep(5)
    if living_clients():
        last_busy = time.time()
        continue
    if time.time() - last_busy >= idle:
        try:
            os.kill(server_pid, signal.SIGTERM)
        except OSError:
            pass
        break

pid_path.unlink(missing_ok=True)
watchdog_path.unlink(missing_ok=True)
"""

_client_registered = False


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


def pot_pid_path() -> Path:
    """
    Pid file for the PO-token server spotdl started.

    ### Returns
    - Path under the spotdl config directory.
    """

    return get_spotdl_path() / "pot-server.pid"


def pot_clients_dir() -> Path:
    """
    Directory of pid markers for spotdl processes using the server.

    ### Returns
    - Path under the spotdl config directory.
    """

    return get_spotdl_path() / "pot-clients"


def pot_watchdog_pid_path() -> Path:
    """
    Pid file for the idle-shutdown watcher.

    ### Returns
    - Path under the spotdl config directory.
    """

    return get_spotdl_path() / "pot-server.watchdog.pid"


def pid_alive(pid: int) -> bool:
    """
    Return True when a process id is running.

    ### Arguments
    - pid: Process id.

    ### Returns
    - True when the process accepts signal 0.
    """

    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_pid_file(path: Path) -> Optional[int]:
    """
    Read a pid file and drop it when the process is gone.
    """

    if not path.is_file():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        path.unlink(missing_ok=True)
        return None
    if not pid_alive(pid):
        path.unlink(missing_ok=True)
        return None
    return pid


def read_owned_pid() -> Optional[int]:
    """
    Return the pid of the PO-token server spotdl started, when it is still running.

    ### Returns
    - Process id, or None when spotdl did not start the current server.
    """

    return _read_pid_file(pot_pid_path())


def should_stop_idle_server(
    living_clients: int, idle_for: float, idle_limit: float = POT_IDLE_SECONDS
) -> bool:
    """
    Decide whether an idle PO-token server should be stopped.

    ### Arguments
    - living_clients: spotdl processes still using the server.
    - idle_for: Seconds since the last client exited.
    - idle_limit: Seconds to wait after the last client.

    ### Returns
    - True when no client is alive and the idle limit has elapsed.
    """

    return living_clients == 0 and idle_for >= idle_limit


def register_po_token_client() -> None:
    """
    Remember this process so the idle watcher keeps the server up.
    """

    global _client_registered  # pylint: disable=global-statement

    directory = pot_clients_dir()
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / str(os.getpid())
    marker.write_text("1", encoding="utf-8")
    if _client_registered:
        return
    _client_registered = True

    def _cleanup() -> None:
        marker.unlink(missing_ok=True)

    atexit.register(_cleanup)


def stop_po_token_server() -> bool:
    """
    Stop the PO-token server spotdl started.

    ### Returns
    - True when a process spotdl started was asked to exit.
      A server spotdl did not start is left running, and this returns False.
    """

    pid = read_owned_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    pot_pid_path().unlink(missing_ok=True)
    return True


def helper_status() -> Dict[str, object]:
    """
    Status of the background PO-token server for the easy page.

    ### Returns
    - Whether something is listening, whether spotdl started it, and the idle timeout.
    """

    return {
        "helper_running": pot_server_listening(),
        "helper_owned": read_owned_pid() is not None,
        "helper_idle_seconds": POT_IDLE_SECONDS,
    }


def start_idle_watchdog(server_pid: int) -> None:
    """
    Start a detached watcher that stops the server after it has been idle.

    ### Arguments
    - server_pid: Pid of the Node server spotdl started.
    """

    if _read_pid_file(pot_watchdog_pid_path()) is not None:
        return

    config_dir = get_spotdl_path()
    config_dir.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(  # pylint: disable=consider-using-with
        [
            sys.executable,
            "-c",
            _WATCHDOG,
            str(server_pid),
            str(pot_pid_path()),
            str(pot_clients_dir()),
            str(pot_watchdog_pid_path()),
            str(POT_IDLE_SECONDS),
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pot_watchdog_pid_path().write_text(str(process.pid), encoding="utf-8")


def po_token_warning_message() -> Optional[str]:
    """
    Explain that spotdl will start the PO-token server, when it is not up yet.

    ### Returns
    - Status text, or None when the server is already listening or auto-start is off.
    """

    if os.environ.get("SPOTDL_NO_POT_SERVER") == "1" or pot_server_listening():
        return None

    minutes = POT_IDLE_SECONDS // 60
    return (
        "The PO-token server is not running. spotdl will start it on "
        f"http://{POT_HOST}:{POT_PORT} when a download starts. The first run clones "
        f"bgutil-ytdlp-pot-provider {POT_REF}, then runs npm ci, npx tsc, and "
        "node build/main.js. Node.js >= 20 and git are required. "
        f"The server closes itself {minutes} minutes after spotdl exits. "
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


def spawn_pot_server(server_dir: Path) -> Optional[int]:
    """
    Launch `node build/main.js` in the background.

    ### Arguments
    - server_dir: Directory that contains build/main.js.

    ### Returns
    - Pid of the Node process, or None when it could not be started.
    """

    node = shutil.which("node")
    if node is None:
        logger.error("node is not on PATH.")
        return None

    log_path = pot_log_path()
    logger.info("Starting PO-token server. Log: %s", log_path)
    log_file = open(log_path, "ab")  # pylint: disable=consider-using-with
    try:
        process = subprocess.Popen(  # pylint: disable=consider-using-with
            [node, "build/main.js"],
            cwd=server_dir,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        logger.error("Could not start the PO-token server: %s", exc)
        return None
    finally:
        log_file.close()

    pid_path = pot_pid_path()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(process.pid), encoding="utf-8")
    return process.pid


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
        owned = read_owned_pid()
        if owned is not None:
            register_po_token_client()
            start_idle_watchdog(owned)
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
    server_pid = spawn_pot_server(server_dir)
    if server_pid is None:
        return False
    register_po_token_client()
    start_idle_watchdog(server_pid)
    if wait_for_pot_server():
        logger.info(
            "PO-token server is listening on http://%s:%s. "
            "It closes itself %s minutes after spotdl exits.",
            POT_HOST,
            POT_PORT,
            POT_IDLE_SECONDS // 60,
        )
        return True
    stop_po_token_server()

    logger.error(
        "PO-token server did not open %s:%s. See %s",
        POT_HOST,
        POT_PORT,
        pot_log_path(),
    )
    return False
