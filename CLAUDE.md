# CLAUDE.md — Contexte et instructions pour Claude Code

Ce fichier est lu automatiquement par Claude Code à chaque session.
Il est tracké dans git mais exclu des déploiements serveur via sparse-checkout.

---

## Projet : IPA Store (sideserver_website)

Interface d'administration self-hosted pour distribuer des fichiers IPA (.ipa) à SideStore (sideloading iOS). L'administrateur upload des IPAs via l'interface web ; SideStore les récupère via un feed `source.json`.

**Repo GitHub (public)** : `MattTen/sideserver_website`

---

## Stack technique

| Composant | Technologie |
|---|---|
| Backend | FastAPI + Uvicorn (Python 3.13) |
| ORM | SQLAlchemy 2.0 (mapped_column style) |
| Base de données | MySQL ou MariaDB (externe, saisie via UI) |
| Templates | Jinja2 |
| Conteneurisation | Docker + docker-compose |
| Auth sessions | bcrypt + itsdangerous (TimestampSigner) |

---

## Modèle mono-environnement (1 VM = 1 env)

Chaque VM héberge **un seul** environnement. Dev et prod vivent sur des machines séparées.

| VM | Branche Git | Mode MAJ |
|---|---|---|
| **Dev** (home lab, LXC Ubuntu 22.04, `192.168.0.210`) | `dev` | rolling — `website-management update` |
| **Prod** (cloud, TBD) | `main` | release-based — releases GitHub |

**Les deux VM utilisent des paths strictement identiques** — seule la branche git clonée diffère :

```
/opt/sideserver-prod      ← git clone (branche selon VM)
/srv/store-prod/          ← IPAs + icônes + screenshots
/etc/ipastore/            ← credentials + version + flags
ipastore-website          ← nom du conteneur (le script détecte le vrai mode
                            via la branche git checkoutée)
```

Les units systemd sont nommées `ipastore-update@prod.{path,service}` (instance toujours `prod`) car `website-management update` détermine dynamiquement le comportement depuis `git rev-parse --abbrev-ref HEAD`.

---

## Bootstrap & déploiement

Un **seul** script `deploy/bootstrap.sh` auto-suffisant : `curl | sudo bash` installe Docker, clone le repo sur `main`, checkout le **dernier tag de release** (HEAD détaché), écrit les units systemd (embedded en heredoc), écrit `/etc/ipastore/{prod.env,secret_key.prod,prod.version}`, et démarre le conteneur. Si aucune release n'existe, fallback sur `main` avec version `rolling-main-<sha>`.

```bash
curl -sSL https://raw.githubusercontent.com/MattTen/sideserver_website/main/deploy/bootstrap.sh | sudo bash
```

