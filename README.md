# SideServer Website

Plateforme auto-hébergée de distribution d'IPAs pour SideStore. Interface d'admin FastAPI + Jinja2, stockage MariaDB, génération dynamique de `source.json`.

## Architecture

- **Backend** : FastAPI + SQLAlchemy 2.0 + Uvicorn
- **Base de données** : MariaDB (sur l'hôte, partagée entre prod et dev)
- **Stockage** : binaires IPA + icônes + screenshots sur le disque hôte
- **Déploiement** : deux conteneurs Docker (prod port 80, dev port 8080) alimentés par les branches `main` et `dev`

## Déploiement initial (VM Debian)

```bash
# Sur la VM, en root
git clone https://github.com/MattTen/sideserver_website.git /opt/sideserver
cd /opt/sideserver
./deploy/bootstrap.sh   # installe Docker, crée les BDD, volumes, fichiers env
docker compose up -d
```

La BDD est créée par le bootstrap avec deux schémas :
- `ipastore-prod` (branche `main`, port 80)
- `ipastore-dev` (branche `dev`, port 8080)

## Administration

Le script `tools/website-management.sh` est distribué sur la VM via un **clone sparse dédié** à `/opt/sideserver-tools/` (un seul exemplaire du script sur disque, versionné via git). Un symlink global `/usr/local/bin/website-management` le rend appelable depuis n'importe où.

Sans argument : menu interactif. Avec un argument : commande unique.

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
website-management sync                  # copie TOTALE prod -> dev (écrase dev)
website-management prod-reset-users      # purge users + prompt login/mdp
website-management dev-reset-users
```

### Workflow release-based

- **Prod** avance uniquement via releases GitHub (`gh release create v1.2.0`). L'UI sur `/settings` vérifie toutes les 6 h via l'API GitHub et affiche un bouton "Appliquer la MAJ" quand un tag plus récent est disponible. Le bouton écrit un flag-file qu'un systemd path unit transforme en appel à `website-management prod-update`.
- **Dev** est rolling : push `dev` → `website-management dev-update` sur la VM. Pas de releases publiées, bouton UI grisé côté dev.

Détails complets : [documentation/server.md](documentation/server.md).

## Configuration

Les conteneurs lisent leur config depuis `/etc/ipastore/prod.env` et `/etc/ipastore/dev.env` (voir [.env.example](.env.example)). Rien n'est hardcodé.

## Structure

```
app/              # code Python (FastAPI)
templates/        # Jinja2
static/           # CSS + JS
deploy/           # bootstrap.sh + systemd units (ipastore-update@.{path,service})
tools/            # website-management.sh (clone sparse dédié)
documentation/    # doc serveur
Dockerfile
docker-compose.yml
```

## Licence

[MIT](LICENSE)
