"""Cache mémoire du flag SEO "désactiver l'indexation".

Le middleware HTTP ajoute `X-Robots-Tag: noindex, nofollow` sur chaque
réponse quand le flag est actif, et /robots.txt retourne `Disallow: /`.
Lire la BDD sur chaque requête (static files compris) serait trop coûteux,
donc on mantient un bool en RAM, rafraîchi au boot et à chaque toggle.
"""
from __future__ import annotations

import threading

from sqlalchemy.orm import Session

from .source_gen import get_setting, set_setting

_lock = threading.Lock()
_disabled = False


def is_indexing_disabled() -> bool:
    with _lock:
        return _disabled


def refresh_from_db(db: Session) -> None:
    global _disabled
    val = get_setting(db, "disable_indexing", "0")
    with _lock:
        _disabled = val == "1"


def set_indexing_disabled(db: Session, disabled: bool) -> None:
    set_setting(db, "disable_indexing", "1" if disabled else "0")
    global _disabled
    with _lock:
        _disabled = disabled
