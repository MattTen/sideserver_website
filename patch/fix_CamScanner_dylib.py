"""
fix_ipa_camscanner_siri.py — patch IPA CamScanner Lite (tweake) pour SideStore.

Corrige le crash au lancement :

    Erreur : Use of the class <INPreferences: 0x...> from an app requires the
    entitlement com.apple.developer.siri. Did you enable the Siri capability
    in your Xcode project?
        ... 2 Intents ...  5 Intents ...  6 Intents ...

Cause :
  le binaire principal CamScanner_Lite est lie contre Intents.framework et
  appelle l'API SiriKit (INPreferences : requestSiriAuthorization /
  siriAuthorizationStatus). Au runtime, le framework Intents verifie que le
  process possede l'entitlement com.apple.developer.siri ; sinon il leve une
  NSException -> crash.

  Sur un compte developpeur GRATUIT (SideStore) cet entitlement n'est pas
  disponible : Apple refuse d'activer la capability Siri, et SideStore
  re-signe donc l'app sans. Ajouter l'entitlement ne survit pas a la
  re-signature : ce n'est pas une solution.

Correctif (survit a la re-signature, car c'est du code) :
  on neutralise l'import statique de la classe Objective-C INPreferences.
  L'import _OBJC_CLASS_$_INPreferences est :
    - marque weak_import  -> dyld n'abandonne pas si le symbole est introuvable
    - renomme vers un symbole inexistant
  Resultat : dyld resout la classref a nil au lieu de la vraie classe. Les
  appels [INPreferences ...] deviennent [nil ...], c.-a-d. des no-op qui
  renvoient 0/nil (semantique ObjC), sans jamais toucher la classe Intents :
  plus d'exception, l'app continue. Les autres symboles d'Intents
  (INPerson, INPersonHandle, ...) restent intacts.

On applique aussi, comme dans fix_ipa.py, le re-layout __LINKEDIT canonique
(thin -> arm64, strip LC_CODE_SIGNATURE) a chaque Mach-O du bundle.

Usage :
    python3 fix_ipa_camscanner_siri.py -s /chemin/vers/CamScanner.ipa

L'IPA d'origine est remplace en place par la version patchee.
"""
import argparse
import os
import shutil
import struct
import sys
import tempfile
import zipfile

import lief

MH_MAGIC_64 = 0xFEEDFACF
MH_MAGIC_32 = 0xFEEDFACE
FAT_MAGIC = 0xCAFEBABE

# Symbole de la classe ObjC a neutraliser, et son nom de remplacement
# (inexistant). La longueur est libre : LIEF reconstruit la table des
# chaines et les opcodes de binding au write().
SIRI_CLASS_SYM = '_OBJC_CLASS_$_INPreferences'
SIRI_CLASS_SYM_DISABLED = '_OBJC_CLASS_$_INPreferences_DISABLED'


def is_macho_or_fat(path):
    try:
        with open(path, 'rb') as f:
            head = f.read(4)
    except Exception:
        return False
    if len(head) < 4:
        return False
    mb = struct.unpack('>I', head)[0]
    ml = struct.unpack('<I', head)[0]
    return mb == FAT_MAGIC or ml in (MH_MAGIC_64, MH_MAGIC_32)


def neutralize_siri(binary):
    """Rend nil la classref INPreferences (weak import + rename).
    Retourne True si le binaire importait la classe."""
    done = False
    for sym in binary.imported_symbols:
        if sym.name == SIRI_CLASS_SYM:
            try:
                sym.binding_info.weak_import = True
            except Exception:
                # pas de binding_info exploitable -> on tente quand meme le rename
                pass
            sym.name = SIRI_CLASS_SYM_DISABLED
            done = True
    return done


def process_macho(path):
    """Retourne (patched, siri_neutralized)."""
    fat = lief.MachO.parse(path)
    if fat is None:
        return False, False
    binaries = list(fat)
    arm64 = None
    for b in binaries:
        if b.header.cpu_type == lief.MachO.Header.CPU_TYPE.ARM64 and b.header.cpu_subtype == 0:
            arm64 = b
            break
    if arm64 is None:
        arm64 = binaries[0]

    siri = neutralize_siri(arm64)

    try:
        arm64.remove_signature()
    except Exception:
        for cmd in list(arm64.commands):
            if cmd.command == lief.MachO.LOAD_COMMAND_TYPES.CODE_SIGNATURE:
                arm64.remove(cmd)
    arm64.write(path)
    return True, siri


