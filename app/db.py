"""SQLAlchemy engine, session factory et utilitaires de base de données."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import Config


class Base(DeclarativeBase):
    pass


# pool_pre_ping : émet un SELECT 1 avant chaque connexion empruntée au pool
# pour détecter les connexions mortes — MySQL ferme les idle connections après
# ~8h par défaut, ce qui causerait des "MySQL has gone away" sans ce flag.
# pool_recycle : renouvelle les connexions après 1h, bien en dessous du
# wait_timeout MySQL (28800s), pour éviter les erreurs en production longue durée.
engine = create_engine(Config.DB_URL, pool_pre_ping=True, pool_recycle=3600)

# expire_on_commit=False : les objets ORM restent accessibles après db.commit()
# sans déclencher de SELECT implicite. Nécessaire dans les routes FastAPI où la
# réponse est construite après le commit (ex: redirect avec données de l'objet).
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db():
    """Dépendance FastAPI : ouvre une session DB et la ferme après la requête."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Crée toutes les tables déclarées dans les modèles si elles n'existent pas (idempotent)."""
    from . import models  # noqa: F401 — import nécessaire pour enregistrer les modèles auprès de Base
    Base.metadata.create_all(engine)
