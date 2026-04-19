"""Catégories : liste et CRUD."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..auth import require_user
from ..categories import get_categories, save_categories
from ..db import get_db
from ..models import User
from ..templates import templates

router = APIRouter()


@router.get("/categories")
def categories_page(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(request, "categories.html", {
        "user": user,
        "categories": get_categories(db),
        "active": "categories",
    })


@router.post("/categories/add")
def categories_add(
    name: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    cats = get_categories(db)
    name = name.strip()
    if name and name not in cats:
        cats.append(name)
        save_categories(db, cats)
    return RedirectResponse("/categories", status_code=303)


@router.post("/categories/{name}/delete")
def categories_delete(
    name: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    cats = get_categories(db)
    save_categories(db, [c for c in cats if c != name])
    return RedirectResponse("/categories", status_code=303)
