# CLAUDE.md — Contexte et instructions pour Claude Code

Ce fichier est lu automatiquement par Claude Code à chaque session.
Il est tracké dans git mais exclu des déploiements serveur via sparse-checkout.

---

## Projet : IPA Store (sideserver_website)

Interface d'administration self-hosted pour distribuer des fichiers IPA (.ipa) à SideStore (sideloading iOS). L'administrateur upload des IPAs via l'interface web ; SideStore les récupère via un feed `source.json`.

**Repo GitHub (privé)** : `MattTen/sideserver_website`
**VM de production** : Debian, IP `192.168.0.202`

---

## Stack technique

| Composant | Technologie |
|---|---|
| Backend | FastAPI + Uvicorn (Python 3.13) |
| ORM | SQLAlchemy 2.0 (mapped_column style) |
| Base de données | MariaDB sur l'hôte (2 schémas séparés) |
| Templates | Jinja2 |
| Conteneurisation | Docker + docker-compose |
| Auth sessions | bcrypt + itsdangerous (TimestampSigner) |

---

## Architecture

```
┌────────────────────── VM 192.168.0.202 ──────────────────────────┐
│                                                                   │
│  MariaDB (hôte, port 3306)                                       │
│   ├── ipastore-prod  (user: ipastore-prod)                       │
│   └── ipastore-dev   (user: ipastore-dev)                        │
│                                                                   │
│  Docker                                                           │
│   ├── sidestore-website-prod  :80    ← /etc/ipastore/prod.env    │
│   └── sidestore-website-dev   :8080  ← /etc/ipastore/dev.env     │
│                                                                   │
│  Filesystem hôte                                                  │
│   ├── /opt/sideserver-prod   (git clone, branche main/tag)       │
│   ├── /opt/sideserver-dev    (git clone, branche dev)            │
│   ├── /opt/sideserver-tools  (git clone, script management)      │
│   ├── /srv/store-prod/       (IPAs, icônes, screenshots prod)    │
│   ├── /srv/store-dev/        (idem dev)                          │
│   └── /etc/ipastore/         (credentials, version files, flags) │
│                                                                   │
│  Script de gestion                                                │
│   └── /usr/local/bin/website-management → symlink vers           │
│       /opt/sideserver-tools/tools/website-management.sh          │
└───────────────────────────────────────────────────────────────────┘
```

---

## Structure du repo

