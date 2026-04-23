"""Routes de configuration initiale de la BDD.

Accessible avant que la BDD ne soit configurée (le middleware dans main.py
redirige tout le reste vers /setup/database). Une fois la config écrite, la
page redirige vers /setup (création admin) ou /login.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import RedirectResponse, StreamingResponse

from ..db import init_db, reset_engine
from ..db_config import DbConfig, is_configured, save_db_config, test_connection
from ..templates import templates

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/setup/database")
def db_setup_page(request: Request):
    # Déjà configurée → pas besoin de revenir ici, retour au flux normal.
    if is_configured():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request, "db_setup.html",
        {"error": None, "values": {"host": "", "port": "3306", "user": "", "database": ""}},
    )


@router.post("/setup/database")
def db_setup_submit(
    request: Request,
    host: str = Form(...),
    port: str = Form("3306"),
    user: str = Form(...),
    password: str = Form(...),
    database: str = Form(...),
    stream: str = "",
):
    """Configure la BDD en 3 phases. Si ?stream=1 (appele par l'UI JS),
    renvoie du NDJSON en streaming pour que l'overlay puisse refleter
    l'etat reel ; sinon, comportement classique non-JS (re-render la page
    avec erreur ou redirige)."""
    if is_configured():
        return RedirectResponse("/login", status_code=303)

    values = {"host": host, "port": port, "user": user, "database": database}

    try:
        port_int = int(port.strip())
    except ValueError:
        err_msg = "Le port doit être un entier."
        if stream == "1":
            return _stream_error(err_msg, http_status=400)
        return templates.TemplateResponse(
            request, "db_setup.html",
            {"error": err_msg, "values": values},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    cfg = DbConfig(
        host=host.strip(),
        port=port_int,
        user=user.strip(),
        password=password,
        database=database.strip(),
    )

    if stream == "1":
        return StreamingResponse(_run_setup_stream(cfg), media_type="application/x-ndjson")

    # Fallback non-JS : flux classique synchrone.
    ok, msg = test_connection(cfg)
    if not ok:
        return templates.TemplateResponse(
            request, "db_setup.html",
            {"error": msg, "values": values},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    save_db_config(cfg)
    reset_engine()
    try:
        init_db()
    except Exception as e:
        logger.exception("init_db a échoué après config BDD")
        return templates.TemplateResponse(
            request, "db_setup.html",
            {"error": f"Connexion OK mais création des tables échouée : {type(e).__name__}: {e}",
             "values": values},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return RedirectResponse("/setup", status_code=303)


def _evt(**kw) -> bytes:
    """Serialise un evenement NDJSON (une ligne JSON + \\n)."""
    return (json.dumps(kw, ensure_ascii=False) + "\n").encode("utf-8")


def _stream_error(msg: str, http_status: int = 400) -> StreamingResponse:
    def gen():
        yield _evt(error=msg)
    return StreamingResponse(gen(), media_type="application/x-ndjson", status_code=http_status)


def _run_setup_stream(cfg: DbConfig):
    """Generateur qui emet les vraies phases : test_connection -> save +
    init_db -> done. Chaque phase est envoyee AVANT le debut de son
    operation pour que l'UI affiche le bon label pendant le travail."""
    yield _evt(phase="test")
    ok, msg = test_connection(cfg)
    if not ok:
        yield _evt(error=msg)
        return

    yield _evt(phase="init")
    save_db_config(cfg)
    reset_engine()
    try:
        init_db()
    except Exception as e:
        logger.exception("init_db a échoué après config BDD")
        yield _evt(error=f"Connexion OK mais création des tables échouée : {type(e).__name__}: {e}")
        return

    yield _evt(phase="final")
    yield _evt(done=True, redirect="/setup")
