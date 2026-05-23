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
│   └── ipastore-website  :8000                                │
│       env_file=/etc/ipastore/prod.env                        │
│       volumes: /srv/store-prod:/srv/store, /etc/ipastore     │
│                                                              │
│  Filesystem                                                  │
│   /srv/store-prod/{ipas,icons,screenshots,news}              │
│   /etc/ipastore/{prod.env, db.json, secret_key, prod.version}│
│   /opt/ipaserver  (git clone ; ref selon env courant)  │
│                                                              │
│  systemd                                                     │
│   ipastore-update@prod.path         watches update-requested-prod  │
│   ipastore-scinsta-build@prod.path  watches scinsta-build-requested-prod │
│   ipastore-scinsta-cancel@prod.path watches scinsta-build-cancel-prod    │
└──────────────────────────────────────────────────────────────┘
```

Le conteneur s'appelle toujours `ipastore-website` et le store `/srv/store-prod` quel que soit l'environnement réel : le mode prod vs dev est détecté **dynamiquement par le script de management** via `git rev-parse --abbrev-ref HEAD` (branche `main` ou `HEAD` détaché sur un tag = prod ; `dev` = dev).

---

## 2. Layout sur disque

### Clone git

Un seul clone par VM : `/opt/ipaserver`. Aucun sparse-checkout côté serveur par défaut (l'ancien setup sparse excluait `tools/` et `documentation/` — à re-configurer manuellement si souhaité).

La branche checkoutée détermine le mode :
- `main` ou tag `vX.Y.Z` (HEAD détaché) → **prod**
- `dev` (ou toute autre branche) → **dev**

Pour éviter "dubious ownership" si le clone est owned par un autre user que celui qui invoque git, `safe.directory` est configuré au bootstrap.

Le script de management vit dans `/opt/ipaserver/tools/website-management.sh`, symlinké en `/usr/local/bin/website-management`.

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

Le compose lui-même lit un `.env` local au clone (`/opt/ipaserver/.env`) — écrit par le bootstrap :

```
IMAGE_TAG=local
CONTAINER_NAME=ipastore-website
HOST_PORT=8000
ENV_FILE=/etc/ipastore/prod.env
STORE_PATH=/srv/store-prod
```

---

## 3.5. Healthcheck Docker

Le `Dockerfile` + `docker-compose.yml` définissent un HEALTHCHECK qui hit `GET /healthz` toutes les 30s :

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/healthz >/dev/null || exit 1
```

`/healthz` (dans `app/routes/public.py`) est **toujours ouvert** : pas de BDD, pas de jeton, retour `200 ok` plain text. La vérif BDD est volontairement exclue — pendant une coupure BDD, le handler global `OperationalError` renvoie 503 sur les routes applicatives, mais le conteneur reste healthy (pas de redémarrage agressif). Ça évite de masquer une vraie coupure BDD derrière un cycle de restart inutile.

Auparavant le healthcheck tapait `/source.json`. Avec l'activation de la protection par jeton (cf. §13), `/source.json` retournait 404 sans jeton → conteneur unhealthy en boucle.

---

## 3.6. Robustesse côté BDD

`app/db.py` configure l'engine SQLAlchemy avec :

| Paramètre | Valeur | Pourquoi |
|---|---|---|
| `pool_pre_ping` | `True` | SELECT 1 avant chaque connexion empruntée → détecte les connexions mortes (MySQL ferme les idle après 8h `wait_timeout`) |
| `pool_recycle` | `3600` | Renouvelle les connexions après 1h, bien sous `wait_timeout` |
| `connect_args.connect_timeout` | `3` | Sans ça, PyMySQL attend le timeout TCP par défaut (~75s) si la BDD est injoignable, gelant l'UI |
| `connect_args.read_timeout` | `10` | Sans ça, une requête posée sur une connexion morte (firewall qui drop sans RST, crash BDD entre 2 paquets) bloque le worker indéfiniment |
| `connect_args.write_timeout` | `10` | Idem côté écriture |

L'engine est construit **paresseusement** : à la première demande de session, pas au boot du conteneur. Permet de démarrer avant que `db.json` ne soit configuré. `reset_engine()` après `POST /setup/database` invalide l'engine pour le reconstruire avec les nouveaux credentials.

Le handler global `OperationalError` (dans `app/main.py`) catch toutes les erreurs SQLAlchemy et renvoie `503 JSON {"error": "Une erreur est survenue", "detail": ...}`. L'UI affiche une alerte rouge propre au lieu de figer l'utilisateur sur un état "slow" infini.

