"""Endpoints publics (sans authentification) : source.json et QR code.

source.json est le feed consommé par SideStore. Il est servi sans cache
et avec CORS ouvert (*) pour que SideStore puisse y accéder depuis n'importe
quelle origine (l'app iOS n'a pas de cookie de session).

Optionnel : protection par token (?t=<256-char>). Quand active, /source.json
et /qr.svg refusent toute requete sans le bon token. C'est un secret long
plutot qu'une auth standard car SideStore ne sait pas presenter de header
custom -- seul GET avec query string est utilisable cote client iOS.
"""
from __future__ import annotations

import io

import segno
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..config import Config
from ..db import get_db
from ..seo import is_indexing_disabled
from ..source_gen import build_source
from .. import source_token

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


def _reject_unauthorized() -> Response:
    # 404 plutot que 401 : on ne veut pas reveler l'existence du feed aux
    # bots de scrapping. Pour eux, l'URL n'existe pas tout court.
    return Response(status_code=404)


@router.get("/source.json")
def source_json(
    request: Request,
    t: str | None = Query(None),
    db: Session = Depends(get_db),
):
    if source_token.is_enabled() and not source_token.check(t):
        return _reject_unauthorized()
    payload = build_source(db, _base_from_request(request))
    return JSONResponse(
        payload,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.get("/healthz")
def healthz():
    """Endpoint liveness pour le HEALTHCHECK Docker. Toujours ouvert (pas de
    jeton, pas de BDD) : un 200 signifie uniquement que le process uvicorn
    repond. La verif BDD est volontairement exclue pour que le conteneur ne
    soit pas marque unhealthy pendant une coupure BDD (l'UI affiche deja 503
    dans ce cas, pas besoin de redemarrer le conteneur)."""
    return Response("ok", media_type="text/plain")


@router.get("/robots.txt")
def robots_txt():
    body = "User-agent: *\nDisallow: /\n" if is_indexing_disabled() else "User-agent: *\nAllow: /\n"
    return Response(body, media_type="text/plain")


@router.get("/qr.svg")
def source_qr(
    request: Request,
    t: str | None = Query(None),
    db: Session = Depends(get_db),
):
    if source_token.is_enabled() and not source_token.check(t):
        return _reject_unauthorized()
    url = f"{_base_from_request(request)}/source.json"
    if source_token.is_enabled():
        url = f"{url}?t={source_token.get_token()}"
    qr = segno.make(url, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=6, dark="#e8e8ee", light="#13131a", border=2)
    return Response(buf.getvalue(), media_type="image/svg+xml")