def find_app_bundle(payload_dir):
    for entry in os.listdir(payload_dir):
        if entry.endswith('.app'):
            return os.path.join(payload_dir, entry)
    raise RuntimeError(f"aucun bundle .app trouve dans {payload_dir}")


def repack_ipa(src_dir, out_ipa):
    with zipfile.ZipFile(out_ipa, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, dirs, files in os.walk(src_dir):
            rel_root = os.path.relpath(root, src_dir).replace('\\', '/')
            if rel_root != '.':
                zi = zipfile.ZipInfo(rel_root + '/')
                zi.external_attr = (0o755 << 16) | 0x10
                z.writestr(zi, b'')
            for f in files:
                full = os.path.join(root, f)
                arc = os.path.relpath(full, src_dir).replace('\\', '/')
                z.write(full, arc, zipfile.ZIP_DEFLATED)


def patch(ipa_path):
    ipa_path = os.path.abspath(ipa_path)
    workdir = tempfile.mkdtemp(prefix='camscanner-siri-fix-')
    try:
        extract_dir = os.path.join(workdir, 'extract')
        os.makedirs(extract_dir)

        print(f"[+] extraction de {ipa_path}")
        with zipfile.ZipFile(ipa_path, 'r') as z:
            z.extractall(extract_dir)

        payload = os.path.join(extract_dir, 'Payload')
        if not os.path.isdir(payload):
            raise RuntimeError("pas de dossier Payload/ dans l'IPA")

        app_bundle = find_app_bundle(payload)
        print(f"[+] bundle: {os.path.basename(app_bundle)}")

        n = 0
        siri_total = 0
        for dp, _, files in os.walk(app_bundle):
            for f in files:
                p = os.path.join(dp, f)
                if not is_macho_or_fat(p):
                    continue
                try:
                    patched, siri = process_macho(p)
                    if patched:
                        n += 1
                        rel = os.path.relpath(p, app_bundle)
                        tag = "  [INPreferences -> nil]" if siri else ""
                        print(f"    patched {rel}{tag}")
                        if siri:
                            siri_total += 1
                except Exception as e:
                    print(f"    SKIP {os.path.relpath(p, app_bundle)}: {e}")
        print(f"[+] {n} binaires patches, {siri_total} avec neutralisation Siri")
        if siri_total == 0:
            print("[!] ATTENTION : aucun binaire n'importait INPreferences. "
                  "Le crash Siri n'a peut-etre pas ete corrige (verifier l'IPA).")

        # On ecrit le temp IPA dans le MEME dir que la destination pour que
        # os.replace soit atomique ET puisse overwrite un fichier dont on
        # n'est pas owner (seul le parent dir doit etre writable).
        out_dir = os.path.dirname(ipa_path) or '.'
        tmp_fd, tmp_ipa = tempfile.mkstemp(
            prefix='.fix-ipa-camscanner-siri-', suffix='.ipa.tmp', dir=out_dir,
        )
        os.close(tmp_fd)
        print(f"[+] repack...")
        try:
            repack_ipa(extract_dir, tmp_ipa)
            os.chmod(tmp_ipa, 0o644)
            os.replace(tmp_ipa, ipa_path)
        except Exception:
            try:
                os.unlink(tmp_ipa)
            except OSError:
                pass
            raise
        print(f"[+] remplace en place: {ipa_path}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(
        description="Patch IPA CamScanner Lite pour SideStore "
                    "(neutralise l'appel Siri/INPreferences + re-layout __LINKEDIT)"
    )
    ap.add_argument('-s', '--source', required=True,
                    help="chemin vers l'IPA a patcher (remplace en place)")
    args = ap.parse_args()

    if not os.path.isfile(args.source):
        print(f"fichier introuvable: {args.source}", file=sys.stderr)
        sys.exit(1)

    patch(args.source)


if __name__ == '__main__':
    main()