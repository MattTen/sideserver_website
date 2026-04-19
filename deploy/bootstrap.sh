#!/usr/bin/env bash
# bootstrap.sh : prépare une VM Debian vierge pour héberger les deux conteneurs.
# À exécuter UNE FOIS en root sur la VM.
#
# Prérequis : MariaDB déjà installé et root@localhost configuré.
# Variables : DB_PASS_PROD et DB_PASS_DEV doivent être exportées avant l'exécution.
#             (Ces mots de passe seront aussi écrits dans /etc/ipastore/{prod,dev}.env.)

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Ce script doit être lancé en root." >&2
  exit 1
fi

: "${DB_PASS_PROD:?Exporter DB_PASS_PROD (mot de passe MariaDB pour l'user ipastore-prod)}"
: "${DB_PASS_DEV:?Exporter DB_PASS_DEV (mot de passe MariaDB pour l'user ipastore-dev)}"
: "${BASE_URL:?Exporter BASE_URL (ex: http://192.168.1.100)}"

echo "[bootstrap] Installation des paquets..."
apt-get update
apt-get install -y ca-certificates curl gnupg rsync git mariadb-client

if ! command -v docker >/dev/null 2>&1; then
  echo "[bootstrap] Installation de Docker..."
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi

echo "[bootstrap] Création des répertoires..."
mkdir -p /srv/store-prod/{ipas,icons,screenshots}
mkdir -p /srv/store-dev/{ipas,icons,screenshots}
mkdir -p /etc/ipastore
mkdir -p /var/lib/ipastore-sync
chmod 750 /etc/ipastore

echo "[bootstrap] Création des bases de données..."
mysql -u root <<SQL
CREATE DATABASE IF NOT EXISTS \`ipastore-prod\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS \`ipastore-dev\`  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'ipastore-prod'@'%' IDENTIFIED BY '${DB_PASS_PROD}';
CREATE USER IF NOT EXISTS 'ipastore-dev'@'%'  IDENTIFIED BY '${DB_PASS_DEV}';
ALTER USER 'ipastore-prod'@'%' IDENTIFIED BY '${DB_PASS_PROD}';
ALTER USER 'ipastore-dev'@'%'  IDENTIFIED BY '${DB_PASS_DEV}';

GRANT ALL PRIVILEGES ON \`ipastore-prod\`.* TO 'ipastore-prod'@'%';
GRANT ALL PRIVILEGES ON \`ipastore-dev\`.*  TO 'ipastore-dev'@'%';
FLUSH PRIVILEGES;
SQL

echo "[bootstrap] Vérification du bind MariaDB (bind-address)..."
if grep -Rq "^bind-address\s*=\s*127\.0\.0\.1" /etc/mysql/ 2>/dev/null; then
  echo "  /!\\ MariaDB écoute sur 127.0.0.1 uniquement."
  echo "      Les conteneurs y accèdent via host.docker.internal -> passe bind-address à 0.0.0.0"
  echo "      (ou utilise network_mode: host). Édite /etc/mysql/mariadb.conf.d/50-server.cnf."
fi

echo "[bootstrap] Écriture des fichiers d'environnement..."
cat > /etc/ipastore/prod.env <<EOF
IPASTORE_DB_URL=mysql+pymysql://ipastore-prod:${DB_PASS_PROD}@host.docker.internal:3306/ipastore-prod?charset=utf8mb4
IPASTORE_STORE_DIR=/srv/store
IPASTORE_SECRET_FILE=/etc/ipastore/secret_key.prod
IPASTORE_BASE_URL=${BASE_URL}
IPASTORE_ENV=prod
IPASTORE_GITHUB_REPO=MattTen/sideserver_website
EOF
chmod 640 /etc/ipastore/prod.env

cat > /etc/ipastore/dev.env <<EOF
IPASTORE_DB_URL=mysql+pymysql://ipastore-dev:${DB_PASS_DEV}@host.docker.internal:3306/ipastore-dev?charset=utf8mb4
IPASTORE_STORE_DIR=/srv/store
IPASTORE_SECRET_FILE=/etc/ipastore/secret_key.dev
IPASTORE_BASE_URL=${BASE_URL}
IPASTORE_ENV=dev
IPASTORE_GITHUB_REPO=MattTen/sideserver_website
EOF
chmod 640 /etc/ipastore/dev.env

echo "[bootstrap] Génération des clés de session si absentes..."
# Le conteneur tourne en uid 1000 (user 'ipastore') — les clés doivent lui appartenir.
for f in /etc/ipastore/secret_key.prod /etc/ipastore/secret_key.dev; do
  if [[ ! -f "$f" ]]; then
    head -c 64 /dev/urandom > "$f"
  fi
  chown 1000:1000 "$f"
  chmod 600 "$f"
done

echo "[bootstrap] Fichiers version (placeholders)..."
for env in prod dev; do
  f="/etc/ipastore/${env}.version"
  [[ -f "$f" ]] || : > "$f"
  chmod 644 "$f"
done

echo "[bootstrap] Installation des unités systemd update (path + service templatisés)..."
SRC_DIR="$(cd "$(dirname "$0")" && pwd)/systemd"
if [[ -d "$SRC_DIR" ]]; then
  install -m 644 "$SRC_DIR/ipastore-update@.path"    /etc/systemd/system/ipastore-update@.path
  install -m 644 "$SRC_DIR/ipastore-update@.service" /etc/systemd/system/ipastore-update@.service
  systemctl daemon-reload
  systemctl enable --now ipastore-update@prod.path ipastore-update@dev.path
  echo "  units activées : ipastore-update@prod.path / ipastore-update@dev.path"
else
  echo "  /!\\ $SRC_DIR absent — unités systemd non installées."
  echo "      (normal si tu lances bootstrap.sh depuis un clone sparse qui exclut deploy/)"
fi

echo "[bootstrap] Terminé."
echo
echo "Étapes suivantes :"
echo "  1. git clone https://github.com/MattTen/sideserver_website.git /opt/sideserver-prod"
echo "  2. git clone -b dev https://github.com/MattTen/sideserver_website.git /opt/sideserver-dev"
echo "  3. Créer /opt/sideserver-prod/.env (CONTAINER_NAME, HOST_PORT=80, ENV_FILE=/etc/ipastore/prod.env, STORE_PATH=/srv/store-prod)"
echo "  4. Créer /opt/sideserver-dev/.env  (CONTAINER_NAME, HOST_PORT=8080, ENV_FILE=/etc/ipastore/dev.env,  STORE_PATH=/srv/store-dev)"
echo "  5. cd /opt/sideserver-prod && docker compose up -d --build"
echo "  6. cd /opt/sideserver-dev  && ./website-management.sh dev-start"
echo
echo "Le script ./website-management.sh (dans chaque clone) gère les 2 environnements."
echo "Utilise ./website-management.sh sans argument pour le menu interactif."
