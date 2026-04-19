"""Session auth: bcrypt hashing + signed cookie."""
from __future__ import annotations

from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, TimestampSigner
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Config, load_secret_key
from .db import get_db
from .models import User


_signer = TimestampSigner(load_secret_key())


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


def create_session_token(user_id: int) -> str:
    return _signer.sign(str(user_id).encode()).decode()


def parse_session_token(token: str) -> Optional[int]:
    try:
        raw = _signer.unsign(token.encode(), max_age=Config.SESSION_MAX_AGE)
        return int(raw.decode())
    except (BadSignature, ValueError):
        return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    token = request.cookies.get(Config.SESSION_COOKIE)
    if not token:
        return None
    user_id = parse_session_token(token)
    if user_id is None:
        return None
    return db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()


def require_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    user = get_current_user(request, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login"},
        )
    return user


def has_any_user(db: Session) -> bool:
    return db.execute(select(User.id).limit(1)).first() is not None


def redirect_to_setup_if_needed(db: Session) -> Optional[RedirectResponse]:
    if not has_any_user(db):
        return RedirectResponse("/setup", status_code=303)
    return None
