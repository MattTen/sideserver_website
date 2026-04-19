"""Runtime configuration loaded from environment."""
from __future__ import annotations

import os
import secrets
from pathlib import Path


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"Variable d'environnement {name} requise. "
            f"Voir .env.example et /etc/ipastore/{{prod,dev}}.env."
        )
    return val


class Config:
    DB_URL = _required("IPASTORE_DB_URL")

    STORE_DIR = Path(os.environ.get("IPASTORE_STORE_DIR", "/srv/store"))
    IPAS_DIR = STORE_DIR / "ipas"
    ICONS_DIR = STORE_DIR / "icons"
    SCREENSHOTS_DIR = STORE_DIR / "screenshots"

    SECRET_KEY_FILE = Path(
        os.environ.get("IPASTORE_SECRET_FILE", "/etc/ipastore/secret_key")
    )

    DEFAULT_BASE_URL = os.environ.get("IPASTORE_BASE_URL", "http://192.168.0.202")

    SESSION_COOKIE = "ipastore_session"
    SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def load_secret_key() -> bytes:
    path = Config.SECRET_KEY_FILE
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(64)
    path.write_bytes(key)
    path.chmod(0o600)
    return key


def ensure_dirs() -> None:
    for d in (Config.IPAS_DIR, Config.ICONS_DIR, Config.SCREENSHOTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
