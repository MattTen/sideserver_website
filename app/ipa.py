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
import struct
import zipfile
import zlib
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


_PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


def _is_standard_png(data: bytes) -> bool:
    """True si les bytes sont un PNG decodable par un navigateur desktop
    (Standard PNG : magic + chunk IHDR en premier).
    Les PNG iOS CgBI ont un chunk CgBI avant IHDR -> retourne False ici.
    """
    if len(data) < 16:
        return False
    if data[:8] != _PNG_MAGIC:
        return False
    return data[12:16] == b'IHDR'


def _cgbi_to_standard_png(data: bytes) -> Optional[bytes]:
    """Convertit un PNG iOS optimise CgBI en PNG standard.

    Differences CgBI vs PNG standard :
      - Chunk 'CgBI' ajoute avant IHDR (a strip)
      - IDAT compresse en raw deflate (pas zlib-wrapped) -> wbits=-15
      - Pixels stockes en BGR(A) au lieu de RGB(A) -> swap canaux R<->B
      - Alpha pre-multiplie : R = R_orig * A / 255 -> il faut diviser

    Etapes : parse chunks, decompress raw IDAT, unfilter scanlines, swap
    B/R + un-premultiply alpha, refilter (filter=None), recompress avec
    zlib standard, reassemble PNG.

    Retourne None si :
      - pas un PNG (ou pas CgBI)
      - format non supporte (paletted, grayscale, bit_depth != 8)
      - decompression / parse echoue
    """
    if data[:8] != _PNG_MAGIC:
        return None

    chunks: list[tuple[bytes, bytes]] = []
    has_cgbi = False
    pos = 8
    while pos + 12 <= len(data):
        ln = struct.unpack('>I', data[pos:pos+4])[0]
        ct = data[pos+4:pos+8]
        cd = data[pos+8:pos+8+ln]
        pos += 12 + ln
        if ct == b'CgBI':
            has_cgbi = True
            continue
        chunks.append((ct, cd))
        if ct == b'IEND':
            break

    if not has_cgbi:
        return None  # deja standard, rien a faire

    ihdr = next((cd for ct, cd in chunks if ct == b'IHDR'), None)
    if ihdr is None or len(ihdr) < 13:
        return None
    width = struct.unpack('>I', ihdr[0:4])[0]
    height = struct.unpack('>I', ihdr[4:8])[0]
    bit_depth = ihdr[8]
    color_type = ihdr[9]
    if bit_depth != 8 or color_type not in (2, 6):
        return None  # on gere RGB et RGBA 8-bit uniquement
    bpp = 4 if color_type == 6 else 3
    has_alpha = (color_type == 6)

    idat_combined = b''.join(cd for ct, cd in chunks if ct == b'IDAT')
    try:
        raw = zlib.decompress(idat_combined, wbits=-15)  # raw deflate (CgBI)
    except zlib.error:
        try:
            raw = zlib.decompress(idat_combined)  # fallback zlib-wrapped
        except zlib.error:
            return None

    stride = width * bpp + 1  # +1 filter byte / row
    if len(raw) < stride * height:
        return None

    new_rows = bytearray()
    prev_row = bytearray(stride - 1)
    for y in range(height):
        off = y * stride
        ftype = raw[off]
        row = bytearray(raw[off+1:off+stride])

        # Unfilter (cf. PNG spec section 6).
        if ftype == 0:
            pass
        elif ftype == 1:  # Sub : recon = filt + recon[a]
            for i in range(bpp, len(row)):
                row[i] = (row[i] + row[i - bpp]) & 0xFF
        elif ftype == 2:  # Up : recon = filt + recon[b]
            for i in range(len(row)):
                row[i] = (row[i] + prev_row[i]) & 0xFF
        elif ftype == 3:  # Average : recon = filt + (recon[a] + recon[b]) // 2
            for i in range(len(row)):
                left = row[i - bpp] if i >= bpp else 0
                up = prev_row[i]
                row[i] = (row[i] + (left + up) // 2) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(len(row)):
                a = row[i - bpp] if i >= bpp else 0
                b = prev_row[i]
                c = prev_row[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                if pa <= pb and pa <= pc:
                    pred = a
                elif pb <= pc:
                    pred = b
                else:
                    pred = c
                row[i] = (row[i] + pred) & 0xFF
        else:
            return None

        # Swap B<->R + un-premultiply alpha (CgBI utilise premultiplied).
        for x in range(width):
            px = x * bpp
            b_ch, g_ch, r_ch = row[px], row[px+1], row[px+2]
            if has_alpha:
                a_ch = row[px+3]
                if a_ch != 0 and a_ch != 255:
                    # +a//2 = arrondi a l'entier le plus proche
                    r_ch = min(255, (r_ch * 255 + a_ch // 2) // a_ch)
                    g_ch = min(255, (g_ch * 255 + a_ch // 2) // a_ch)
                    b_ch = min(255, (b_ch * 255 + a_ch // 2) // a_ch)
                row[px], row[px+1], row[px+2], row[px+3] = r_ch, g_ch, b_ch, a_ch
            else:
                row[px], row[px+1], row[px+2] = r_ch, g_ch, b_ch

        # Reecrit avec filter=None (pas de difference de qualite, juste
        # taille -- l'icone fait quelques Ko, ca ne pese pas).
        new_rows.append(0)
        new_rows.extend(row)
        prev_row = row

    new_idat = zlib.compress(bytes(new_rows), level=6)

    out = bytearray(_PNG_MAGIC)
    written_idat = False
    for ct, cd in chunks:
        if ct == b'IDAT':
            if written_idat:
                continue
            cd = new_idat
            written_idat = True
        out += struct.pack('>I', len(cd))
        out += ct
        out += cd
        out += struct.pack('>I', zlib.crc32(ct + cd) & 0xFFFFFFFF)
    return bytes(out)


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
    cgbi_fallback: Optional[bytes] = None  # converti en cas d'echec d'un standard

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
                # Format CgBI iOS (illisible cote desktop) -> on tente
                # une conversion en PNG standard. On garde la premiere
                # conversion reussie comme fallback : on continue d'abord
                # de chercher un PNG deja standard (priorite a un fichier
                # natif si l'IPA en contient un).
                if cgbi_fallback is None:
                    converted = _cgbi_to_standard_png(data)
                    if converted is not None:
                        cgbi_fallback = converted
                break

    return cgbi_fallback


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
