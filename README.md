# SideServer Website

Plateforme auto-hébergée de distribution d'IPAs pour SideStore. Interface d'admin FastAPI + Jinja2, stockage MariaDB, génération dynamique de `source.json`.

## Architecture

- **Backend** : FastAPI + SQLAlchemy 2.0 + Uvicorn
- **Base de données** : MariaDB sur l'hôte (2 schémas séparés `ipastore-prod` / `ipastore-dev`)
- **Stockage** : binaires IPA + icônes + screenshots sur le disque hôte (`/srv/store-prod/`, `/srv/store-dev/`)
- **Déploiement** : deux conteneurs Docker (prod port 80, dev port 8080) alimentés par les branches `main` et `dev`
- **Isolation prod/dev** : même code, variables d'environnement différentes — `STORE_DIR` et `DB_URL` pointent vers des ressources séparées selon le conteneur

Sur la VM, 3 clones git sparse coexistent :

| Clone | Chemin | Contenu |
|---|---|---|
| prod | `/opt/sideserver-prod` | branche `main`, exclut `documentation/` et `CLAUDE.md` |
| dev | `/opt/sideserver-dev` | branche `dev`, idem |
| tools | `/opt/sideserver-tools` | branche `main`, script de management uniquement |

## Branches — règle absolue

> **Tout développement sur `dev` uniquement. Ne jamais committer ni pusher directement sur `main`.**

| Branche | Déploiement |
|---|---|
| `dev` | rolling — `website-management dev-update` |
| `main` | release-based — release GitHub → `website-management prod-update` |

## Déploiement initial (VM Debian)

**Prérequis :**
- MariaDB installé et `root@localhost` configuré
- Variables exportées dans le shell avant d'exécuter le bootstrap :

```bash
export DB_PASS_PROD=<mot_de_passe_prod>
export DB_PASS_DEV=<mot_de_passe_dev>
export BASE_URL=http://<IP_SERVEUR>
```

```bash
# Sur la VM, en root
git clone https://github.com/MattTen/sideserver_website.git /tmp/bootstrap
cd /tmp/bootstrap
./deploy/bootstrap.sh
```

Le bootstrap crée les BDD, les volumes, les fichiers env dans `/etc/ipastore/`, installe Docker, configure systemd, et monte les 3 clones sparse.

**GitHub PAT requis** : pour que `prod-update` vérifie les releases, un token GitHub fine-grained (Contents : read-only) doit être déposé dans `/etc/ipastore/.git-credentials`. Sans lui, le check GitHub échoue silencieusement.

**Premier lancement** : ouvrir `http://<IP_SERVEUR>/` → redirige vers `/setup` pour créer le compte administrateur.

## Intégration SideStore

Dans SideStore (iOS) → Sources → Ajouter :

```
http://<IP_SERVEUR>/source.json
```

C'est l'URL configurée dans `IPASTORE_BASE_URL`. Le feed `source.json` intègre cette adresse dans toutes les URLs de téléchargement (`downloadURL`, `iconURL`…) — SideStore effectuant des requêtes HTTP indépendantes depuis l'app iOS, des chemins relatifs ne suffisent pas.

## Administration

Le script `tools/website-management.sh` est accessible via le symlink `/usr/local/bin/website-management`.

```bash
website-management                  # menu interactif
website-management --help           # aide détaillée

# Conteneurs
website-management prod-start       # start/stop/restart/logs
website-management dev-start
website-management status

# Mise à jour du code
website-management prod-update      # déploie la dernière release GitHub (si plus récente)
website-management prod-check       # affiche current/latest/update_available
website-management dev-update       # pull dev + rebuild (rolling)
website-management self-update      # pull le script lui-même

# Données
website-management sync             # copie TOTALE prod -> dev (BDD + fichiers, écrase dev)
website-management prod-reset-users # purge users + prompt login/mdp
website-management dev-reset-users
```

### Workflow release prod

1. Merger `dev` → `main`
2. Créer une release GitHub avec tag semver (`gh release create v1.2.0`)
3. L'UI `/settings` détecte la MAJ toutes les 6 h et affiche le bouton "Appliquer"
4. Le bouton écrit un flag-file → systemd path unit → `website-management prod-update`

## Configuration

Les conteneurs lisent leur config depuis `/etc/ipastore/prod.env` et `/etc/ipastore/dev.env` (voir [.env.example](.env.example)). Rien n'est hardcodé dans le code.

| Variable | Rôle |
|---|---|
| `IPASTORE_BASE_URL` | URL publique du serveur (entrée dans SideStore) |
| `IPASTORE_STORE_DIR` | Racine des binaires (`/srv/store-prod` ou `/srv/store-dev`) |
| `IPASTORE_SECRET_FILE` | Chemin vers la clé de signature des cookies |
| `IPASTORE_ENV` | `prod` ou `dev` |
| `IPASTORE_GITHUB_REPO` | Repo pour les releases (`MattTen/sideserver_website`) |

La connexion BDD (host/user/mdp/nom de base) n'est **plus** fournie en env var : elle est saisie via l'UI au premier démarrage (`/setup/database`) puis persistée dans `/etc/ipastore/db.json` (mode 600).

Voir [documentation/server.md](documentation/server.md) pour les détails complets.

## Structure

```
app/              # code Python (FastAPI)
templates/        # Jinja2
static/           # CSS + JS
deploy/           # bootstrap.sh + systemd units (ipastore-update@.{path,service})
tools/            # website-management.sh (clone sparse dédié)
documentation/    # doc serveur + credentials
Dockerfile
docker-compose.yml
```

## Licence

[MIT](LICENSE)
