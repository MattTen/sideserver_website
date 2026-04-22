# SCInsta Build Pipeline

Documentation technique de l'image Docker `scinsta-builder` et du script `build.py`. Couvre la chaîne de compilation iOS cross-compile Linux → iOS arm64/arm64e et l'ensemble des workarounds nécessaires pour qu'un pipeline SCInsta prévu pour macOS tourne sur Debian.

Pour la partie UI / flux admin / bypass Cloudflare / intégration BDD, voir [scinsta_builder.md](scinsta_builder.md).

---

## 1. Vue d'ensemble

Le builder est une image Docker `debian:bookworm-slim` invoquée en one-shot par systemd. À chaque build :

```
flag systemd   →  website-management <env>-scinsta-build
                  └─ docker build (cache-hit après 1er run)
                  └─ docker run --rm --name scinsta-builder-<env>
                        -v /etc/ipastore:/etc/ipastore
                        -v /srv/store-<env>:/srv/store
                        scinsta-builder:latest
                        │
                        └─ python3 /opt/builder/build.py
                           1. read_flag_payload()      → {patch, requested_at}
                           2. git clone SoCuul/SCInsta (main, --recursive --depth 1)
                           3. place_ig_ipa()           → packages/com.burbn.instagram.ipa
                           4. fix_case_sensitive_submodule()  (voir §4)
                           5. run_scinsta_build()      → bash ./build.sh sideload
                                    ├─ Theos compile FLEXing + libFLEX + SCInsta
                                    ├─ cyan inject dylibs dans l'IPA
                                    └─ ipapatch sideload patches (18 binaires)
                           6. apply_optional_patch()   (si patch demandé)
                           7. deploy_ipa()             → /srv/store/ipas/SCInsta-ig<v>-sc<sha>-<short>.ipa
                           8. finish_success()         → scinsta-build-result-<env>
```

Durée typique : **5-10 min** avec l'image cachée (premier build ~25 min à cause du download du SDK via git-lfs).

Container nommé `scinsta-builder-<env>` (obligatoire pour que `ipastore-scinsta-cancel@<env>` puisse faire `docker stop -t 2`).

---

## 2. Layers du Dockerfile

Ordre optimisé pour maximiser le cache (dépendances stables en haut, code changeant en bas).

| # | Layer | Coût | Cache |
|---|-------|------|-------|
| 1 | `apt-get install` (clang, make, cmake, python3, git-lfs, libplist-dev, libssl-dev, ldid deps…) | ~2 min | Stable |
| 2 | Build `ldid` depuis source (absent de bookworm) | <30 s | Stable |
| 3 | `git clone theos/theos` dans `/opt/theos` | ~10 s | Stable |
| 4 | Download + extract `L1ghtmann/llvm-project` iOSToolchain-x86_64 | ~45 s | Stable |
| 5 | Clone `theos/sdks` avec `GIT_LFS_SKIP_SMUDGE=1` puis `git lfs pull --include=iPhoneOS16.5.sdk/**` + symlink 16.2→16.5 | ~8 min | Stable |
| 6 | **Stub `libclang_rt.ios.a`** compilé pour arm64+arm64e (cf. §5) | <10 s | Stable |
| 7 | `pip install cyan lief requests` (`--break-system-packages`) | ~30 s | Stable |
| 8 | Download binaire `ipapatch.linux-amd64 v2.1.3` | <5 s | Stable |
| 9 | `COPY build.py` | instant | Changeant |

Le changement de `build.py` n'invalide que le dernier layer. Rebuild post-modif ≈ 2 s.

---

## 3. Chaîne de toolchain

### Problème initial

Le repo `theos/theos` ne contient que la scaffolding (Makefiles, scripts) ; il ne package **pas** le cross-compiler. Sur macOS, Theos détecte le toolchain système via Xcode. Sur Linux, il faut l'installer soi-même.

### Solution retenue

**`L1ghtmann/llvm-project` iOSToolchain-x86_64** (clang 11.1.0 basé LLVM patché pour iOS). C'est exactement ce que le script officiel `install-theos` utilise sur Linux x86_64 quand on n'a pas besoin du support Swift.

```dockerfile
RUN mkdir -p $THEOS/toolchain \
    && curl -sL https://github.com/L1ghtmann/llvm-project/releases/latest/download/iOSToolchain-x86_64.tar.xz \
       | tar -xJf - -C $THEOS/toolchain/ \
    && test -x $THEOS/toolchain/linux/iphone/bin/clang
```

Le tarball extrait directement la structure `linux/iphone/bin/` attendue par les Makefiles Theos.

### SDK iOS (theos/sdks)

Le repo `theos/sdks` stocke les SDKs comme **répertoires** trackés par Git LFS (~500 Mo chacun). Le clone complet ferait ~8 Go.

