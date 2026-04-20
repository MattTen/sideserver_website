"""SCInsta builder : check decrypt.day + pipeline de build via conteneur.

Flux :
  1. L'admin upload une IPA Instagram officielle (recuperee manuellement sur
     decrypt.day) via le formulaire web.
  2. L'IPA est deposee dans /etc/ipastore/scinsta-upload-<env>.ipa.
  3. Un flag-file /etc/ipastore/scinsta-build-requested-<env> contenant le
     nom du patch optionnel a appliquer est ecrit.
  4. systemd (path unit) detecte le flag et lance le conteneur builder qui :
     - clone SoCuul/SCInsta main (fresh a chaque build)
     - `./build.sh sideload` (Theos compile dylibs + cyan inject + ipapatch)
     - applique le patch optionnel choisi
     - depose le resultat dans /srv/store/ipas/
     - ecrit /etc/ipastore/scinsta-build-result-<env>
  5. Le watcher du conteneur web lit le result, cree App Instagram + Version.

Cles settings utilisees (clefs/valeurs, aucune migration BDD) :
- `scinsta_decrypt_url`          — URL source editable (defaut decrypt.day IG)
- `scinsta_ig_version_latest`    — derniere version vue sur decrypt.day
- `scinsta_last_check_at`        — ISO timestamp du dernier check version
- `scinsta_last_check_error`     — raison si le check a echoue
- `scinsta_last_build_at`        — ISO timestamp du dernier build termine
- `scinsta_last_build_status`    — idle|requested|running|success|failed
- `scinsta_last_build_error`     — message si failed
- `scinsta_last_build_ipa`       — filename du dernier IPA produit
- `scinsta_last_build_patch`     — filename du patch applique (ou vide)
- `scinsta_last_build_scinsta_sha` — short SHA du commit SCInsta clone
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from .config import Config
from .source_gen import get_setting, set_setting

logger = logging.getLogger(__name__)

DECRYPT_URL_DEFAULT = "https://decrypt.day/app/id389801252"
INSTAGRAM_BUNDLE_ID = "com.burbn.instagram"


def get_decrypt_url(db: Session) -> str:
    """URL source pour le check de version. Editable via l'UI."""
    return get_setting(db, "scinsta_decrypt_url", "") or DECRYPT_URL_DEFAULT


def set_decrypt_url(db: Session, url: str) -> None:
    set_setting(db, "scinsta_decrypt_url", url.strip())
    db.commit()

# Le parser du HTML decrypt.day cible le microdata schema.org softwareVersion.
# Pattern tolerant aux attributs supplementaires entre itemprop et le contenu.
_SOFTWARE_VERSION_RE = re.compile(
    r'itemprop=["\']softwareVersion["\'][^>]*>([^<]+)<',
    re.IGNORECASE,
)
_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*")

# Marqueurs qui identifient SEULEMENT les pages de challenge/block Cloudflare.
# Attention : "challenge-platform" et "cf-beacon" apparaissent aussi dans des
# reponses 200 legitimes (scripts embarques pour la telemetrie CF) — les
# exclure d'ici evite les faux positifs.
_CF_CHALLENGE_MARKERS = (
    "just a moment",          # titre de la page "Un instant..."
    "attention required",     # titre de la page de block
    "checking your browser",  # ancien wording des challenges
    "cf-chl-bypass",           # meta refresh des challenges
)

# Liste d'impersonations tentees dans l'ordre. curl_cffi accepte "chrome"
# (alias vers la plus recente), puis on retombe sur des versions figees au cas
# ou celle par defaut serait refusee par le WAF du jour.
_IMPERSONATIONS = ("chrome", "chrome131", "safari17_0", "firefox133")


def _parse_version(v: str) -> tuple[int, ...]:
    if not v:
        return ()
    s = v.strip().lstrip("vV")
    m = _VERSION_RE.match(s)
    if not m:
        return ()
    return tuple(int(x) for x in m.group(0).split("."))


def version_gt(a: str, b: str) -> bool:
    if not a:
        return False
    if not b:
        return True
    return _parse_version(a) > _parse_version(b)


# === chemins partages avec le conteneur builder (monte dans /etc/ipastore) ===

def _build_flag() -> Path:
    return Config.IPASTORE_ETC / f"scinsta-build-requested-{Config.ENV_NAME}"


