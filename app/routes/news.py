"""Actualités : CRUD des articles publiés dans source.json (champ `news`)."""
from __future__ import annotations

import datetime as dt
import re
import secrets

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_user
from ..config import Config
from ..db import get_db
from ..models import App, News, User
from ..templates import templates

router = APIRouter()


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_ALLOWED_IMG_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
}


def _slugify(s: str) -> str:
    return _SLUG_RE.sub("-", s.lower()).strip("-") or secrets.token_hex(4)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def _unique_identifier(db: Session, title: str) -> str:
    base = _slugify(title)
    ident = base
    # SideStore utilise l'identifier comme clé stable pour marquer les articles vus.
    if db.execute(select(News).where(News.identifier == ident)).scalar_one_or_none():
        ident = f"{base}-{secrets.token_hex(3)}"
    return ident


def _save_image(upload: UploadFile) -> str | None:
    if upload is None or not upload.filename:
        return None
    ext = _ALLOWED_IMG_EXT.get((upload.content_type or "").lower())
    if ext is None:
        raise HTTPException(status_code=400, detail="Format non supporté (PNG/JPG/WebP)")
    data = upload.file.read()
    if not data:
        return None
    name = f"{secrets.token_hex(6)}.{ext}"
    (Config.NEWS_DIR / name).write_bytes(data)
    return name


def _sanitize_tint(raw: str) -> str:
    return re.sub(r"[^0-9a-fA-F]", "", raw)[:6].lower()


@router.get("/news")
def news_list(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    items = db.execute(select(News).order_by(News.date.desc())).scalars().all()
    return templates.TemplateResponse(
        request, "news.html",
        {"user": user, "items": items, "active": "news"},
    )


@router.get("/news/new")
def news_new(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    apps = db.execute(select(App).order_by(App.name)).scalars().all()
    return templates.TemplateResponse(
        request, "news_edit.html",
        {"user": user, "item": None, "apps": apps, "active": "news", "err": None},
    )


@router.post("/news/new")
async def news_create(
    title: str = Form(...),
    caption: str = Form(""),
    tint_color: str = Form(""),
    app_bundle_id: str = Form(""),
    notify: str = Form(""),
    image: UploadFile | None = File(None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    title = title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Titre requis")

    article = News(
        identifier=_unique_identifier(db, title),
        title=title,
        caption=caption.strip(),
        date=_utcnow(),
        tint_color=_sanitize_tint(tint_color),
        url="",
        app_bundle_id=app_bundle_id.strip(),
        notify=1 if notify else 0,
        image_path=_save_image(image) if image else None,
    )
    db.add(article)
    db.commit()
    return RedirectResponse("/news", status_code=303)


@router.get("/news/{article_id}")
def news_edit_page(
    article_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    article = db.get(News, article_id)
    if article is None:
        raise HTTPException(status_code=404)
    apps = db.execute(select(App).order_by(App.name)).scalars().all()
    return templates.TemplateResponse(
        request, "news_edit.html",
        {"user": user, "item": article, "apps": apps, "active": "news", "err": None},
    )


@router.post("/news/{article_id}")
async def news_update(
    article_id: int,
    title: str = Form(...),
    caption: str = Form(""),
    tint_color: str = Form(""),
    app_bundle_id: str = Form(""),
    notify: str = Form(""),
    image: UploadFile | None = File(None),
    remove_image: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    article = db.get(News, article_id)
    if article is None:
        raise HTTPException(status_code=404)
    article.title = title.strip() or article.title
    article.caption = caption.strip()
    article.tint_color = _sanitize_tint(tint_color)
    article.app_bundle_id = app_bundle_id.strip()
    article.notify = 1 if notify else 0
    # date inchangée à l'édition — correspond au moment de publication initial

    if remove_image and article.image_path:
        (Config.NEWS_DIR / article.image_path).unlink(missing_ok=True)
        article.image_path = None

    if image and image.filename:
        new_name = _save_image(image)
        if new_name:
            if article.image_path:
                (Config.NEWS_DIR / article.image_path).unlink(missing_ok=True)
            article.image_path = new_name

    db.commit()
    return RedirectResponse("/news", status_code=303)


@router.post("/news/{article_id}/delete")
def news_delete(
    article_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    article = db.get(News, article_id)
    if article is None:
        raise HTTPException(status_code=404)
    if article.image_path:
        (Config.NEWS_DIR / article.image_path).unlink(missing_ok=True)
    db.delete(article)
    db.commit()
    return RedirectResponse("/news", status_code=303)
