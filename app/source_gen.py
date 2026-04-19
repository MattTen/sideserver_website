"""Build the public SideStore source.json feed from the DB."""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import select

from .config import Config
from .models import App, News, Setting
from .news_bg import PRESETS as _NEWS_BG_PRESETS


_DEFAULT_ICON_PATH = "/static/default-app.png"


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row and row.value is not None else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def build_source(db: Session, base_url: str) -> dict[str, Any]:
    """base_url doit être dérivé de la requête HTTP par l'appelant
    (str(request.base_url).rstrip('/')) pour que les URLs d'icône, IPAs
    et screenshots soient joignables par SideStore depuis le même host
    et port que celui utilisé pour fetcher source.json."""
    base_url = base_url.rstrip("/")
    store_name = get_setting(db, "store_name", "Magasin Perso")
    store_subtitle = get_setting(db, "store_subtitle", "")
    store_tint = get_setting(db, "store_tint", "c9a678")
    store_icon_file = get_setting(db, "store_icon_file", "")
    store_header_file = get_setting(db, "store_header_file", "")

    apps: list[dict[str, Any]] = []
    featured: list[str] = []

    for app in db.execute(select(App).order_by(App.name)).scalars():
        versions_payload: list[dict[str, Any]] = []
        for v in app.versions:  # already ordered desc by uploaded_at
            versions_payload.append({
                "version": v.version,
                "buildVersion": v.build_version,
                "date": v.uploaded_at.replace(tzinfo=dt.UTC).isoformat(),
                "localizedDescription": v.changelog or app.description,
                "downloadURL": f"{base_url}/ipas/{v.ipa_filename}",
                "size": v.size,
                "sha256": v.sha256,
                "minOSVersion": v.min_os_version,
            })
        if not versions_payload:
            continue
        if app.icon_path and (Config.ICONS_DIR / app.icon_path).exists():
            icon_url = f"{base_url}/icons/{app.icon_path}"
        else:
            icon_url = f"{base_url}{_DEFAULT_ICON_PATH}"
        try:
            raw_shots = json.loads(app.screenshot_urls or "[]")
        except json.JSONDecodeError:
            raw_shots = []
        screenshots = [
            s if s.startswith(("http://", "https://")) else f"{base_url}/screenshots/{s}"
            for s in raw_shots
        ]
        apps.append({
            "name": app.name,
            "bundleIdentifier": app.bundle_id,
            "developerName": app.developer_name,
            "subtitle": app.subtitle,
            "localizedDescription": app.description,
            "iconURL": icon_url,
            "tintColor": app.tint_color,
            "category": app.category,
            "screenshotURLs": screenshots,
            "versions": versions_payload,
            "appPermissions": {"entitlements": [], "privacy": []},
        })
        if app.featured:
            featured.append(app.bundle_id)

    # Icône du store : fichier uploadé via UI (store_icon_file) en priorité,
    # _store.png en fallback pour compat avec une éventuelle pose manuelle,
    # puis default-app si vraiment rien.
    if store_icon_file and (Config.ICONS_DIR / store_icon_file).exists():
        store_icon_url = f"{base_url}/icons/{store_icon_file}"
    elif (Config.ICONS_DIR / "_store.png").exists():
        store_icon_url = f"{base_url}/icons/_store.png"
    else:
        store_icon_url = f"{base_url}{_DEFAULT_ICON_PATH}"

    news_payload: list[dict[str, Any]] = []
    for article in db.execute(select(News).order_by(News.date.desc())).scalars():
        preset = _NEWS_BG_PRESETS.get(article.bg_preset) if article.bg_preset else None
        entry: dict[str, Any] = {
            "title": article.title,
            "identifier": article.identifier,
            "caption": article.caption,
            "date": article.date.replace(tzinfo=dt.UTC).isoformat(),
            "tintColor": preset["tint"] if preset else store_tint,
            "notify": bool(article.notify),
        }
        # Priorité imageURL : image uploadée > PNG du preset > absent
        if article.image_path and (Config.NEWS_DIR / article.image_path).exists():
            entry["imageURL"] = f"{base_url}/news-img/{article.image_path}"
        elif preset:
            entry["imageURL"] = f"{base_url}/static/news-bg/{article.bg_preset}.png"
        if article.app_bundle_id:
            entry["appID"] = article.app_bundle_id
        news_payload.append(entry)

    if store_header_file and (Config.ICONS_DIR / store_header_file).exists():
        header_url = f"{base_url}/icons/{store_header_file}"
    else:
        header_url = f"{base_url}/static/store-header.png"

    payload: dict[str, Any] = {
        "name": store_name,
        "subtitle": store_subtitle,
        "iconURL": store_icon_url,
        "headerURL": header_url,
        "tintColor": store_tint,
        "featuredApps": featured,
        "apps": apps,
        "news": news_payload,
    }
    return payload
