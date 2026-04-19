"""Dashboard (home)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import get_current_user, has_any_user
from ..db import get_db
from ..models import App, Version
from ..source_gen import get_setting
from ..templates import templates

router = APIRouter()


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    if not has_any_user(db):
        return RedirectResponse("/setup", status_code=303)
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    app_count = db.scalar(select(func.count(App.id))) or 0
    version_count = db.scalar(select(func.count(Version.id))) or 0
    total_size = db.scalar(select(func.coalesce(func.sum(Version.size), 0))) or 0

    recent = (
        db.execute(
            select(Version, App)
            .join(App, Version.app_id == App.id)
            .order_by(Version.uploaded_at.desc())
            .limit(8)
        ).all()
    )

    base_url = get_setting(db, "base_url", "http://192.168.0.202").rstrip("/")

    return templates.TemplateResponse(
        request, "dashboard.html",
        {
            "user": user,
            "app_count": app_count,
            "version_count": version_count,
            "total_size": total_size,
            "recent": recent,
            "base_url": base_url,
            "source_url": f"{base_url}/source.json",
            "active": "dashboard",
        },
    )
