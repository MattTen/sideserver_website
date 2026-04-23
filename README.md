# SideServer Website

Plateforme auto-hébergée de distribution d'IPAs pour SideStore. Interface d'admin FastAPI + Jinja2, stockage MySQL/MariaDB, génération dynamique de `source.json`.

## Architecture

- **Backend** : FastAPI + SQLAlchemy 2.0 + Uvicorn (Python 3.13)
- **Base de données** : MySQL ou MariaDB (externe, pas embarqué dans le conteneur). La connexion est saisie via l'UI à la première connexion.
- **Stockage** : binaires IPA + icônes + screenshots sur le disque hôte (`/srv/store-prod/`), montés en volume dans le conteneur.
- **Déploiement** : un conteneur Docker par VM.

### Modèle mono-environnement (1 VM = 1 environnement)

Chaque VM héberge **un seul** environnement. Dev et prod vivent sur des machines séparées, pas côte-à-côte :

| VM | Branche Git | Mode de mise à jour |
|---|---|---|
| **Dev** (maison / lab) | `dev` | rolling — `git pull origin dev` + rebuild à la demande |
| **Prod** (cloud / serveur public) | `main` | release-based — `git checkout <tag>` + rebuild sur release GitHub |

Les deux VM utilisent **la même configuration infra** (chemins, nom de conteneur, port 8000, units systemd). La seule différence est la branche qui est clonée. Le script de management détecte automatiquement le mode (prod vs dev) en lisant la branche courante.

## Branches — règle absolue

> **Tout développement sur `dev` uniquement. Ne jamais committer ni pusher directement sur `main`.**

Publier une release prod :

1. Merger `dev` → `main` (PR ou fast-forward)
2. Créer une release GitHub avec un tag semver (ex `v1.2.0`) via l'UI ou `gh release create v1.2.0`
3. L'UI admin détecte la MAJ → bouton "Appliquer" → le bootstrap de la VM prod checkout le tag et rebuild

## Déploiement initial

Un **seul** script bootstrap auto-suffisant (`curl | bash`) : il installe Docker, clone le repo, checkout la **dernière release GitHub** (HEAD détaché), configure systemd et démarre le conteneur.

```bash
# En root ou via sudo. Tout est auto : Docker, clone, systemd, conteneur.
curl -sSL https://raw.githubusercontent.com/MattTen/sideserver_website/refs/heads/main/deploy/bootstrap.sh | sudo bash
```

La VM démarre toujours en **env prod**. Pour basculer en dev après coup (ou revenir en prod), on utilise le script de management :

```bash
website-management switch-dev    # passe sur la branche dev (rolling, pas de release)
website-management switch-prod   # revient sur la derniere release (= update prod)
```

### Variables optionnelles (env vars à passer à `sudo bash`)

| Variable | Défaut | Rôle |
|---|---|---|
| `BASE_URL` | *(vide)* | URL publique forcée. Si absent, l'app dérive l'URL depuis les headers HTTP (`X-Forwarded-*` via `--proxy-headers`) — c'est **recommandé** : changer d'IP ou ajouter un domaine ne nécessite pas de re-bootstrap. |
| `GITHUB_USER` | `MattTen` | Utilisateur Git pour l'auth du clone. |
| `GITHUB_TOKEN` | *(vide)* | PAT GitHub seulement si le repo est privé. Le repo actuel étant public, laisser vide. |
| `HOST_PORT` | `8000` | Port HTTP hôte. |

Exemple avec URL forcée :

```bash
curl -sSL https://raw.githubusercontent.com/MattTen/sideserver_website/refs/heads/main/deploy/bootstrap.sh \
  | sudo BASE_URL=https://store.mon-domaine.com bash
```

### Premier lancement

1. Ouvrir `http://<IP_VM>/` → redirection automatique vers `/setup/database` pour saisir la connexion MySQL/MariaDB (host/port/user/password/database)
2. Puis `/setup` pour créer le compte admin

> **Note SSH** : le bootstrap ajoute le user applicatif au groupe `docker`, mais les sessions SSH ouvertes **avant** le bootstrap ne voient pas le nouveau groupe. Reconnecte-toi (`exit` + `ssh`) ou lance `newgrp docker` pour utiliser `docker` sans sudo.

## Intégration SideStore

Dans SideStore (iOS) → Sources → Ajouter :