def _upload_file() -> Path:
    """Chemin de l'IPA Instagram uploadee (consommee par le builder)."""
    return Config.IPASTORE_ETC / f"scinsta-upload-{Config.ENV_NAME}.ipa"


def _build_progress() -> Path:
    return Config.IPASTORE_ETC / f"scinsta-build-progress-{Config.ENV_NAME}"


def _build_result() -> Path:
    return Config.IPASTORE_ETC / f"scinsta-build-result-{Config.ENV_NAME}"


def _cancel_flag() -> Path:
    """Flag-file ecrit pour demander l'arret d'un build en cours.

    Consomme par ipastore-scinsta-cancel@<env>.path -> service qui fait
    docker kill sur scinsta-builder-<env> + ecrit un result failed.
    """
    return Config.IPASTORE_ETC / f"scinsta-build-cancel-{Config.ENV_NAME}"


def _build_log_file() -> Path:
    """Fichier log temps reel du conteneur builder (tee de stdout/stderr).

    Ecrit par tools/scinsta-builder/build.py via _install_log_tee().
    L'UI le poll via /scinsta/logs?offset=N pour afficher la sortie en direct.
    """
    return Config.IPASTORE_ETC / f"scinsta-build-log-{Config.ENV_NAME}.txt"


def clear_build_log() -> None:
    """Truncate le fichier log (bouton Effacer de l'UI).

    Le fichier est re-truncate de toute facon au prochain build par
    cmd_scinsta_build, mais ce helper permet a l'admin de faire le menage
    manuellement entre deux builds sans avoir a relancer.
    """
    path = _build_log_file()
    if path.exists():
        try:
            path.write_text("", encoding="utf-8")
        except OSError as e:
            logger.warning("scinsta: clear log failed: %s", e)


def read_build_log(offset: int = 0) -> dict:
    """Lit le log de build a partir d'un offset.

    Retour : {content, next_offset, size}. Si le fichier n'existe pas ou si
    l'offset depasse la taille actuelle (cas d'une reinitialisation du log
    entre deux polls), on remet offset=0 pour renvoyer tout le contenu.
    """
    path = _build_log_file()
    if not path.exists():
        return {"content": "", "next_offset": 0, "size": 0}
    try:
        size = path.stat().st_size
        # Si le fichier a ete tronque entre deux polls (nouveau build), on
        # repart de zero. Sinon on lit seulement le delta pour pas renvoyer
        # plusieurs dizaines de Mo a chaque tick.
        if offset > size:
            offset = 0
        with path.open("rb") as f:
            f.seek(offset)
            data = f.read()
        content = data.decode("utf-8", errors="replace")
        return {"content": content, "next_offset": offset + len(data), "size": size}
    except OSError as e:
        logger.warning("scinsta: lecture log failed: %s", e)
        return {"content": "", "next_offset": offset, "size": 0}


@dataclass
class ScinstaState:
    ig_deployed: Optional[str]
    ig_latest: Optional[str]
    last_check_at: Optional[str]
    last_check_error: Optional[str]
    last_build_at: Optional[str]
    last_build_status: Optional[str]
    last_build_error: Optional[str]
    last_build_ipa: Optional[str]
    last_build_patch: Optional[str]
    last_build_scinsta_sha: Optional[str]
    decrypt_url: str = DECRYPT_URL_DEFAULT
    build_progress_step: Optional[str] = None
    upload_ready: bool = False                # une IPA est deja en attente
    upload_version: Optional[str] = None      # version lue dans l'Info.plist de l'upload
    ig_update_available: bool = field(default=False)

    @property
    def is_running(self) -> bool:
        return self.last_build_status in ("requested", "running")

    def to_dict(self) -> dict:
        return {
            "ig_deployed": self.ig_deployed,
            "ig_latest": self.ig_latest,
            "last_check_at": self.last_check_at,
            "last_check_error": self.last_check_error,
            "last_build_at": self.last_build_at,
            "last_build_status": self.last_build_status,
            "last_build_error": self.last_build_error,
            "last_build_ipa": self.last_build_ipa,
            "last_build_patch": self.last_build_patch,
            "last_build_scinsta_sha": self.last_build_scinsta_sha,
            "decrypt_url": self.decrypt_url,
            "build_progress_step": self.build_progress_step,
            "is_running": self.is_running,
            "upload_ready": self.upload_ready,
            "upload_version": self.upload_version,
            "ig_update_available": self.ig_update_available,
        }


