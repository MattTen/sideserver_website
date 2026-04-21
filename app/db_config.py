"""Gestion de la configuration BDD post-install.

Flux :
  1. Premier démarrage : aucune config. L'engine ne peut pas être construit.
  2. L'admin ouvre l'UI, le middleware le redirige vers /setup/database.
  3. Il saisit host/port/user/mdp/db → POST teste la connexion → écrit db.json.
  4. L'engine SQLAlchemy est (re)construit et init_db() crée les tables.
  5. Redirige vers /setup (création admin) puis flux normal.

Fallback : si db.json est absent mais IPASTORE_DB_URL est défini dans l'env,
on l'utilise directement (compatibilité avec l'ancien déploiement).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

from .config import Config


# Fichier JSON 600 contenant host/port/user/password/database.
# Monté depuis /etc/ipastore/ (partagé hôte ↔ conteneur).
DB_CONFIG_FILE: Path = Config.IPASTORE_ETC / "db.json"


@dataclass
class DbConfig:
    host: str
    port: int
    user: str
    password: str
    database: str

    def to_url(self) -> str:
        # quote_plus : le mot de passe peut contenir des caractères spéciaux
        # (@, :, /) qui casseraient l'URL sans encodage.
        pw = quote_plus(self.password)
        user = quote_plus(self.user)
        return (
            f"mysql+pymysql://{user}:{pw}@{self.host}:{self.port}/"
            f"{self.database}?charset=utf8mb4"
        )


def load_db_config() -> Optional[DbConfig]:
    """Lit le fichier db.json s'il existe et est valide. Sinon None."""
    if not DB_CONFIG_FILE.exists():
        return None
    try:
        data = json.loads(DB_CONFIG_FILE.read_text(encoding="utf-8"))
        return DbConfig(
            host=str(data["host"]),
            port=int(data["port"]),
            user=str(data["user"]),
            password=str(data["password"]),
            database=str(data["database"]),
        )
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        return None


def save_db_config(cfg: DbConfig) -> None:
    """Écrit db.json avec permissions 600 (contient le mot de passe)."""
    DB_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "host": cfg.host,
        "port": cfg.port,
        "user": cfg.user,
        "password": cfg.password,
        "database": cfg.database,
    }
    DB_CONFIG_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    DB_CONFIG_FILE.chmod(0o600)


def resolve_db_url() -> Optional[str]:
    """Retourne l'URL SQLAlchemy à utiliser : db.json en priorité, sinon env var."""
    cfg = load_db_config()
    if cfg is not None:
        return cfg.to_url()
    env = os.environ.get("IPASTORE_DB_URL")
    return env or None


def is_configured() -> bool:
    return resolve_db_url() is not None


def test_connection(cfg: DbConfig) -> tuple[bool, str]:
    """Tente un SELECT 1 pour valider host/user/mdp/db. Retourne (ok, message)."""
    # Import local : sqlalchemy ne doit pas être requis au niveau module
    # de config quand on démarre sans BDD.
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import SQLAlchemyError

    try:
        engine = create_engine(cfg.to_url(), pool_pre_ping=False)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True, "Connexion OK"
    except SQLAlchemyError as e:
        return False, f"{type(e).__name__}: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
