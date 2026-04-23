"""FastAPI application entry point."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError

from .config import Config, ensure_dirs, load_secret_key
from .news_bg import ensure_news_bg
from .db import init_db
from .db_config import is_configured
from .routes import apps as apps_routes
from .routes import auth as auth_routes
from .routes import categories as categories_routes
from .routes import dashboard as dashboard_routes
from .routes import db_setup as db_setup_routes
from .routes import news as news_routes
from .routes import patches as patches_routes
from .routes import public as public_routes
from .routes import scinsta as scinsta_routes
from .routes import settings as settings_routes
from .routes import updates as updates_routes
from .scinsta import consume_build_result, integrate_build_result
from .seo import is_indexing_disabled, refresh_from_db as refresh_seo
from .source_token import refresh_from_db as refresh_source_token
from .updates import get_status
from .db import SessionLocal

logger = logging.getLogger(__name__)


class _IpaFormatter(logging.Formatter):
    """Format '[date] LEVEL name -- msg' avec 'uvicorn.error' remappe en
    'uvicorn' : uvicorn utilise 'uvicorn.error' pour tous les messages non-access
    (y compris startup/info), ce qui induit en erreur dans les logs."""

    _RENAME = {"uvicorn.error": "uvicorn"}

    def format(self, record: logging.LogRecord) -> str:
        record.name = self._RENAME.get(record.name, record.name)
        return super().format(record)


LOG_FILE = Path("/etc/ipastore") / "app.log"


def _configure_logging() -> None:
    fmt = "[%(asctime)s] %(levelname)s %(name)s -- %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = _IpaFormatter(fmt, datefmt=datefmt)

    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    else:
        for h in root.handlers:
            h.setFormatter(formatter)

    # RotatingFileHandler : 5 MB * 3 fichiers garde ~15 MB de logs max,
    # consultables via /settings/logs sans sortir du conteneur.
    try:
        from logging.handlers import RotatingFileHandler
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except Exception:
        logger.exception("RotatingFileHandler indisponible (logs UI desactives)")
        file_handler = None

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        for h in lg.handlers:
            h.setFormatter(formatter)
        if file_handler is not None:
            lg.addHandler(file_handler)


_configure_logging()


async def _wait_until_configured() -> None:
    """Bloque tant que la BDD n'est pas configurée via /setup/database.

    Les boucles d'arrière-plan (updates, scinsta result watcher) s'appuient
    dessus pour ne pas crasher quand l'app boot sans credentials.
    """
    while not is_configured():
        await asyncio.sleep(5)


async def _update_check_loop() -> None:
    """Background task: refresh update status every INTERVAL seconds."""
    interval = Config.UPDATE_CHECK_INTERVAL_SECONDS
    await _wait_until_configured()
    # First check shortly after startup (don't block request handling).
    await asyncio.sleep(30)
    while True:
        try:
            status = await asyncio.to_thread(get_status, True)
            logger.info(
                "update-check current=%s latest=%s available=%s",
                status.current, status.latest, status.update_available,
            )
        except Exception:
            logger.exception("update-check failed")
        await asyncio.sleep(interval)


def _process_scinsta_result() -> None:
    """Consomme le result file ecrit par le builder a la fin de son execution.

    Execute dans un thread via asyncio.to_thread : les Sessions SQLAlchemy
    n'aiment pas traverser les frontieres async.

    Si integrate_build_result leve une exception (bug applicatif, contrainte
    BDD, etc.), on bascule quand meme le status en "failed" pour debloquer
    l'UI — sinon elle reste figee sur "build en cours" indefiniment alors
    que le builder a termine depuis longtemps.
    """
    result = consume_build_result()
    if result is None:
        return
    db = SessionLocal()
    try:
        try:
            err = integrate_build_result(db, result)
        except Exception as e:
            logger.exception("scinsta integration crashed")
            db.rollback()
            from .source_gen import set_setting
            set_setting(db, "scinsta_last_build_at",
                        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            set_setting(db, "scinsta_last_build_status", "failed")
            set_setting(db, "scinsta_last_build_error",
                        f"Intégration échouée : {type(e).__name__}: {e}")
            db.commit()
            return
        if err:
            logger.error("scinsta integration failed: %s", err)
        else:
            logger.info("scinsta build integrated: %s", result.get("ipa_filename"))
    finally:
        db.close()


async def _scinsta_result_loop() -> None:
    """Watcher : toutes les 5s, integre un result file si present."""
    await _wait_until_configured()
    await asyncio.sleep(10)
    while True:
        try:
            await asyncio.to_thread(_process_scinsta_result)
        except Exception:
            logger.exception("scinsta result loop failed")
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    update_task = asyncio.create_task(_update_check_loop())
    scinsta_task = asyncio.create_task(_scinsta_result_loop())
    try:
        yield
    finally:
        for t in (update_task, scinsta_task):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


def create_app() -> FastAPI:
    ensure_dirs()
    load_secret_key()
    # init_db() n'est tenté que si la BDD est déjà configurée. Si ce n'est
    # pas le cas (premier démarrage), l'appel sera fait après POST /setup/database.
    if is_configured():
        try:
            init_db()
            db = SessionLocal()
            try:
                refresh_seo(db)
                refresh_source_token(db)
            finally:
                db.close()
        except Exception:
            logger.exception("init_db() a échoué au boot — la page /setup/database restera accessible")
    static_dir = Path(__file__).resolve().parent.parent / "static"
    ensure_news_bg(static_dir)

    app = FastAPI(title="IPA Store", docs_url=None, redoc_url=None, lifespan=lifespan)

    # BDD injoignable : on log UNE ligne courte ("BDD injoignable: timeout
    # sur 192.168.0.212") plutot que le traceback SQLAlchemy complet (plus
    # de 100 lignes par requete sinon). L'UI recoit un 503 clair.
    @app.exception_handler(OperationalError)
    async def _db_unreachable_handler(request: Request, exc: OperationalError):
        orig = getattr(exc, "orig", None)
        detail = None
        if orig is not None and getattr(orig, "args", None):
            a = orig.args
            if len(a) >= 2 and isinstance(a[0], int):
                detail = f"MySQL {a[0]}: {a[1]}"
            else:
                detail = str(orig)
        else:
            detail = str(exc).splitlines()[0]
        logger.error("Database unavailable (%s %s) -- %s", request.method, request.url.path, detail)
        return JSONResponse(
            {"error": "Database unavailable", "detail": detail},
            status_code=503,
        )

    # Middleware de garde : tant que la BDD n'est pas configurée, seules les
    # routes publiques (static, setup/database) sont accessibles.
    @app.middleware("http")
    async def _db_setup_guard(request, call_next):
        if is_configured():
            return await call_next(request)
        path = request.url.path
        allowed_prefixes = ("/setup/database", "/static/", "/favicon")
        if any(path.startswith(p) for p in allowed_prefixes):
            return await call_next(request)
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/setup/database", status_code=303)

    # Ajoute X-Robots-Tag: noindex sur toutes les reponses quand l'option
    # "desactiver l'indexation" est active dans Reglages. S'applique aussi
    # aux static files, IPAs, icones, bref tout ce qui sort du conteneur.
    @app.middleware("http")
    async def _noindex_headers(request, call_next):
        resp = await call_next(request)
        if is_indexing_disabled():
            resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"
        return resp

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Store assets (IPAs + icons + screenshots) are served as static files from
    # the shared volume. SideStore clients fetch these URLs directly from source.json.
    Config.IPAS_DIR.mkdir(parents=True, exist_ok=True)
    Config.ICONS_DIR.mkdir(parents=True, exist_ok=True)
    Config.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    Config.NEWS_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/ipas", StaticFiles(directory=str(Config.IPAS_DIR)), name="ipas")
    app.mount("/icons", StaticFiles(directory=str(Config.ICONS_DIR)), name="icons")
    app.mount("/screenshots", StaticFiles(directory=str(Config.SCREENSHOTS_DIR)), name="screenshots")
    # /news-img sert les visuels d'articles publiés dans source.json.news[].imageURL
    app.mount("/news-img", StaticFiles(directory=str(Config.NEWS_DIR)), name="news-img")

    app.include_router(db_setup_routes.router)
    app.include_router(public_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(dashboard_routes.router)
    app.include_router(apps_routes.router)
    app.include_router(news_routes.router)
    app.include_router(categories_routes.router)
    app.include_router(patches_routes.router)
    app.include_router(scinsta_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(updates_routes.router)

    return app


app = create_app()