def _read_upload_version() -> Optional[str]:
    """Lit CFBundleShortVersionString dans l'IPA upload en attente.

    Permet d'afficher dans l'UI "IPA prête : Vx.x.x" — utile pour verifier
    qu'on s'apprete a builder la bonne version (surtout en cas d'echec
    precedent ou si l'admin a upload plusieurs fois).
    """
    import plistlib
    import zipfile

    path = _upload_file()
    if not path.exists():
        return None
    try:
        with zipfile.ZipFile(path) as zf:
            info_name = next(
                (n for n in zf.namelist()
                 if n.startswith("Payload/") and n.endswith(".app/Info.plist")
                 and n.count("/") == 2),
                None,
            )
            if not info_name:
                return None
            with zf.open(info_name) as f:
                plist = plistlib.load(f)
        v = str(plist.get("CFBundleShortVersionString") or "").strip()
        return v or None
    except Exception:  # pragma: no cover - lecture best-effort
        return None


def _latest_instagram_version_in_store(db: Session) -> Optional[str]:
    """Retourne la derniere version de com.burbn.instagram presente dans le
    store (ordonnee par uploaded_at desc).

    Source de verite pour "version deployee" : la BDD, pas la clef settings.
    La clef scinsta_ig_version_deployed n'est ecrite qu'au bout d'un build
    SCInsta — si l'admin a uploade l'IPA manuellement via l'onglet Apps,
    elle reste vide. On requete donc directement la table versions.
    """
    from .models import App, Version

    app = db.query(App).filter(App.bundle_id == INSTAGRAM_BUNDLE_ID).first()
    if app is None:
        return None
    ver = (
        db.query(Version)
        .filter(Version.app_id == app.id)
        .order_by(Version.uploaded_at.desc())
        .first()
    )
    return ver.version if ver else None


def get_state(db: Session) -> ScinstaState:
    state = ScinstaState(
        ig_deployed=_latest_instagram_version_in_store(db),
        ig_latest=get_setting(db, "scinsta_ig_version_latest", "") or None,
        last_check_at=get_setting(db, "scinsta_last_check_at", "") or None,
        last_check_error=get_setting(db, "scinsta_last_check_error", "") or None,
        last_build_at=get_setting(db, "scinsta_last_build_at", "") or None,
        last_build_status=get_setting(db, "scinsta_last_build_status", "") or None,
        last_build_error=get_setting(db, "scinsta_last_build_error", "") or None,
        last_build_ipa=get_setting(db, "scinsta_last_build_ipa", "") or None,
        last_build_patch=get_setting(db, "scinsta_last_build_patch", "") or None,
        last_build_scinsta_sha=get_setting(db, "scinsta_last_build_scinsta_sha", "") or None,
        decrypt_url=get_decrypt_url(db),
        upload_ready=_upload_file().exists(),
        upload_version=_read_upload_version(),
    )
    prog = _build_progress()
    if prog.exists():
        try:
            data = json.loads(prog.read_text(encoding="utf-8"))
            state.build_progress_step = data.get("step")
        except (OSError, json.JSONDecodeError):
            pass
    state.ig_update_available = version_gt(state.ig_latest or "", state.ig_deployed or "")
    return state


# -----------------------------------------------------------------------------
# Check version Instagram sur decrypt.day (HTTP direct, pas de headless browser)
# -----------------------------------------------------------------------------

def _is_challenge_page(body: str) -> bool:
    lower = body.lower()
    return any(m in lower for m in _CF_CHALLENGE_MARKERS)


def _extract_version(body: str) -> Optional[str]:
    m = _SOFTWARE_VERSION_RE.search(body)
    return m.group(1).strip() if m else None


