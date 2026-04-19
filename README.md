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

## Développement

```bash
./dev.sh start    # démarre le conteneur dev
./dev.sh stop     # arrête le conteneur dev
./dev.sh restart  # redémarre
./dev.sh sync     # sync incrémentale prod -> dev (data + fichiers)
./dev.sh logs     # suit les logs du conteneur dev
./dev.sh status   # état des conteneurs
```

Le workflow : modifier le code sur la branche `dev`, `git push`, `./dev.sh restart` pour recharger.

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
dev.sh
```

## Licence

[MIT](LICENSE)
