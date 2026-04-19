#!/usr/bin/env python3
"""Schema sync (DDL only) : emet un plan SQL additif pour aligner une BDD
target sur la structure d'une BDD source.

Utilise le binaire `mysql` via subprocess — aucune dep Python externe requise.

Le plan genere contient UNIQUEMENT des operations additives :
- CREATE TABLE pour les tables absentes de target
- ALTER TABLE ADD COLUMN pour les colonnes manquantes
- ALTER TABLE ADD INDEX/UNIQUE pour les index manquants (hors PRIMARY)
- ALTER TABLE ADD CONSTRAINT FOREIGN KEY pour les FKs manquantes

Raison d'etre : permettre de propager un changement de schema effectue en
dev vers prod sans jamais toucher aux donnees. Les divergences de type ou
de nullability sur des colonnes existantes sont reportees en commentaire
dans le plan mais NON corrigees — a l'admin de les appliquer manuellement
s'il juge que c'est sans risque.

Usage :
    schema-sync.py --source ipastore-dev --target ipastore-prod \
        [--defaults /etc/ipastore/.mysql.cnf] [--out plan.sql]
"""
from __future__ import annotations

import argparse
import subprocess
import sys


def mysql_query(sql: str, defaults_file: str, database: str | None = None) -> list[list[str]]:
    """Execute SQL via le CLI mysql et renvoie les lignes TSV.

    -N : pas de header. -B : format batch (TSV).
    Les NULL sortent en litteral "NULL" — on les reconvertit via null_if().
    """
    cmd = ["mysql", f"--defaults-extra-file={defaults_file}", "-N", "-B", "-e", sql]
    if database:
        cmd.append(database)
    try:
        res = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"mysql CLI error: {e.stderr}", file=sys.stderr)
        raise
    return [line.split("\t") for line in res.stdout.split("\n") if line]


def null_if(s: str) -> str | None:
    return None if s == "NULL" else s


def get_tables(defaults: str, db: str) -> set[str]:
    sql = ("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
           f"WHERE TABLE_SCHEMA='{db}' AND TABLE_TYPE='BASE TABLE'")
    return {r[0] for r in mysql_query(sql, defaults)}


def get_columns(defaults: str, db: str, table: str) -> dict[str, dict]:
    sql = (
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, "
        "EXTRA, ORDINAL_POSITION, IFNULL(COLUMN_COMMENT, '') "
        "FROM INFORMATION_SCHEMA.COLUMNS "
        f"WHERE TABLE_SCHEMA='{db}' AND TABLE_NAME='{table}' "
        "ORDER BY ORDINAL_POSITION"
    )
    out: dict[str, dict] = {}
    for r in mysql_query(sql, defaults):
        out[r[0]] = {
            "type": r[1],
            "nullable": r[2] == "YES",
            "default": null_if(r[3]),
            "extra": r[4] or "",
            "pos": int(r[5]),
            "comment": r[6] or "",
        }
    return out


def get_indexes(defaults: str, db: str, table: str) -> dict[str, dict]:
    sql = (
        "SELECT INDEX_NAME, NON_UNIQUE, COLUMN_NAME, SEQ_IN_INDEX "
        "FROM INFORMATION_SCHEMA.STATISTICS "
        f"WHERE TABLE_SCHEMA='{db}' AND TABLE_NAME='{table}' "
        "ORDER BY INDEX_NAME, SEQ_IN_INDEX"
    )
    idx: dict[str, dict] = {}
    for r in mysql_query(sql, defaults):
        name, non_unique, col = r[0], r[1], r[2]
        if name not in idx:
            idx[name] = {"unique": non_unique == "0", "columns": []}
        idx[name]["columns"].append(col)
    return idx


def get_foreign_keys(defaults: str, db: str, table: str) -> dict[str, dict]:
    sql = (
        "SELECT kcu.CONSTRAINT_NAME, kcu.COLUMN_NAME, kcu.REFERENCED_TABLE_NAME, "
        "       kcu.REFERENCED_COLUMN_NAME, rc.UPDATE_RULE, rc.DELETE_RULE "
        "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu "
        "JOIN INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc "
        "  ON rc.CONSTRAINT_SCHEMA = kcu.CONSTRAINT_SCHEMA "
        " AND rc.CONSTRAINT_NAME   = kcu.CONSTRAINT_NAME "
        f"WHERE kcu.TABLE_SCHEMA='{db}' AND kcu.TABLE_NAME='{table}' "
        "ORDER BY kcu.CONSTRAINT_NAME, kcu.ORDINAL_POSITION"
    )
    fks: dict[str, dict] = {}
    for r in mysql_query(sql, defaults):
        name, col, rtbl, rcol, upd, dele = r[0], r[1], r[2], r[3], r[4], r[5]
        if name not in fks:
            fks[name] = {
                "ref_table": rtbl, "update": upd, "delete": dele,
                "columns": [], "ref_columns": [],
            }
        fks[name]["columns"].append(col)
        fks[name]["ref_columns"].append(rcol)
    return fks


def show_create_table(defaults: str, db: str, table: str) -> str:
    """Recupere le DDL complet via SHOW CREATE TABLE.

    On passe en mode non-batch (pas de -B) pour preserver les newlines internes
    du DDL. -N vire le header. Format de sortie : "tablename<TAB>CREATE TABLE...".
    """
    cmd = [
        "mysql", f"--defaults-extra-file={defaults}", "-N",
        "-e", f"SHOW CREATE TABLE `{db}`.`{table}`",
    ]
    res = subprocess.run(cmd, check=True, capture_output=True, text=True)
    line = res.stdout.rstrip("\n")
    try:
        _, ddl = line.split("\t", 1)
    except ValueError:
        return ""
    # En sortie -N, les newlines internes du DDL sont echappees en "\n" litteral.
    return ddl.replace("\\n", "\n")