```
app/
  config.py         # Variables d'environnement + chemins
  db.py             # Engine SQLAlchemy, session factory
  models.py         # ORM : User, Setting, App, Version, News
  auth.py           # bcrypt + TimestampSigner + dépendances FastAPI
  ipa.py            # Parser IPA (ZIP + Info.plist + extraction icône)
  source_gen.py     # Génération du feed source.json pour SideStore
  updates.py        # Polling GitHub releases + flag-file pour MAJ
  patches.py        # Découverte + exécution des scripts patch/ (PatchInfo, run_patch)
  scinsta.py        # Onglet SCInsta : check decrypt.day + upload IPA + flag build + intégration result
  templates.py      # Instance Jinja2 + filtres (size, date)
  main.py           # create_app(), montage routes + static, lifespan (update check + scinsta result loop)
  routes/
    auth.py         # /login /logout /setup
    dashboard.py    # / (tableau de bord)
    apps.py         # /apps/** (upload, détail, édition, suppression)
    settings.py     # /settings (métadonnées store + mot de passe)
    public.py       # /source.json /qr.svg (sans auth)
    updates.py      # /settings/updates/check|apply
    news.py         # /news/** (articles du feed SideStore)
    patches.py      # /patches/** (listing, détail, rename, run)
    scinsta.py      # /scinsta/** (UI + status + check + upload + build)

patch/              # Scripts de patch IPA (copiés dans l'image via Dockerfile)
  fix_ipa.py        # Patch générique : FAT→thin arm64, strip signature (ldid assertion)
  fix_ipa_scinsta.py  # Idem + suppression Extensions/ (SCInsta / IXErrorDomain Code=8)

templates/          # Jinja2 HTML
  _icons/           # SVG inline (Feather-style) : wrench.svg (Patch), instagram.svg (SCInsta)…
  scinsta.html      # UI onglet SCInsta (check version + upload dropzone + build + polling)
static/             # CSS (style.css), JS (app.js), default-app.png
tools/
  website-management.sh   # Script de gestion prod/dev (voir ci-dessous)
  schema-sync.py          # Génère un plan SQL additif pour aligner le schéma d'une BDD sur une autre
  scinsta-builder/        # Conteneur one-shot pour builder SCInsta (lancé par systemd)
    Dockerfile            # Theos + SDK iOS 16.5 + cyan + ipapatch + lief
    build.py              # Clone SCInsta main → build.sh sideload → patch optionnel → store
    README.md             # Pipeline + I/O
deploy/
  systemd/          # Units systemd :
                    #   ipastore-update@.{path,service}         — MAJ code
                    #   ipastore-scinsta-build@.{path,service}  — build SCInsta
                    #   ipastore-scinsta-cancel@.{path,service} — kill build SCInsta en cours
  bootstrap.sh      # Script d'installation initiale (exécuté en root)
documentation/      # Documentation serveur et credentials (exclu du serveur)
  server.md         # Architecture, déploiement, features, onglet Patch (pointer vers scinsta_builder.md pour SCInsta)
  databases.md      # Schéma BDD complet (tables + settings keys)
  credentials.md    # Cycle de vie des secrets
  scinsta_builder.md        # Doc complète onglet SCInsta (flux, pipeline, bypass CF, URL editable)
  patch_fix_ipa.md          # Doc technique fix_ipa.py
  patch_fix_ipa_scinsta.md  # Doc technique fix_ipa_scinsta.py
CLAUDE.md           # Ce fichier (exclu du serveur)
Dockerfile          # COPY patch ./patch inclus
docker-compose.yml
requirements.txt    # lief>=0.16 requis pour les scripts de patch
```

---

## Branches et workflow de déploiement

> **RÈGLE ABSOLUE : tout développement sur la branche `dev` uniquement.**
> Ne jamais committer ou pusher directement sur `main`.

| Branche | Déployée sur | Méthode |
|---|---|---|
| `dev` | conteneur dev (port 8080) | rolling — `website-management dev-update` |
| `main` | conteneur prod (port 80) | release-based — `website-management prod-update` |

**Publier une release prod** :
1. Merger `dev` → `main` (PR ou fast-forward)
2. Créer une release GitHub avec tag semver (ex: `v1.2.0`) via l'UI GitHub ou l'API
3. L'UI admin ou `prod-check` détectera la MAJ → `prod-update` déploiera

---

## Accès SSH au serveur

```bash
# Connexion (plink avec hostkey pour éviter le prompt interactif)
plink -batch -ssh -pw altuser \
  -hostkey "SHA256:TojKt8WJuS1VChTBlb5miM6H/M/K+y1DLD1S1VAgSgc" \
  altuser@192.168.0.202 "commande"
```

- **User** : `altuser` / **Password** : `altuser`
- `altuser` est dans le groupe `docker` → peut gérer les conteneurs sans sudo
- Il n'y a **pas de sudo** sur cette VM
- `root` password : `root` (pour les opérations exceptionnelles via `su root`)

---

## Script de management (`website-management`)

Toutes les commandes fonctionnent en tant qu'`altuser` (aucun sudo requis).

| Commande | Action |
|---|---|
| `prod-update` | Déploie la dernière release GitHub si > version actuelle |
| `prod-check` | Affiche current/latest/update_available (machine-readable) |
| `dev-update` | `git pull` branche dev + rebuild conteneur |
| `dev-check` | Retourne toujours update_available=0 (dev est rolling) |
| `self-update` | Met à jour le script depuis /opt/sideserver-tools |
| `sync` | Sync TOTALE prod → dev (BDD + fichiers, écrase dev) |
| `sync-to-prod` | Sync TOTALE dev → prod (BDD + fichiers, **IRRÉVERSIBLE**) |
| `sync-schema-to-prod` | Aligne la structure de la BDD prod sur dev (tables/colonnes/index/FKs manquants) — pas de données touchées, additif uniquement via `tools/schema-sync.py` |
| `prod-reset-users` | Supprime tous les admins prod + crée un nouveau |
| `dev-reset-users` | Idem sur dev |
| `status` | État des conteneurs + versions déployées |
| `prod-start/stop/restart/logs` | Gestion du conteneur prod |
| `dev-start/stop/restart/logs` | Gestion du conteneur dev |