```
http://<IP_VM>/source.json
```

Le feed `source.json` utilise l'URL publique de la requête (ou `IPASTORE_BASE_URL` si défini) pour générer les `downloadURL`, `iconURL` etc. — SideStore effectuant des requêtes HTTP indépendantes depuis l'app iOS, des chemins relatifs ne suffisent pas.

## Sécurité : protection du dépôt

Par défaut, `source.json` est public : n'importe qui connaissant l'URL du serveur peut récupérer la liste des IPAs (et les télécharger). Pour limiter l'accès aux personnes à qui vous donnez le lien (et bloquer les bots de scraping), activez la protection par jeton dans **Réglages → Sécurité → "Protéger l'accès au dépôt d'IPA"**.

Une fois activé :

- Un jeton aléatoire de **256 caractères alphanumériques** est généré
- `GET /source.json` et `GET /qr.svg` exigent `?t=<jeton>`. Sans ce jeton, le serveur répond `404` (volontairement opaque pour les bots de scraping)
- L'URL du dépôt affichée sur le dashboard et le QR code intègrent automatiquement le jeton
- Le bouton **Régénérer** crée un nouveau jeton (avec confirmation). Les anciens liens cessent immédiatement de fonctionner

URL à coller dans SideStore quand la protection est activée :

```
http://<IP_VM>/source.json?t=<jeton-256-caracteres>
```

C'est un secret long plutôt qu'une authentification standard car SideStore ne sait pas envoyer de header custom : seul un `GET` avec query string est utilisable côté client iOS.

## Administration

Le script `tools/website-management.sh` est exposé via le symlink `/usr/local/bin/website-management`.

```bash
website-management                  # menu interactif
website-management --help           # aide complète

# Conteneur
website-management start / stop / restart / logs / status

# Code
website-management update           # prod : release-based / dev : rolling (auto-detect branche)
website-management check            # machine-readable : current / latest / update_available
website-management pull             # force pull HEAD de la branche courante (dev uniquement)
website-management self-update      # pull ce script (git pull de /opt/sideserver-prod)

# Bascule d'environnement
website-management switch-dev       # passe la VM en env dev (branche dev, rolling)
website-management switch-prod      # revient en env prod (checkout derniere release)

# Admin
website-management reset-users      # supprime tous les admins + en crée un nouveau (prompt login/mdp)
```

### Workflow release prod

1. Merger `dev` → `main`
2. `gh release create v1.2.0`
3. Sur la VM prod, l'UI `/settings` détecte la MAJ toutes les 6 h et affiche le bouton "Appliquer"
4. Le bouton écrit un flag-file → `ipastore-update@prod.path` (systemd) → `website-management prod-update`

## Configuration

Le conteneur lit sa config depuis `/etc/ipastore/prod.env`, écrit par le bootstrap. Voir [.env.example](.env.example) pour le détail.

| Variable | Rôle |
|---|---|
| `IPASTORE_BASE_URL` | *(optionnel)* URL publique forcée. Si absent, l'app dérive depuis les headers HTTP. |
| `IPASTORE_STORE_DIR` | Racine des binaires dans le conteneur (toujours `/srv/store`, monté depuis `/srv/store-prod` de l'hôte). |
| `IPASTORE_SECRET_FILE` | Chemin vers la clé de signature des cookies. |
| `IPASTORE_ENV` | `prod` (toujours `prod` dans le modèle mono-env, même sur la VM dev). |
| `IPASTORE_GITHUB_REPO` | Repo GitHub pour le check de releases (`MattTen/sideserver_website`). |

La connexion BDD (host/user/mdp/nom de base) n'est **pas** fournie en env var : elle est saisie via l'UI au premier démarrage (`/setup/database`) puis persistée dans `/etc/ipastore/db.json` (mode 600).

Voir [documentation/server.md](documentation/server.md) pour les détails complets.

## Structure

```
app/              # code Python (FastAPI)
templates/        # Jinja2
static/           # CSS + JS
patch/            # scripts de patch IPA (fix_ipa.py, etc.)
deploy/           # bootstrap.sh unique (curl | bash auto-suffisant, clone + release)
tools/            # website-management.sh + scinsta-builder/
documentation/    # doc serveur + credentials (exclu du déploiement serveur via sparse-checkout)
Dockerfile
docker-compose.yml
```

## Licence

[MIT](LICENSE)
