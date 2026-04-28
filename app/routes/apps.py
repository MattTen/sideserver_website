"""Apps: list, detail, upload, edit, delete."""
from __future__ import annotations

import json
import logging
import re
import secrets
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_user
from ..categories import get_categories
from ..config import Config
from ..db import _get_session_factory, get_db
from ..ipa import parse_ipa, sha256_of_file
from ..models import App, User, Version
from ..templates import templates

logger = logging.getLogger(__name__)

router = APIRouter()


_SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]+")

TINT_COLORS: list[tuple[str, str]] = [
    # Violets
    ("Lavande",     "a78bfa"),
    ("Violet",      "7c3aed"),
    ("Indigo",      "4f46e5"),
    ("Aubergine",   "6b21a8"),
    # Bleus
    ("Ciel",        "38bdf8"),
    ("Bleu",        "2563eb"),
    ("Bleu foncé",  "1d4ed8"),
    ("Marine",      "1e3a5f"),
    # Teals / Cyans
    ("Cyan",        "06b6d4"),
    ("Teal",        "0d9488"),
    # Verts
    ("Vert",        "16a34a"),
    ("Émeraude",    "059669"),
    ("Olive",       "65a30d"),
    # Jaunes / Ambrés
    ("Jaune",       "eab308"),
    ("Ambre",       "f59e0b"),
    ("Or",          "c9a678"),
    # Oranges / Rouges
    ("Orange",      "f97316"),
    ("Corail",      "f87171"),
    ("Rouge",       "ef4444"),
    ("Cramoisi",    "b91c1c"),
    # Roses
    ("Rose vif",    "ec4899"),
    ("Rose",        "db2777"),
    ("Bordeaux",    "9f1239"),
    # Neutres
    ("Gris",        "6b7280"),
    ("Ardoise",     "475569"),
]
_TINT_PRESET_VALUES = {h for _, h in TINT_COLORS}

_ALLOWED_SCREENSHOT_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
}


def _safe_filename(bundle_id: str, version: str, build: str) -> str:
    base = _SAFE_NAME.sub("-", f"{bundle_id}-{version}-{build}")
    return f"{base}.ipa"


def _stream_upload_to_tmp(upload: UploadFile) -> Path:
    """Stream l'upload vers un fichier temporaire dans STORE_DIR.

    Le fichier temporaire est créé dans STORE_DIR (même filesystem que la
    destination finale) pour permettre un rename atomique via Path.replace().
    Un rename cross-filesystem serait une copie non-atomique, risquant des IPA
    partiellement écrits en cas d'erreur.
    """
    tmp = tempfile.NamedTemporaryFile(
        dir=Config.STORE_DIR, prefix=".upload-", suffix=".ipa", delete=False
    )
    try:
        shutil.copyfileobj(upload.file, tmp, length=8 * 1024 * 1024)
    finally:
        tmp.close()
    return Path(tmp.name)


def _save_icon(icon_bytes: bytes, bundle_id: str) -> str:
    """Save icon PNG and return the basename."""
    safe = _SAFE_NAME.sub("-", bundle_id)
    filename = f"{safe}.png"
    (Config.ICONS_DIR / filename).write_bytes(icon_bytes)
    return filename


@router.get("/apps")
def apps_list(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(
            App,
            func.count(Version.id).label("n_versions"),
            func.coalesce(func.sum(Version.size), 0).label("total_size"),
            func.max(Version.uploaded_at).label("last_upload"),
        )
        .outerjoin(Version, Version.app_id == App.id)
        .group_by(App.id)
        .order_by(App.updated_at.desc())
    ).all()
    return templates.TemplateResponse(
        request, "apps.html",
        {"user": user, "rows": rows, "active": "apps"},
    )


