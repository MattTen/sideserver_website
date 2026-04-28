"""
fix_ipa_scinsta.py — patch IPA SCInsta (Instagram tweake) pour SideStore.

Inclut les corrections de fix_ipa.py (re-layout __LINKEDIT, thin FAT arm64,
strip LC_CODE_SIGNATURE) + suppression du dossier Extensions/ (widget Lock
Screen Camera non gere par SideStore : IXErrorDomain Code=8).

Voir documentation/patch_fix_ipa_scinsta.md pour le detail et l'impact.

Usage :
    python3 fix_ipa_scinsta.py -s /chemin/vers/SCInsta.ipa

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


def process_macho(path):
    fat = lief.MachO.parse(path)
    if fat is None:
        return False
    binaries = list(fat)
    arm64 = None
    for b in binaries:
        if b.header.cpu_type == lief.MachO.Header.CPU_TYPE.ARM64 and b.header.cpu_subtype == 0:
            arm64 = b
            break
    if arm64 is None:
        arm64 = binaries[0]
    try:
        arm64.remove_signature()
    except Exception:
        for cmd in list(arm64.commands):
            if cmd.command == lief.MachO.LOAD_COMMAND_TYPES.CODE_SIGNATURE:
                arm64.remove(cmd)
    arm64.write(path)
    return True


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
    workdir = tempfile.mkdtemp(prefix='scinsta-fix-')
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

        ext_dir = os.path.join(app_bundle, 'Extensions')
        if os.path.isdir(ext_dir):
            shutil.rmtree(ext_dir)
            print(f"[+] Extensions/ supprime (widget Lock Screen Camera, non gere par SideStore)")

        n = 0
        for dp, _, files in os.walk(app_bundle):
            for f in files:
                p = os.path.join(dp, f)
                if not is_macho_or_fat(p):
                    continue
                try:
                    if process_macho(p):
                        n += 1
                        rel = os.path.relpath(p, app_bundle)
                        print(f"    patched {rel}")
                except Exception as e:
                    print(f"    SKIP {os.path.relpath(p, app_bundle)}: {e}")
        print(f"[+] {n} binaires patches")

        # On ecrit le temp IPA dans le MEME dir que la destination pour que
        # os.replace soit atomique (rename(2) sans cross-device fallback) ET
        # qu'il puisse overwrite un fichier dont on n'est pas owner -- seul
        # le parent dir doit etre writable. Sans ca, sur les builds
        # precedents (scinsta-builder en root, web-app en uid ipastore), le
        # patch echouait avec PermissionError sur shutil.move.
        out_dir = os.path.dirname(ipa_path) or '.'
        tmp_fd, tmp_ipa = tempfile.mkstemp(
            prefix='.fix-ipa-scinsta-', suffix='.ipa.tmp', dir=out_dir,
        )
        os.close(tmp_fd)
        print(f"[+] repack...")
        try:
            repack_ipa(extract_dir, tmp_ipa)
            # mkstemp cree en 0600. On rend lisible par tous les uid du
            # conteneur web (static file serving, parse_ipa via docker exec
            # avec uid different, etc.). 0644 = rw-r--r--.
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
        description="Patch IPA SCInsta pour SideStore (layout __LINKEDIT + suppression Extensions/)"
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
