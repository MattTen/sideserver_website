"""SCInsta builder — pipeline complet Instagram + SCInsta + patch optionnel.

Invoque en one-shot par systemd (voir ipastore-scinsta-build@.service) des
qu'un flag /etc/ipastore/scinsta-build-requested-<env> apparait.

Entrees (via volumes montes) :
- /etc/ipastore/scinsta-build-requested-<env>  : flag JSON {patch, requested_at}
- /etc/ipastore/scinsta-upload-<env>.ipa       : IPA Instagram officielle
- /etc/ipastore/patches-<env>/                 : patches sync'es depuis l'app
- /srv/store                                    : store volume (ipas/ final)

Sorties :
- /etc/ipastore/scinsta-build-progress-<env>   : JSON mis a jour par etape
- /etc/ipastore/scinsta-build-result-<env>     : JSON final (success|failed)
- /srv/store/ipas/SCInsta-ig<v>-<sha>.ipa      : IPA final deploye
"""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
import traceback
import zipfile
from pathlib import Path
from typing import Any, Optional

ETC = Path("/etc/ipastore")
STORE = Path("/srv/store")
IPAS = STORE / "ipas"

ENV = os.environ.get("IPASTORE_ENV", "dev")
FLAG_FILE = ETC / f"scinsta-build-requested-{ENV}"
UPLOAD_FILE = ETC / f"scinsta-upload-{ENV}.ipa"
PROGRESS_FILE = ETC / f"scinsta-build-progress-{ENV}"
RESULT_FILE = ETC / f"scinsta-build-result-{ENV}"
LOG_FILE = ETC / f"scinsta-build-log-{ENV}.txt"
PATCHES_DIR = ETC / f"patches-{ENV}"


SCINSTA_REPO = "https://github.com/SoCuul/SCInsta.git"
INSTAGRAM_BUNDLE_ID = "com.burbn.instagram"


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log(step: str, **kv: Any) -> None:
    """Ecrit l'avancement dans le progress file + stdout pour systemd journal."""
    payload = {"status": "running", "step": step, "updated_at": _iso_now(), **kv}
    PROGRESS_FILE.write_text(json.dumps(payload), encoding="utf-8")
    print(f"[{step}] {json.dumps(kv)[:500]}", flush=True)


def finish_success(**kv: Any) -> None:
    payload = {"status": "success", "finished_at": _iso_now(), **kv}
    RESULT_FILE.write_text(json.dumps(payload), encoding="utf-8")
    PROGRESS_FILE.unlink(missing_ok=True)
    print(f"[done] {json.dumps(kv)[:500]}", flush=True)


def finish_failure(error: str, **kv: Any) -> None:
    payload = {"status": "failed", "finished_at": _iso_now(), "error": error, **kv}
    RESULT_FILE.write_text(json.dumps(payload), encoding="utf-8")
    PROGRESS_FILE.unlink(missing_ok=True)
    print(f"[failed] {error}", flush=True)
    sys.exit(1)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_flag_payload() -> dict:
    """Lit le flag-file puis le supprime pour que le path unit ne retrigger pas."""
    try:
        data = json.loads(FLAG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    FLAG_FILE.unlink(missing_ok=True)
    return data


def read_ig_version(ipa_path: Path) -> Optional[str]:
    """Lit CFBundleShortVersionString dans l'Info.plist de l'IPA upload."""
    try:
        with zipfile.ZipFile(ipa_path) as zf:
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
        return str(plist.get("CFBundleShortVersionString") or "") or None
    except Exception:  # pragma: no cover - lecture best-effort
        return None


# -----------------------------------------------------------------------------
# Pipeline
# -----------------------------------------------------------------------------

def clone_scinsta(workdir: Path) -> tuple[Path, str]:
    """Clone frais SoCuul/SCInsta main + submodules. Retourne (repo_path, short_sha)."""
    repo = workdir / "SCInsta"
    log("clone_scinsta", branch="main")
    subprocess.run(
        ["git", "clone", "--recursive", "--depth", "1", "--branch", "main",
         SCINSTA_REPO, str(repo)],
        check=True,
    )
    sha = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
        text=True,
    ).strip()
    log("scinsta_cloned", sha=sha)
    return repo, sha


