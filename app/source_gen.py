"""Build the public SideStore source.json feed from the DB."""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import select

from .config import Config
from .models import App, Setting


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


def build_source(db: Session) -> dict[str, Any]:
    base_url = get_setting(db, "base_url", Config.DEFAULT_BASE_URL).rstrip("/")
    store_name = get_setting(db, "store_name", "Magasin Perso")
    store_subtitle = get_setting(db, "store_subtitle", "")
    store_description = get_setting(db, "store_description", "")
    store_tint = get_setting(db, "store_tint", "c9a678")

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
            screenshots = json.loads(app.screenshot_urls or "[]")
        except json.JSONDecodeError:
            screenshots = []
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

    return {
        "name": store_name,
        "subtitle": store_subtitle,
        "description": store_description,
        "iconURL": (
            f"{base_url}/icons/_store.png"
            if (Config.ICONS_DIR / "_store.png").exists()
            else f"{base_url}{_DEFAULT_ICON_PATH}"
        ),
        "website": base_url + "/",
        "tintColor": store_tint,
        "featuredApps": featured,
        "apps": apps,
        "news": [],
    }
