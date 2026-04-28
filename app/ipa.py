"""Analyse des fichiers IPA : extraction des métadonnées Info.plist et de l'icône.

Un IPA est une archive ZIP. Sa structure est :
  Payload/
    MonApp.app/
      Info.plist   ← métadonnées (bundle_id, version, icônes…)
      AppIcon*.png ← icônes (noms variés selon la version du SDK)
"""
from __future__ import annotations

import hashlib
import plistlib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(slots=True)
class IPAInfo:
    """Métadonnées extraites d'un fichier IPA."""
    bundle_id: str
    name: str
    version: str
    build_version: str
    min_os_version: str
    icon_bytes: Optional[bytes] = None  # None si aucune icône trouvée dans l'archive


def _find_app_dir(zf: zipfile.ZipFile) -> Optional[str]:
    """Localise le dossier .app principal dans Payload/.

    On vérifie que le chemin a exactement 2 séparateurs (Payload/Foo.app/Info.plist)
    pour ignorer les .app imbriqués (ex: extensions, frameworks).
    """
    for name in zf.namelist():
        if name.startswith("Payload/") and name.endswith(".app/Info.plist"):
            if name.count("/") == 2:
                return name.rsplit("/", 1)[0] + "/"
    return None


def _is_standard_png(data: bytes) -> bool:
    """Filtre les PNG qui peuvent etre rendus par un navigateur desktop.

    Xcode optimise les icones d'app iOS en format Apple "CgBI" (BGR au lieu
    de RGB + chunk 'CgBI' ajoute avant IHDR). Ces PNG sont valides cote iOS
    (SideStore les rend nativement) mais Firefox/Chrome/Safari desktop les
    affichent en image cassee ou vide. On les detecte pour skipper et
    essayer un autre candidat ou retomber sur l'icone par defaut.

    Standard PNG : magic + chunk IHDR en premier.
    CgBI PNG     : magic + chunk CgBI + chunk IHDR.
    """
    if len(data) < 16:
        return False
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        return False
    return data[12:16] == b'IHDR'


def _extract_icon(zf: zipfile.ZipFile, app_dir: str, plist: dict) -> Optional[bytes]:
    """Extraction best-effort de l'icône principale.

    Stratégie : d'abord les noms déclarés dans CFBundleIcons > CFBundlePrimaryIcon
    (avec suffixes @3x, @2x, .png), puis une liste de noms génériques courants
    dans les IPA modernes et anciens. On skipe les fichiers en format Apple
    CgBI (illisibles cote desktop) pour essayer un candidat alternatif.
    Retourne None si aucun candidat n'est un PNG standard.
    """
    candidates: list[str] = []

    # Noms déclarés dans le plist (priorité maximale).
    icons = plist.get("CFBundleIcons") or {}
    primary = icons.get("CFBundlePrimaryIcon") or {}
    files = primary.get("CFBundleIconFiles") or []
    if isinstance(files, list):
        candidates.extend(files)
        for f in files:
            for suffix in ("@3x.png", "@2x.png", ".png"):
                candidates.append(f + suffix)

    # Noms génériques présents dans la majorité des IPA iOS modernes.
    candidates.extend([
        "AppIcon60x60@3x.png",
        "AppIcon60x60@2x.png",
        "AppIcon76x76@2x~ipad.png",
        "Icon-60@3x.png",
        "Icon-60@2x.png",
        "Icon.png",
    ])

    tried: set[str] = set()
    names = zf.namelist()
    for c in candidates:
        if c in tried:
            continue
        tried.add(c)
        for n in names:
            if n.startswith(app_dir) and n.endswith(c):
                try:
                    data = zf.read(n)
                except KeyError:
                    continue
                if _is_standard_png(data):
                    return data
                # Pas un PNG standard (probablement CgBI iOS) -- on continue
                # a chercher un autre candidat. Si tout est CgBI, on retourne
                # None et l'admin uploadera l'icone manuellement.
                break
    return None


def parse_ipa(path: Path) -> Optional[IPAInfo]:
    """Parse un fichier IPA et retourne ses métadonnées, ou None si invalide."""
    try:
        with zipfile.ZipFile(path) as zf:
            app_dir = _find_app_dir(zf)
            if not app_dir:
                return None
            with zf.open(app_dir + "Info.plist") as f:
                plist = plistlib.load(f)
            icon = _extract_icon(zf, app_dir, plist)
    except (zipfile.BadZipFile, KeyError, plistlib.InvalidFileException, OSError):
        return None

    bundle_id = plist.get("CFBundleIdentifier") or ""
    if not bundle_id:
        return None

    return IPAInfo(
        bundle_id=bundle_id,
        name=(plist.get("CFBundleDisplayName") or plist.get("CFBundleName") or bundle_id),
        version=str(plist.get("CFBundleShortVersionString", "0.0.0")),
        build_version=str(plist.get("CFBundleVersion", "1")),
        min_os_version=str(plist.get("MinimumOSVersion", "14.0")),
        icon_bytes=icon,
    )


def sha256_of_file(path: Path) -> str:
    """Calcule le SHA-256 d'un fichier par blocs de 1 Mo (compatible fichiers volumineux)."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