**Gotcha découvert** : le SCInsta Makefile référence `iPhoneOS16.2.sdk` en dur. Or `theos/sdks` master ne contient **que 16.5** pour la série 16.x (pas 16.2, pas 16.4). Les deux SDKs sont ABI-compatibles sur la plage iOS 15+ ciblée par SCInsta — le header diff entre minor versions est négligeable pour les tweaks Instagram.

**Stratégie** :
1. `git clone --depth 1 --branch master` avec `GIT_LFS_SKIP_SMUDGE=1` (aucun binaire LFS).
2. `git lfs pull --include="iPhoneOS16.5.sdk/**"` — download **uniquement** le SDK ciblé.
3. `ln -s iPhoneOS16.5.sdk /opt/theos/sdks/iPhoneOS16.2.sdk`.

Résultat : ~500 Mo téléchargés (vs 8 Go), Theos trouve les deux paths.

---

## 4. Case-sensitivity : macOS HFS+ vs Linux ext4

SCInsta est développé sur macOS avec **HFS+ case-insensitive**. Deux mismatches se révèlent en build Linux :

### 4.1. `modules/flexing` vs `modules/FLEXing`

- Le `Makefile` principal déclare : `SUBPROJECTS += modules/flexing`
- Le submodule git est checkout sous le nom réel : `modules/FLEXing/`

Sur macOS : même chemin. Sur Linux : `No such file or directory`.

### 4.2. `libflex.dylib` vs `libFLEX.dylib`

- Le `libflex/Makefile` déclare `TWEAK_NAME = libFLEX` → Theos produit `libFLEX.dylib`.
- `build.sh` référence explicitement `".theos/obj/debug/libflex.dylib"` lors de l'appel cyan.

Sur macOS : résolution case-insensitive. Sur Linux : `"...libflex.dylib" does not exist`.

### Fix appliqué

`tools/scinsta-builder/build.py:143` — `fix_case_sensitive_submodule(repo)` :

```python
# 1. Symlink
modules/flexing → modules/FLEXing (target_is_directory=True)

# 2. Sed dans build.sh
libflex.dylib → libFLEX.dylib
```

Exécuté après `git clone`, avant `./build.sh`. Pourquoi pas patcher le Makefile du submodule ? Le clone est fresh à chaque build → patch volatile, à réappliquer systématiquement. Un symlink + sed sur `build.sh` (fichier du repo principal) est plus simple et idempotent.

---

## 5. Stub `libclang_rt.ios.a` — compiler-rt manquant

### Problème

Le toolchain L1ghtmann ne package **pas** compiler-rt pour iOS (contrairement à macOS Xcode). Résultat : toutes les références à `__isOSVersionAtLeast` — générées par le compiler pour chaque `@available(...)` Swift/ObjC ou `__builtin_available(...)` C — produisent des `Undefined symbols` au link :

```
ld: Undefined symbols:
  ___isOSVersionAtLeast, referenced from:
      _FLEXBackdropViewController[...].o
      _FLEXTVC[...].o
  ...
```

FLEX use `@available` partout (UI iOS version-dependent), ça touche des centaines de fichiers.

### Solution

Un stub C qui délègue à `_availability_version_check` (symbole présent dans libSystem iOS 12+, celui que le vrai compiler-rt utilise sous le capot) :

```c
#include <stdint.h>
typedef struct { uint32_t major, minor, patch; } _DarwinAvailVersion;
extern uint32_t _availability_version_check(uint32_t count, _DarwinAvailVersion versions[]);

int32_t __isOSVersionAtLeast(int32_t major, int32_t minor, int32_t subminor) {
    _DarwinAvailVersion v = { (uint32_t)major, (uint32_t)minor, (uint32_t)subminor };
    return _availability_version_check(1, &v);
}
```

Placé dans `<clang-resource-dir>/lib/darwin/libclang_rt.ios.a` → auto-lié par les targets iOS.

### Gotcha 1 : archive format (GNU ar vs Apple linker)

Premier essai : `ar rcs libclang_rt.ios.a stub.o`. Échec :

```
ld: warning: ignoring file libclang_rt.ios.a, archive has no table of contents file
```

GNU `ar` produit un format (System V / SysV) qui ne contient pas la TOC attendue par le linker Apple (format BSD). Fix : utiliser `llvm-ar` + `ranlib` livrés avec le toolchain L1ghtmann, qui génèrent le format BSD attendu.

### Gotcha 2 : architectures multiples (arm64 + arm64e)

SCInsta builde en **fat arm64+arm64e** (cf. `CMAKE_OSX_ARCHITECTURES="arm64e;arm64"` côté cyan). Le linker Apple ignore les objets de l'archive qui ne matchent pas la cible, donc une archive arm64-only cause :

```
building for iOS-arm64e but attempting to link with file built for iOS-arm64
```