def col_ddl(name: str, c: dict) -> str:
    """Reconstruit un fragment DDL pour ADD COLUMN depuis INFORMATION_SCHEMA."""
    parts = [f"`{name}`", c["type"]]
    if not c["nullable"]:
        parts.append("NOT NULL")
    if c["default"] is not None:
        d = c["default"]
        upper = d.upper()
        # CURRENT_TIMESTAMP et expressions entre parentheses sont des
        # defaults "dynamiques" — pas de quote autour.
        if upper == "CURRENT_TIMESTAMP" or d.startswith("("):
            parts.append(f"DEFAULT {d}")
        elif upper == "NULL":
            parts.append("DEFAULT NULL")
        else:
            esc = d.replace("'", "''")
            parts.append(f"DEFAULT '{esc}'")
    elif c["nullable"]:
        parts.append("DEFAULT NULL")
    if "auto_increment" in (c["extra"] or "").lower():
        parts.append("AUTO_INCREMENT")
    if c["comment"]:
        esc = c["comment"].replace("'", "''")
        parts.append(f"COMMENT '{esc}'")
    return " ".join(parts)


def build_plan(defaults: str, source: str, target: str) -> list[str]:
    lines: list[str] = []
    src_tables = get_tables(defaults, source)
    tgt_tables = get_tables(defaults, target)

    # 1. Tables manquantes : CREATE TABLE integral depuis source.
    for t in sorted(src_tables - tgt_tables):
        ddl = show_create_table(defaults, source, t)
        lines.append(f"-- [CREATE] Table manquante : `{t}`")
        lines.append(ddl + ";")
        lines.append("")

    # 2. Tables presentes des deux cotes : diff colonnes / indexes / FKs.
    for t in sorted(src_tables & tgt_tables):
        src_cols = get_columns(defaults, source, t)
        tgt_cols = get_columns(defaults, target, t)

        missing = sorted(
            [c for c in src_cols if c not in tgt_cols],
            key=lambda c: src_cols[c]["pos"],
        )
        if missing:
            lines.append(f"-- [ALTER] Colonnes manquantes dans `{t}` :")
            for c in missing:
                info = src_cols[c]
                # AFTER : position relative — on cherche la colonne precedente
                # dans source qui existe aussi en target (sinon rien, append end).
                prev_in_target = [
                    cc for cc, vv in src_cols.items()
                    if vv["pos"] < info["pos"] and cc in tgt_cols
                ]
                after = ""
                if prev_in_target:
                    prev_name = max(prev_in_target, key=lambda cc: src_cols[cc]["pos"])
                    after = f" AFTER `{prev_name}`"
                lines.append(
                    f"ALTER TABLE `{t}` ADD COLUMN {col_ddl(c, info)}{after};"
                )
            lines.append("")

        # Indexes. On ignore PRIMARY : gere par les CREATE initiaux, et changer
        # la PK d'une table existante est destructif — hors scope d'un sync
        # additif.
        src_idx = get_indexes(defaults, source, t)
        tgt_idx = get_indexes(defaults, target, t)
        missing_idx = [i for i in src_idx if i not in tgt_idx and i != "PRIMARY"]
        if missing_idx:
            lines.append(f"-- [ALTER] Index manquants dans `{t}` :")
            for i in missing_idx:
                info = src_idx[i]
                cols = ", ".join(f"`{c}`" for c in info["columns"])
                kind = "UNIQUE INDEX" if info["unique"] else "INDEX"
                lines.append(f"ALTER TABLE `{t}` ADD {kind} `{i}` ({cols});")
            lines.append("")

        src_fk = get_foreign_keys(defaults, source, t)
        tgt_fk = get_foreign_keys(defaults, target, t)
        missing_fk = [f for f in src_fk if f not in tgt_fk]
        if missing_fk:
            lines.append(f"-- [ALTER] Foreign keys manquantes dans `{t}` :")
            for f in missing_fk:
                info = src_fk[f]
                cols = ", ".join(f"`{c}`" for c in info["columns"])
                rcols = ", ".join(f"`{c}`" for c in info["ref_columns"])
                lines.append(
                    f"ALTER TABLE `{t}` ADD CONSTRAINT `{f}` "
                    f"FOREIGN KEY ({cols}) REFERENCES `{info['ref_table']}` "
                    f"({rcols}) ON UPDATE {info['update']} "
                    f"ON DELETE {info['delete']};"
                )
            lines.append("")

        # Divergences sur colonnes existantes : reportees en commentaire seulement.
        # On ne corrige PAS automatiquement pour eviter un MODIFY COLUMN qui
        # tronquerait ou casterait silencieusement des donnees prod.
        for c in sorted(set(src_cols.keys()) & set(tgt_cols.keys())):
            s = src_cols[c]
            t2 = tgt_cols[c]
            if s["type"] != t2["type"] or s["nullable"] != t2["nullable"]:
                lines.append(
                    f"-- [DIVERGENCE] `{t}`.`{c}` non modifiee : "
                    f"source={s['type']} nullable={s['nullable']} | "
                    f"target={t2['type']} nullable={t2['nullable']}"
                )

    return lines


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, help="BDD schema de reference")
    ap.add_argument("--target", required=True, help="BDD a migrer (additive)")
    ap.add_argument("--defaults", default="/etc/ipastore/.mysql.cnf",
                    help="Fichier de defaults mysql (--defaults-extra-file)")
    ap.add_argument("--out", help="Ecrire le plan dans ce fichier au lieu de stdout")
    args = ap.parse_args()

    plan = build_plan(args.defaults, args.source, args.target)
    text = "\n".join(plan) if plan else "-- Rien a faire : schemas identiques"

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