Vars optionnelles : `BASE_URL` (si absent, l'app dérive via `--proxy-headers`), `GITHUB_USER`, `GITHUB_TOKEN` (seulement si repo privé), `HOST_PORT` (défaut `80`).

La VM démarre toujours en **env prod** (dernière release). Pour basculer après coup :

```bash
website-management switch-dev    # bascule sur la branche dev (rolling)
website-management switch-prod   # revient sur la derniere release
```

Après bootstrap : l'UI redirige automatiquement vers `/setup/database` pour saisir la connexion BDD puis `/setup` pour créer le compte admin.

> **Note SSH** : le bootstrap ajoute l'app user au groupe `docker`, mais les sessions SSH ouvertes avant ne voient pas le nouveau groupe. `exit` + reconnect, ou `newgrp docker`.

---

## Structure du repo

```
app/
  config.py         # Variables d'environnement + chemins
  db.py             # Engine SQLAlchemy, session factory
  db_config.py      # Résolution dynamique de DB_URL (db.json → env)
  models.py         # ORM : User, Setting, App, Version, News
  auth.py           # bcrypt + TimestampSigner + dépendances FastAPI
  ipa.py            # Parser IPA (ZIP + Info.plist + extraction icône)
  source_gen.py     # Génération du feed source.json pour SideStore
  updates.py        # Polling GitHub releases + flag-file pour MAJ
  patches.py        # Découverte + exécution des scripts patch/
  scinsta.py        # Onglet SCInsta : check decrypt.day + upload IPA + flag build
  templates.py      # Instance Jinja2 + filtres (size, date)
  main.py           # create_app(), routes + static, lifespan
  routes/
    auth.py         # /login /logout /setup
    setup.py        # /setup/database (saisie BDD au premier démarrage)
    dashboard.py    # /
    apps.py         # /apps/**
    settings.py     # /settings
    public.py       # /source.json /qr.svg
    updates.py      # /settings/updates/check|apply
    news.py         # /news/**
    patches.py      # /patches/**
    scinsta.py      # /scinsta/**

patch/              # Scripts de patch IPA (copiés dans l'image)
  fix_ipa.py        # FAT→thin arm64, strip signature
  fix_ipa_scinsta.py  # Idem + suppression Extensions/

templates/          # Jinja2 HTML
static/             # CSS, JS, default-app.png

tools/
  website-management.sh   # Script de gestion mono-env (auto-detect branche)
  schema-sync.py          # Plan SQL additif pour aligner un schéma BDD sur un autre
  scinsta-builder/        # Conteneur one-shot pour builder SCInsta (systemd)

deploy/
  bootstrap.sh            # Bootstrap unique (clone main + checkout derniere release, units systemd embedded)

documentation/            # Doc technique (exclu du serveur via sparse-checkout)
  server.md               # Architecture, déploiement, features
  databases.md            # Schéma BDD complet
  credentials.md          # Cycle de vie des secrets
  scinsta_builder.md      # Doc onglet SCInsta (UI/web)
  scinsta_build.md        # Doc pipeline de build SCInsta
  patch_fix_ipa.md
  patch_fix_ipa_scinsta.md

CLAUDE.md           # Ce fichier (exclu du serveur)
Dockerfile
docker-compose.yml
requirements.txt
```

---

## Branches et workflow

> **RÈGLE ABSOLUE : tout développement sur la branche `dev` uniquement.**
> Ne jamais committer ou pusher directement sur `main`.

**Publier une release prod** :
1. Merger `dev` → `main` (PR ou fast-forward)
2. Créer une release GitHub avec tag semver (ex: `v1.2.0`)
3. Sur la VM prod, l'UI `/settings` détecte la MAJ toutes les 6 h et affiche le bouton "Appliquer" → `ipastore-update@prod.service` → `website-management update` (mode release sur branche `main`)

---

## Script de management (`website-management`)

Auto-détection du mode via `git rev-parse --abbrev-ref HEAD` dans `/opt/sideserver-prod` :
- `main` ou `HEAD` (détaché sur un tag de release) = mode **prod** (release-based)
- `dev` = mode **dev** (rolling sur HEAD de la branche)

| Commande | Action |
|---|---|
| `update` | Prod : checkout dernière release si > current / Dev : `git pull` branche courante |
| `check` | Affiche current/latest/update_available (machine-readable) |
| `pull` | Force `git pull` HEAD de la branche courante (dev uniquement — refuse sur HEAD détaché) |
| `self-update` | `git pull` du repo (dev) ou re-checkout de la dernière release (prod) |
| `switch-dev` | Bascule la VM en env dev : checkout branche `dev` + reset hard + rebuild |
| `switch-prod` | Revient en env prod : checkout dernière release + rebuild |
| `reset-users` | Supprime tous les admins + crée un nouveau (via `docker exec`) |
| `status` | État du conteneur + version déployée |
| `start/stop/restart/logs` | Gestion du conteneur |

Aliases systemd (ne pas utiliser en ligne de commande, conservés pour compat avec les unit names `@prod`) : `prod-update`, `prod-scinsta-build`, `prod-scinsta-cancel`.

---

## Système de mise à jour (flag-file + systemd)

```
[conteneur] request_update()
      ↓ écrit /etc/ipastore/update-requested-prod
[hôte] ipastore-update@prod.path (path unit systemd)
      ↓ détecte le fichier
[hôte] ipastore-update@prod.service
      ↓ supprime le flag + exécute website-management update
[hôte] rebuild + redémarrage du conteneur
```

Le vérificateur automatique tourne en arrière-plan dans le conteneur toutes les 6h (`_update_check_loop` dans `main.py`).

---

## Credentials et fichiers sensibles

Tous dans `/etc/ipastore/` (app-user:app-user 750, monté en volume dans le conteneur). Les noms de fichiers contiennent toujours `prod` quel que soit le mode réel (mono-env).

| Fichier | Contenu |
|---|---|
| `.git-credentials` | PAT GitHub (optionnel, seulement si repo privé) |
| `prod.env` | `IPASTORE_STORE_DIR=/srv/store` + `IPASTORE_SECRET_FILE` + `IPASTORE_ENV=prod` + `IPASTORE_GITHUB_REPO` + éventuellement `IPASTORE_BASE_URL`. **Ne contient plus la connexion BDD**. |
| `db.json` | Config BDD (host/port/user/password/database) saisie via `/setup/database`. Mode 600, owner uid 1000. |
| `secret_key` | Clé HMAC de signature des cookies (64 octets, généré au 1er démarrage) |
| `prod.version` | Version actuellement déployée (tag ou `rolling-<sha>`) |
| `update-requested-prod` | Flag-file pour déclencher une MAJ |
| `scinsta-build-<env>.ipa` / `scinsta-build-log-<env>.txt` / etc. | I/O pipeline SCInsta |

Voir `documentation/credentials.md` pour le détail complet.

---

## Structure des bases de données

5 tables, créées automatiquement au boot par SQLAlchemy (`Base.metadata.create_all()`).

| Table | Rôle |
|---|---|
| `users` | Comptes admin (username + hash bcrypt) |
| `settings` | Paramètres clé/valeur du magasin |
| `apps` | Métadonnées des apps iOS |
| `versions` | IPAs uploadés (relation N/1 vers `apps`, cascade delete) |
| `news` | Articles du feed SideStore |

Détail complet : `documentation/databases.md`.

---

## Règles de développement

### 1. Branche
Toujours travailler sur `dev`. Ne jamais pusher sur `main` directement.

### 2. Commentaires
Commenter le **WHY**, pas le WHAT. Ajouter un commentaire quand :
- La logique est non-évidente (rename atomique, pool_recycle, CORS ouvert)
- Il y a une contrainte cachée (même filesystem pour tmp → final)
- Un choix technique a été fait pour une raison spécifique (bcrypt rounds=12)
- Une sécurité est en place (échappement SQL injection)

Ne pas commenter ce que le nom exprime déjà.

### 3. Prod — interdiction absolue
**Claude ne doit jamais intervenir directement sur la VM de prod.** Cela inclut :
- Toute commande SSH ciblant la VM prod
- Toute modification directe de `/srv/store-prod/` ou `/etc/ipastore/prod.env` sur prod
- Tout appel à `website-management update` sur prod

Le flux autorisé : merger `dev` → `main` + release GitHub, et uniquement à la demande **explicite** de l'utilisateur après validation en dev.

### 4. Documentation
**À chaque feature ajoutée, modifiée ou supprimée** : mettre à jour les fichiers concernés dans `documentation/` :
- `documentation/server.md` → architecture, script, déploiement, systemd
- `documentation/scinsta_builder.md` → tout changement onglet SCInsta côté UI
- `documentation/scinsta_build.md` → tout changement pipeline de build
- `documentation/credentials.md` → tout ce qui touche aux credentials
- `documentation/databases.md` → toute modification du schéma BDD
- `documentation/patch_fix_ipa.md` + `patch_fix_ipa_scinsta.md` → si les scripts de patch évoluent

### 5. Onglet Patch
- Scripts dans `patch/` auto-découverts à chaque requête (pas de registration).
- Contrat CLI strict : `script.py -s /chemin/vers/app.ipa` — écrase l'IPA en place.
- Ajouter un patch = créer le `.py` dans `patch/` + commit + rebuild.
- Nom d'affichage : clé `patch_display_name:{filename}` dans `settings`.
- Description : clé `patch_description:{filename}` dans `settings`.
- Après exécution, `size` et `sha256` de la version sont recalculés en BDD.

### 6. Onglet SCInsta
- Check de version sur decrypt.day via `curl_cffi` (TLS impersonation Chrome). Fallback multi-impersonations puis urllib.
- Upload manuel de l'IPA Instagram (Turnstile interactif, pas bypass).
- Build clone **toujours** `main` de `SoCuul/SCInsta` (fresh).
- Pipeline systemd `ipastore-scinsta-build@prod.path` → conteneur one-shot `tools/scinsta-builder/` → IPA déposé dans `/srv/store-prod/ipas/` → watcher lifespan crée la Version (changelog `Instagram <v> + SCInsta`, override via `scinsta_meta_changelog`). **Pas d'article news auto**. Métadonnées App (`name`, `developer_name`, `subtitle`, etc.) figées côté build, éditables via la section `Métadonnées` de l'onglet SCInsta.
- `bundle_id = com.burbn.instagram` ; `build_version = CFBundleVersion` de l'IPA (obligatoire : SideStore compare `buildVersion` du source.json au `CFBundleVersion` de l'IPA et refuse l'install si ça diverge). Les rebuilds de la même version IG remplacent la ligne Version en place.
- `ig_deployed` lu depuis la table `versions` (dernière `uploaded_at` de `com.burbn.instagram`).
- URL source modifiable via l'UI (clé `scinsta_decrypt_url`). Défaut : `https://decrypt.day/app/id389801252`.
- **Sortie build temps réel** : `tools/scinsta-builder/build.py` tee vers `/etc/ipastore/scinsta-build-log-prod.txt` (line-buffered) ; l'UI poll `GET /scinsta/logs?offset=N` toutes les 1.5s.
- **Annulation** : bouton → `POST /scinsta/cancel` → flag `scinsta-build-cancel-prod` → path unit → `docker stop -t 2 scinsta-builder-prod` (SIGTERM → 2s → SIGKILL). `docker kill --signal=SIGTERM` ne fonctionne pas : `build.py` est PID 1 et le kernel ignore SIGTERM sans handler pour PID 1.
- Patch optionnel au build = n'importe quel script de `patch/` (même contrat CLI).
- État persistant dans `settings` via clés `scinsta_*`.
- Doc UI : [documentation/scinsta_builder.md](documentation/scinsta_builder.md).
- Doc pipeline : [documentation/scinsta_build.md](documentation/scinsta_build.md).

### 7. Static files
Les assets publics (IPAs, icônes, screenshots) sont servis via `StaticFiles` montés sur `/ipas`, `/icons`, `/screenshots` depuis `STORE_DIR`. Ces URLs apparaissent dans `source.json` et sont accédées directement par SideStore sans authentification.

### 8. source.json
- Servi sans cache (`Cache-Control: no-cache`) et avec CORS ouvert (`*`)
- `iconURL` ne doit **jamais** être une chaîne vide (SideStore rejette) → fallback sur `/static/default-app.png`
- `downloadURL` pointe vers `/ipas/{filename}` — les fichiers doivent exister dans `STORE_DIR/ipas/`
- L'URL publique est dérivée dynamiquement depuis `request.base_url` (via `--proxy-headers --forwarded-allow-ips=*`) sauf si `IPASTORE_BASE_URL` est forcé

### 9. Commits
Format : `type(scope): description courte` (conventionnel).
Toujours avec co-auteur :
```
Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

---

## Fichiers exclus des déploiements serveur

Via sparse-checkout (config locale git sur la VM, pas dans .gitignore) :
- `documentation/` — doc technique, inutile sur le serveur
- `CLAUDE.md` — ce fichier

Pour vérifier : `git sparse-checkout list` dans `/opt/sideserver-prod`.