Fix : compiler le stub **deux fois** (une par arch) avec des noms de .o distincts (`stub_arm64.o`, `stub_arm64e.o`), puis bundler dans la même archive :

```dockerfile
&& $THEOS/toolchain/linux/iphone/bin/clang -target arm64-apple-ios15.0  ... -o stub_arm64.o
&& $THEOS/toolchain/linux/iphone/bin/clang -target arm64e-apple-ios15.0 ... -o stub_arm64e.o
&& $THEOS/toolchain/linux/iphone/bin/llvm-ar rcs libclang_rt.ios.a stub_arm64.o stub_arm64e.o
&& $THEOS/toolchain/linux/iphone/bin/ranlib libclang_rt.ios.a
```

Le linker pioche le bon objet selon la cible en cours.

---

## 6. Outils annexes

### `ldid` (ProcursusTeam)

Absent de Debian bookworm. Theos l'exige pour signer les dylibs en fin de build.

```dockerfile
RUN git clone --recursive https://github.com/ProcursusTeam/ldid.git /tmp/ldid \
    && make -C /tmp/ldid -j"$(nproc)" \
    && install -m 0755 /tmp/ldid/ldid /usr/local/bin/ldid
```

Build <30 s, binaire seul (~1 Mo). Le fork ProcursusTeam est celui utilisé par Theos upstream.

### `cyan` (pyzule-rw)

Injection de dylibs dans un IPA. Installé depuis git (pas sur PyPI) :

```dockerfile
pip install "cyan @ git+https://github.com/asdfzxcvbn/pyzule-rw"
```

`--break-system-packages` requis parce que Debian 12 applique PEP 668. Le conteneur est éphémère → pas de venv.

### `ipapatch` (asdfzxcvbn, binaire Go)

Patch les extensions `.appex/` et le binaire principal pour le sideloading (entitlements, bundle_id remap…). **Invoqué en fin de `build.sh`** — pas de fallback, c'est obligatoire.

Tentative initiale : `pip install asdfzxcvbn/ipapatch`. Échec — c'est un projet Go sans `setup.py`.

Solution : download direct du binaire release depuis GitHub :

```dockerfile
ARG IPAPATCH_VERSION=v2.1.3
RUN curl -fsSL \
    "https://github.com/asdfzxcvbn/ipapatch/releases/download/${IPAPATCH_VERSION}/ipapatch.linux-amd64" \
    -o /usr/local/bin/ipapatch \
    && chmod +x /usr/local/bin/ipapatch \
    && ipapatch --version
```

Version pinnée via `ARG` → bump + rebuild pour mise à jour.

---

## 7. Phase patch optionnel

Juste après `build.sh`, si le flag contenait `patch: <filename>`, `build.py` exécute :

```python
python3 /etc/ipastore/patches-<env>/<filename> -s <ipa_path>
```