def _fetch_with_curl_cffi(url: str) -> tuple[Optional[str], Optional[str]]:
    """Tentative principale : curl_cffi avec impersonation Chrome.

    Cloudflare filtre sur le fingerprint TLS (JA3/JA4). curl_cffi utilise
    libcurl-impersonate qui renvoie exactement le ClientHello de Chrome —
    la requete est indistinguable d'un vrai navigateur au niveau reseau.

    On essaie plusieurs impersonations pour etre robuste aux mises a jour
    du WAF : si chrome131 est un jour flag, chrome ou safari17_0 passent.
    """
    try:
        from curl_cffi import requests as cffi_requests  # type: ignore
    except ImportError as e:
        return None, f"curl_cffi indisponible : {e}"

    last_err: Optional[str] = None
    for imp in _IMPERSONATIONS:
        try:
            resp = cffi_requests.get(
                url,
                impersonate=imp,
                timeout=30,
                headers={
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;"
                        "q=0.9,image/webp,*/*;q=0.8"
                    ),
                    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
                },
            )
        except Exception as e:  # noqa: BLE001 (curl_cffi leve plusieurs types)
            last_err = f"{imp}: {type(e).__name__}: {e}"
            logger.info("scinsta: curl_cffi %s a echoue (%s)", imp, e)
            continue

        if resp.status_code != 200:
            last_err = f"{imp}: HTTP {resp.status_code}"
            logger.info("scinsta: curl_cffi %s -> HTTP %s", imp, resp.status_code)
            continue

        body = resp.text
        if _is_challenge_page(body):
            last_err = f"{imp}: Cloudflare challenge"
            logger.info("scinsta: curl_cffi %s -> challenge page", imp)
            continue

        version = _extract_version(body)
        if version:
            logger.info("scinsta: version %s recuperee via curl_cffi[%s]", version, imp)
            return version, None
        last_err = f"{imp}: pattern softwareVersion absent"

    return None, last_err or "curl_cffi : aucune impersonation n'a abouti"


