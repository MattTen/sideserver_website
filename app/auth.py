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


def require_user_db_optional(request: Request) -> int:
    """Comme require_user MAIS tolerant a une BDD injoignable.

    Normalement : charge l'User depuis la BDD, ce qui invalide immediatement
    les cookies d'admins supprimes (ex. via reset-users).
    Si la BDD est HS : on degrade au cookie-only (signature HMAC + timestamp
    verifies via itsdangerous, zero acces BDD). L'attaquant ne peut toujours
    pas forger de cookie sans /etc/ipastore/secret_key. Le seul relachement
    c'est qu'un admin supprime garde l'acces a cette route tant que son
    cookie n'a pas expire (30 j max) ET que la BDD est down.

    A reserver aux routes qui doivent rester dispo hors-BDD, typiquement
    /settings/logs : on veut voir les logs justement parce que la BDD est HS.
    """
    from sqlalchemy.exc import OperationalError
    from .db import _get_session_factory

    token = request.cookies.get(Config.SESSION_COOKIE)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login"},
        )
    user_id = parse_session_token(token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login"},
        )

    db = _get_session_factory()()
    try:
        user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers={"Location": "/login"},
            )
    except OperationalError:
        # BDD HS : on se rabat sur la validation cookie-only deja faite
        # ci-dessus. Loggue quand meme pour que ca reste visible.
        import logging
        logging.getLogger(__name__).warning(
            "require_user_db_optional: BDD HS, fallback cookie-only pour user_id=%s",
            user_id,
        )
    finally:
        db.close()
    return user_id


def has_any_user(db: Session) -> bool:
    """Retourne True si au moins un utilisateur existe en base (évite la page de setup répétée)."""
    return db.execute(select(User.id).limit(1)).first() is not None


def redirect_to_setup_if_needed(db: Session) -> Optional[RedirectResponse]:
    """Redirige vers /setup si aucun utilisateur n'existe encore."""
    if not has_any_user(db):
        return RedirectResponse("/setup", status_code=303)
    return None