Contrat CLI **identique** à l'onglet Patch : le script **écrase l'IPA en place** (pas d'original préservé). Le builder relit ensuite size/sha256 du fichier patché.

Les scripts sont montés depuis `/etc/ipastore/patches-<env>/` (sync'és depuis le clone `/opt/sideserver-<env>/patch/` par le web app au boot). Ça évite de rebuild l'image à chaque ajout/modif de patch.

Exemple d'utilisation validée : `fix_ipa_scinsta.py` (FAT→thin arm64 + strip `Extensions/`), qui réduit l'IPA de ~285 Mo à ~243 Mo et corrige l'erreur `IXErrorDomain Code=8` sur sideload iOS 15+.

---

## 8. IPC flag-file

Tous les échanges hôte ↔ conteneur passent par `/etc/ipastore/` (volume monté read-write dans les deux).

| Fichier | Direction | Lifecycle |
|---|---|---|
| `scinsta-build-requested-<env>` | web → systemd | Créé par `POST /scinsta/build`, **lu puis supprimé par `build.py:read_flag_payload()`** |
| `scinsta-build-progress-<env>` | builder → web | Écrasé à chaque étape par `log()`, supprimé en fin |
| `scinsta-build-result-<env>` | builder → web | Écrit en fin, consommé par `_scinsta_result_loop` (lifespan web) |
| `scinsta-build-log-<env>.txt` | builder → web | Tee stdout/stderr, lu incrémentalement par `GET /scinsta/logs?offset=N` |
| `scinsta-build-cancel-<env>` | web → systemd | Créé par `POST /scinsta/cancel`, déclenche `docker stop -t 2 scinsta-builder-<env>` |
| `scinsta-upload-<env>.ipa` | web → builder | IPA Instagram uploadée manuellement, supprimée par le web après intégration |

### Gotcha systemd : pas d'`ExecStartPre=/bin/rm`

**Piège contourné** (commit initial avait ce bug) : ne **pas** mettre d'`ExecStartPre=/bin/rm -f` pour supprimer le flag avant le run. Ça vide le fichier avant que `build.py` puisse lire son contenu JSON (`patch`, `requested_at`), donc le nom du patch est perdu et aucun patch n'est appliqué.

`read_flag_payload()` dans `build.py` fait déjà l'unlink **après** lecture. Le `PathExists=` unit ne retrigger que sur transition absent→présent, donc pas de boucle même si `build.py` crash : le flag reste en place et ne sera retraité qu'à la prochaine création par l'UI.

Voir [deploy/systemd/ipastore-scinsta-build@.service](../deploy/systemd/ipastore-scinsta-build@.service) pour les commentaires in-situ.

---

## 9. Output et convention de nommage

IPA final déposé dans `/srv/store-<env>/ipas/` sous la forme :

```
SCInsta-ig<igver>-sc<scsha>-<ipa_short_sha>.ipa
```

Exemple : `SCInsta-ig424.1.0-scdd125eb-e3e9c37756.ipa` (243 Mo avec `fix_ipa_scinsta.py`).

- `igver` : `CFBundleShortVersionString` de l'IPA source (lu via `read_ig_version`).
- `scsha` : short SHA du HEAD de `SoCuul/SCInsta` main.
- `ipa_short_sha` : 10 premiers caractères du SHA256 de l'IPA final (dédup + idempotence).

Le triple `(igver, scsha, patch)` détermine `build_version` côté BDD — permet de garder plusieurs builds d'une même version IG distincts si le commit SCInsta change entre deux runs.

---

## 10. Debug et rebuild local

### Rebuild depuis zéro (sans cache)

```bash
# SSH vers la VM dev/prod puis :
docker build --no-cache \
    -t scinsta-builder:latest \
    /opt/sideserver-prod/tools/scinsta-builder
```

~25 min (git-lfs pull du SDK domine). Rarement nécessaire — seul le changement de `build.py` ou du Dockerfile justifie ça.

### Run manuel (hors systemd)

```bash
docker run --rm --name scinsta-builder-manual \
    -v /etc/ipastore:/etc/ipastore \
    -v /srv/store-prod:/srv/store \
    -e IPASTORE_ENV=prod \
    scinsta-builder:latest
```

Utile pour reproduire un échec sans passer par l'UI (il faut juste avoir un `scinsta-upload-prod.ipa` et un `scinsta-build-requested-prod` en place).

### Log temps réel

Pendant un build, côté hôte :

```bash
tail -f /etc/ipastore/scinsta-build-log-dev.txt
```

Ou côté UI : onglet SCInsta, carte 3, le `<pre>` en dessous du bouton (poll 1.5 s).

### État du conteneur (si kill figé)

```bash
docker ps -a --filter name=scinsta-builder-
docker logs scinsta-builder-dev
```

---

## 11. Version matrix

| Composant | Version | Source |
|---|---|---|
| Base image | `debian:bookworm-slim` | Docker Hub |
| clang (toolchain iOS) | 11.1.0 | L1ghtmann/llvm-project (latest release) |
| Theos | HEAD `main` | github.com/theos/theos |
| SDK iOS | 16.5 (+symlink 16.2) | theos/sdks (LFS) |
| ldid | HEAD `main` | ProcursusTeam/ldid |
| cyan | HEAD `main` | asdfzxcvbn/pyzule-rw |
| ipapatch | v2.1.3 (`ARG IPAPATCH_VERSION`) | asdfzxcvbn/ipapatch releases |
| lief | ≥ 0.16 | PyPI |

SCInsta lui-même n'est **pas** pinné : clone `main --depth 1` à chaque build (fresh). C'est volontaire — l'admin veut toujours la dernière version du tweak.

---

## 12. Fichiers du repo

| Fichier | Rôle |
|---|---|
| [tools/scinsta-builder/Dockerfile](../tools/scinsta-builder/Dockerfile) | Image Theos + toolchain + SDK + stubs + cyan + ipapatch |
| [tools/scinsta-builder/build.py](../tools/scinsta-builder/build.py) | Pipeline du conteneur one-shot |
| [tools/scinsta-builder/README.md](../tools/scinsta-builder/README.md) | Pipeline + I/O (résumé court) |
| [deploy/systemd/ipastore-scinsta-build@.service](../deploy/systemd/ipastore-scinsta-build@.service) | Runner systemd (timeout 30 min) |
| [deploy/systemd/ipastore-scinsta-build@.path](../deploy/systemd/ipastore-scinsta-build@.path) | Watcher du flag |
| [deploy/systemd/ipastore-scinsta-cancel@.service](../deploy/systemd/ipastore-scinsta-cancel@.service) | Cancel via `docker stop -t 2` (SIGTERM → 2s → SIGKILL) |
