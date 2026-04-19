"""Endpoints publics (sans authentification) : source.json et QR code.

source.json est le feed consommé par SideStore. Il est servi sans cache
et avec CORS ouvert (*) pour que SideStore puisse y accéder depuis n'importe
quelle origine (l'app iOS n'a pas de cookie de session).
"""
from __future__ import annotations

import io

import segno
from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..source_gen import build_source, get_setting

router = APIRouter()


@router.get("/source.json")
def source_json(db: Session = Depends(get_db)):
    payload = build_source(db)
    return JSONResponse(
        payload,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.get("/qr.svg")
def source_qr(db: Session = Depends(get_db)):
    base = get_setting(db, "base_url", "http://192.168.0.202").rstrip("/")
    url = f"{base}/source.json"
    qr = segno.make(url, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=6, dark="#e8e8ee", light="#13131a", border=2)
    return Response(buf.getvalue(), media_type="image/svg+xml")