---

## Système de mise à jour (flag-file + systemd)

```
[conteneur] request_update()
      ↓ écrit /etc/ipastore/update-requested-{env}
[hôte] ipastore-update@{env}.path (path unit systemd, active)
      ↓ détecte le fichier
[hôte] ipastore-update@{env}.service
      ↓ supprime le flag + exécute website-management {env}-update
[hôte] rebuild + redémarrage du conteneur
```

Le vérificateur automatique tourne en arrière-plan dans le conteneur toutes les 6h (`_update_check_loop` dans `main.py`).

---

## Credentials et fichiers sensibles

Tous dans `/etc/ipastore/` (altuser:altuser 750, monté en volume dans les conteneurs).

| Fichier | Contenu |
|---|---|
| `.git-credentials` | PAT GitHub fine-grained (Contents read-only) pour lire les releases |
| `.mysql.cnf` | Credentials MySQL du user `ipastore-mgmt` (utilisé par le script) |
| `prod.env` / `dev.env` | DB_URL + `STORE_DIR=/srv/store-{prod\|dev}` + variables d'environnement des conteneurs — c'est ce qui isole les données (IPAs, icônes, BDD) entre les deux environnements avec le même code |
| `secret_key.prod` / `secret_key.dev` | Clé HMAC de signature des cookies (64 octets) |
| `prod.version` / `dev.version` | Version actuellement déployée |

Voir `documentation/credentials.md` pour le détail complet.

---

## Structure des bases de données

5 tables, créées automatiquement au boot par SQLAlchemy (`Base.metadata.create_all()`).

| Table | Rôle |
|---|---|
| `users` | Comptes admin (username + hash bcrypt) |
| `settings` | Paramètres clé/valeur du magasin (nom, base_url, tint, icône/header store, noms/descriptions des patchs…) |
| `apps` | Métadonnées des apps iOS (bundle_id, nom, icône…) |
| `versions` | IPAs uploadés — relation N/1 vers `apps` (cascade delete) |
| `news` | Articles du feed SideStore (titre, caption, image, notify, lien app) |

Détail complet des colonnes, index et contraintes : `documentation/databases.md`.

---

## Règles de développement

### 1. Branche
Toujours travailler sur `dev`. Ne jamais pusher sur `main` directement.

### 2. Commentaires
Commenter le **WHY**, pas le WHAT. Ajouter un commentaire quand :
- La logique est non-évidente (ex: rename atomique, pool_recycle, CORS ouvert)
- Il y a une contrainte cachée (ex: même filesystem pour tmp → final)
- Un choix technique a été fait pour une raison spécifique (ex: bcrypt rounds=12)
- Une sécurité est en place (ex: échappement SQL injection dans le script bash)

Ne pas commenter ce que le nom de la fonction ou variable exprime déjà.

### 3. Prod — interdiction absolue
**Claude ne doit jamais intervenir directement sur la prod.** Cela inclut :
- Toute commande SSH ciblant le conteneur prod ou la BDD `ipastore-prod`
- Tout appel à `website-management prod-*` qui modifie des données (reset-users, sync-to-prod…)
- Toute modification directe de `/srv/store-prod/` ou `/etc/ipastore/prod.env`

Le seul flux autorisé : dev → prod via `sync-to-prod`, et uniquement à la demande **explicite** de l'utilisateur après validation en dev.

### 4. Documentation
**À chaque feature ajoutée, modifiée ou supprimée** : mettre à jour les fichiers concernés dans `documentation/` :
- `documentation/server.md` → architecture, script, déploiement, systemd, onglet Patch
- `documentation/scinsta_builder.md` → tout changement sur l'onglet SCInsta (UI, pipeline, bypass CF, URL source…)
- `documentation/credentials.md` → tout ce qui touche aux credentials
- `documentation/databases.md` → toute modification du schéma BDD (tables + clés `settings`)
- `documentation/patch_fix_ipa.md` + `patch_fix_ipa_scinsta.md` → si les scripts de patch évoluent