def place_ig_ipa(repo: Path, ig_ipa: Path) -> Path:
    """Copie l'IPA uploadee dans packages/com.burbn.instagram.ipa (glob attendu)."""
    pkg_dir = repo / "packages"
    pkg_dir.mkdir(exist_ok=True)
    dest = pkg_dir / "com.burbn.instagram.ipa"
    log("place_ig_ipa", src=str(ig_ipa), dest=str(dest))
    shutil.copy2(ig_ipa, dest)
    return dest


def fix_case_sensitive_submodule(repo: Path) -> None:
    """Patche les references case-sensitive du repo SCInsta.

    SCInsta a ete developpe sur macOS avec HFS+ case-insensitive ; deux
    mismatches se revelent en builds Linux :

    1. SCInsta/Makefile : SUBPROJECTS += modules/flexing (minuscules) vs
       submodule checkout comme modules/FLEXing (camelCase).
    2. SCInsta/build.sh : FLEXPATH refere .theos/obj/debug/libflex.dylib
       mais le libflex/Makefile declare TWEAK_NAME = libFLEX, produisant
       libFLEX.dylib (idem arm64/arm64e).

    (1) se resout avec un symlink ; (2) avec un sed dans build.sh pour
    pointer sur le vrai nom de dylib produit. Patcher les Makefiles du
    submodule n'est pas viable (rebase en vain a chaque clone).
    """
    modules = repo / "modules"
    src = modules / "FLEXing"
    dst = modules / "flexing"
    if src.is_dir() and not dst.exists():
        dst.symlink_to("FLEXing", target_is_directory=True)
        log("flexing_symlink", path=str(dst))

    build_sh = repo / "build.sh"
    if build_sh.is_file():
        content = build_sh.read_text(encoding="utf-8")
        patched = content.replace("libflex.dylib", "libFLEX.dylib")
        if patched != content:
            build_sh.write_text(patched, encoding="utf-8")
            log("build_sh_patched", file="libflex.dylib->libFLEX.dylib")


def run_scinsta_build(repo: Path) -> Path:
    """Lance `./build.sh sideload` dans le clone. Retourne l'IPA genere.

    build.sh fait : make clean + make SIDELOAD=1 + cyan inject + ipapatch.
    Log en temps reel via subprocess.Popen pour voir la progression Theos.
    """
    log("scinsta_build", cmd="./build.sh sideload")
    env = os.environ.copy()
    # THEOS est deja exporte dans l'image (ENV THEOS=/opt/theos), mais on
    # force ici au cas ou un script wrapper aurait supprime la variable.
    env["THEOS"] = env.get("THEOS", "/opt/theos")
    env["PATH"] = f"{env['THEOS']}/bin:{env.get('PATH', '')}"

    script = repo / "build.sh"
    if not script.is_file():
        raise RuntimeError("build.sh absent dans le clone SCInsta (repo mal cloné)")
    script.chmod(0o755)

    proc = subprocess.Popen(
        ["bash", "./build.sh", "sideload"],
        cwd=str(repo),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # Stream les logs ligne par ligne vers stdout pour journal systemd.
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line.rstrip(), flush=True)
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"build.sh a echoue (rc={rc})")

    out = repo / "packages" / "SCInsta-sideloaded.ipa"
    if not out.is_file():
        raise RuntimeError(f"IPA final introuvable : {out}")
    log("scinsta_build_done", ipa=str(out), size=out.stat().st_size)
    return out


