# SideServer Website — Fonctionnement côté serveur

Doc de référence du déploiement : ce qui tourne sur la VM, comment c'est organisé, comment on met à jour.

---

## 1. Vue d'ensemble

- **Repo** : `github.com/MattTen/sideserver_website` (public)
- **Modèle mono-environnement** : **1 VM = 1 environnement**. Dev et prod vivent sur des machines séparées, pas côte-à-côte. Un **seul** bootstrap (`deploy/bootstrap.sh`) qui déploie toujours la dernière release ; la bascule dev/prod après coup se fait via le script de management (`switch-dev` / `switch-prod`).
  - VM **prod** (cloud) : HEAD détaché sur le tag de release, mode release-based
  - VM **dev** (home lab) : branche `dev` checkoutée, mode rolling
- **Stack** : FastAPI + Uvicorn dans Docker, MySQL/MariaDB externe (sur la même VM ou distant)
- Les deux VM utilisent des **paths strictement identiques** — seul le ref git checkouté diffère.

```
┌────────────── VM (dev OU prod) ─────────────────────────────┐
│                                                              │
│  MySQL/MariaDB (saisi via UI au premier démarrage)          │
│                                                              │
│  Docker                                                      │
│   └── sidestore-website-prod  :80                            │
│       env_file=/etc/ipastore/prod.env                        │
│       volumes: /srv/store-prod:/srv/store, /etc/ipastore     │
│                                                              │
│  Filesystem                                                  │
│   /srv/store-prod/{ipas,icons,screenshots,news}              │
│   /etc/ipastore/{prod.env, db.json, secret_key, prod.version}│
│   /opt/sideserver-prod  (git clone ; ref selon env courant)  │
│                                                              │
│  systemd                                                     │
│   ipastore-update@prod.path         watches update-requested-prod  │
│   ipastore-scinsta-build@prod.path  watches scinsta-build-requested-prod │
│   ipastore-scinsta-cancel@prod.path watches scinsta-build-cancel-prod    │
└──────────────────────────────────────────────────────────────┘
```

Le conteneur s'appelle toujours `sidestore-website-prod` et le store `/srv/store-prod` quel que soit l'environnement réel : le mode prod vs dev est détecté **dynamiquement par le script de management** via `git rev-parse --abbrev-ref HEAD` (branche `main` ou `HEAD` détaché sur un tag = prod ; `dev` = dev).

---

## 2. Layout sur disque

### Clone git

