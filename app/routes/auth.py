"""Routes d'authentification : login, logout, et setup initial.

/setup est accessible uniquement si la table users est vide (premier démarrage).
Une fois un compte créé, /setup redirige vers /login.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..auth import (
    create_session_token, get_current_user, hash_password,
    has_any_user, verify_password,
)
from ..config import Config
from ..db import get_db
from ..models import User
from ..templates import templates

router = APIRouter()


@router.get("/login")
def login_page(request: Request, db: Session = Depends(get_db)):
    if not has_any_user(db):
        return RedirectResponse("/setup", status_code=303)
    if get_current_user(request, db):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter_by(username=username.strip()).one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Identifiants incorrects", "username": username},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    user.last_login = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    db.commit()
    token = create_session_token(user.id)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        Config.SESSION_COOKIE, token,
        max_age=Config.SESSION_MAX_AGE, httponly=True, samesite="lax",
    )
    return resp


@router.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(Config.SESSION_COOKIE)
    return resp


@router.get("/setup")
def setup_page(request: Request, db: Session = Depends(get_db)):
    if has_any_user(db):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "setup.html", {"error": None})


@router.post("/setup")
def setup_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    if has_any_user(db):
        return RedirectResponse("/login", status_code=303)

    username = username.strip()
    error: str | None = None
    if len(username) < 3:
        error = "Identifiant trop court (3 caractères min)"
    elif len(password) < 8:
        error = "Mot de passe trop court (8 caractères min)"
    elif password != password_confirm:
        error = "Les mots de passe ne correspondent pas"

    if error:
        return templates.TemplateResponse(
            request, "setup.html",
            {"error": error, "username": username},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_session_token(user.id)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        Config.SESSION_COOKIE, token,
        max_age=Config.SESSION_MAX_AGE, httponly=True, samesite="lax",
    )
    return resp