---

## 4. Script `website-management`

Unique script de gestion : `/opt/ipaserver/tools/website-management.sh`, symlinké en `/usr/local/bin/website-management`. Détection mono-env via `git rev-parse --abbrev-ref HEAD`.

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
| `self-update` | `git pull` dans `/opt/ipaserver` (dev) ou re-checkout de la dernière release (prod). |
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
4. `git fetch --tags && git checkout --force <tag>` dans `/opt/ipaserver` (HEAD détaché)
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

### `ipastore-scinsta-build@.path` / `.service`

```ini
[Service]
Type=oneshot
User=<app-user>
Group=<app-user>
# Le flag est lu PUIS supprime par build.py (read_flag_payload), pas en
# ExecStartPre sinon le payload JSON (patch a appliquer) est perdu.
ExecStart=/usr/local/bin/website-management %i-scinsta-build
# Safety net si SIGKILL (timeout 2h depasse) -- le trap bash dans
# cmd_scinsta_build n'aura pas tourne. ExecStopPost garantit le cleanup.
ExecStopPost=/bin/rm -f /etc/ipastore/scinsta-build-requested-%i /etc/ipastore/scinsta-build-cancel-%i
StandardOutput=journal
StandardError=journal
# Natif amd64 : 5-15 min. Sur ARM64 (qemu-user-static), facteur 3-4x ->
# 2h couvre largement FLEX arm64 + arm64e + SCInsta + ipapatch.
TimeoutStartSec=7200
```

Le **cleanup des flags** est essentiel : `PathExists=` ne se redéclenche que sur transition absent→présent. Si un build foire avant que `build.py` n'unlinke le flag (docker build cassé, qemu pas chargé, OOM…), le flag reste, les clics suivants depuis l'UI réécrivent le même fichier sans transition → aucun trigger. Mécanisme à 2 niveaux :
1. `cmd_scinsta_build` dans `website-management.sh` installe `trap 'rm -f $req_flag $cancel_flag' EXIT` → couvre 99% des sorties (succès, set -e, SIGTERM)
2. `ExecStopPost` du service catch le 1% restant (SIGKILL après timeout)

### `ipastore-scinsta-cancel@.path` / `.service`

Même pattern. Le service appelle `website-management %i-scinsta-cancel` qui fait `docker stop -t 2 scinsta-builder-<env>` (SIGTERM → 2s → SIGKILL) puis écrit un `scinsta-build-result-<env>` avec status `failed` et message "Build annule". `cmd_scinsta_cancel` supprime aussi le `scinsta-build-cancel-<env>` et le `scinsta-build-requested-<env>` après le stop.

Voir [scinsta_builder.md](scinsta_builder.md) et [scinsta_build.md](scinsta_build.md) pour le détail.

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
cd /opt/ipaserver
git checkout --force v1.1.5
docker compose up -d --build
echo v1.1.5 | sudo tee /etc/ipastore/prod.version
```

---

## 8. Troubleshooting

### "Dubious ownership"

```bash
sudo git config --global --add safe.directory /opt/ipaserver
```

Déjà fait par le bootstrap pour root et l'app-user. À refaire pour tout nouveau user amené à invoquer git dans `/opt/ipaserver`.

### Conteneur qui ne démarre pas après update

```bash
docker logs ipastore-website --tail 100
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
git -C /opt/ipaserver config credential.helper
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

## 12.5. Exposition publique (HTTPS)

Le conteneur écoute sur `HOST_PORT` (défaut `8000`, défini dans `/opt/ipaserver/.env`). Trois architectures possibles selon le contexte de la VM :

### A. Cloudflare Tunnel (recommandé pour la prod)

```
[Internet] ──TLS──→ [CF edge] ──TLS tunnel──→ [cloudflared sur VM] ──HTTP loopback──→ [HAProxy ou container]
```

Le daemon `cloudflared` ouvre une connexion **sortante** vers Cloudflare en TCP/443 (QUIC en priorité, fallback HTTP/2). Aucun port entrant à ouvrir, IP serveur masquée, **aucune limite d'upload** (contrairement au proxy DNS classique limité à 100 Mo sur le plan Free).

**Setup** (cf. README pour la procédure pas-à-pas) :
1. Installer `cloudflared` (`.deb` arm64 ou amd64 selon la VM)
2. `cloudflared tunnel login` → autorise la zone DNS
3. `cloudflared tunnel create ipastore`
4. Config `/etc/cloudflared/config.yml` (ingress → service local)
5. `cloudflared tunnel route dns ipastore <hostname>` → CNAME auto
6. `sudo cloudflared service install && sudo systemctl enable --now cloudflared`