Un seul clone par VM : `/opt/sideserver-prod`. Aucun sparse-checkout côté serveur par défaut (l'ancien setup sparse excluait `tools/` et `documentation/` — à re-configurer manuellement si souhaité).

La branche checkoutée détermine le mode :
- `main` ou tag `vX.Y.Z` (HEAD détaché) → **prod**
- `dev` (ou toute autre branche) → **dev**

Pour éviter "dubious ownership" si le clone est owned par un autre user que celui qui invoque git, `safe.directory` est configuré au bootstrap.

Le script de management vit dans `/opt/sideserver-prod/tools/website-management.sh`, symlinké en `/usr/local/bin/website-management`.

### `/etc/ipastore/`

Répertoire 750 owned par l'app-user (uid 1000). Monté en volume dans le conteneur (à `/etc/ipastore`). Contient :

| Fichier | Owner | Mode | Rôle |
|---|---|---|---|
| `prod.env` | app-user | 640 | Variables du conteneur (`IPASTORE_STORE_DIR`, `IPASTORE_SECRET_FILE`, `IPASTORE_ENV`, `IPASTORE_GITHUB_REPO`, éventuellement `IPASTORE_BASE_URL`). **Pas la connexion BDD**. |
| `db.json` | uid 1000 | 600 | Config BDD (host/port/user/password/database) saisie via `/setup/database` |
| `secret_key` | uid 1000 | 600 | Clé signature cookies (64 octets, générée au 1er boot) |
| `prod.version` | app-user | 644 | Version déployée (tag ou `rolling-<sha>`) |
| `.git-credentials` | app-user | 600 | Token GitHub (optionnel — seulement si repo privé) |
| `update-requested-prod` | uid 1000 | 644 | Flag transitoire : présence = demande de MAJ |
| `scinsta-*-prod.{ipa,txt,json}` | uid 1000 | variés | I/O pipeline SCInsta |

### `/srv/store-prod/`

Monté dans le conteneur à `/srv/store`. Sous-dossiers :

- `ipas/` — binaires `.ipa` servis sur `/ipas/{filename}`
- `icons/` — icônes d'apps + icône et header du store (`_store-<token>.png`, `_header-<token>.png`), servis sur `/icons/{filename}`
- `screenshots/` — captures uploadées manuellement, servies sur `/screenshots/{filename}`
- `news/` — visuels joints aux articles d'actualités, servis sur `/news-img/{filename}`

---

## 3. Configuration (env vars du conteneur)

Toutes les vars sont injectées via `env_file: /etc/ipastore/prod.env` dans `docker-compose.yml`.

| Variable | Exemple | Rôle |
|---|---|---|
| `IPASTORE_STORE_DIR` | `/srv/store` | Racine binaires (monté depuis `/srv/store-prod`) |
| `IPASTORE_SECRET_FILE` | `/etc/ipastore/secret_key` | Clé cookies (lue au boot) |
| `IPASTORE_BASE_URL` | *(vide)* ou `http://store.mon-domaine.com` | URL publique forcée. **Si absent**, l'app dérive depuis `request.base_url` (via uvicorn `--proxy-headers --forwarded-allow-ips=*`). Ne définir que derrière un reverse proxy sans X-Forwarded correct. |
| `IPASTORE_ENV` | `prod` | Toujours `prod` dans le modèle mono-env (utilisé par le module updates + noms de fichiers SCInsta). |
| `IPASTORE_GITHUB_REPO` | `MattTen/sideserver_website` | Repo consulté pour les releases |

**Connexion BDD** : saisie via `/setup/database` au premier démarrage puis persistée dans `/etc/ipastore/db.json`. Ne transite jamais par les fichiers `.env` ni par git.

Le compose lui-même lit un `.env` local au clone (`/opt/sideserver-prod/.env`) — écrit par le bootstrap :

```
IMAGE_TAG=local
CONTAINER_NAME=sidestore-website-prod
HOST_PORT=80
ENV_FILE=/etc/ipastore/prod.env
STORE_PATH=/srv/store-prod
```

---

## 4. Script `website-management`

Unique script de gestion : `/opt/sideserver-prod/tools/website-management.sh`, symlinké en `/usr/local/bin/website-management`. Détection mono-env via `git rev-parse --abbrev-ref HEAD`.

### Usage

```bash
website-management                  # menu interactif (TUI)
website-management <commande>       # commande unique
website-management --help           # aide
```

### Commandes principales

| Commande | Action |
|---|---|
| `start` / `stop` / `restart` / `logs` / `status` | Gestion du conteneur (docker compose) |
| `update` | Prod (HEAD détaché sur tag ou branche `main`) : checkout dernière release si > current, rebuild. No-op si déjà à jour. Écrit `/etc/ipastore/prod.version`. / Dev (branche `dev`) : `git pull` + rebuild. |
| `check` | `current=…/latest=…/update_available=0|1` (machine-readable). En dev : toujours `update_available=0` (rolling). |
| `pull` | `git pull` HEAD de la branche courante + rebuild (dev uniquement — refuse sur HEAD détaché). |
| `self-update` | `git pull` dans `/opt/sideserver-prod` (dev) ou re-checkout de la dernière release (prod). |
| `switch-dev` | Bascule la VM en env dev : `git checkout dev` + `git reset --hard origin/dev` + rebuild. Écrit `rolling-dev-<sha>` dans `prod.version`. |
| `switch-prod` | Revient en env prod : `git checkout <latest-release-tag>` + rebuild. Équivalent à `update` forcé depuis une autre branche. |
| `reset-users` | Supprime tous les admins + prompt création d'un nouveau (via `docker exec`, pas de client mysql sur l'hôte requis). |

