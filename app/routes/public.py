"""Endpoints publics (sans authentification) : source.json et QR code.

source.json est le feed consommé par SideStore. Il est servi sans cache
et avec CORS ouvert (*) pour que SideStore puisse y accéder depuis n'importe
quelle origine (l'app iOS n'a pas de cookie de session).
"""
from __future__ import annotations

import io

import segno
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..config import Config
from ..db import get_db
from ..source_gen import build_source

router = APIRouter()


def _base_from_request(request: Request) -> str:
    """URL publique utilisée dans source.json + QR code.

    `IPASTORE_BASE_URL` (env file) prime sur `request.base_url` pour que
    source.json reste correct derrière un reverse proxy / Cloudflare Tunnel :
    `request.base_url` reflète le host interne (ex: http://127.0.0.1:80)
    alors que SideStore doit joindre l'URL publique (ex: https://store.mon-domaine).
    Fallback sur la requête si la variable n'est pas définie (dev local).
    """
    if Config.DEFAULT_BASE_URL:
        return Config.DEFAULT_BASE_URL.rstrip("/")
    return str(request.base_url).rstrip("/")


@router.get("/source.json")
def source_json(request: Request, db: Session = Depends(get_db)):
    payload = build_source(db, _base_from_request(request))
    return JSONResponse(
        payload,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.get("/qr.svg")
def source_qr(request: Request, db: Session = Depends(get_db)):
    url = f"{_base_from_request(request)}/source.json"
    qr = segno.make(url, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=6, dark="#e8e8ee", light="#13131a", border=2)
    return Response(buf.getvalue(), media_type="image/svg+xml")
