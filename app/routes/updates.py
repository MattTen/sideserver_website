"""Routes de gestion des mises à jour (préfixe : /settings/updates).

GET  /check    → interroge l'API GitHub et retourne le statut en JSON.
POST /apply    → écrit le fichier-drapeau dans /etc/ipastore/ ; le path unit
                 systemd ipastore-update@prod.path sur l'hôte le détecte et
                 déclenche website-management prod-update puis supprime le fichier.
POST /restart  → redémarre le conteneur en envoyant SIGTERM au process uvicorn.
                 Docker Compose ayant `restart: unless-stopped`, le daemon
                 relance automatiquement le conteneur.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal

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


async def _kill_after(delay: float) -> None:
    """Envoie SIGTERM au process PID 1 apres un court delai.

    Laisse le temps a la reponse HTTP de partir avant que le process quitte.
    Docker redemarrera le conteneur grace a restart: unless-stopped.
    """
    await asyncio.sleep(delay)
    os.kill(os.getpid(), signal.SIGTERM)


@router.post("/restart")
def updates_restart(user: User = Depends(require_user)):
    logger.info("Restart requested by user=%s", user.username)
    asyncio.get_event_loop().create_task(_kill_after(0.5))
    return JSONResponse({
        "ok": True,
        "message": "Redémarrage en cours — le conteneur reviendra dans quelques secondes.",
    })
