"""Update mechanism: version tracking + GitHub release polling.

Flow:
- `get_status()` reads local version file + polls GitHub `/releases/latest`, returns dict.
- `request_update()` writes a flag file that a systemd path unit on the host watches;
  when the file appears, the host runs `website-management.sh prod-update` and removes it.
- A background task calls `get_status()` every 6h to refresh the cache and log the result.

L'UI est mono-env : elle ignore la branche reellement checkoutee et expose
toujours le meme flow (current vs latest release GitHub). La bascule
dev/main se fait via le script de management (pull-dev/pull-main).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import Config

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*")
_GIT_CRED_RE = re.compile(r"https://[^:]+:([^@]+)@github\.com")


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a dotted numeric version (optional leading 'v'). Non-numeric trailing parts ignored."""
    if not v:
        return ()
    s = v.strip().lstrip("vV")
    m = _VERSION_RE.match(s)
    if not m:
        return ()
    return tuple(int(x) for x in m.group(0).split("."))


def _read_github_token() -> Optional[str]:
    """Read the PAT from /etc/ipastore/.git-credentials. Needed for private repos."""
    try:
        raw = Path("/etc/ipastore/.git-credentials").read_text(encoding="utf-8")
    except OSError:
        return None
    m = _GIT_CRED_RE.search(raw)
    return m.group(1) if m else None


def version_gt(a: str, b: str) -> bool:
    """Return True if version a is strictly greater than version b."""
    if not a:
        return False
    if not b:
        return True
    return _parse_version(a) > _parse_version(b)


def read_current_version() -> Optional[str]:
    try:
        v = Config.VERSION_FILE.read_text(encoding="utf-8").strip()
        return v or None
    except FileNotFoundError:
        return None
    except OSError:
        logger.exception("Failed to read version file %s", Config.VERSION_FILE)
        return None


def fetch_latest_release() -> Optional[str]:
    """Call GitHub API and return the tag_name of the latest release, or None."""
    url = f"https://api.github.com/repos/{Config.GITHUB_REPO}/releases/latest"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ipastore-update-checker",
    }
    token = _read_github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name")
        return tag if isinstance(tag, str) and tag else None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # no release published
        logger.warning("GitHub API HTTP %s on %s", e.code, url)
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.warning("GitHub API error: %s", e)
        return None
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("GitHub API parse error: %s", e)
        return None


@dataclass
class UpdateStatus:
    current: Optional[str]
    latest: Optional[str]
    update_available: bool
    checked_at: float = field(default_factory=time.time)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "current": self.current,
            "latest": self.latest,
            "update_available": self.update_available,
            "checked_at": self.checked_at,
            "error": self.error,
        }


# In-process cache — last result, updated by foreground calls and by the background loop.
_cache_lock = threading.Lock()
_cache: Optional[UpdateStatus] = None


def _compute_status() -> UpdateStatus:
    current = read_current_version()
    latest = fetch_latest_release()
    if latest is None:
        return UpdateStatus(
            current=current,
            latest=None,
            update_available=False,
            error="no-release-or-api-error",
        )
    available = version_gt(latest, current or "")
    return UpdateStatus(
        current=current,
        latest=latest,
        update_available=available,
    )


def get_status(refresh: bool = True) -> UpdateStatus:
    """Return the update status. If refresh=True, always re-query GitHub."""
    global _cache
    if not refresh:
        with _cache_lock:
            if _cache is not None:
                return _cache
    status = _compute_status()
    with _cache_lock:
        _cache = status
    return status


def get_cached_status() -> Optional[UpdateStatus]:
    with _cache_lock:
        return _cache


def request_update() -> Path:
    """Write the flag file; the host systemd path unit picks it up."""
    Config.IPASTORE_ETC.mkdir(parents=True, exist_ok=True)
    flag = Config.UPDATE_FLAG_FILE
    flag.write_text(str(int(time.time())) + "\n", encoding="utf-8")
    return flag
