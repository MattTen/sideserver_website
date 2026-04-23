"""SQLAlchemy engine, session factory et utilitaires de base de données.

L'engine est construit paresseusement : la connection string n'est résolue
que lorsqu'une session est réellement demandée (après /setup/database).
Cela permet au conteneur de démarrer avant que la BDD ne soit configurée.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .db_config import resolve_db_url


class Base(DeclarativeBase):
    pass


# État global : construit à la première demande, reset après /setup/database.
_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def _build_engine() -> Engine:
    url = resolve_db_url()
    if url is None:
        raise RuntimeError(
            "Base de données non configurée. Ouvre /setup/database "
            "dans l'interface pour saisir les identifiants."
        )
    # pool_pre_ping : émet un SELECT 1 avant chaque connexion empruntée au pool
    # pour détecter les connexions mortes — MySQL ferme les idle connections après
    # ~8h par défaut, ce qui causerait des "MySQL has gone away" sans ce flag.
    # pool_recycle : renouvelle les connexions après 1h, bien en dessous du
    # wait_timeout MySQL (28800s), pour éviter les erreurs en production longue durée.
    # connect_args.connect_timeout=3 : sans ca PyMySQL attend le timeout TCP
    # par defaut (~75s sous Linux) si la BDD est injoignable, ce qui gele
    # toutes les requetes le temps du timeout. 3s laisse le temps a un reseau
    # lent, court assez pour ne pas bloquer l'UI.
    # read_timeout / write_timeout : sans ca une requete posee sur une connexion
    # qui ne repond plus (firewall qui drop sans RST, crash BDD entre 2 paquets)
    # bloque le worker FastAPI indefiniment -- l'UI reste figee sur "slow" sans
    # jamais recevoir d'erreur. 10s coupe la requete, remonte une OperationalError,
    # logguee par uvicorn et renvoyee en 500 au client.
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args={
            "connect_timeout": 3,
            "read_timeout": 10,
            "write_timeout": 10,
        },
    )


def get_engine() -> Engine:
    """Retourne l'engine, le construit à la première demande."""
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def _get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        # expire_on_commit=False : les objets ORM restent accessibles après db.commit()
        # sans déclencher de SELECT implicite. Nécessaire dans les routes FastAPI où la
        # réponse est construite après le commit (ex: redirect avec données de l'objet).
        _SessionLocal = sessionmaker(
            bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False
        )
    return _SessionLocal


def reset_engine() -> None:
    """Jette l'engine courant ; la prochaine get_engine() reconstruira.

    Utilisé après POST /setup/database pour prendre en compte les nouveaux
    credentials sans redémarrer le conteneur.
    """
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


class _SessionLocalProxy:
    """Objet appelable qui délègue à la factory courante (lazy).

    Permet de conserver `from app.db import SessionLocal` ailleurs dans
    le code sans changer les imports existants.
    """

    def __call__(self, *args, **kwargs):
        return _get_session_factory()(*args, **kwargs)


SessionLocal = _SessionLocalProxy()


def get_db():
    """Dépendance FastAPI : ouvre une session DB et la ferme après la requête."""
    db = _get_session_factory()()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Crée les tables manquantes ET applique les migrations additives.

    Appelé au boot du conteneur (après update / rebuild) : tout changement
    de schéma poussé dans une nouvelle version est rattrapé automatiquement
    sans intervention manuelle. Voir app/schema_migrate.py pour la logique.
    """
    from . import models  # noqa: F401 — import nécessaire pour enregistrer les modèles auprès de Base
    from .schema_migrate import apply_pending_migrations
    apply_pending_migrations(get_engine(), Base.metadata)
    _legacy_migrate()


def _legacy_migrate() -> None:
    """Migrations de schéma destructives héritées (DROP COLUMN).

    Le mécanisme générique ne fait que de l'additif. Pour les opérations
    DROP / RENAME historiques (qu'on ne veut pas réintroduire dans les modèles),
    on garde ce bloc bien identifié.
    """
    from sqlalchemy import inspect, text

    engine = get_engine()
    inspector = inspect(engine)
    if "news" not in inspector.get_table_names():
        return

    cols = {c["name"] for c in inspector.get_columns("news")}
    with engine.connect() as conn:
        for old in ("tint_color", "url"):
            if old in cols:
                conn.execute(text(f"ALTER TABLE news DROP COLUMN {old}"))
                conn.commit()
