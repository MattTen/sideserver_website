"""Authentification par session : hachage bcrypt + cookie signé HMAC.

Flux de connexion :
  1. L'utilisateur soumet login/mot de passe via POST /login.
  2. Le mot de passe est vérifié contre le hash bcrypt en base.
  3. Un token signé contenant l'user_id est créé avec TimestampSigner.
  4. Le token est placé dans un cookie httpOnly (inaccessible au JS).
  5. À chaque requête, le cookie est vérifié : signature + expiration + user en base.
"""
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


# TimestampSigner : signe les tokens avec HMAC-SHA1 + timestamp intégré.
# Le timestamp permet de vérifier l'expiration côté serveur sans état (stateless).
# La clé est chargée depuis /etc/ipastore/secret_key.{env} (64 octets aléatoires).
_signer = TimestampSigner(load_secret_key())


def hash_password(plain: str) -> str:
    """Hache un mot de passe en bcrypt. rounds=12 : bon compromis sécurité/temps (~300ms)."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Vérifie un mot de passe contre son hash bcrypt. Résistant aux timing attacks."""
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


def create_session_token(user_id: int) -> str:
    """Génère un token signé contenant l'user_id et un timestamp."""
    return _signer.sign(str(user_id).encode()).decode()


def parse_session_token(token: str) -> Optional[int]:
    """Valide la signature et l'expiration du token. Retourne l'user_id ou None."""
    try:
        raw = _signer.unsign(token.encode(), max_age=Config.SESSION_MAX_AGE)
        return int(raw.decode())
    except (BadSignature, ValueError):
        return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """Retourne l'utilisateur connecté depuis le cookie de session, ou None."""
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
    """Dépendance FastAPI : exige un utilisateur connecté, sinon redirige vers /login."""
    user = get_current_user(request, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login"},
        )
    return user


def has_any_user(db: Session) -> bool:
    """Retourne True si au moins un utilisateur existe en base (évite la page de setup répétée)."""
    return db.execute(select(User.id).limit(1)).first() is not None


def redirect_to_setup_if_needed(db: Session) -> Optional[RedirectResponse]:
    """Redirige vers /setup si aucun utilisateur n'existe encore."""
    if not has_any_user(db):
        return RedirectResponse("/setup", status_code=303)
    return None