def _fetch_with_urllib(url: str) -> tuple[Optional[str], Optional[str]]:
    """Fallback urllib. Ne sert que si curl_cffi n'est pas installe (dev
    local sans rebuild). En prod il sera quasi toujours bloque en 403.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} sur decrypt.day (Cloudflare ?)"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, f"reseau : {e}"

    if _is_challenge_page(body):
        return None, "Cloudflare challenge — urllib incapable de passer."
    version = _extract_version(body)
    if not version:
        return None, "version introuvable dans le HTML"
    return version, None


def fetch_instagram_version_online(url: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """Recupere la version affichee sur l'URL source (decrypt.day par defaut).

    Chaine de fallback :
      1. curl_cffi (TLS impersonation Chrome) — passe dans la quasi-totalite
         des cas en production.
      2. urllib (stdlib, sans TLS impersonation) — uniquement si curl_cffi
         n'est pas disponible. Sera bloque par Cloudflare la plupart du
         temps, mais permet de tourner en local sans dep native.

    Si les deux echouent, on retourne (None, message) — l'UI propose alors
    a l'admin de saisir la version manuellement.

    Retourne (version, error_message). L'un des deux est None.
    """
    target = url or DECRYPT_URL_DEFAULT
    version, err = _fetch_with_curl_cffi(target)
    if version:
        return version, None

    # Pas de curl_cffi ? Retombe sur urllib. Avec curl_cffi qui echoue
    # (rarissime), inutile de retenter urllib : le fingerprint est encore
    # plus fragile que Chrome-impersonne.
    if err and err.startswith("curl_cffi indisponible"):
        logger.info("scinsta: curl_cffi absent, fallback urllib")
        return _fetch_with_urllib(target)

    return None, err or "erreur inconnue"


def run_check(db: Session) -> ScinstaState:
    version, err = fetch_instagram_version_online(get_decrypt_url(db))
    set_setting(db, "scinsta_last_check_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    if version:
        set_setting(db, "scinsta_ig_version_latest", version)
        set_setting(db, "scinsta_last_check_error", "")
    else:
        set_setting(db, "scinsta_last_check_error", err or "erreur inconnue")
    db.commit()
    return get_state(db)


# -----------------------------------------------------------------------------
# Upload + declenchement du build
# -----------------------------------------------------------------------------

def upload_instagram_ipa(stream, total_size_hint: Optional[int] = None) -> Path:
    """Stream l'IPA uploadee vers /etc/ipastore/scinsta-upload-<env>.ipa.

    On ecrit d'abord dans un temp voisin puis rename atomique — evite qu'un
    flag-file arrive avant que l'upload soit complet si l'admin clique
    rapidement sur Build.
    """
    Config.IPASTORE_ETC.mkdir(parents=True, exist_ok=True)
    final = _upload_file()
    tmp = final.with_suffix(".ipa.tmp")
    with tmp.open("wb") as f:
        while True:
            chunk = stream.read(8 * 1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(final)
    return final


def clear_upload() -> None:
    _upload_file().unlink(missing_ok=True)


def dismiss_last_build_error(db: Session) -> None:
    """Efface le message d'erreur du dernier build (fermeture par l'admin).

    Utile apres une annulation : l'alert "Build annulé" reste
    affichee tant que le setting n'est pas vide — ce helper permet a l'UI
    de retirer le message via un bouton de fermeture.
    """
    set_setting(db, "scinsta_last_build_error", "")
    db.commit()


def request_cancel(db: Session) -> Path:
    """Ecrit le flag-file de cancel.

    systemd (path unit) detecte le fichier et lance un service host-side
    qui docker kill le conteneur builder et ecrit un result failed. On
    passe aussi le status en "failed" cote settings immediatement pour
    debloquer l'UI (le watcher lifespan va aussi le confirmer quand le
    result file arrivera).
    """
    Config.IPASTORE_ETC.mkdir(parents=True, exist_ok=True)
    flag = _cancel_flag()
    flag.write_text(
        json.dumps({"requested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}),
        encoding="utf-8",
    )
    return flag


def request_build(db: Session, patch_filename: Optional[str]) -> Path:
    """Ecrit le flag-file pour declencher le builder via systemd.

    Le contenu du flag est un JSON avec le nom du patch optionnel a
    appliquer a la fin du build (decouvert via app.patches).
    """
    Config.IPASTORE_ETC.mkdir(parents=True, exist_ok=True)
    flag = _build_flag()
    payload = {
        "requested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "patch": patch_filename or "",
    }
    flag.write_text(json.dumps(payload), encoding="utf-8")
    set_setting(db, "scinsta_last_build_status", "requested")
    set_setting(db, "scinsta_last_build_error", "")
    set_setting(db, "scinsta_last_build_patch", patch_filename or "")
    db.commit()
    return flag


# -----------------------------------------------------------------------------
# Consommation du result file (appele depuis le lifespan loop)
# -----------------------------------------------------------------------------

def consume_build_result() -> Optional[dict]:
    path = _build_result()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("scinsta result file unreadable: %s", e)
        path.unlink(missing_ok=True)
        return None
    path.unlink(missing_ok=True)
    return data


# -----------------------------------------------------------------------------
# Integration post-build : cree l'App Instagram + la Version dans la BDD
# -----------------------------------------------------------------------------

def _ensure_instagram_app(db: Session, ipa_path: Path):
    """Retourne l'App Instagram en BDD, la creant depuis l'IPA si absente."""
    from .models import App
    from .ipa import parse_ipa
    import secrets as _secrets

    app = db.query(App).filter(App.bundle_id == INSTAGRAM_BUNDLE_ID).first()
    if app is not None:
        return app

    info = parse_ipa(ipa_path)
    name = info.name if info and info.bundle_id == INSTAGRAM_BUNDLE_ID else "Instagram"
    icon_path: Optional[str] = None
    if info and info.icon_bytes:
        # Meme scheme que les uploads d'icone (token aleatoire pour invalider
        # le cache HTTP de SideStore quand l'icone change).
        fname = f"{INSTAGRAM_BUNDLE_ID}-{_secrets.token_hex(6)}.png"
        Config.ICONS_DIR.mkdir(parents=True, exist_ok=True)
        (Config.ICONS_DIR / fname).write_bytes(info.icon_bytes)
        icon_path = fname

    app = App(
        bundle_id=INSTAGRAM_BUNDLE_ID,
        name=name,
        developer_name="Instagram, Inc. (SCInsta)",
        subtitle="Instagram patché via SCInsta",
        description=(
            "Version d'Instagram injectée avec SCInsta, un tweak iOS qui "
            "débride l'app (pas de pubs, téléchargement de médias, modes "
            "cachés…). Build généré depuis l'onglet SCInsta du serveur."
        ),
        tint_color="E1306C",
        category="social",
        icon_path=icon_path,
        screenshot_urls="[]",
        featured=1,
    )
    db.add(app)
    db.flush()
    return app


def integrate_build_result(db: Session, result: dict) -> Optional[str]:
    """Applique un result file de build : cree la Version en BDD.

    L'App Instagram est creee si absente (metadonnees figees a la creation,
    non mises a jour aux builds suivants). Aucun article news automatique :
    l'admin les redige manuellement via l'onglet News s'il le souhaite.

    Retourne None en cas de succes, un message d'erreur sinon.
    """
    from .models import Version
    from .ipa import sha256_of_file

    status = result.get("status")
    set_setting(db, "scinsta_last_build_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    if status != "success":
        err = result.get("error") or "erreur inconnue"
        set_setting(db, "scinsta_last_build_status", "failed")
        set_setting(db, "scinsta_last_build_error", err)
        db.commit()
        return err

    filename = result.get("ipa_filename") or ""
    ig_v = result.get("ig_version") or ""
    sci_sha = result.get("scinsta_sha") or ""
    patch_used = result.get("patch") or ""
    ipa_path = Config.IPAS_DIR / filename

    if not ipa_path.exists():
        msg = f"IPA introuvable dans le store : {filename}"
        set_setting(db, "scinsta_last_build_status", "failed")
        set_setting(db, "scinsta_last_build_error", msg)
        db.commit()
        return msg

    app = _ensure_instagram_app(db, ipa_path)

    # Relit toujours version ET build_version depuis l'Info.plist : SideStore
    # compare ces champs a ceux du source.json au moment du sideload et refuse
    # l'install en cas de mismatch (CFBundleVersion/CFBundleShortVersionString).
    # Utiliser le SHA SCInsta comme build_version cassait l'install — la seule
    # valeur valide est le CFBundleVersion reel de l'IPA Instagram.
    from .ipa import parse_ipa
    info = parse_ipa(ipa_path)
    if info:
        if not ig_v:
            ig_v = info.version
        build_label = info.build_version or "1"
    else:
        build_label = "1"

    size = result.get("size") or ipa_path.stat().st_size
    sha256 = result.get("sha256") or sha256_of_file(ipa_path)

    existing = db.query(Version).filter(
        Version.app_id == app.id,
        Version.version == ig_v,
        Version.build_version == build_label,
    ).first()

    if existing is None:
        ver = Version(
            app_id=app.id,
            ipa_filename=filename,
            version=ig_v or "0.0.0",
            build_version=build_label,
            size=size,
            sha256=sha256,
            min_os_version="15.0",
            changelog=f"Instagram {ig_v} + SCInsta",
        )
        db.add(ver)
    else:
        # Rebuild de la meme version IG (meme CFBundleVersion) avec un nouveau
        # commit SCInsta : on remplace l'IPA existant en place et on bump
        # uploaded_at pour que SideStore re-propose l'install.
        old_filename = existing.ipa_filename
        existing.ipa_filename = filename
        existing.size = size
        existing.sha256 = sha256
        existing.changelog = f"Instagram {ig_v} + SCInsta"
        existing.uploaded_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
        if old_filename and old_filename != filename:
            (Config.IPAS_DIR / old_filename).unlink(missing_ok=True)

    # Purge des rows Version obsoletes pour la meme IG version : anciennes
    # lignes creees avec build_version = short SHA SCInsta (refus d'install
    # SideStore), ou upload vanille avant le build SCInsta. On ne garde
    # qu'un seul IPA par (app, version, CFBundleVersion).
    stale_versions = db.query(Version).filter(
        Version.app_id == app.id,
        Version.version == ig_v,
        Version.build_version != build_label,
    ).all()
    for stale in stale_versions:
        if stale.ipa_filename and stale.ipa_filename != filename:
            (Config.IPAS_DIR / stale.ipa_filename).unlink(missing_ok=True)
        db.delete(stale)

    set_setting(db, "scinsta_last_build_status", "success")
    set_setting(db, "scinsta_last_build_error", "")
    set_setting(db, "scinsta_last_build_ipa", filename)
    set_setting(db, "scinsta_last_build_scinsta_sha", sci_sha)
    set_setting(db, "scinsta_last_build_patch", patch_used)

    db.commit()
    # Upload consomme par le builder — on s'assure qu'il est supprime cote web
    # aussi pour que l'UI n'affiche plus "upload en attente".
    clear_upload()
    return None
