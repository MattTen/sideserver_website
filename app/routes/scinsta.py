"""Routes SCInsta : check version, upload IPA, déclenchement du build."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..auth import require_user
from ..db import get_db
from ..models import User
from ..patches import discover_patches, get_patch
from ..scinsta import (
    clear_upload, get_state, read_build_log, request_build, run_check,
    set_decrypt_url, upload_instagram_ipa,
)
from ..templates import templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scinsta")


@router.get("")
def scinsta_page(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    state = get_state(db)
    patches = discover_patches(db)
    return templates.TemplateResponse(
        request,
        "scinsta.html",
        {
            "user": user,
            "active": "scinsta",
            "state": state.to_dict(),
            "patches": patches,
        },
    )


@router.get("/status")
def scinsta_status(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Etat actuel sans requete reseau. Polle par l'UI pendant un build."""
    return JSONResponse(get_state(db).to_dict())


@router.get("/logs")
def scinsta_logs(offset: int = 0, user: User = Depends(require_user)):
    """Retourne le delta du log de build depuis `offset`.

    L'UI passe `next_offset` du tick precedent pour recuperer uniquement
    les nouvelles lignes — evite de renvoyer plusieurs Mo a chaque poll.
    """
    if offset < 0:
        offset = 0
    return JSONResponse(read_build_log(offset))


@router.post("/check")
def scinsta_check(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Interroge decrypt.day pour la derniere version Instagram."""
    state = run_check(db)
    return JSONResponse(state.to_dict())


@router.post("/source")
def scinsta_set_source(
    url: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Met a jour l'URL source interrogee pour le check de version.

    Validation minimale (schema http/https) ; on laisse l'admin assumer
    le contenu — la page cible doit contenir le microdata softwareVersion.
    """
    url = url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="URL http(s) requise")
    set_decrypt_url(db, url)
    return JSONResponse(get_state(db).to_dict())


@router.post("/upload")
async def scinsta_upload(
    ipa: UploadFile = File(...),
    user: User = Depends(require_user),
):
    """Stream l'IPA Instagram vers /etc/ipastore/scinsta-upload-<env>.ipa."""
    # On lit par chunks pour eviter de charger 280 Mo en memoire.
    path = upload_instagram_ipa(ipa.file)
    size = path.stat().st_size
    logger.info("scinsta IG upload received: %s (%d bytes)", path, size)
    return JSONResponse({"ok": True, "size": size})


@router.post("/clear-upload")
def scinsta_clear_upload(user: User = Depends(require_user)):
    clear_upload()
    return JSONResponse({"ok": True})


@router.post("/build")
def scinsta_build(
    patch: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Declenche le build. Requiert qu'une IPA ait ete uploadee prealablement.

    Si `patch` est non vide, doit correspondre a un filename decouvert
    dans patch/ (validation stricte contre path traversal).
    """
    state = get_state(db)
    if state.is_running:
        return JSONResponse(
            {"ok": False, "message": "Un build est déjà en cours."},
            status_code=409,
        )
    if not state.upload_ready:
        return JSONResponse(
            {"ok": False, "message": "Aucune IPA Instagram uploadée."},
            status_code=400,
        )
    patch_filename = patch.strip() or None
    if patch_filename:
        info = get_patch(db, patch_filename)
        if info is None:
            raise HTTPException(status_code=400, detail="Patch inconnu")
    flag = request_build(db, patch_filename)
    logger.info("SCInsta build requested: flag=%s patch=%s", flag, patch_filename)
    return JSONResponse({
        "ok": True,
        "message": "Build lancé. La page se rafraîchit automatiquement.",
    })
