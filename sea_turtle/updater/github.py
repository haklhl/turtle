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


def install_update() -> bool:
    """Install the latest version via pip.

    Returns:
        True if update was successful.
    """
    try:
        logger.info("Installing update...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", PYPI_PACKAGE],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            logger.info("Update installed successfully.")
            return True
        else:
            logger.error(f"pip upgrade failed: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("Update timed out.")
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