**Firewall cloud** : Oracle Cloud Security Lists (ou équivalent AWS/GCP) doivent autoriser l'**egress** TCP+UDP vers `0.0.0.0/0` (ou au moins port 7844). Sans ouverture UDP, le tunnel échoue avec `failed to dial to edge with quic: timeout`. Workaround possible : forcer HTTP/2 dans `config.yml` (`protocol: http2`), mais ouvrir l'UDP est plus propre.

### B. HAProxy + Cloudflare Tunnel (multi-services sur la VM)

Si la VM héberge d'autres services en plus d'IPA Store, HAProxy reste utile pour multiplexer. Conf type :

```haproxy
frontend http_front
    bind 127.0.0.1:80
    mode http
    http-request deny if !{ req.hdr(Host) -m str -i ipastore.ton-domaine.fr }
    default_backend ipastore_backend

backend ipastore_backend
    mode http
    option httpchk GET /healthz
    http-check expect status 200
    server ipastore 127.0.0.1:8000 check inter 5s fall 3 rise 2
```

cloudflared pointe vers `http://127.0.0.1:80` avec `httpHostHeader: ipastore.ton-domaine.fr` pour que HAProxy retrouve son ACL. Pas de SSL termination côté HAProxy : CF s'en charge en upstream, HAProxy parle HTTP loopback.

### C. Reverse proxy direct (sans tunnel)

Bind public, cert Let's Encrypt local. Expose ton IP publique au scraping et au DDoS. À éviter en prod.

### Chiffrement bout-en-bout

| Lien | Chiffrement |
|---|---|
| Browser ↔ CF edge | TLS 1.3 (cert universel CF) |
| CF edge ↔ cloudflared | TLS dans le tunnel (QUIC ou HTTP/2 + TLS) |
| cloudflared ↔ HAProxy ↔ container | HTTP loopback (jamais sur le réseau) |

CF dashboard : `SSL/TLS → Overview` doit être en **Full (strict)** (pas Flexible).

---

## 12.6. Multi-architecture (ARM64 / amd64)

Le scinsta-builder dépend de binaires qui n'existent qu'en x86_64 :
- Toolchain L1ghtmann iOSToolchain : seul `iOSToolchain-x86_64.tar.xz` est publié
- ipapatch : seul `ipapatch.linux-amd64` est publié

Sur un hôte ARM64 (Oracle Ampere, Raspberry Pi…), ces binaires plantent avec `Exec format error` au moment de leur exécution.

**Solution** : émulation x86_64 via `qemu-user-static` + `binfmt-support`. Le bootstrap détecte `uname -m` :

```bash
case "$HOST_ARCH" in
  aarch64|arm64)
    echo "[bootstrap] Hote ARM64 detecte -> qemu-user-static install"
    apt-get install -y qemu-user-static binfmt-support
    ;;
  x86_64|amd64)
    echo "[bootstrap] Hote amd64 detecte -> qemu inutile"
    ;;
esac
```

Et `cmd_scinsta_build` dans `website-management.sh` détecte l'arch courante pour passer `--platform=linux/amd64` au `docker build` + `docker run` :

```bash
case "$(uname -m)" in
  x86_64|amd64)
    info "Hote amd64 -> build natif"
    ;;
  aarch64|arm64)
    info "Hote ARM64 -> build amd64 via qemu (--platform=linux/amd64)"
    platform_args=(--platform=linux/amd64)
    ;;
esac
docker build "${platform_args[@]}" -t scinsta-builder:latest "$dir"
docker run --rm "${platform_args[@]}" ... scinsta-builder:latest
```

**Coût** : sur ARM64 émulé, le build SCInsta prend ~3-4× plus de temps qu'en natif. D'où `TimeoutStartSec=7200` (2h) sur le service systemd.

**Vérification que qemu fonctionne** :
```bash
ls /proc/sys/fs/binfmt_misc/qemu-x86_64                              # doit exister
sudo docker run --rm --platform=linux/amd64 debian:bookworm-slim uname -m   # doit retourner x86_64
```

