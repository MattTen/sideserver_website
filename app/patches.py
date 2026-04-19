"""Découverte et exécution des scripts de patch IPA.

Les patchs sont des scripts Python placés dans patch/ à la racine du repo
et copiés dans l'image Docker (voir Dockerfile). Chaque patch doit respecter
la signature CLI :

    script.py -s /chemin/vers/app.ipa

et écraser l'IPA en place. Le découverte est faite à chaque requête : ajouter
un .py dans patch/ sur GitHub + git pull + rebuild rend le patch dispo sans
modif de code côté app.

Le nom d'affichage personnalisé est stocké en settings avec la clé
`patch_display_name:{filename}`. Si aucun nom n'est défini, on retourne le
stem du fichier (ex: "fix_ipa.py" → "fix_ipa").
"""
from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from .source_gen import get_setting, set_setting

logger = logging.getLogger(__name__)

# Le dossier patch/ est copié dans l'image à /opt/ipastore/patch (COPY Dockerfile).
# En dev local, c'est le dossier patch/ du repo.
PATCH_DIR = Path(__file__).resolve().parent.parent / "patch"


@dataclass(slots=True)
class PatchInfo:
    filename: str           # "fix_ipa.py"
    display_name: str       # nom affiché dans l'UI (éditable)
    path: Path              # chemin absolu vers le script


def _display_key(filename: str) -> str:
    return f"patch_display_name:{filename}"


def discover_patches(db: Session) -> list[PatchInfo]:
    """Liste les scripts .py dans PATCH_DIR (triés alphabétiquement).

    Un fichier est considéré comme patch si :
      - extension .py
      - n'est pas un __init__.py ou fichier caché
      - est directement dans patch/ (pas de récursion)
    """
    if not PATCH_DIR.is_dir():
        return []
    patches: list[PatchInfo] = []
    for p in sorted(PATCH_DIR.iterdir()):
        if not p.is_file() or p.suffix != ".py":
            continue
        if p.name.startswith(("_", ".")):
            continue
        display = get_setting(db, _display_key(p.name), "") or p.stem
        patches.append(PatchInfo(filename=p.name, display_name=display, path=p))
    return patches


def get_patch(db: Session, filename: str) -> PatchInfo | None:
    """Retourne le PatchInfo d'un filename donné (sécurisé contre path traversal)."""
    # Path traversal guard : on refuse tout filename contenant des séparateurs
    # ou commençant par un point. Seul un basename plat est accepté.
    if "/" in filename or "\\" in filename or filename.startswith(".") or ".." in filename:
        return None
    p = PATCH_DIR / filename
    if not p.is_file() or p.suffix != ".py":
        return None
    display = get_setting(db, _display_key(filename), "") or p.stem
    return PatchInfo(filename=filename, display_name=display, path=p)


def set_display_name(db: Session, filename: str, name: str) -> None:
    set_setting(db, _display_key(filename), name.strip())


def run_patch(patch_path: Path, ipa_path: Path, timeout: int = 900) -> tuple[bool, str]:
    """Exécute un patch sur un IPA. Retourne (succes, output combiné stdout/stderr).

    Le script est invoqué avec le même interpréteur Python que le serveur
    (sys.executable) pour bénéficier du venv / deps installées. Le timeout
    par défaut (15 min) couvre les gros IPAs (Instagram 270 Mo).
    """
    cmd = [sys.executable, str(patch_path), "-s", str(ipa_path)]
    logger.info("running patch: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout après {timeout}s"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out
