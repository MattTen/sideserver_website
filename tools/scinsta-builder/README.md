# scinsta-builder

Conteneur one-shot qui build une IPA Instagram patchée avec SCInsta à partir
d'une IPA Instagram décryptée fournie manuellement par l'admin.

## Pipeline

1. Lit le flag `/etc/ipastore/scinsta-build-requested-<env>` (contenant
   optionnellement `{"patch": "fix_ipa.py"}`).
2. Copie l'IPA uploadée `/etc/ipastore/scinsta-upload-<env>.ipa` en working dir.
3. `git clone --recursive --depth 1 --branch main SoCuul/SCInsta` (FRESH).
4. Place l'IPA dans `packages/com.burbn.instagram.ipa` (glob attendu par
   `build.sh`).
5. `./build.sh sideload` → Theos compile dylibs, cyan injecte, ipapatch
   patche les extensions.
6. Si un patch a été choisi, lance `python3 patch/<file>.py -s <ipa_final>`.
7. Dépose l'IPA dans `/srv/store/ipas/SCInsta-ig<version>-sc<sha>-<short>.ipa`.
8. Écrit le result JSON dans `/etc/ipastore/scinsta-build-result-<env>`.

## Dépendances installées dans l'image

- **Theos** (cloné dans `/opt/theos`) avec SDK iPhoneOS 16.5
- **cyan** (`pyzule-rw`, git)
- **ipapatch** (git, optionnel — fallback sans si indispo)
- **CMake** (requis par le sous-module FLEXing)
- **lief** + **requests**
- Toolchain Debian : clang, make, perl, ldid, fakeroot, zstd…

## Inputs / Outputs

| Chemin (monté) | Sens | Contenu |
|---|---|---|
| `/etc/ipastore/scinsta-build-requested-<env>` | in | Flag JSON `{patch,requested_at}` |
| `/etc/ipastore/scinsta-upload-<env>.ipa` | in | IPA Instagram officielle |
| `/etc/ipastore/patches-<env>/` | in | Scripts de patch (sync depuis l'app) |
| `/opt/ipaserver/patch/` | in (fallback) | Patchs depuis le clone hôte |
| `/srv/store/ipas/` | out | IPA final |
| `/etc/ipastore/scinsta-build-progress-<env>` | out | Progression JSON |
| `/etc/ipastore/scinsta-build-result-<env>` | out | Résultat JSON final |

## Invocation

Le conteneur est lancé par `website-management <env>-scinsta-build`,
lui-même déclenché par le path unit systemd
`ipastore-scinsta-build@<env>.path`.

## Durée typique

5-15 minutes selon la connexion (clone SCInsta + submodules + SDK iOS
déjà cached dans l'image). Timeout systemd à 30 min.
