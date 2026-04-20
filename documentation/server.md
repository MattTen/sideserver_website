# SideServer Website — Fonctionnement côté serveur

Doc de référence du déploiement : ce qui tourne sur la VM, comment c'est organisé, comment on met à jour.

---

## 1. Vue d'ensemble

- **Repo** : `github.com/MattTen/sideserver_website`
- **Une seule codebase**, deux branches :
  - `main` → déployée en **prod** (uniquement via releases GitHub)
  - `dev`  → déployée en **dev** (rolling, à chaque push)
- **VM** : Debian, IP `192.168.0.202`
- **Stack** : FastAPI + Uvicorn dans Docker, MariaDB sur l'hôte (partagée entre les deux environnements via 2 schémas séparés)
- **Deux conteneurs Docker** sur la même VM :
  - `sidestore-website-prod` — port 80  → BDD `ipastore-prod`, store `/srv/store-prod`
  - `sidestore-website-dev`  — port 8080 → BDD `ipastore-dev`,  store `/srv/store-dev`

```
┌────────────────────── VM 192.168.0.202 ─────────────────────────┐
│                                                                  │
│  MariaDB (hôte, 3306)                                           │
│   ├── ipastore-prod                                             │
│   └── ipastore-dev                                              │
│                                                                  │
│  Docker                                                          │
│   ├── sidestore-website-prod  :80    → lit /etc/ipastore/prod.env│
│   └── sidestore-website-dev   :8080  → lit /etc/ipastore/dev.env │
│                                                                  │
│  Filesystem                                                      │
│   /srv/store-prod/{ipas,icons,screenshots}                      │
│   /srv/store-dev/{ipas,icons,screenshots}                       │
│   /etc/ipastore/{prod,dev}.env, secret_key.*, *.version, ...    │
│   /opt/sideserver-prod   (git clone branche main, sparse)       │
│   /opt/sideserver-dev    (git clone branche dev,  sparse)       │
│   /opt/sideserver-tools  (git clone sparse -> tools/ seul)      │
│                                                                  │
│  systemd                                                         │
│   ipastore-update@prod.path  watches update-requested-prod      │
│   ipastore-update@dev.path   watches update-requested-dev       │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Layout sur disque

### Clones git

Tout vit sous `/opt/` :

| Chemin                   | Branche/Tag   | Sparse filter              | Contenu                   |
|--------------------------|---------------|----------------------------|---------------------------|
| `/opt/sideserver-prod`   | tag de release | `/*  !tools/  !documentation/` | Code de l'app prod        |
| `/opt/sideserver-dev`    | `dev`         | `/*  !tools/  !documentation/` | Code de l'app dev         |
| `/opt/sideserver-tools`  | `main`        | `tools/`                   | Script `website-management.sh` |

> `documentation/` est exclu des trois clones serveur via sparse-checkout (config locale git, le repo GitHub reste complet). Cela évite de déployer de la doc inutile sur la VM.

Les trois clones sont **owned par `altuser`**. Pour éviter l'erreur "dubious ownership" quand root invoque git, `safe.directory` est configuré globalement.

Le script de gestion existe en **un seul exemplaire** sur le disque, dans `/opt/sideserver-tools/tools/website-management.sh`, accessible via le symlink `/usr/local/bin/website-management`.

### `/etc/ipastore/`

Répertoire 750 owned par altuser. Monté en volume dans chaque conteneur (à `/etc/ipastore`). Contient :

| Fichier                         | Owner         | Mode  | Rôle                                              |
|---------------------------------|---------------|-------|---------------------------------------------------|
| `prod.env` / `dev.env`          | altuser       | 640   | Variables pour le conteneur (`IPASTORE_DB_URL`, `IPASTORE_ENV`, …) |
| `secret_key.prod` / `.dev`      | uid 1000      | 600   | Clé de signature cookies (lue par le conteneur)    |
| `prod.version` / `dev.version`  | altuser       | 644   | Version déployée (écrite par le script, lue par l'UI) |
| `.mysql.cnf`                    | altuser (root-safe) | 600 | `[client] user=root password=…` pour mysqldump   |
| `.git-credentials`              | altuser       | 600   | Token GitHub pour pull privés                      |
| `update-requested-prod` / `-dev`| (ipastore uid 1000 depuis conteneur) | 644 | Flag transitoire : présence = demande de maj |

### `/srv/store-{prod,dev}/`

Monté dans le conteneur à `/srv/store`. **Séparés** entre prod et dev pour ne jamais se marcher dessus. Sous-dossiers :

- `ipas/` — binaires `.ipa` servis sur `/ipas/{filename}`
- `icons/` — icônes d'apps + icône et header du store (`_store-<token>.png`, `_header-<token>.png`), servis sur `/icons/{filename}`
- `screenshots/` — captures uploadées manuellement, servies sur `/screenshots/{filename}`
- `news/` — visuels joints aux articles d'actualités, servis sur `/news-img/{filename}`

---

## 3. Configuration (env vars du conteneur)

Toutes les vars sont injectées via `env_file: /etc/ipastore/{prod,dev}.env` dans `docker-compose.yml`.

| Variable                 | Exemple prod                          | Rôle                                  |
|--------------------------|---------------------------------------|---------------------------------------|
| `IPASTORE_DB_URL`        | `mysql+pymysql://ipastore-prod:…@host.docker.internal:3306/ipastore-prod?charset=utf8mb4` | Connexion MariaDB |
| `IPASTORE_STORE_DIR`     | `/srv/store`                          | Racine binaires (monté depuis `/srv/store-prod`) |
| `IPASTORE_SECRET_FILE`   | `/etc/ipastore/secret_key.prod`       | Clé cookies (lue au boot)             |
| `IPASTORE_BASE_URL`      | `http://<IP_SERVEUR>`                 | URL que SideStore utilise pour télécharger IPAs/icônes. C'est l'adresse que l'utilisateur entre dans SideStore pour ajouter la source — le feed `source.json` y intègre toutes les URLs absolues (ex: `http://<IP>/ipas/app.ipa`). Sans chemins absolus, SideStore ne saurait pas où se connecter. |
| `IPASTORE_ENV`           | `prod` ou `dev`                       | Identifie l'environnement du conteneur (utilisé par le module updates) |
| `IPASTORE_GITHUB_REPO`   | `MattTen/sideserver_website`          | Repo consulté pour les releases       |

**Les credentials BDD ne transitent jamais par GitHub.** Ils sont créés par `deploy/bootstrap.sh` à partir des variables `DB_PASS_PROD` / `DB_PASS_DEV` exportées localement avant l'exécution.

Le compose lui-même lit un `.env` local à chaque clone (`/opt/sideserver-prod/.env`, `/opt/sideserver-dev/.env`) :

```
CONTAINER_NAME=sidestore-website-prod
HOST_PORT=80
ENV_FILE=/etc/ipastore/prod.env
STORE_PATH=/srv/store-prod
```

---

## 4. Script `website-management`

Unique script de gestion : `/opt/sideserver-tools/tools/website-management.sh`, symlinké en `/usr/local/bin/website-management`.

### Usage

```bash
website-management                  # menu interactif (TUI)
website-management <commande>       # commande unique
website-management --help           # aide
```

### Conteneurs

| Commande              | Action                                   |
|-----------------------|------------------------------------------|
| `prod-start` / `dev-start`     | Build + up (docker compose)     |
| `prod-stop`  / `dev-stop`      | docker compose down             |
| `prod-restart` / `dev-restart` | Rebuild + force-recreate        |
| `prod-logs` / `dev-logs`       | Suit les logs (Ctrl+C pour sortir) |
| `status`                       | État des 2 conteneurs + versions déployées |

### Mise à jour du code

| Commande         | Action                                                                 |
|------------------|------------------------------------------------------------------------|
| `prod-update`    | Récupère la dernière release GitHub, `git checkout <tag>`, rebuild. **No-op si déjà à jour.** Écrit `/etc/ipastore/prod.version` après succès. |
| `prod-check`     | Affiche `current / latest / update_available` (machine-readable : `key=value`). |
| `dev-update`     | `git pull origin dev` + rebuild (rolling).                             |
| `dev-check`      | Retourne toujours `update_available=0` (dev est rolling).              |
| `self-update`    | Met à jour **le script lui-même** (pull dans `/opt/sideserver-tools`). |

### Données

| Commande             | Action                                                                 |
|----------------------|------------------------------------------------------------------------|
| `sync`               | Clone TOTAL prod → dev (drop+recreate BDD dev + rsync --delete du store). **Écrase tout ce qui est dans dev.** |
| `sync-to-prod`       | Clone TOTAL dev → prod (drop+recreate BDD prod + rsync --delete du store). **IRRÉVERSIBLE — écrase prod.** Double confirmation exigée. |
| `sync-schema-to-prod`| Aligne la **structure** de la BDD prod sur celle de dev (tables + colonnes + index + FKs manquants). **Aucune donnée touchée** — opérations ADDITIVES uniquement (`CREATE TABLE`, `ADD COLUMN`, `ADD INDEX`, `ADD FOREIGN KEY`). Pas de `DROP`, pas de `MODIFY`. Les divergences de type sur colonnes existantes sont affichées en commentaire dans le plan pour revue manuelle. Génère un plan SQL via `tools/schema-sync.py`, l'affiche, demande confirmation avant application. |
| `prod-reset-users`   | Prompt login/mdp, supprime tous les users prod, crée un nouvel admin.  |
| `dev-reset-users`    | Idem sur dev.                                                          |

---

## 5. Mécanisme de mise à jour — prod

### Principe

La prod n'avance qu'**à chaque release GitHub publiée**. Chaque release porte un tag (`v1.0.0`, potentiellement `v14.26.35.2664.32` — format libre de dotted-numeric, avec un `v` optionnel).

Trois sources de vérité :
1. GitHub Releases (source de vérité du "dernière version disponible")
2. `/etc/ipastore/prod.version` (source de vérité du "version actuellement déployée")
3. L'état du clone (`git rev-parse HEAD`, qui devrait matcher le tag)

### Comparaison de versions

On utilise `sort -V` (bash) ou la comparaison par tuple d'entiers (Python). Ce qui supporte naturellement :
- `v1.0.0 < v1.0.1`
- `v1.9 < v1.10`
- `v14.26.35.2664.32 > v14.26.35.2664.31`

Le `v` initial est strippé avant comparaison.

### Flux via CLI

```bash
website-management prod-update
```

Étapes :
1. `curl api.github.com/repos/.../releases/latest` → tag_name
2. Lit `/etc/ipastore/prod.version`
3. Si tag ≤ version actuelle → no-op ("Prod déjà à jour")
4. `git fetch --tags && git checkout --force <tag>` dans `/opt/sideserver-prod` (HEAD détaché)
5. `docker compose up -d --build`
6. Écrit le tag dans `/etc/ipastore/prod.version`

### Flux via l'UI

L'UI ne peut pas `docker compose` elle-même (elle tourne dans le conteneur). Elle demande au host via un **flag-file** :

```
1. Utilisateur clique "Appliquer la mise à jour" dans /settings
   └─> POST /settings/updates/apply (FastAPI)
       └─> écrit /etc/ipastore/update-requested-prod  (ce fichier est
            monté en volume depuis le conteneur, donc visible par le host)

2. Sur le host, systemd path unit détecte le fichier :
   ipastore-update@prod.path  (PathExists=/etc/ipastore/update-requested-prod)
   └─> déclenche ipastore-update@prod.service :
       ExecStartPre=/bin/rm -f /etc/ipastore/update-requested-prod
       ExecStart=/usr/local/bin/website-management prod-update
       User=altuser

3. Le script fait son job (checkout + rebuild + up). Le conteneur prod
   redémarre. La page UI attend ~30s puis se recharge.
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

## 6. Mécanisme de mise à jour — dev

Dev est **rolling** : chaque push sur `dev` doit pouvoir être testé vite, pas question de publier une release par commit.

Conséquences :
- `dev-update` = `git pull origin dev` + rebuild. Pas de check de version.
- `/etc/ipastore/dev.version` contient `rolling-<sha court>`.
- `/settings/updates/check` côté dev renvoie toujours `rolling=true, update_available=false` → le bouton "Appliquer" reste grisé dans l'UI dev. La présence du bouton est juste un artefact du fait qu'on a une seule codebase.
- Le workflow côté dev est : push `dev` → sur la VM, `website-management dev-update`.

---

## 7. Unités systemd

Deux unités **templatisées** (un seul fichier pour prod et dev) :

### `/etc/systemd/system/ipastore-update@.path`

```ini
[Unit]
Description=Watch /etc/ipastore/update-requested-%i flag

[Path]
PathExists=/etc/ipastore/update-requested-%i
Unit=ipastore-update@%i.service

[Install]
WantedBy=multi-user.target
```

### `/etc/systemd/system/ipastore-update@.service`

```ini
[Unit]
Description=Apply update to %i environment
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
User=altuser
Group=altuser
ExecStartPre=/bin/rm -f /etc/ipastore/update-requested-%i
ExecStart=/usr/local/bin/website-management %i-update
StandardOutput=journal
StandardError=journal
TimeoutStartSec=600
```

### Activation

```bash
systemctl enable --now ipastore-update@prod.path
systemctl enable --now ipastore-update@dev.path
```

Logs :

```bash
journalctl -u ipastore-update@prod.service -f
```

---

## 8. Workflow complet — exemple

### Release de la v1.2.0

```bash
# Sur ta machine
git checkout main
git merge dev                         # récupère ce qui a été validé sur dev
git push origin main
gh release create v1.2.0 --generate-notes

# Sur la VM (rien à faire tout de suite — la prod reste sur v1.1.x)
# Option 1 : ouvrir l'UI /settings et cliquer "Appliquer"
# Option 2 : website-management prod-update

# Ou attendre le check auto 6h puis agir depuis l'UI
```

### Push dev

```bash
# Sur ta machine
git checkout dev
# … commit …
git push origin dev

# Sur la VM
website-management dev-update    # rolling, pas de release
```

### Rollback rapide à la release précédente

```bash
# Supprime le tag de la dernière release OU republie une release "v1.1.x" = v1.2.0
# Plus simple : checkout manuel
cd /opt/sideserver-prod
git checkout --force v1.1.5
docker compose up -d --build
echo v1.1.5 > /etc/ipastore/prod.version
```

---

## 9. Troubleshooting

### "Dubious ownership"

```bash
git config --global --add safe.directory /opt/sideserver-prod
git config --global --add safe.directory /opt/sideserver-dev
git config --global --add safe.directory /opt/sideserver-tools
```

Déjà fait pour `root` et `altuser` lors du setup, à refaire par user si nouveau compte.

### Conteneur qui ne démarre pas après update

```bash
docker logs sidestore-website-prod --tail 100
systemctl status ipastore-update@prod.service -l
journalctl -u ipastore-update@prod.service -n 200
```

Causes fréquentes :
- `/etc/ipastore/prod.env` inaccessible (permissions) → `chown altuser:altuser /etc/ipastore/prod.env`
- `secret_key.prod` non lisible par uid 1000 → `chown 1000:1000 /etc/ipastore/secret_key.prod`
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
| `deploy/bootstrap.sh`                         | Setup initial VM (paquets, BDD, env files, systemd units) |
| `deploy/systemd/ipastore-update@.path`        | Watcher de flag-file                          |
| `deploy/systemd/ipastore-update@.service`     | Exécuteur d'update                            |
| `tools/website-management.sh`                 | Script de gestion unique                      |
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
| `deploy/systemd/ipastore-scinsta-build@.path` | Watcher du flag-file de build SCInsta           |
| `deploy/systemd/ipastore-scinsta-build@.service` | Exécuteur : lance `website-management {env}-scinsta-build` |
| `deploy/systemd/ipastore-scinsta-cancel@.path` | Watcher du flag-file de cancel SCInsta         |
| `deploy/systemd/ipastore-scinsta-cancel@.service` | Exécuteur : `docker kill scinsta-builder-{env}` + result failed |

---

## 11. Patchs IPA (onglet Patch)

L'onglet **Patch** de l'UI permet d'appliquer un script de correction sur un IPA déjà uploadé. Conçu pour les cas comme l'assertion ldid `end >= size - 0x10` (iOS 15+).

### Découverte automatique

Au boot du conteneur, aucune inscription n'est nécessaire : chaque `.py` placé directement dans `patch/` (pas récursif, pas les fichiers cachés ni `__init__.py`) est automatiquement listé. Ajouter un patch :

1. Créer `patch/mon_patch.py` dans le repo GitHub
2. Merger sur `dev` (ou `main` pour prod via release)
3. `website-management dev-update` (ou `prod-update`) → rebuild de l'image → le patch apparaît dans l'UI

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

Commandes `website-management` associées (pilotées par la path unit `ipastore-scinsta-build@`, pas listées dans le menu interactif) :

| Commande | Action |
|---|---|
| `prod-scinsta-build`  | Build + run du conteneur builder pour prod |
| `dev-scinsta-build`   | Idem pour dev |
| `prod-scinsta-cancel` | `docker kill scinsta-builder-prod` + écrit un result failed (pilotée par `ipastore-scinsta-cancel@prod.path`) |
| `dev-scinsta-cancel`  | Idem pour dev |

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
