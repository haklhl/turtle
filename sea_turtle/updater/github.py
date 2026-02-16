"""GitHub-based auto-update module for Sea Turtle."""

import json
import logging
import subprocess
import sys
import urllib.request
import urllib.error
from typing import Any

from sea_turtle import __version__, __github__

logger = logging.getLogger("sea_turtle.updater")

GITHUB_API_URL = "https://api.github.com/repos/haklhl/turtle/releases/latest"
PYPI_PACKAGE = "sea-turtle"


def check_update() -> str | None:
    """Check GitHub for the latest release version.

    Returns:
        Latest version string (e.g., '0.2.0'), or None if check failed.
    """
    try:
        req = urllib.request.Request(
            GITHUB_API_URL,
            headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "sea-turtle-updater"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            tag = data.get("tag_name", "")
            # Strip leading 'v' if present
            version = tag.lstrip("v")
            return version if version else None
    except urllib.error.URLError as e:
        logger.warning(f"Failed to check for updates: {e}")
        return None
    except Exception as e:
        logger.warning(f"Update check error: {e}")
        return None


def compare_versions(current: str, latest: str) -> int:
    """Compare two version strings.

    Returns:
        -1 if current < latest, 0 if equal, 1 if current > latest.
    """
    def parse(v: str) -> tuple[int, ...]:
        parts = []
        for p in v.split("."):
            try:
                parts.append(int(p))
            except ValueError:
                parts.append(0)
        return tuple(parts)

    c = parse(current)
    l = parse(latest)

    if c < l:
        return -1
    elif c > l:
        return 1
    return 0


def _find_project_root() -> str | None:
    """Find the project root by looking for pyproject.toml or .git."""
    import pathlib
    # Start from the package directory and walk up
    current = pathlib.Path(__file__).resolve().parent
    for _ in range(10):
        if (current / ".git").exists() or (current / "pyproject.toml").exists():
            return str(current)
        current = current.parent
    return None


def install_update() -> bool:
    """Update by running git pull + pip install -e . in the project root.

    Returns:
        True if update was successful.
    """
    project_root = _find_project_root()
    if not project_root:
        logger.error("Cannot find project root (no .git or pyproject.toml found)")
        return False

    # Step 1: git pull
    try:
        logger.info(f"Running git pull in {project_root}...")
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            capture_output=True, text=True, timeout=60,
            cwd=project_root,
        )
        if result.returncode != 0:
            logger.error(f"git pull failed: {result.stderr}")
            return False
        logger.info(f"git pull: {result.stdout.strip()}")
    except FileNotFoundError:
        logger.error("git is not installed")
        return False
    except subprocess.TimeoutExpired:
        logger.error("git pull timed out")
        return False

    # Step 2: pip install -e .
    try:
        logger.info("Running pip install -e . ...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", "."],
            capture_output=True, text=True, timeout=120,
            cwd=project_root,
        )
        if result.returncode == 0:
            logger.info("Update installed successfully.")
            return True
        else:
            logger.error(f"pip install failed: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("pip install timed out")
        return False
    except Exception as e:
        logger.error(f"Update failed: {e}")
        return False


def get_update_info() -> dict[str, Any]:
    """Get update status information.

    Returns:
        Dict with current_version, latest_version, update_available.
    """
    latest = check_update()
    info = {
        "current_version": __version__,
        "latest_version": latest,
        "update_available": False,
    }
    if latest:
        info["update_available"] = compare_versions(__version__, latest) < 0
    return info
