"""FastAPI application entry point."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import Config, ensure_dirs, load_secret_key
from .db import init_db
from .routes import apps as apps_routes
from .routes import auth as auth_routes
from .routes import dashboard as dashboard_routes
from .routes import public as public_routes
from .routes import settings as settings_routes
from .routes import updates as updates_routes
from .updates import get_status

logger = logging.getLogger(__name__)


async def _update_check_loop() -> None:
    """Background task: refresh update status every INTERVAL seconds."""
    interval = Config.UPDATE_CHECK_INTERVAL_SECONDS
    # First check shortly after startup (don't block request handling).
    await asyncio.sleep(30)
    while True:
        try:
            status = await asyncio.to_thread(get_status, True)
            logger.info(
                "update-check env=%s current=%s latest=%s available=%s rolling=%s",
                status.env, status.current, status.latest,
                status.update_available, status.rolling,
            )
        except Exception:
            logger.exception("update-check failed")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_update_check_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def create_app() -> FastAPI:
    ensure_dirs()
    load_secret_key()
    init_db()

    app = FastAPI(title="IPA Store", docs_url=None, redoc_url=None, lifespan=lifespan)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(public_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(dashboard_routes.router)
    app.include_router(apps_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(updates_routes.router)

    return app


app = create_app()
