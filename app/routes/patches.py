"""Patches : listing, détail (avec dropdown app/version), renommage, exécution."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_user
from ..config import Config
from ..db import get_db
from ..ipa import sha256_of_file
from ..models import App, User, Version
from ..patches import (
    discover_patches, get_patch, run_patch,
    set_description, set_display_name,
)
from ..templates import templates

router = APIRouter()


@router.get("/patches")
def patches_list(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    patches = discover_patches(db)
    return templates.TemplateResponse(
        request, "patches.html",
        {"user": user, "patches": patches, "active": "patches"},
    )


@router.post("/patches/{filename}/rename")
def patches_rename(
    filename: str,
    display_name: str = Form(""),
    description: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    patch = get_patch(db, filename)
    if patch is None:
        raise HTTPException(status_code=404, detail="Patch inconnu")
    set_display_name(db, filename, display_name)
    set_description(db, filename, description)
    return RedirectResponse(f"/patches/{filename}", status_code=303)


@router.get("/patches/{filename}")
def patch_detail(
    filename: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    msg: str | None = None,
    err: str | None = None,
    log: str | None = None,
):
    patch = get_patch(db, filename)
    if patch is None:
        raise HTTPException(status_code=404, detail="Patch inconnu")

    # Liste toutes les apps avec leurs versions pour la dropdown en cascade.
    apps = db.execute(select(App).order_by(App.name)).scalars().all()
    apps_with_versions = [
        {
            "bundle_id": a.bundle_id,
            "name": a.name,
            "versions": [
                {
                    "id": v.id,
                    "version": v.version,
                    "build_version": v.build_version,
                    "ipa_filename": v.ipa_filename,
                    "size": v.size,
                }
                for v in a.versions
            ],
        }
        for a in apps
        if a.versions
    ]

    return templates.TemplateResponse(
        request, "patch_detail.html",
        {
            "user": user,
            "patch": patch,
            "apps": apps_with_versions,
            "msg": msg,
            "err": err,
            "log": log,
            "active": "patches",
        },
    )


@router.post("/patches/{filename}/run")
def patch_run(
    filename: str,
    request: Request,
    version_id: int = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    patch = get_patch(db, filename)
    if patch is None:
        raise HTTPException(status_code=404, detail="Patch inconnu")

    version = db.get(Version, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Version inconnue")

    ipa_path = Config.IPAS_DIR / version.ipa_filename
    if not ipa_path.is_file():
        raise HTTPException(status_code=404, detail=f"IPA introuvable : {ipa_path}")

    success, log_output = run_patch(patch.path, ipa_path)

    if not success:
        return templates.TemplateResponse(
            request, "patch_detail.html",
            {
                "user": user,
                "patch": patch,
                "apps": [
                    {
                        "bundle_id": a.bundle_id, "name": a.name,
                        "versions": [
                            {"id": v.id, "version": v.version,
                             "build_version": v.build_version,
                             "ipa_filename": v.ipa_filename, "size": v.size}
                            for v in a.versions
                        ],
                    }
                    for a in db.execute(select(App).order_by(App.name)).scalars().all()
                    if a.versions
                ],
                "err": "Le patch a échoué — voir log ci-dessous.",
                "log": log_output,
                "selected_version_id": version_id,
                "active": "patches",
            },
            status_code=500,
        )

    # Le patch a écrasé l'IPA en place : on recalcule taille et sha256 pour
    # garder la DB et source.json alignés avec le vrai fichier.
    version.size = ipa_path.stat().st_size
    version.sha256 = sha256_of_file(ipa_path)
    db.commit()

    app = db.get(App, version.app_id)
    app_label = f"{app.name} {version.version} (build {version.build_version})" if app else f"version #{version_id}"
    return templates.TemplateResponse(
        request, "patch_detail.html",
        {
            "user": user,
            "patch": patch,
            "apps": [
                {
                    "bundle_id": a.bundle_id, "name": a.name,
                    "versions": [
                        {"id": v.id, "version": v.version,
                         "build_version": v.build_version,
                         "ipa_filename": v.ipa_filename, "size": v.size}
                        for v in a.versions
                    ],
                }
                for a in db.execute(select(App).order_by(App.name)).scalars().all()
                if a.versions
            ],
            "msg": f"Patch appliqué sur {app_label}. Taille et hash mis à jour.",
            "log": log_output,
            "active": "patches",
        },
    )
