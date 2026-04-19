"""Fonds d'actualités prédéfinis : dégradés PNG générés en pur Python stdlib."""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

# Chaque preset définit :
#   label   : nom affiché dans le sélecteur
#   c1/c2   : couleurs RGB haut/bas pour le PNG
#   css     : gradient CSS pour le preview admin (plus riche que le PNG)
#   tint    : hex 6 chars utilisé comme tintColor dans source.json
PRESETS: dict[str, dict] = {
    "midnight": {
        "label": "Minuit",
        "c1": (10, 10, 26),   "c2": (30, 28, 60),
        "css": "linear-gradient(150deg,#0a0a1a,#1e1c3c)",
        "tint": "1e1c3c",
    },
    "aurora": {
        "label": "Aurore",
        "c1": (10, 28, 36),   "c2": (18, 64, 90),
        "css": "linear-gradient(150deg,#0a1c24,#12405a)",
        "tint": "12405a",
    },
    "ember": {
        "label": "Braise",
        "c1": (22, 8, 5),     "c2": (110, 34, 12),
        "css": "linear-gradient(150deg,#160805,#6e220c)",
        "tint": "6e220c",
    },
    "forest": {
        "label": "Forêt",
        "c1": (8, 30, 24),    "c2": (16, 76, 58),
        "css": "linear-gradient(150deg,#081e18,#104c3a)",
        "tint": "104c3a",
    },
    "royal": {
        "label": "Royal",
        "c1": (16, 8, 42),    "c2": (52, 18, 106),
        "css": "linear-gradient(150deg,#10082a,#34126a)",
        "tint": "34126a",
    },
    "slate": {
        "label": "Ardoise",
        "c1": (16, 16, 20),   "c2": (38, 38, 52),
        "css": "linear-gradient(150deg,#101014,#262634)",
        "tint": "262634",
    },
    "ocean": {
        "label": "Océan",
        "c1": (4, 14, 42),    "c2": (8, 50, 112),
        "css": "linear-gradient(150deg,#040e2a,#083270)",
        "tint": "083270",
    },
    "garnet": {
        "label": "Grenat",
        "c1": (36, 10, 26),   "c2": (102, 24, 56),
        "css": "linear-gradient(150deg,#240a1a,#661838)",
        "tint": "661838",
    },
}


def _make_gradient_png(c1: tuple[int, int, int], c2: tuple[int, int, int],
                       w: int = 600, h: int = 240) -> bytes:
    """Génère un PNG dégradé top→bottom en pur stdlib (struct + zlib)."""
    def lerp(a: int, b: int, t: float) -> int:
        return round(a + (b - a) * t)

    raw = bytearray()
    for y in range(h):
        t = y / max(h - 1, 1)
        r, g, b = lerp(c1[0], c2[0], t), lerp(c1[1], c2[1], t), lerp(c1[2], c2[2], t)
        raw += b'\x00' + bytes([r, g, b] * w)

    def png_chunk(tag: bytes, data: bytes) -> bytes:
        payload = tag + data
        return (struct.pack('>I', len(data))
                + payload
                + struct.pack('>I', zlib.crc32(payload) & 0xFFFFFFFF))

    sig  = b'\x89PNG\r\n\x1a\n'
    ihdr = png_chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
    idat = png_chunk(b'IDAT', zlib.compress(bytes(raw), level=6))
    iend = png_chunk(b'IEND', b'')
    return sig + ihdr + idat + iend


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    payload = tag + data
    return (struct.pack('>I', len(data))
            + payload
            + struct.pack('>I', zlib.crc32(payload) & 0xFFFFFFFF))


def _make_color_wheel_png(size: int = 128) -> bytes:
    """PNG RGBA : roue chromatique HSV (teinte autour, saturation du centre
    vers le bord, valeur=1). Hors du cercle inscrit = transparent → border-radius
    CSS n'entoure pas les coins."""
    cx = cy = (size - 1) / 2
    r_max = size / 2
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter byte (None) pour chaque scanline
        for x in range(size):
            dx = x - cx
            dy = y - cy
            dist = math.hypot(dx, dy)
            if dist > r_max:
                raw += b'\x00\x00\x00\x00'
                continue
            angle = math.degrees(math.atan2(dy, dx))
            if angle < 0:
                angle += 360
            h = angle / 60.0
            s = min(dist / r_max, 1.0)
            c = s                      # v = 1 → c = v*s = s
            x_ = c * (1 - abs(h % 2 - 1))
            m = 1.0 - c                # v - c
            if h < 1:
                r, g, b = c, x_, 0
            elif h < 2:
                r, g, b = x_, c, 0
            elif h < 3:
                r, g, b = 0, c, x_
            elif h < 4:
                r, g, b = 0, x_, c
            elif h < 5:
                r, g, b = x_, 0, c
            else:
                r, g, b = c, 0, x_
            raw += bytes([
                round((r + m) * 255),
                round((g + m) * 255),
                round((b + m) * 255),
                255,
            ])
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = _png_chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))
    idat = _png_chunk(b'IDAT', zlib.compress(bytes(raw), level=6))
    iend = _png_chunk(b'IEND', b'')
    return sig + ihdr + idat + iend


def ensure_news_bg(static_dir: Path) -> None:
    """Génère les PNGs manquants dans static/news-bg/ au démarrage du serveur."""
    dest = static_dir / "news-bg"
    dest.mkdir(exist_ok=True)
    for key, p in PRESETS.items():
        path = dest / f"{key}.png"
        if not path.exists():
            path.write_bytes(_make_gradient_png(p["c1"], p["c2"]))
    # Header par défaut du store : dégradé violet sombre (royal), large format
    header = static_dir / "store-header.png"
    if not header.exists():
        header.write_bytes(_make_gradient_png((16, 8, 42), (52, 18, 106), w=1200, h=340))
    # Roue chromatique pour le swatch « teinte personnalisée »
    wheel = static_dir / "color-wheel.png"
    if not wheel.exists():
        wheel.write_bytes(_make_color_wheel_png(128))
