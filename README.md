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

Le script `website-management.sh` gère les deux environnements. Sans argument, il affiche un menu interactif. Avec un argument, il exécute une commande unique.

```bash
./website-management.sh                  # menu interactif
./website-management.sh --help           # aide détaillée

# Conteneurs
./website-management.sh prod-start       # start/stop/restart/logs
./website-management.sh dev-start
./website-management.sh status

# Mise à jour après un git push
./website-management.sh prod-update      # pull main  + rebuild + restart
./website-management.sh dev-update       # pull dev   + rebuild + restart

# Données
./website-management.sh sync                  # copie TOTALE prod -> dev (écrase dev)
./website-management.sh prod-reset-users      # purge users + prompt login/mdp
./website-management.sh dev-reset-users
```

### Workflow code

1. Modifier localement sur la branche `dev`, `git push`.
2. Sur la VM : `./website-management.sh dev-update`.
3. Une fois validé, merger `dev` -> `main` et `git push`.
4. Sur la VM : `./website-management.sh prod-update`.

## Configuration

Les conteneurs lisent leur config depuis `/etc/ipastore/prod.env` et `/etc/ipastore/dev.env` (voir [.env.example](.env.example)). Rien n'est hardcodé.

## Structure

```
app/              # code Python (FastAPI)
templates/        # Jinja2
static/           # CSS + JS
deploy/           # install.sh, nginx, systemd (legacy) + bootstrap.sh
Dockerfile
docker-compose.yml
website-management.sh
```

## Licence

[MIT](LICENSE)
