"""Token de protection du feed source.json.

Quand l'option est activee, /source.json (et /qr.svg) exigent un parametre
?t=<token> qui est compare en temps constant au token stocke en BDD.

Le token est genere aleatoirement (256 caracteres alphanumeriques URL-safe :
A-Z a-z 0-9). C'est un secret long pour resister au brute-force et au
scrapping ; on n'utilise PAS un schema d'auth standard car SideStore ne
sait pas presenter de header custom -- seul un GET sur une URL avec query
string est utilisable cote client.

Cache memoire (analogue a seo.py) : la verification du token est sur le
chemin chaud (chaque fetch de source.json par SideStore). Lire la BDD a
chaque requete cassait le 503 handler quand la BDD est HS, donc on garde
le couple (enabled, token) en RAM, refresh au boot et a chaque write.
"""
from __future__ import annotations

import hmac
import secrets
import string
import threading

from sqlalchemy.orm import Session

from .source_gen import get_setting, set_setting

_ALPHABET = string.ascii_letters + string.digits  # 62 chars, URL-safe sans encoding
_TOKEN_LEN = 256

_KEY_ENABLED = "source_token_enabled"
_KEY_TOKEN = "source_token"

_lock = threading.Lock()
_enabled = False
_token = ""


def _generate_token() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_TOKEN_LEN))


def is_enabled() -> bool:
    with _lock:
        return _enabled


def get_token() -> str:
    with _lock:
        return _token


def refresh_from_db(db: Session) -> None:
    """A appeler au boot. Genere un token si absent (paresseux : pas avant
    activation) -- non, on le genere quand l'utilisateur active la protection
    pour eviter d'ecrire en BDD au moindre boot."""
    global _enabled, _token
    raw_enabled = get_setting(db, _KEY_ENABLED, "0")
    raw_token = get_setting(db, _KEY_TOKEN, "")
    with _lock:
        _enabled = raw_enabled == "1"
        _token = raw_token


def regenerate(db: Session) -> str:
    """Genere un nouveau token et le persiste. Retourne le token genere."""
    global _token
    new = _generate_token()
    set_setting(db, _KEY_TOKEN, new)
    with _lock:
        _token = new
    return new


def set_enabled(db: Session, enabled: bool) -> str:
    """Active/desactive la protection. A l'activation, genere le token s'il
    n'existe pas encore. Retourne le token courant (utile pour l'UI)."""
    global _enabled, _token
    set_setting(db, _KEY_ENABLED, "1" if enabled else "0")
    with _lock:
        _enabled = enabled
        current = _token
    if enabled and not current:
        return regenerate(db)
    return current


def check(provided: str | None) -> bool:
    """Verifie un token fourni en query string. Comparaison en temps constant
    pour eviter la fuite par timing. Si la protection est desactivee, retourne
    True (l'appelant ne devrait meme pas appeler check dans ce cas, mais c'est
    une defense en profondeur)."""
    with _lock:
        if not _enabled:
            return True
        expected = _token
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)
