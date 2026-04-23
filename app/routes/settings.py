"""Settings: store metadata + admin password + apparence (icône/header)."""
from __future__ import annotations

import re
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..auth import hash_password, require_user, verify_password
from ..config import Config
from ..db import get_db
from ..models import User
from .apps import TINT_COLORS, _TINT_PRESET_VALUES
from ..seo import is_indexing_disabled, set_indexing_disabled
from ..source_gen import get_setting, set_setting
from ..templates import templates

router = APIRouter()


# SideStore accepte PNG et JPG pour iconURL/headerURL/imageURL. On ajoute WebP
# côté serveur car SideStore récent (iOS 15+) le supporte aussi via WKWebView.
_ALLOWED_IMG_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/svg+xml": "svg",
    "image/heic": "heic",
    "image/heif": "heif",
}
_ALLOWED_IMG_SUFFIXES = [
    (".png", "png"), (".jpg", "jpg"), (".jpeg", "jpg"),
    (".webp", "webp"), (".gif", "gif"), (".svg", "svg"),
    (".heic", "heic"), (".heif", "heif"),
]


def _settings_context(db: Session, user: User, msg: str | None = None, err: str | None = None):
    icon_file = get_setting(db, "store_icon_file", "")
    header_file = get_setting(db, "store_header_file", "")
    return {
        "user": user,
        "store_name": get_setting(db, "store_name", "Magasin Perso"),
        "store_subtitle": get_setting(db, "store_subtitle", ""),
        "store_tint": get_setting(db, "store_tint", "c9a678"),
        "store_icon_file": icon_file if icon_file and (Config.ICONS_DIR / icon_file).exists() else "",
        "store_header_file": header_file if header_file and (Config.ICONS_DIR / header_file).exists() else "",
        "tint_colors": TINT_COLORS,
        "tint_preset_values": _TINT_PRESET_VALUES,
        "indexing_disabled": is_indexing_disabled(),
        "msg": msg,
        "err": err,
        "active": "settings",
    }


@router.get("/settings")
def settings_page(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(request, "settings.html", _settings_context(db, user))


@router.post("/settings")
def settings_save(
    request: Request,
    store_name: str = Form("Magasin Perso"),
    store_subtitle: str = Form(""),
    store_tint: str = Form("c9a678"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    tint = re.sub(r"[^0-9a-fA-F]", "", store_tint)[:6].lower() or "c9a678"
    set_setting(db, "store_name", store_name.strip() or "Magasin Perso")
    set_setting(db, "store_subtitle", store_subtitle.strip())
    set_setting(db, "store_tint", tint)
    return Response(status_code=204)


async def _save_appearance_image(upload: UploadFile, prefix: str) -> str:
    """Persiste une image d'apparence dans ICONS_DIR et retourne son basename.

    Le nom final embarque un token aléatoire pour invalider le cache HTTP de
    SideStore à chaque nouvel upload : sans ça le client garderait l'ancienne
    image indéfiniment même si le fichier a été remplacé côté serveur.
    """
    ext = _ALLOWED_IMG_EXT.get((upload.content_type or "").lower())
    if ext is None:
        fname = (upload.filename or "").lower()
        for suffix, e in _ALLOWED_IMG_SUFFIXES:
            if fname.endswith(suffix):
                ext = e
                break
    if ext is None:
        raise HTTPException(status_code=400, detail="Format non supporté")
    await upload.seek(0)
    data = await upload.read()
    if not data:
        raise HTTPException(status_code=400, detail="Image vide")
    name = f"{prefix}-{secrets.token_hex(6)}.{ext}"
    (Config.ICONS_DIR / name).write_bytes(data)
    return name


def _drop_previous(db: Session, key: str) -> None:
    old = get_setting(db, key, "")
    if old:
        (Config.ICONS_DIR / old).unlink(missing_ok=True)


@router.post("/settings/icon")
async def settings_upload_icon(
    icon: UploadFile = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    new_name = await _save_appearance_image(icon, "store")
    _drop_previous(db, "store_icon_file")
    set_setting(db, "store_icon_file", new_name)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/icon/remove")
def settings_remove_icon(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _drop_previous(db, "store_icon_file")
    set_setting(db, "store_icon_file", "")
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/header")
async def settings_upload_header(
    header: UploadFile = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    new_name = await _save_appearance_image(header, "header")
    _drop_previous(db, "store_header_file")
    set_setting(db, "store_header_file", new_name)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/indexing")
def settings_indexing(
    disable_indexing: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    set_indexing_disabled(db, disable_indexing == "1")
    return Response(status_code=204)


@router.post("/settings/header/remove")
def settings_remove_header(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _drop_previous(db, "store_header_file")
    set_setting(db, "store_header_file", "")
    return RedirectResponse("/settings", status_code=303)


@router.get("/settings/logs")
def settings_logs(
    lines: int = Query(500, ge=1, le=5000),
    user: User = Depends(require_user),
):
    """Retourne les dernieres lignes de /etc/ipastore/app.log (file handler
    configure dans app.main._configure_logging)."""
    log_path = Path("/etc/ipastore/app.log")
    if not log_path.exists():
        return JSONResponse({"lines": [], "note": "Aucun log pour l'instant."})
    with log_path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        # 400 octets par ligne en moyenne : on lit large, puis on tronque.
        read_size = min(size, lines * 400)
        f.seek(size - read_size)
        chunk = f.read().decode("utf-8", errors="replace")
    tail = chunk.splitlines()[-lines:]
    return JSONResponse({"lines": tail})


@router.post("/settings/password")
def settings_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    err = None
    if not verify_password(current_password, user.password_hash):
        err = "Mot de passe actuel incorrect"
    elif len(new_password) < 8:
        err = "Nouveau mot de passe trop court"
    elif new_password != new_password_confirm:
        err = "Les mots de passe ne correspondent pas"

    if err:
        return JSONResponse({"error": err}, status_code=status.HTTP_400_BAD_REQUEST)
    user.password_hash = hash_password(new_password)
    db.commit()
    return Response(status_code=204)
