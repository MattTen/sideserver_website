"""Routes de configuration initiale de la BDD.

Accessible avant que la BDD ne soit configurée (le middleware dans main.py
redirige tout le reste vers /setup/database). Une fois la config écrite, la
page redirige vers /setup (création admin) ou /login.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import RedirectResponse

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
):
    if is_configured():
        return RedirectResponse("/login", status_code=303)

    values = {"host": host, "port": port, "user": user, "database": database}

    try:
        port_int = int(port.strip())
    except ValueError:
        return templates.TemplateResponse(
            request, "db_setup.html",
            {"error": "Le port doit être un entier.", "values": values},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    cfg = DbConfig(
        host=host.strip(),
        port=port_int,
        user=user.strip(),
        password=password,
        database=database.strip(),
    )

    ok, msg = test_connection(cfg)
    if not ok:
        return templates.TemplateResponse(
            request, "db_setup.html",
            {"error": f"Connexion impossible : {msg}", "values": values},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    save_db_config(cfg)
    # L'engine courant (s'il existait) est jeté pour prendre en compte la nouvelle
    # URL. init_db() crée ensuite les tables.
    reset_engine()
    try:
        init_db()
    except Exception as e:
        logger.exception("init_db a échoué après config BDD")
        return templates.TemplateResponse(
            request, "db_setup.html",
            {
                "error": f"Connexion OK mais création des tables échouée : {type(e).__name__}: {e}",
                "values": values,
            },
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return RedirectResponse("/setup", status_code=303)