def apply_optional_patch(ipa_path: Path, patch_filename: str) -> None:
    """Execute un script patch/<filename>.py sur l'IPA en place.

    Les patches sont syncs depuis l'app web via /etc/ipastore/patches-<env>/
    (cf. systemd pre-start ou montage direct).
    """
    script = PATCHES_DIR / patch_filename
    if not script.is_file():
        # Fallback : si le patch n'a pas ete synchronise, on recupere
        # le script depuis le repo de l'app clone cote hote via le
        # volume standard /opt/sideserver-<env>/patch. Evite un echec
        # silencieux sur un env ou la pre-sync n'a pas ete configuree.
        alt = Path(f"/opt/sideserver-{ENV}/patch") / patch_filename
        if alt.is_file():
            script = alt
        else:
            raise RuntimeError(
                f"Patch introuvable : ni {PATCHES_DIR / patch_filename} "
                f"ni {alt}"
            )
    log("apply_patch", script=str(script))
    subprocess.run(
        [sys.executable, str(script), "-s", str(ipa_path)],
        check=True,
    )
    log("patch_done")


def deploy_ipa(patched: Path, ig_version: str, scinsta_sha: str) -> Path:
    """Copie l'IPA final dans /srv/store/ipas/ avec un nom unique."""
    IPAS.mkdir(parents=True, exist_ok=True)
    sha = sha256_of(patched)
    short = sha[:10]
    filename = f"SCInsta-ig{ig_version or 'x'}-sc{scinsta_sha}-{short}.ipa"
    final = IPAS / filename
    shutil.move(str(patched), str(final))
    size = final.stat().st_size
    log("ipa_deployed", path=str(final), size=size, sha256=sha)
    return final


# -----------------------------------------------------------------------------
# Orchestration
# -----------------------------------------------------------------------------

def main() -> None:
    ETC.mkdir(parents=True, exist_ok=True)
    IPAS.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.unlink(missing_ok=True)
    # Sortie stdout/stderr capturee par website-management.sh qui tee
    # vers scinsta-build-log-<env>.txt — pas de tee direct ici pour eviter
    # les doublons (le fichier est monte en volume).

    payload = read_flag_payload()
    patch_filename = (payload.get("patch") or "").strip()

    if not UPLOAD_FILE.is_file():
        finish_failure(
            "Aucune IPA Instagram uploadee",
            traceback="UPLOAD_FILE missing",
        )
        return

    # Copie de travail : on ne veut pas que le .ipa upload soit modifie
    # si un retry intervient. On laisse l'original en place et on le
    # supprime seulement en cas de succes final.
    workdir = Path(f"/tmp/scinsta-build-{int(time.time())}")
    workdir.mkdir(parents=True, exist_ok=True)
    ig_ipa = workdir / "instagram-input.ipa"
    shutil.copy2(UPLOAD_FILE, ig_ipa)
    ig_version = read_ig_version(ig_ipa) or ""
    log("ig_upload_ready", version=ig_version, size=ig_ipa.stat().st_size)

    try:
        repo, scinsta_sha = clone_scinsta(workdir)
        place_ig_ipa(repo, ig_ipa)
        fix_case_sensitive_submodule(repo)
        patched = run_scinsta_build(repo)

        if patch_filename:
            apply_optional_patch(patched, patch_filename)

        final = deploy_ipa(patched, ig_version, scinsta_sha)

        # On ne supprime UPLOAD_FILE qu'apres succes complet — permet un
        # retry manuel sans re-upload si une etape a foire.
        UPLOAD_FILE.unlink(missing_ok=True)

        finish_success(
            ipa_filename=final.name,
            ig_version=ig_version,
            scinsta_sha=scinsta_sha,
            patch=patch_filename,
            size=final.stat().st_size,
            sha256=sha256_of(final),
        )
    except subprocess.CalledProcessError as e:
        finish_failure(
            f"subprocess failed: {e.cmd!r} rc={e.returncode}",
            traceback=traceback.format_exc(),
        )
    except Exception as e:
        finish_failure(str(e), traceback=traceback.format_exc())
    finally:
        # Nettoyage du workdir (le clone SCInsta peut peser ~500 Mo avec le
        # .theos/ intermediaire)
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