### Aliases systemd (ne pas utiliser en CLI)

Les units systemd sont nommées avec instance `prod` pour des raisons historiques : `ipastore-update@prod.service` appelle `website-management prod-update`, idem pour `prod-scinsta-build` et `prod-scinsta-cancel`. Ces aliases pointent vers les commandes ci-dessus (le script ignore l'instance `prod` et détecte le vrai mode via la branche).

---

## 5. Mécanisme de mise à jour

### Mode prod (HEAD détaché sur un tag de release)

La VM prod n'avance qu'à chaque release GitHub publiée. Chaque release porte un tag (`v1.0.0`, potentiellement `v14.26.35.2664.32` — format libre de dotted-numeric, avec un `v` optionnel). Le bootstrap initial et chaque `update` font un `git checkout --force <tag>` → HEAD détaché.

Trois sources de vérité :
1. GitHub Releases (source du "dernière version disponible")
2. `/etc/ipastore/prod.version` (source du "version actuellement déployée")
3. L'état du clone (`git rev-parse HEAD`, qui devrait matcher le tag)

Comparaison via `sort -V` (bash) ou tuple d'entiers (Python) : `v1.9 < v1.10`, `v1.0.0 < v1.0.1`. Le `v` initial est strippé avant comparaison.

### Mode dev (branche `dev`)

Rolling : pas de check de version. `update` fait `git pull` + rebuild. `/etc/ipastore/prod.version` contient `rolling-<sha court>`. `/settings/updates/check` côté dev renvoie toujours `rolling=true, update_available=false` → le bouton "Appliquer" reste grisé dans l'UI.

### Flux via CLI

```bash
website-management update
```

En mode prod :
1. `curl api.github.com/repos/.../releases/latest` → tag_name
2. Lit `/etc/ipastore/prod.version`
3. Si tag ≤ version actuelle → no-op
4. `git fetch --tags && git checkout --force <tag>` dans `/opt/sideserver-prod` (HEAD détaché)
5. `docker compose up -d --build`
6. Écrit le tag dans `/etc/ipastore/prod.version`

### Flux via l'UI

L'UI ne peut pas `docker compose` elle-même (elle tourne dans le conteneur). Elle demande au host via un **flag-file** :

```
1. Utilisateur clique "Appliquer la mise à jour" dans /settings
   └─> POST /settings/updates/apply (FastAPI)
       └─> écrit /etc/ipastore/update-requested-prod  (visible du host
            via le volume /etc/ipastore monté)

2. Sur le host, systemd path unit détecte le fichier :
   ipastore-update@prod.path  (PathExists=/etc/ipastore/update-requested-prod)
   └─> déclenche ipastore-update@prod.service :
       ExecStartPre=/bin/rm -f /etc/ipastore/update-requested-prod
       ExecStart=/usr/local/bin/website-management prod-update
       User=<app-user>

3. Le script fait son job (checkout + rebuild + up). Le conteneur redémarre.
   La page UI attend ~30s puis se recharge.
```

Le flag est supprimé **avant** l'update (`ExecStartPre`) pour éviter les boucles si l'update échoue.

### Vérification automatique (6h)

Dans `app/main.py`, un `asyncio.create_task` tourne dans le lifespan :

```python
async def _update_check_loop():
    await asyncio.sleep(30)
    while True:
        status = await asyncio.to_thread(get_status, True)
        logger.info("update-check %s", status)
        await asyncio.sleep(6 * 3600)
```

Il met à jour le cache in-memory (`_cache` dans `app/updates.py`) et logge. L'UI relit ce cache (ou redemande un check) quand on ouvre /settings.

---

## 6. Unités systemd

Toutes les units sont **embedded en heredoc dans le bootstrap** (`deploy/bootstrap.sh`) — elles ne sont plus stockées dans `deploy/systemd/` dans le repo. Instance toujours `prod` (mono-env).

### `ipastore-update@.path` / `.service`

```ini
# path
[Unit]
Description=Watch /etc/ipastore/update-requested-%i flag
[Path]
PathExists=/etc/ipastore/update-requested-%i
Unit=ipastore-update@%i.service
[Install]
WantedBy=multi-user.target
```

```ini
# service
[Unit]
Description=Apply update to %i environment
After=docker.service
Requires=docker.service
[Service]
Type=oneshot
User=<app-user>
Group=<app-user>
ExecStartPre=/bin/rm -f /etc/ipastore/update-requested-%i
ExecStart=/usr/local/bin/website-management %i-update
StandardOutput=journal
StandardError=journal
TimeoutStartSec=600
```

Activation : `systemctl enable --now ipastore-update@prod.path` (fait par le bootstrap).

Logs : `journalctl -u ipastore-update@prod.service -f`.

Les units SCInsta (`ipastore-scinsta-build@.{path,service}` et `ipastore-scinsta-cancel@.{path,service}`) suivent le même modèle — voir [scinsta_builder.md](scinsta_builder.md).

---

## 7. Workflow complet — exemple

### Release v1.2.0 (merge dev → main + tag)

```bash
# Sur ta machine
git checkout main
git merge dev                         # récupère ce qui a été validé sur dev
git push origin main
gh release create v1.2.0 --generate-notes

# Sur la VM prod (rien à faire tout de suite — elle reste sur v1.1.x)
# Option 1 : ouvrir l'UI /settings et cliquer "Appliquer"
# Option 2 : website-management update

# Ou attendre le check auto 6h puis agir depuis l'UI.
```

### Push dev

```bash
# Sur ta machine
git checkout dev
# … commit …
git push origin dev

# Sur la VM dev
website-management update    # rolling, git pull + rebuild
```

### Bascule rapide prod ↔ dev (mono-env, même VM)

```bash
# Basculer une VM prod en env dev (branche dev, rolling)
website-management switch-dev

# Revenir en prod (dernière release)
website-management switch-prod
```

### Rollback à une release précédente (sur la VM prod)

```bash
cd /opt/sideserver-prod
git checkout --force v1.1.5
docker compose up -d --build
echo v1.1.5 | sudo tee /etc/ipastore/prod.version
```

---

## 8. Troubleshooting

### "Dubious ownership"

```bash
sudo git config --global --add safe.directory /opt/sideserver-prod
```

Déjà fait par le bootstrap pour root et l'app-user. À refaire pour tout nouveau user amené à invoquer git dans `/opt/sideserver-prod`.

### Conteneur qui ne démarre pas après update

```bash
docker logs sidestore-website-prod --tail 100
systemctl status ipastore-update@prod.service -l
journalctl -u ipastore-update@prod.service -n 200
```

Causes fréquentes :
- `/etc/ipastore/prod.env` inaccessible → vérifier perms
- `secret_key` non lisible par uid 1000 → `chown 1000:1000 /etc/ipastore/secret_key`
- Migration BDD qui échoue au boot → voir logs

### Pull GitHub qui demande des credentials

```bash
cat /etc/ipastore/.git-credentials
# Doit contenir : https://MattTen:<TOKEN>@github.com

# Vérifier que les repos l'utilisent :
git -C /opt/sideserver-prod config credential.helper
# doit afficher : store --file=/etc/ipastore/.git-credentials
```

### L'UI ne déclenche pas l'update

1. Flag écrit ? `ls -la /etc/ipastore/update-requested-*`
2. Path unit actif ? `systemctl status ipastore-update@prod.path`
3. Service déclenché ? `journalctl -u ipastore-update@prod.service --since "10 min ago"`

Le flag doit apparaître puis être supprimé en ~1s. S'il reste, le service n'a pas démarré (vérifier `systemctl status`).

### MariaDB bind-address

Par défaut Debian, MariaDB écoute sur `127.0.0.1`. Les conteneurs y accèdent via `host.docker.internal` (résolu en `host-gateway` par `docker-compose.yml`), donc MariaDB doit écouter sur `0.0.0.0` :

```ini
# /etc/mysql/mariadb.conf.d/50-server.cnf
bind-address = 0.0.0.0
```

---

## 10. Fichiers de référence dans le repo

| Fichier                                       | Rôle                                          |
|-----------------------------------------------|-----------------------------------------------|
| `Dockerfile`                                  | Image Python 3.13-slim, uid 1000              |
| `docker-compose.yml`                          | Service paramétré via `.env` local            |
| `deploy/bootstrap.sh`                         | Setup initial VM auto-suffisant (curl \| sudo bash) — clone + checkout dernière release + units systemd embedded en heredoc. |
| `tools/website-management.sh`                 | Script de gestion mono-env (détection via `git rev-parse --abbrev-ref HEAD`, commandes `switch-dev` / `switch-prod`) |
| `tools/schema-sync.py`                        | Tool standalone — plan SQL additif pour aligner 2 schémas BDD |
| `app/config.py`                               | Vars d'env lues au boot                       |
| `app/updates.py`                              | Logique check + flag-file                     |
| `app/routes/updates.py`                       | Routes `/settings/updates/{check,apply}`      |
| `app/main.py`                                 | Lifespan + boucle check 6h                    |
| `app/source_gen.py`                           | Construction du feed `source.json` (apps, featured, news, header, icône) |
| `app/routes/news.py`                          | CRUD actualités (section `news[]` du feed)    |
| `app/patches.py`                              | Découverte + exécution des scripts de patch IPA |
| `app/routes/patches.py`                       | Routes `/patches/**` (listing, renommage, run) |
| `patch/*.py`                                  | Scripts de patch IPA (signature `-s /path/to.ipa`) |
| `documentation/patch_fix_ipa.md`              | Doc du patch générique                          |
| `documentation/patch_fix_ipa_scinsta.md`      | Doc du wrapper SCInsta                          |
| `app/scinsta.py`                              | Logique SCInsta (check decrypt.day, upload IPA, flag build, intégration) |
| `app/routes/scinsta.py`                       | Routes `/scinsta/**` (UI, upload, build)        |
| `templates/scinsta.html`                      | UI de l'onglet SCInsta                          |
| `tools/scinsta-builder/Dockerfile`            | Image Theos + cyan + ipapatch + lief (builder one-shot) |
| `tools/scinsta-builder/build.py`              | Pipeline : clone SCInsta main → `build.sh sideload` → patch optionnel → store |
| `deploy/bootstrap.sh`                         | Bootstrap unique auto-suffisant (`curl \| sudo bash`) — installe Docker, clone, checkout dernière release, écrit les units systemd embedded, démarre le conteneur |

---

## 11. Patchs IPA (onglet Patch)

L'onglet **Patch** de l'UI permet d'appliquer un script de correction sur un IPA déjà uploadé. Conçu pour les cas comme l'assertion ldid `end >= size - 0x10` (iOS 15+).

### Découverte automatique

Au boot du conteneur, aucune inscription n'est nécessaire : chaque `.py` placé directement dans `patch/` (pas récursif, pas les fichiers cachés ni `__init__.py`) est automatiquement listé. Ajouter un patch :

1. Créer `patch/mon_patch.py` dans le repo GitHub
2. Merger sur `dev` (ou `main` pour prod via release)
3. `website-management update` sur la VM cible → rebuild de l'image → le patch apparaît dans l'UI

### Contrat CLI des scripts

Chaque script doit respecter la signature :

```
python3 script.py -s /chemin/vers/app.ipa
```

Le script doit écraser l'IPA en place. Sortie stdout/stderr capturée et affichée dans l'UI. Exit code 0 = succès, autre = erreur.

### Flow d'exécution

1. Utilisateur ouvre `/patches/{filename}` et choisit une version (dropdown app+version)
2. POST `/patches/{filename}/run` → subprocess `python {script} -s {ipa_path}` avec timeout 900s
3. Si succès : recalcul de `size` + `sha256` du fichier écrasé → update de la ligne `versions` en BDD
4. Log complet (stdout + stderr) affiché dans la page

### Nom d'affichage et description

Deux métadonnées libres par patch, éditables depuis la page détail et stockées dans la table `settings` :

- `patch_display_name:{filename}` — nom affiché dans la liste. Défaut : stem du fichier (`fix_ipa.py` → `fix_ipa`).
- `patch_description:{filename}` — description libre (multi-lignes), montrée dans la colonne "Description" du listing. Défaut : `""`.

### Feedback visuel pendant l'exécution

L'exécution côté serveur est synchrone (`subprocess.run` bloquant). Pour éviter qu'un gros IPA (Instagram ~270 Mo, plusieurs minutes) laisse l'utilisateur face à une page blanche, le template `patch_detail.html` déclenche un overlay plein écran (`.patch-overlay` + `.patch-spinner` dans `style.css`) dès la soumission du formulaire `POST /patches/{filename}/run`. L'overlay reste visible jusqu'à ce que la réponse arrive et remplace la page.

### Dépendances

Les scripts de patch partagent le venv du conteneur (même `sys.executable`). Les deps communes (notamment `lief` pour la re-sérialisation Mach-O) sont déclarées dans `requirements.txt` à la racine.

---

## 12. SCInsta builder (onglet SCInsta)

Onglet dédié à la production de builds **Instagram + SCInsta** ([SoCuul/SCInsta](https://github.com/SoCuul/SCInsta)) directement depuis l'UI admin, avec bypass Cloudflare (via `curl_cffi`) pour le check de version et upload manuel pour l'IPA (Turnstile infranchissable sur le bouton de téléchargement).

Commandes `website-management` associées (pilotées par les path units `ipastore-scinsta-build@prod.path` / `ipastore-scinsta-cancel@prod.path`, pas listées dans le menu interactif) :

| Commande | Action |
|---|---|
| `prod-scinsta-build`  | Build + run du conteneur builder (alias systemd — le script détecte le vrai mode via la branche) |
| `prod-scinsta-cancel` | `docker stop -t 2 scinsta-builder-prod` + écrit un result failed |

**Doc complète** : [scinsta_builder.md](scinsta_builder.md) — flux utilisateur, pipeline systemd/Docker, bypass Cloudflare, URL source modifiable, routes API, clés settings, intégration BDD.

---

## 13. Apparence du store (source.json)

Le rendu SideStore dépend de ce qui est publié dans `source.json`. L'UI admin remplit ces champs :

| Champ `source.json` | Où le configurer                         | Stockage physique                                |
|---------------------|------------------------------------------|--------------------------------------------------|
| `iconURL`           | Réglages → Apparence → Icône du store   | `STORE_DIR/icons/_store-<token>.ext`             |
| `headerURL`         | Réglages → Apparence → Bannière         | `STORE_DIR/icons/_header-<token>.ext` (optionnel)|
| `tintColor`         | Réglages → Métadonnées → Teinte         | table `settings`, clé `store_tint`               |
| `featuredApps`      | Fiche app → toggle "Mettre en avant"    | colonne `apps.featured`                          |
| `news[]`            | Actualités (nouveau menu)               | table `news` + fichiers dans `STORE_DIR/news/`   |

Le suffixe aléatoire (`-<token>`) dans les noms de fichiers d'apparence invalide le cache HTTP de SideStore à chaque upload — sans ça, le client garde l'ancienne image même après remplacement côté serveur.

---

## 14. Flux de promotion entre machines

Les VM dev et prod sont **disjointes** — pas de sync physique BDD/store. Le cycle de promotion passe uniquement par git :

1. Développement sur la VM dev (branche `dev`, rolling via `website-management update`)
2. Validation dev OK → merge `dev` → `main` → push
3. Tag GitHub release (ex `v1.4.0`)
4. Sur la VM prod : `website-management update` détecte la release et déploie ; `init_db()` applique automatiquement les nouvelles tables/colonnes manquantes (migrations additives — `ALTER TABLE ADD COLUMN`, `CREATE INDEX`, jamais de `DROP` ni `MODIFY`)
