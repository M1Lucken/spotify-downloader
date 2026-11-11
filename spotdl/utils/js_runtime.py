"""
Pick a JavaScript runtime for yt-dlp signature solving.
"""

import logging
import os
import shutil
import subprocess
from typing import Dict, Optional

from spotdl.utils.deno import get_deno_path, get_local_deno

logger = logging.getLogger(__name__)

__all__ = [
    "MISSING_JS_RUNTIME_HINT",
    "user_specified_js_runtime",
    "node_major_version",
    "js_runtime_options",
    "describe_js_runtime",
]

MISSING_JS_RUNTIME_HINT = (
    "YouTube signature solving needs a JavaScript runtime. "
    "Install Node.js >= 20 (https://nodejs.org/) or Deno "
    "(`spotdl --download-deno`) and make sure `node` or `deno` is on PATH. "
    "When Node is available, spotdl passes --js-runtimes node; otherwise deno."
)


def user_specified_js_runtime(yt_dlp_args: Optional[str]) -> bool:
    """
    Return True when yt-dlp args already choose a JavaScript runtime.

    ### Arguments
    - yt_dlp_args: Raw `--yt-dlp-args` string.

    ### Returns
    - True if the user passed `--js-runtimes` or `--no-js-runtimes`.
    """

    if not yt_dlp_args:
        return False

    tokens = yt_dlp_args.split()
    return any(
        token == "--js-runtimes"
        or token.startswith("--js-runtimes=")
        or token == "--no-js-runtimes"
        for token in tokens
    )


def node_major_version() -> Optional[int]:
    """
    Return the major version of `node` on PATH, when it is usable.

    ### Returns
    - Major version, or None when Node is missing or did not respond.
    """

    node = shutil.which("node")
    if node is None:
        return None

    try:
        completed = subprocess.run(
            [node, "-v"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    version = completed.stdout.strip().lstrip("vV")
    major = version.split(".", 1)[0]
    if not major.isdigit():
        return None

    return int(major)


def _deno_runtime_options() -> Dict[str, Dict[str, Dict[str, str]]]:
    """
    yt-dlp options for an installed Deno binary.
    """

    deno_path = get_deno_path()
    if deno_path is None or not os.access(deno_path, os.X_OK):
        return {}

    local_deno = get_local_deno()
    if local_deno is not None and local_deno.resolve() == deno_path.resolve():
        return {"js_runtimes": {"deno": {"path": str(local_deno.resolve())}}}

    return {"js_runtimes": {"deno": {"path": str(deno_path.resolve())}}}


def js_runtime_options(
    yt_dlp_args: Optional[str],
) -> Dict[str, Dict[str, Dict[str, str]]]:
    """
    Default yt-dlp `js_runtimes` when the user did not set one.

    Prefers Node >= 20, then Deno.

    ### Arguments
    - yt_dlp_args: Raw `--yt-dlp-args` string.

    ### Returns
    - yt-dlp options, or an empty dict when the user already chose a runtime
      or no runtime is installed.
    """

    if user_specified_js_runtime(yt_dlp_args):
        return {}

    major = node_major_version()
    if major is not None and major >= 20:
        return {"js_runtimes": {"node": {}}}

    if major is not None:
        logger.warning(
            "Node.js %s is on PATH. YouTube downloads need Node >= 20 or Deno.",
            major,
        )

    return _deno_runtime_options()


def describe_js_runtime(yt_dlp_args: Optional[str]) -> Optional[str]:
    """
    Short name of the runtime spotdl will pass to yt-dlp.

    ### Arguments
    - yt_dlp_args: Raw `--yt-dlp-args` string.

    ### Returns
    - `node`, `deno`, `custom`, or None.
    """

    if user_specified_js_runtime(yt_dlp_args):
        return "custom"

    options = js_runtime_options(yt_dlp_args)
    runtimes = options.get("js_runtimes", {})
    if "node" in runtimes:
        return "node"
    if "deno" in runtimes:
        return "deno"

    return None
