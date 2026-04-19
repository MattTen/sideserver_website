"""Settings: store metadata + admin password."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..auth import hash_password, require_user, verify_password
from ..db import get_db
from ..models import User
from ..source_gen import get_setting, set_setting
from ..templates import templates

router = APIRouter()


@router.get("/settings")
def settings_page(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request, "settings.html",
        {
            "user": user,
            "store_name": get_setting(db, "store_name", "Magasin Perso"),
            "store_subtitle": get_setting(db, "store_subtitle", ""),
            "store_description": get_setting(db, "store_description", ""),
            "store_tint": get_setting(db, "store_tint", "c9a678"),
            "base_url": get_setting(db, "base_url", "http://192.168.0.202"),
            "msg": None,
            "err": None,
            "active": "settings",
        },
    )


@router.post("/settings")
def settings_save(
    request: Request,
    store_name: str = Form("Magasin Perso"),
    store_subtitle: str = Form(""),
    store_description: str = Form(""),
    store_tint: str = Form("c9a678"),
    base_url: str = Form("http://192.168.0.202"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    tint = re.sub(r"[^0-9a-fA-F]", "", store_tint)[:6].lower() or "c9a678"
    base_url = base_url.strip().rstrip("/") or "http://192.168.0.202"
    set_setting(db, "store_name", store_name.strip() or "Magasin Perso")
    set_setting(db, "store_subtitle", store_subtitle.strip())
    set_setting(db, "store_description", store_description.strip())
    set_setting(db, "store_tint", tint)
    set_setting(db, "base_url", base_url)
    return RedirectResponse("/settings", status_code=303)


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
        return templates.TemplateResponse(
            request, "settings.html",
            {
                "user": user,
                "store_name": get_setting(db, "store_name", "Magasin Perso"),
                "store_subtitle": get_setting(db, "store_subtitle", ""),
                "store_description": get_setting(db, "store_description", ""),
                "store_tint": get_setting(db, "store_tint", "c9a678"),
                "base_url": get_setting(db, "base_url", "http://192.168.0.202"),
                "msg": None,
                "err": err,
                "active": "settings",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    user.password_hash = hash_password(new_password)
    db.commit()
    return RedirectResponse("/settings", status_code=303)
