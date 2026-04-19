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

# Mise à jour du code après un git push
website-management prod-update      # pull main + rebuild + restart
website-management dev-update       # pull dev  + rebuild + restart
website-management self-update      # pull le script lui-même

# Données
website-management sync                  # copie TOTALE prod -> dev (écrase dev)
website-management prod-reset-users      # purge users + prompt login/mdp
website-management dev-reset-users
```

### Workflow de mise à jour (manuel)

1. Push sur `dev` depuis ta machine.
2. Sur la VM : `website-management dev-update`.
3. Validation OK, merge `dev` -> `main` + push.
4. Sur la VM : `website-management prod-update`.
5. Si le script a changé : `website-management self-update`.

## Configuration

Les conteneurs lisent leur config depuis `/etc/ipastore/prod.env` et `/etc/ipastore/dev.env` (voir [.env.example](.env.example)). Rien n'est hardcodé.

## Structure

```
app/              # code Python (FastAPI)
templates/        # Jinja2
static/           # CSS + JS
deploy/           # install.sh, nginx, systemd (legacy) + bootstrap.sh
tools/            # website-management.sh (distribué en clone sparse dédié)
Dockerfile
docker-compose.yml
```

## Licence

[MIT](LICENSE)
