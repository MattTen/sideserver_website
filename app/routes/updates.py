"""Routes de gestion des mises à jour (préfixe : /settings/updates).

GET  /check  → interroge l'API GitHub et retourne le statut en JSON.
POST /apply  → écrit le fichier-drapeau dans /etc/ipastore/ ; le path unit
               systemd ipastore-update@{env}.path sur l'hôte le détecte et
               déclenche website-management {env}-update puis supprime le fichier.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..auth import require_user
from ..models import User
from ..updates import get_status, request_update

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings/updates")


@router.get("/check")
def updates_check(user: User = Depends(require_user)):
    status = get_status(refresh=True)
    return JSONResponse(status.to_dict())


@router.post("/apply")
def updates_apply(user: User = Depends(require_user)):
    status = get_status(refresh=True)
    if status.rolling:
        return JSONResponse(
            {"ok": False, "reason": "dev-is-rolling", "message": "Dev est rolling — utilise `dev-update` en CLI."},
            status_code=400,
        )
    if not status.update_available:
        return JSONResponse(
            {"ok": False, "reason": "no-update", "status": status.to_dict()},
            status_code=400,
        )
    flag = request_update()
    logger.info("Update requested: flag=%s current=%s latest=%s",
                flag, status.current, status.latest)
    return JSONResponse({
        "ok": True,
        "flag": str(flag),
        "status": status.to_dict(),
        "message": "Mise à jour demandée — le conteneur va redémarrer dans quelques secondes.",
    })
