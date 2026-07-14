"""Runtime helpers for locating Node.js and the Artistoo bridge.

The wrapper drives the *real* Artistoo simulator, which is a JavaScript
library. It runs inside a persistent Node.js subprocess (see
``node_bridge/artistoo_bridge.js``). This module resolves the ``node``
executable and makes sure Artistoo is installed for the bridge, installing it
on first use when necessary so the package works from a plain
``pip install`` even though the JS dependency is not vendored into the wheel.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent / "node_bridge"
BRIDGE_SCRIPT = BRIDGE_DIR / "artistoo_bridge.js"

# Fallback install location when the packaged bridge dir is read-only
# (e.g. installed into a system site-packages).
CACHE_DIR = Path(
    os.environ.get("PBG_ARTISTOO_CACHE", Path.home() / ".cache" / "pbg-artistoo")
)


class NodeUnavailableError(RuntimeError):
    """Raised when a working Node.js / Artistoo install cannot be found."""


def find_node(explicit: str = "") -> str:
    """Return a path to the ``node`` executable or raise."""
    candidate = explicit or os.environ.get("PBG_ARTISTOO_NODE") or "node"
    resolved = shutil.which(candidate)
    if resolved is None:
        raise NodeUnavailableError(
            "Node.js is required to run Artistoo but `node` was not found on "
            "PATH. Install Node.js (https://nodejs.org) or set PBG_ARTISTOO_NODE."
        )
    return resolved


def _artistoo_bundle(mod_dir: Path) -> Path:
    return mod_dir / "node_modules" / "Artistoo" / "build" / "artistoo-cjs.js"


def _npm_install(target_dir: Path) -> None:
    npm = shutil.which("npm")
    if npm is None:
        raise NodeUnavailableError(
            "Artistoo is not installed for the bridge and `npm` was not found "
            "on PATH to install it. Install Node.js/npm, then run "
            f"`npm install` in {target_dir}."
        )
    target_dir.mkdir(parents=True, exist_ok=True)
    # Ensure a package.json referencing Artistoo exists in the target.
    pkg = target_dir / "package.json"
    if not pkg.exists():
        pkg.write_text(
            '{\n  "name": "pbg-artistoo-bridge-cache",\n  "private": true,\n'
            '  "dependencies": { "Artistoo": "github:ingewortel/artistoo" }\n}\n'
        )
    subprocess.run(
        [npm, "install", "--no-audit", "--no-fund"],
        cwd=str(target_dir),
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )


def ensure_artistoo() -> dict:
    """Ensure Artistoo is available to the bridge.

    Returns a dict with keys ``node`` (executable), ``script`` (bridge JS path),
    and ``env`` (environment overrides, e.g. ``ARTISTOO_CJS``). Installs
    Artistoo on first use if it is missing and ``npm`` is available.
    """
    node = find_node()
    env = {}

    # Preferred: Artistoo already installed next to the bridge script.
    if _artistoo_bundle(BRIDGE_DIR).exists():
        return {"node": node, "script": str(BRIDGE_SCRIPT), "env": env}

    # Try installing into the packaged bridge dir (works for editable installs).
    try:
        if os.access(BRIDGE_DIR, os.W_OK):
            _npm_install(BRIDGE_DIR)
            if _artistoo_bundle(BRIDGE_DIR).exists():
                return {"node": node, "script": str(BRIDGE_SCRIPT), "env": env}
    except (OSError, subprocess.SubprocessError):
        pass  # fall through to cache dir

    # Fallback: install into a writable cache dir and point the bridge at it.
    if not _artistoo_bundle(CACHE_DIR).exists():
        _npm_install(CACHE_DIR)
    env["ARTISTOO_CJS"] = str(_artistoo_bundle(CACHE_DIR))
    return {"node": node, "script": str(BRIDGE_SCRIPT), "env": env}


def artistoo_available() -> bool:
    """Cheap check used by tests to skip when Node/Artistoo is absent."""
    if shutil.which(os.environ.get("PBG_ARTISTOO_NODE") or "node") is None:
        return False
    return _artistoo_bundle(BRIDGE_DIR).exists() or _artistoo_bundle(CACHE_DIR).exists()