@router.post("/apps/upload")
async def apps_upload(
    request: Request,
    ipa: UploadFile = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not ipa.filename or not ipa.filename.lower().endswith(".ipa"):
        raise HTTPException(status_code=400, detail="Fichier IPA requis")

    tmp_path = _stream_upload_to_tmp(ipa)
    try:
        info = parse_ipa(tmp_path)
        if info is None:
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="IPA invalide: Info.plist introuvable")

        app = db.query(App).filter_by(bundle_id=info.bundle_id).one_or_none()
        is_new_app = app is None
        if is_new_app:
            app = App(bundle_id=info.bundle_id, name=info.name)
            db.add(app)
            db.flush()

        existing = db.query(Version).filter_by(
            app_id=app.id, version=info.version, build_version=info.build_version,
        ).one_or_none()
        if existing is not None:
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=409,
                detail=f"Version {info.version} build {info.build_version} déjà présente",
            )

        final_name = _safe_filename(info.bundle_id, info.version, info.build_version)
        final_path = Config.IPAS_DIR / final_name
        tmp_path.replace(final_path)

        sha = sha256_of_file(final_path)
        size = final_path.stat().st_size

        # L'icône extraite de l'IPA est sauvegardée uniquement si l'app n'en a
        # pas déjà une — pour ne pas écraser une icône uploadée manuellement.
        if info.icon_bytes and not app.icon_path:
            app.icon_path = _save_icon(info.icon_bytes, info.bundle_id)

        version = Version(
            app_id=app.id,
            ipa_filename=final_name,
            version=info.version,
            build_version=info.build_version,
            size=size,
            sha256=sha,
            min_os_version=info.min_os_version,
        )
        db.add(version)
        db.commit()
    except HTTPException:
        raise
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return RedirectResponse(f"/apps/{app.bundle_id}", status_code=303)


# ── Upload depuis une URL directe (background + polling) ───────────────────
# Meme pattern que /scinsta/upload-url : le serveur fait le GET (evite la
# limite Cloudflare 100 Mo cote upload client) et l'UI poll la progression
# pendant le download. Etat en RAM, suffisant pour un seul worker uvicorn.

_apps_url_dl_lock = threading.Lock()
_apps_url_dl_state: dict = {
    "status": "idle",          # idle | downloading | processing | done | error
    "bytes_downloaded": 0,
    "bytes_total": 0,
    "error": None,
    "started_at": None,
    "completed_at": None,
    "redirect_url": None,       # /apps/{bundle_id} a la fin pour que l'UI redirige
}


def _get_apps_url_dl_state() -> dict:
    with _apps_url_dl_lock:
        return dict(_apps_url_dl_state)


def _set_apps_url_dl_state(**kwargs) -> None:
    with _apps_url_dl_lock:
        _apps_url_dl_state.update(kwargs)


