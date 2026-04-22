"""Migration additive du schéma BDD vs les modèles SQLAlchemy.

Au boot (init_db) ET sur demande explicite (CLI / commande docker exec),
compare la structure de la BDD live aux modèles déclarés dans app/models.py
et applique les opérations strictement additives nécessaires pour aligner :

- CREATE TABLE pour les tables manquantes (déléguée à metadata.create_all)
- ALTER TABLE ADD COLUMN pour les colonnes manquantes
- CREATE INDEX pour les indexes nommés manquants

Strictement additif : aucun DROP, aucun MODIFY, aucun renommage. Si une
divergence de type ou de nullability est détectée sur une colonne existante,
elle est loggée mais NON corrigée — c'est à l'admin de décider.

CLI :
    python -m app.schema_migrate            # applique
    python -m app.schema_migrate --dry-run  # liste sans appliquer
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Iterable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateIndex, MetaData, Table

logger = logging.getLogger(__name__)


def _column_ddl(engine: Engine, column) -> str:
    """Construit le fragment DDL pour ADD COLUMN (sans le ALTER TABLE)."""
    col_type = column.type.compile(engine.dialect)
    parts = [f"`{column.name}`", col_type]

    if not column.nullable:
        parts.append("NOT NULL")

    # Server default explicite : on l'utilise tel quel.
    if column.server_default is not None:
        sd = column.server_default
        if hasattr(sd, "arg"):
            arg = sd.arg
            text_val = arg.text if hasattr(arg, "text") else str(arg)
            parts.append(f"DEFAULT {text_val}")
    elif column.default is not None and not column.default.is_callable:
        # Default Python scalaire : on tente une représentation MySQL.
        val = column.default.arg
        if isinstance(val, (int, float)):
            parts.append(f"DEFAULT {val}")
        elif isinstance(val, str):
            parts.append(f"DEFAULT '{val.replace(chr(39), chr(39)*2)}'")
    elif not column.nullable:
        # NOT NULL sans default → impossible d'ajouter sur table existante
        # sans valeur. On met un défaut "vide" raisonnable selon le type.
        type_str = col_type.upper()
        if "INT" in type_str or "DECIMAL" in type_str or "FLOAT" in type_str or "DOUBLE" in type_str:
            parts.append("DEFAULT 0")
        elif "DATETIME" in type_str or "TIMESTAMP" in type_str:
            parts.append("DEFAULT CURRENT_TIMESTAMP")
        else:
            parts.append("DEFAULT ''")

    return " ".join(parts)


def _index_ddl(table_name: str, index) -> str:
    cols = ", ".join(f"`{c.name}`" for c in index.columns)
    unique = "UNIQUE " if index.unique else ""
    return f"CREATE {unique}INDEX `{index.name}` ON `{table_name}` ({cols})"


def plan_migrations(engine: Engine, metadata: MetaData) -> list[str]:
    """Retourne la liste ordonnée des SQL à exécuter pour aligner la BDD.

    N'effectue aucune écriture. Utile pour --dry-run ou inspection.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    statements: list[str] = []

    for table_name, table in metadata.tables.items():
        if table_name not in existing_tables:
            # CREATE TABLE complet — délégué à metadata.create_all dans apply().
            statements.append(f"-- CREATE TABLE `{table_name}` (via metadata.create_all)")
            continue

        # Colonnes manquantes
        existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
        for col in table.columns:
            if col.name not in existing_cols:
                statements.append(
                    f"ALTER TABLE `{table_name}` ADD COLUMN {_column_ddl(engine, col)}"
                )

        # Indexes manquants (hors PRIMARY KEY)
        existing_idx = {i["name"] for i in inspector.get_indexes(table_name)}
        # Les UniqueConstraint sont aussi exposés comme unique_constraints.
        existing_uc = {u["name"] for u in inspector.get_unique_constraints(table_name)}
        existing_all = existing_idx | existing_uc
        for idx in table.indexes:
            if idx.name and idx.name not in existing_all:
                statements.append(_index_ddl(table_name, idx))

    return statements


def apply_pending_migrations(engine: Engine, metadata: MetaData) -> list[str]:
    """Applique les migrations additives. Retourne la liste des SQL exécutés."""
    # 1) Tables manquantes : create_all est idempotent.
    metadata.create_all(engine)

    # 2) Re-plan après create_all pour ne lister que les vraies opérations restantes.
    plan = plan_migrations(engine, metadata)
    executed: list[str] = []

    with engine.begin() as conn:
        for stmt in plan:
            if stmt.startswith("--"):
                continue  # commentaire (CREATE TABLE déjà fait)
            try:
                conn.execute(text(stmt))
                executed.append(stmt)
                logger.info("schema-migrate: %s", stmt)
            except Exception as e:
                logger.exception("schema-migrate FAILED: %s — %s", stmt, e)
                raise

    return executed


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Liste les opérations sans les appliquer.")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    _setup_logging(args.verbose)

    # Import retardé : on a besoin que la BDD soit configurée.
    from .db import Base, get_engine
    from . import models  # noqa: F401 — enregistre les modèles

    try:
        engine = get_engine()
    except RuntimeError as e:
        print(f"ERREUR : {e}", file=sys.stderr)
        return 2

    if args.dry_run:
        plan = plan_migrations(engine, Base.metadata)
        if not plan:
            print("Schéma à jour, rien à faire.")
        else:
            print("Plan de migration :")
            for s in plan:
                print(f"  {s}")
        return 0

    executed = apply_pending_migrations(engine, Base.metadata)
    if not executed:
        print("Schéma à jour, rien à faire.")
    else:
        print(f"{len(executed)} opération(s) appliquée(s) :")
        for s in executed:
            print(f"  {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