### 5. Onglet Patch
- Les scripts dans `patch/` sont auto-découverts à chaque requête (pas de registration).
- Contrat CLI strict : `script.py -s /chemin/vers/app.ipa` — le script écrase l'IPA en place.
- Ajouter un patch = créer le `.py` dans `patch/` sur GitHub + pull + rebuild.
- Nom d'affichage : clé `patch_display_name:{filename}` dans `settings`.
- Description : clé `patch_description:{filename}` dans `settings`.
- Après exécution réussie, `size` et `sha256` de la version sont recalculés en BDD.

### 6. Onglet SCInsta
- Check de version sur decrypt.day via `curl_cffi` (TLS impersonation Chrome) — passe Cloudflare. Fallback multi-impersonations puis urllib.
- Upload manuel de l'IPA Instagram (le bouton de téléchargement decrypt.day est derrière Turnstile interactif, pas bypass-able proprement) — **pas** de scraping automatique de l'IPA.
- Le build clone **toujours** `main` de `SoCuul/SCInsta` (fresh), jamais la release `.deb`.
- Pipeline systemd `ipastore-scinsta-build@{env}.path` → conteneur one-shot `tools/scinsta-builder/` → IPA déposé dans `/srv/store-{env}/ipas/` → watcher lifespan crée Version + News.
- `bundle_id = com.burbn.instagram` ; `build_version = <short_sha_scinsta>` pour éviter le conflit `UNIQUE(app_id, version, build_version)`.
- `ig_deployed` (version intégrée) est lu **depuis la table `versions`** (dernière `uploaded_at` de `com.burbn.instagram`), pas depuis les settings. Ça reste correct quand l'admin upload l'IPA manuellement via l'onglet Apps.
- URL source modifiable via l'UI (clé `scinsta_decrypt_url`, route `POST /scinsta/source`). Défaut : `https://decrypt.day/app/id389801252`.
- **Sortie build temps réel** : `tools/scinsta-builder/build.py` tee `stdout`/`stderr` vers `/etc/ipastore/scinsta-build-log-<env>.txt` (line-buffered) ; l'UI poll `GET /scinsta/logs?offset=N` toutes les 1.5s pour afficher le delta dans un `<pre>`.
- **Annulation d'un build** : bouton `Annuler le build` → `POST /scinsta/cancel` → flag `scinsta-build-cancel-<env>` → path unit `ipastore-scinsta-cancel@<env>.path` → `docker kill --signal=SIGTERM scinsta-builder-<env>` + écrit un result failed. Conteneur builder nommé `scinsta-builder-<env>` (nom déterministe requis pour le kill).
- Alerte "IPA prête : V{version}" : `upload_version` lu dans l'Info.plist de `scinsta-upload-<env>.ipa` (exposé dans le state).
- Patch optionnel au build = n'importe quel script de `patch/` (même contrat CLI que l'onglet Patch — **écrase l'IPA en place**, pas d'original préservé).
- État persistant dans `settings` via clés `scinsta_*` (voir [databases.md](documentation/databases.md)).
- **Doc complète** : [documentation/scinsta_builder.md](documentation/scinsta_builder.md).

### 7. Static files
Les assets publics (IPAs, icônes, screenshots) sont servis via `StaticFiles` montés sur `/ipas`, `/icons`, `/screenshots` depuis `STORE_DIR`. Ces URLs apparaissent dans `source.json` et sont accédées directement par SideStore sans authentification.

### 8. source.json
- Servi sans cache (`Cache-Control: no-cache`) et avec CORS ouvert (`*`)
- `iconURL` ne doit **jamais** être une chaîne vide (SideStore rejette) → fallback sur `/static/default-app.png`
- `downloadURL` pointe vers `/ipas/{filename}` — les fichiers doivent exister dans `STORE_DIR/ipas/`

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

Pour vérifier : `git sparse-checkout list` dans `/opt/sideserver-{prod,dev,tools}`.