def _stream_url_to_path(url: str, target: Path) -> None:
    """Telecharge `url` vers `target` en mettant a jour la progression.

    curl_cffi prioritaire (TLS impersonation Chrome -- certains CDN refusent
    les UA Python par defaut), fallback urllib si dep absente.
    """
    chunk_size = 1 * 1024 * 1024
    downloaded = 0
    try:
        from curl_cffi import requests as cffi_requests  # type: ignore
        resp = cffi_requests.get(url, impersonate="chrome", timeout=600, stream=True)
        try:
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            total = int(resp.headers.get("content-length") or 0)
            _set_apps_url_dl_state(bytes_total=total)
            with target.open("wb") as f:
                for chunk in resp.iter_content(chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        _set_apps_url_dl_state(bytes_downloaded=downloaded)
        finally:
            resp.close()
    except ImportError:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )},
        )
        with urllib.request.urlopen(req, timeout=600) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            total = int(resp.headers.get("content-length") or 0)
            _set_apps_url_dl_state(bytes_total=total)
            with target.open("wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    _set_apps_url_dl_state(bytes_downloaded=downloaded)


def _process_downloaded_ipa(tmp_path: Path) -> str:
    """Parse l'IPA, cree App + Version en BDD, deplace vers IPAS_DIR.

    Le thread n'a pas de session FastAPI (dependances request-scoped) -- on
    ouvre la session via la factory directement.
    Retourne le bundle_id pour la redirection UI.
    """
    db = _get_session_factory()()
    try:
        info = parse_ipa(tmp_path)
        if info is None:
            raise RuntimeError("IPA invalide : Info.plist introuvable")

        app = db.query(App).filter_by(bundle_id=info.bundle_id).one_or_none()
        if app is None:
            app = App(bundle_id=info.bundle_id, name=info.name)
            db.add(app)
            db.flush()

        existing = db.query(Version).filter_by(
            app_id=app.id, version=info.version, build_version=info.build_version,
        ).one_or_none()
        if existing is not None:
            raise RuntimeError(
                f"Version {info.version} build {info.build_version} déjà présente"
            )

        final_name = _safe_filename(info.bundle_id, info.version, info.build_version)
        final_path = Config.IPAS_DIR / final_name
        tmp_path.replace(final_path)

        sha = sha256_of_file(final_path)
        size = final_path.stat().st_size

        if info.icon_bytes and not app.icon_path:
            app.icon_path = _save_icon(info.icon_bytes, info.bundle_id)

        version = Version(
            app_id=app.id,
            ipa_filename=final_name,
            version=info.version,
            build_version=info.build_version,
            size=size,
            sha256=sha,
            min_os_version=info.min_os_version,
        )
        db.add(version)
        db.commit()
        return info.bundle_id
    finally:
        db.close()


def _run_apps_url_download_async(url: str) -> None:
    """Lance le download + integration BDD dans un thread dedie."""
    def _runner() -> None:
        _set_apps_url_dl_state(
            status="downloading",
            bytes_downloaded=0,
            bytes_total=0,
            error=None,
            redirect_url=None,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            completed_at=None,
        )
        # Tempfile dans STORE_DIR pour rester sur le meme filesystem que
        # IPAS_DIR (rename atomique sans cross-device fallback).
        tmp_handle = tempfile.NamedTemporaryFile(
            dir=Config.STORE_DIR, prefix=".upload-url-", suffix=".ipa", delete=False,
        )
        tmp_handle.close()
        tmp_path = Path(tmp_handle.name)
        try:
            _stream_url_to_path(url, tmp_path)
            _set_apps_url_dl_state(status="processing")
            bundle_id = _process_downloaded_ipa(tmp_path)
            _set_apps_url_dl_state(
                status="done",
                redirect_url=f"/apps/{bundle_id}",
                completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("apps URL download failed: %s", e)
            tmp_path.unlink(missing_ok=True)
            _set_apps_url_dl_state(
                status="error",
                error=str(e),
                completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )

    threading.Thread(target=_runner, daemon=True).start()


@router.post("/apps/upload-url")
def apps_upload_url(
    url: str = Form(...),
    user: User = Depends(require_user),
):
    """Lance le telechargement d'un IPA depuis une URL en background.

    Retourne 202 immediatement -- l'UI poll /apps/upload-url-progress pour
    afficher l'avancement et recuperer redirect_url a la fin."""
    url = url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="URL http(s) requise")
    state = _get_apps_url_dl_state()
    if state["status"] in ("downloading", "processing"):
        raise HTTPException(status_code=409, detail="Un téléchargement est déjà en cours")
    _run_apps_url_download_async(url)
    return JSONResponse({"ok": True}, status_code=202)


@router.get("/apps/upload-url-progress")
def apps_upload_url_progress(user: User = Depends(require_user)):
    """Etat courant du telechargement URL (poll par l'UI toutes les ~1s)."""
    return JSONResponse(_get_apps_url_dl_state())


@router.get("/apps/{bundle_id}")
def app_detail(
    bundle_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    app = db.query(App).filter_by(bundle_id=bundle_id).one_or_none()
    if app is None:
        raise HTTPException(status_code=404, detail="App inconnue")
    try:
        screenshots = json.loads(app.screenshot_urls or "[]")
    except json.JSONDecodeError:
        screenshots = []
    return templates.TemplateResponse(
        request, "app_detail.html",
        {
            "user": user,
            "app": app,
            "versions": list(app.versions),
            "screenshots": screenshots,
            "tint_colors": TINT_COLORS,
            "tint_preset_values": _TINT_PRESET_VALUES,
            "categories": get_categories(db),
            "active": "apps",
        },
    )


@router.post("/apps/{bundle_id}/edit")
def app_edit(
    bundle_id: str,
    name: str = Form(...),
    developer_name: str = Form(""),
    subtitle: str = Form(""),
    description: str = Form(""),
    tint_color: str = Form("c9a678"),
    category: str = Form("other"),
    featured: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    app = db.query(App).filter_by(bundle_id=bundle_id).one_or_none()
    if app is None:
        raise HTTPException(status_code=404)
    app.name = name.strip() or app.name
    app.developer_name = developer_name.strip() or "Self"
    app.subtitle = subtitle.strip()
    app.description = description.strip()
    app.tint_color = re.sub(r"[^0-9a-fA-F]", "", tint_color)[:6].lower() or "c9a678"
    app.category = category.strip() or "other"
    app.featured = 1 if featured else 0
    db.commit()
    return RedirectResponse(f"/apps/{bundle_id}", status_code=303)


@router.post("/apps/{bundle_id}/icon")
async def app_icon(
    bundle_id: str,
    icon: UploadFile = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    app = db.query(App).filter_by(bundle_id=bundle_id).one_or_none()
    if app is None:
        raise HTTPException(status_code=404)
    data = await icon.read()
    if not data:
        raise HTTPException(status_code=400, detail="Icône vide")
    app.icon_path = _save_icon(data, bundle_id)
    db.commit()
    return RedirectResponse(f"/apps/{bundle_id}", status_code=303)


@router.post("/apps/{bundle_id}/versions/{version_id}/delete")
def version_delete(
    bundle_id: str,
    version_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    version = db.get(Version, version_id)
    if version is None:
        raise HTTPException(status_code=404)
    app = db.get(App, version.app_id)
    if not app or app.bundle_id != bundle_id:
        raise HTTPException(status_code=404)
    (Config.IPAS_DIR / version.ipa_filename).unlink(missing_ok=True)
    db.delete(version)
    db.commit()
    remaining = db.scalar(select(func.count(Version.id)).where(Version.app_id == app.id)) or 0
    if remaining == 0:
        db.delete(app)
        db.commit()
        return RedirectResponse("/apps", status_code=303)
    return RedirectResponse(f"/apps/{bundle_id}", status_code=303)


@router.post("/apps/{bundle_id}/versions/{version_id}/changelog")
def version_changelog(
    bundle_id: str,
    version_id: int,
    changelog: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    version = db.get(Version, version_id)
    if version is None:
        raise HTTPException(status_code=404)
    version.changelog = changelog.strip()
    db.commit()
    return RedirectResponse(f"/apps/{bundle_id}", status_code=303)


@router.post("/apps/{bundle_id}/delete")
def app_delete(
    bundle_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    app = db.query(App).filter_by(bundle_id=bundle_id).one_or_none()
    if app is None:
        raise HTTPException(status_code=404)
    for v in list(app.versions):
        (Config.IPAS_DIR / v.ipa_filename).unlink(missing_ok=True)
    if app.icon_path:
        (Config.ICONS_DIR / app.icon_path).unlink(missing_ok=True)
    db.delete(app)
    db.commit()
    return RedirectResponse("/apps", status_code=303)


@router.post("/apps/{bundle_id}/screenshots/upload")
async def app_screenshot_upload(
    bundle_id: str,
    screenshot: list[UploadFile] = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    app = db.query(App).filter_by(bundle_id=bundle_id).one_or_none()
    if app is None:
        raise HTTPException(status_code=404)
    try:
        existing: list[str] = json.loads(app.screenshot_urls or "[]")
    except json.JSONDecodeError:
        existing = []
    for f in screenshot:
        data = await f.read()
        if not data:
            continue
        ext = _ALLOWED_SCREENSHOT_EXT.get((f.content_type or "").lower())
        if ext is None:
            fname = (f.filename or "").lower()
            for suf, e in [(".png", "png"), (".jpg", "jpg"), (".jpeg", "jpg"), (".webp", "webp")]:
                if fname.endswith(suf):
                    ext = e
                    break
        if ext is None:
            continue
        safe = _SAFE_NAME.sub("-", bundle_id)
        filename = f"{safe}-{secrets.token_hex(4)}.{ext}"
        (Config.SCREENSHOTS_DIR / filename).write_bytes(data)
        existing.append(filename)
    app.screenshot_urls = json.dumps(existing)
    db.commit()
    return RedirectResponse(f"/apps/{bundle_id}", status_code=303)


@router.post("/apps/{bundle_id}/screenshots/{idx}/delete")
def app_screenshot_delete(
    bundle_id: str,
    idx: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    app = db.query(App).filter_by(bundle_id=bundle_id).one_or_none()
    if app is None:
        raise HTTPException(status_code=404)
    try:
        shots: list[str] = json.loads(app.screenshot_urls or "[]")
    except json.JSONDecodeError:
        shots = []
    if 0 <= idx < len(shots):
        url = shots[idx]
        if not url.startswith(("http://", "https://")):
            (Config.SCREENSHOTS_DIR / url).unlink(missing_ok=True)
        shots.pop(idx)
    app.screenshot_urls = json.dumps(shots)
    db.commit()
    return RedirectResponse(f"/apps/{bundle_id}", status_code=303)