Si `qemu-x86_64` est absent (par ex. après un reboot où binfmt-support n'a pas redémarré), `sudo systemctl restart binfmt-support`.

---

## 12.7. Mécanisme d'alerte UI (slow + erreur)

Tous les formulaires async (toggle indexation, toggle jeton, password, source URL, métadonnées SCInsta…) utilisent le helper partagé `wireAlertBox` dans `static/app.js`. Comportement à 3 états :

1. **OK rapide** (< 1s) → check vert succès, pas d'alerte
2. **Slow** (> 1s sans réponse) → bandeau jaune *"Cela prend plus de temps que prévu"* + spinner rotatif
3. **Échec** (timeout TCP, 5xx, exception) → bandeau rouge *"Une erreur est survenue"* + détail si dispo

Pas de hard abort côté client (le `fetch` n'a pas de `signal` AbortController). L'admin doit voir la vraie réalité du backend ; un timeout client masquerait un état "slow" persistant qui mériterait d'être debug.

Côté serveur, les `OperationalError` SQLAlchemy sont catchées par un handler global et renvoyées en `503 JSON {"error": "Une erreur est survenue", "detail": ...}`. Combiné aux `connect_timeout=3 / read_timeout=10 / write_timeout=10` de PyMySQL (cf. §3.6), ça garantit qu'une BDD HS coupe la requête en max ~13s au lieu de pendre indéfiniment.

---

## 12.8. Upload des IPAs (dashboard + SCInsta)

Deux mécanismes pour pousser un IPA dans le store :

### Drag-and-drop / sélection de fichier

Dropzone HTML5 dans le dashboard et l'onglet SCInsta. Stream XHR vers `/apps/upload` (resp. `/scinsta/upload`) avec barre de progression (event `xhr.upload.onprogress`). Auto-submit dès qu'un fichier est sélectionné — pas de bouton "Téléverser" séparé. Limite : la requête transite par Cloudflare ; sur le plan Free, **plafond 100 Mo**. Pour des IPAs plus gros (Instagram = 250-300 Mo), passer par l'option URL.

### Upload depuis URL (background + polling)

| Endpoint | Description |
|---|---|
| `POST /apps/upload-url` | Lance le download de l'URL fournie en background thread, retourne 202 |
| `GET /apps/upload-url-progress` | Poll : `{status, bytes_downloaded, bytes_total, error, redirect_url}` |
| `POST /scinsta/upload-url` | Idem côté SCInsta (l'IPA est déposée dans `scinsta-upload-<env>.ipa`) |
| `GET /scinsta/upload-url-progress` | Poll de la progression SCInsta |

Le serveur fait un `GET` direct vers l'URL via `curl_cffi` (impersonation Chrome — certains CDN fingerprint les requêtes Python natives) avec fallback urllib. Pas de limite Cloudflare puisque le download ne traverse pas le tunnel — c'est la VM qui se connecte au CDN externe.

**Polling JS** (toutes les 1s) : affiche `Téléchargement : 42 Mo / 280 Mo (15.0 %)` en direct. Si l'admin recharge la page pendant un download, le polling reprend automatiquement.

**Hôtes recommandés** pour l'URL :
- `litterbox.catbox.moe` (drag-drop, 1 Go max, expire 1h-72h, lien direct)
- `0x0.st` (curl-friendly, 512 Mo, expire selon taille)
- `bashupload.com` (50 Go, expire 3 jours)

À éviter : services qui retournent une page HTML wrapper (gofile.io, MEGA, MediaFire) — le serveur récupérerait du HTML au lieu de l'IPA.

---

## 12.9. Permissions des fichiers IPA

Trois sources d'IPAs avec des owners/perms initiaux différents. Tous fonctionnent grâce à `os.replace` (POSIX permissive sur le parent dir) et au chmod 0644 explicite :

| Source | Owner | Perms | Mécanisme |
|---|---|---|---|
| Upload UI Apps tab | `ipastore:ipastore` | `0600` | `tempfile.NamedTemporaryFile(dir=STORE_DIR)` → `replace` atomique |
| Upload URL (Apps + SCInsta) | `ipastore:ipastore` | `0600` | Idem, fichier .tmp dans `STORE_DIR` |
| Build SCInsta | `root:root` | `0644` | Le builder tourne en root → `build.py` force `chmod 0644` après `shutil.move` (sinon l'app web ne peut pas relire l'IPA root-owned 0600) |
| Patch (`fix_ipa.py` / `fix_ipa_scinsta.py`) | inchangé | `0644` | `tempfile.mkstemp(dir=os.path.dirname(ipa_path))` → `chmod 0644` → `os.replace` ; reste sur le même filesystem (rename atomique) ; POSIX permissive permet d'overwrite un fichier dont on n'est pas owner si le parent dir est writable |

`/srv/store-prod/ipas/` est `ipastore:ipastore` 755, donc tout user `ipastore` peut renommer/supprimer dedans.

---

## 12.10. Conversion CgBI ↔ PNG standard pour les icônes

Xcode optimise les PNGs des bundles iOS au format Apple **CgBI** :
- Chunk `CgBI` ajouté avant `IHDR`
- IDAT compressé en raw deflate (sans header zlib) → `wbits=-15`
- Pixels stockés en BGR(A) au lieu de RGB(A)
- Alpha pré-multiplié

Format valide pour iOS et SideStore (qui sait décoder), mais **illisible par les navigateurs desktop** : Firefox/Chrome/Safari refusent de rendre l'image (header IHDR au mauvais offset). Côté UI admin web, les icônes apparaissaient cassées / vides malgré des URLs qui répondaient 200 + content-type image/png.

`_extract_icon` dans `app/ipa.py` traite ça en pur stdlib (`struct` + `zlib`) :
1. Cherche d'abord un PNG **standard** dans le bundle (priorité maximale)
2. Si seuls des CgBI sont disponibles : tente une **conversion**
   - Parse les chunks, strip `CgBI`
   - Décompresse en raw deflate, unfilter les scanlines (filtres None/Sub/Up/Average/Paeth)
   - Swap B↔R par pixel + un-premultiply alpha (`R = R_premul * 255 / A` quand `A > 0`)
   - Réécrit toutes les lignes avec filter=None, recompresse avec zlib standard
   - Réassemble le PNG (CRC recalculé)
3. Si tout échoue (format paletted/grayscale, ou pas d'icône dans le bundle) → retourne `None` → templates affichent `/static/default-app.png`

Limites de la conversion : RGB et RGBA 8-bit uniquement (couvre 99% des AppIcon iOS). Paletted / grayscale / 16-bit non gérés — fallback `default-app.png` + l'admin uploade manuellement via la fiche app.

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

### Fallback icône d'app

Si une app n'a pas d'`icon_path` (extraction `parse_ipa` échouée, ou IPA sans icône standard) :
- Côté `source.json` : `iconURL` pointe vers `/static/default-app.png`
- Côté UI admin (templates `apps.html`, `app_detail.html`, `dashboard.html`) : même fallback dans les `<img>`

L'admin peut toujours uploader une icône custom via la fiche app (`/apps/{bundle_id}` → "Changer l'icône"). Le filename inclut un token (`<bundle_id>-<6hex>.png`) pour invalider le cache HTTP.

---

## 13bis. Protection optionnelle du dépôt (source token)

Par défaut `/source.json` et `/qr.svg` sont publics — n'importe qui connaissant l'URL voit la liste des IPAs et peut les télécharger. Pour limiter l'accès :

**Réglages → Sécurité → toggle "Protéger l'accès au dépôt d'IPA"**

Quand activé :
- Génère un jeton aléatoire de 256 caractères alphanumériques (clé settings `source_token_value`)
- `/source.json` et `/qr.svg` exigent `?t=<jeton>` (sinon `404` — volontairement opaque pour les bots de scraping, pas `401`)
- Le dashboard et le QR code intègrent automatiquement le jeton dans l'URL partagée
- L'admin peut **Régénérer** un nouveau jeton (avec confirmation) → invalide tous les liens précédents
- L'admin peut **Afficher / Masquer** le jeton (par défaut masqué dans l'UI)

C'est volontairement un secret long en query string plutôt qu'une vraie auth : SideStore ne sait pas envoyer de header custom, seul `GET ?token=...` est utilisable côté client iOS.

Persistance : `app/source_token.py` cache le jeton en RAM (lu au boot depuis `settings`, `refresh_from_db()` après modif). Un `threading.Lock` couvre les accès cross-thread (uvicorn + le polling lifespan).

---

## 14. Flux de promotion entre machines

Les VM dev et prod sont **disjointes** — pas de sync physique BDD/store. Le cycle de promotion passe uniquement par git :

1. Développement sur la VM dev (branche `dev`, rolling via `website-management update`)
2. Validation dev OK → merge `dev` → `main` → push
3. Tag GitHub release (ex `v1.4.0`)
4. Sur la VM prod : `website-management update` détecte la release et déploie ; `init_db()` applique automatiquement les nouvelles tables/colonnes manquantes (migrations additives — `ALTER TABLE ADD COLUMN`, `CREATE INDEX`, jamais de `DROP` ni `MODIFY`)
