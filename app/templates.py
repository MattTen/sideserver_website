"""Shared Jinja2 templates instance."""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _format_size(n) -> str:
    n = float(n or 0)
    step = 1024.0
    for unit in ("o", "Kio", "Mio", "Gio"):
        if n < step:
            return f"{n:.1f} {unit}" if unit != "o" else f"{int(n)} {unit}"
        n /= step
    return f"{n:.1f} Tio"


def _format_date(d) -> str:
    if d is None:
        return "-"
    return d.strftime("%d/%m/%Y %H:%M")


templates.env.filters["size"] = _format_size
templates.env.filters["date"] = _format_date
