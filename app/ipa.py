"""IPA file parsing: extract Info.plist metadata + app icon."""
from __future__ import annotations

import hashlib
import plistlib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(slots=True)
class IPAInfo:
    bundle_id: str
    name: str
    version: str
    build_version: str
    min_os_version: str
    icon_bytes: Optional[bytes] = None


def _find_app_dir(zf: zipfile.ZipFile) -> Optional[str]:
    for name in zf.namelist():
        if name.startswith("Payload/") and name.endswith(".app/Info.plist"):
            if name.count("/") == 2:
                return name.rsplit("/", 1)[0] + "/"
    return None


def _extract_icon(zf: zipfile.ZipFile, app_dir: str, plist: dict) -> Optional[bytes]:
    """Best-effort icon extraction from Info.plist CFBundleIcons."""
    candidates: list[str] = []
    icons = plist.get("CFBundleIcons") or {}
    primary = icons.get("CFBundlePrimaryIcon") or {}
    files = primary.get("CFBundleIconFiles") or []
    if isinstance(files, list):
        candidates.extend(files)
    for f in files:
        for suffix in ("@3x.png", "@2x.png", ".png"):
            candidates.append(f + suffix)

    # Generic fallbacks often found in IPAs
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
                    return zf.read(n)
                except KeyError:
                    continue
    return None


def parse_ipa(path: Path) -> Optional[IPAInfo]:
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
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
