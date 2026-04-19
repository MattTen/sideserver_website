"""Gestion des catégories d'apps (stockées en Settings JSON)."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from .source_gen import get_setting, set_setting

_SETTING_KEY = "categories"
_DEFAULTS = ["Jeux", "Utilitaires", "Social", "Photo & Vidéo", "Musique", "Productivité", "Autre"]


def get_categories(db: Session) -> list[str]:
    raw = get_setting(db, _SETTING_KEY, "")
    if not raw:
        return _DEFAULTS.copy()
    try:
        cats = json.loads(raw)
        return cats if isinstance(cats, list) else _DEFAULTS.copy()
    except json.JSONDecodeError:
        return _DEFAULTS.copy()


def save_categories(db: Session, cats: list[str]) -> None:
    set_setting(db, _SETTING_KEY, json.dumps(cats))
