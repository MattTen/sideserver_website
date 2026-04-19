"""Configuration runtime chargée depuis les variables d'environnement.

Toutes les variables sont lues au démarrage du processus.
Les valeurs sensibles (DB_URL, SECRET_FILE) doivent être injectées
via /etc/ipastore/{prod,dev}.env monté en env_file dans docker-compose.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path


def _required(name: str) -> str:
    """Lit une variable d'environnement obligatoire. Lève RuntimeError si absente."""
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"Variable d'environnement {name} requise. "
            f"Voir .env.example et /etc/ipastore/{{prod,dev}}.env."
        )
    return val


class Config:
    # ── Base de données ──────────────────────────────────────────────────────
    # Connection string complète injectée par l'env file (ex: mysql+pymysql://...).
    DB_URL = _required("IPASTORE_DB_URL")

    # ── Système de fichiers du magasin ───────────────────────────────────────
    # STORE_DIR est monté depuis l'hôte (/srv/store-prod ou /srv/store-dev)
    # via le volume Docker, ce qui permet la persistance entre rebuilds.
    STORE_DIR = Path(os.environ.get("IPASTORE_STORE_DIR", "/srv/store"))
    IPAS_DIR = STORE_DIR / "ipas"
    ICONS_DIR = STORE_DIR / "icons"
    SCREENSHOTS_DIR = STORE_DIR / "screenshots"

    # ── Clé secrète de session ───────────────────────────────────────────────
    # Fichier binaire de 64 octets utilisé par itsdangerous pour signer les
    # cookies. Monté depuis /etc/ipastore/ pour survivre aux rebuilds.
    SECRET_KEY_FILE = Path(
        os.environ.get("IPASTORE_SECRET_FILE", "/etc/ipastore/secret_key")
    )

    # URL publique du serveur, utilisée pour construire les liens dans source.json.
    # Doit être définie dans /etc/ipastore/{prod,dev}.env via IPASTORE_BASE_URL.
    DEFAULT_BASE_URL = os.environ.get("IPASTORE_BASE_URL", "")

    # ── Sessions ─────────────────────────────────────────────────────────────
    SESSION_COOKIE = "ipastore_session"
    SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 jours en secondes

    # ── Système de mise à jour ───────────────────────────────────────────────
    # ENV_NAME détermine le comportement du module updates :
    #   "prod" → vérifie les releases GitHub, bouton MAJ actif
    #   "dev"  → rolling, bouton MAJ toujours grisé
    ENV_NAME = os.environ.get("IPASTORE_ENV", "prod")
    GITHUB_REPO = os.environ.get("IPASTORE_GITHUB_REPO", "MattTen/sideserver_website")

    # /etc/ipastore est partagé entre l'hôte et les deux conteneurs via volume.
    # Le conteneur y lit le token GitHub et y écrit le flag de déclenchement MAJ.
    IPASTORE_ETC = Path(os.environ.get("IPASTORE_ETC_DIR", "/etc/ipastore"))

    # Fichier lu par read_current_version() : contient le tag de la dernière
    # release déployée (ex: "v1.0.0") ou une ref rolling (ex: "rolling-abc1234").
    VERSION_FILE = IPASTORE_ETC / f"{ENV_NAME}.version"

    # Fichier-drapeau écrit par request_update() et surveillé par le path unit
    # systemd ipastore-update@{env}.path sur l'hôte. Sa création déclenche le
    # service qui exécute website-management {env}-update puis supprime le fichier.
    UPDATE_FLAG_FILE = IPASTORE_ETC / f"update-requested-{ENV_NAME}"

    # Intervalle de la vérification automatique des MAJ en arrière-plan.
    UPDATE_CHECK_INTERVAL_SECONDS = 6 * 3600


def load_secret_key() -> bytes:
    """Charge ou génère la clé secrète de signature des sessions.

    Si le fichier n'existe pas (premier démarrage), génère 64 octets aléatoires
    et les persiste avec droits 600. Le fichier est monté depuis l'hôte donc
    survit aux rebuilds du conteneur.
    """
    path = Config.SECRET_KEY_FILE
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(64)
    path.write_bytes(key)
    path.chmod(0o600)
    return key


def ensure_dirs() -> None:
    """Crée les sous-dossiers du store s'ils n'existent pas encore."""
    for d in (Config.IPAS_DIR, Config.ICONS_DIR, Config.SCREENSHOTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
