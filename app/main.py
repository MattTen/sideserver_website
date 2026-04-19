"""FastAPI application entry point."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .config import ensure_dirs, load_secret_key
from .db import init_db
from .routes import apps as apps_routes
from .routes import auth as auth_routes
from .routes import dashboard as dashboard_routes
from .routes import public as public_routes
from .routes import settings as settings_routes


def create_app() -> FastAPI:
    ensure_dirs()
    load_secret_key()
    init_db()

    app = FastAPI(title="IPA Store", docs_url=None, redoc_url=None)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(public_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(dashboard_routes.router)
    app.include_router(apps_routes.router)
    app.include_router(settings_routes.router)

    return app


app = create_app()
