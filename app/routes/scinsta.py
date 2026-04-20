"""Routes SCInsta : check version, upload IPA, déclenchement du build."""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..auth import require_user
from ..categories import get_categories
from ..db import get_db
from ..models import User
from ..patches import discover_patches, get_patch
from .apps import TINT_COLORS, _TINT_PRESET_VALUES
from ..scinsta import (
    _META_FIELDS, clear_build_log, clear_upload, dismiss_last_build_error,
    get_state, read_build_log, request_build, request_cancel, run_check,
    save_changelog, save_metadata_field, set_decrypt_url, upload_instagram_ipa,
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
    # Categories : on prepend "aucune" (choix par defaut quand l'App n'existe
    # pas encore) + les categories existantes du store. "aucune" est aussi
    # stocke tel quel dans App.category si l'admin garde le defaut.
    cats = ["aucune"] + [c for c in get_categories(db) if c.lower() != "aucune"]
    return templates.TemplateResponse(
        request,
        "scinsta.html",
        {
            "user": user,
            "active": "scinsta",
            "state": state.to_dict(),
            "patches": patches,
            "categories": cats,
            "tint_colors": TINT_COLORS,
            "tint_preset_values": _TINT_PRESET_VALUES,
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


@router.post("/logs/clear")
def scinsta_logs_clear(user: User = Depends(require_user)):
    """Efface le fichier log live (bouton Effacer de l'UI)."""
    clear_build_log()
    return JSONResponse({"ok": True})


@router.post("/check")
def scinsta_check(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Interroge decrypt.day pour la derniere version Instagram."""
    state = run_check(db)
    return JSONResponse(state.to_dict())


@router.post("/metadata/{field}")
def scinsta_set_metadata(
    field: str,
    value: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Met a jour un champ metadata Instagram (App row ou setting pending).

    Regle UI : aucun champ ne peut etre vide (sauf via l'UI on envoie
    toujours quelque chose — "aucune" pour category si l'admin n'a rien
    specifie). On valide ici pour eviter qu'un admin contournant la
    validation client se retrouve avec une App sans nom ou description.
    """
    if field not in _META_FIELDS:
        raise HTTPException(status_code=404, detail="Champ inconnu")
    value = value.strip()
    if not value:
        raise HTTPException(status_code=400, detail="Valeur requise")
    if field == "tint_color":
        # Normalisation : hex 3 ou 6 caracteres sans '#'. L'UI envoie deja
        # sans le '#', mais un admin pourrait coller depuis un picker.
        v = value.lstrip("#").lower()
        if not re.fullmatch(r"[0-9a-f]{3}|[0-9a-f]{6}", v):
            raise HTTPException(status_code=400, detail="Couleur hex invalide")
        value = v
    save_metadata_field(db, field, value)
    return JSONResponse(get_state(db).to_dict())


@router.post("/changelog")
def scinsta_set_changelog(
    value: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Met a jour la Note de version (override persistant).

    Chaine vide -> reset au template auto "Instagram <v> + SCInsta".
    """
    save_changelog(db, value)
    return JSONResponse(get_state(db).to_dict())


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


@router.post("/dismiss-error")
def scinsta_dismiss_error(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Efface le dernier message d'erreur du build (fermeture UI).

    Ne touche pas au statut, juste au champ d'erreur — le badge reste
    correct (cancelled/failed) mais l'alerte disparait de la carte 3.
    """
    dismiss_last_build_error(db)
    return JSONResponse(get_state(db).to_dict())


@router.post("/cancel")
def scinsta_cancel(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Demande l'annulation du build en cours.

    Ecrit un flag-file que systemd surveille ; le service host-side fait
    `docker kill` sur le conteneur builder puis ecrit un result failed.
    Retourne 409 si aucun build n'est en cours.
    """
    state = get_state(db)
    if not state.is_running:
        return JSONResponse(
            {"ok": False, "message": "Aucun build en cours."},
            status_code=409,
        )
    flag = request_cancel(db)
    logger.info("SCInsta build cancel requested: flag=%s", flag)
    return JSONResponse({"ok": True, "message": "Annulation demandée."})


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
