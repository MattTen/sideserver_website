#!/usr/bin/env bash
# bootstrap-prod.sh : prépare une VM Debian/Ubuntu vierge pour héberger UNIQUEMENT le conteneur prod.
# À exécuter UNE FOIS en root sur la VM.
#
# Prérequis : MariaDB déjà installé et root@localhost configuré.
# Variables : DB_PASS_PROD doit être exportée avant l'exécution.
#             (Ce mot de passe sera aussi écrit dans /etc/ipastore/prod.env.)

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Ce script doit être lancé en root." >&2
  exit 1
fi

: "${DB_PASS_PROD:?Exporter DB_PASS_PROD (mot de passe MariaDB pour l'user ipastore-prod)}"
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
mkdir -p /etc/ipastore
mkdir -p /var/lib/ipastore-sync
chmod 750 /etc/ipastore

echo "[bootstrap] Création de la base de données..."
mysql -u root <<SQL
CREATE DATABASE IF NOT EXISTS \`ipastore-prod\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'ipastore-prod'@'%' IDENTIFIED BY '${DB_PASS_PROD}';
ALTER USER 'ipastore-prod'@'%' IDENTIFIED BY '${DB_PASS_PROD}';

GRANT ALL PRIVILEGES ON \`ipastore-prod\`.* TO 'ipastore-prod'@'%';
FLUSH PRIVILEGES;
SQL

echo "[bootstrap] Vérification du bind MariaDB (bind-address)..."
if grep -Rq "^bind-address\s*=\s*127\.0\.0\.1" /etc/mysql/ 2>/dev/null; then
  echo "  /!\\ MariaDB écoute sur 127.0.0.1 uniquement."
  echo "      Les conteneurs y accèdent via host.docker.internal -> passe bind-address à 0.0.0.0"
  echo "      (ou utilise network_mode: host). Édite /etc/mysql/mariadb.conf.d/50-server.cnf."
fi

echo "[bootstrap] Écriture du fichier d'environnement..."
cat > /etc/ipastore/prod.env <<EOF
IPASTORE_DB_URL=mysql+pymysql://ipastore-prod:${DB_PASS_PROD}@host.docker.internal:3306/ipastore-prod?charset=utf8mb4
IPASTORE_STORE_DIR=/srv/store
IPASTORE_SECRET_FILE=/etc/ipastore/secret_key.prod
IPASTORE_BASE_URL=${BASE_URL}
IPASTORE_ENV=prod
IPASTORE_GITHUB_REPO=MattTen/sideserver_website
EOF
chmod 640 /etc/ipastore/prod.env

echo "[bootstrap] Génération de la clé de session si absente..."
# Le conteneur tourne en uid 1000 (user 'ipastore') — la clé doit lui appartenir.
f=/etc/ipastore/secret_key.prod
if [[ ! -f "$f" ]]; then
  head -c 64 /dev/urandom > "$f"
fi
chown 1000:1000 "$f"
chmod 600 "$f"

echo "[bootstrap] Fichier version (placeholder)..."
f="/etc/ipastore/prod.version"
[[ -f "$f" ]] || : > "$f"
chmod 644 "$f"

echo "[bootstrap] Installation des unités systemd (path + service templatisés)..."
SRC_DIR="$(cd "$(dirname "$0")" && pwd)/systemd"
if [[ -d "$SRC_DIR" ]]; then
  install -m 644 "$SRC_DIR/ipastore-update@.path"            /etc/systemd/system/ipastore-update@.path
  install -m 644 "$SRC_DIR/ipastore-update@.service"         /etc/systemd/system/ipastore-update@.service
  install -m 644 "$SRC_DIR/ipastore-scinsta-build@.path"     /etc/systemd/system/ipastore-scinsta-build@.path
  install -m 644 "$SRC_DIR/ipastore-scinsta-build@.service"  /etc/systemd/system/ipastore-scinsta-build@.service
  install -m 644 "$SRC_DIR/ipastore-scinsta-cancel@.path"    /etc/systemd/system/ipastore-scinsta-cancel@.path
  install -m 644 "$SRC_DIR/ipastore-scinsta-cancel@.service" /etc/systemd/system/ipastore-scinsta-cancel@.service
  systemctl daemon-reload
  systemctl enable --now \
    ipastore-update@prod.path \
    ipastore-scinsta-build@prod.path \
    ipastore-scinsta-cancel@prod.path
  echo "  units activées :"
  echo "    ipastore-update@prod.path"
  echo "    ipastore-scinsta-build@prod.path"
  echo "    ipastore-scinsta-cancel@prod.path"
else
  echo "  /!\\ $SRC_DIR absent — unités systemd non installées."
  echo "      (normal si tu lances bootstrap-prod.sh depuis un clone sparse qui exclut deploy/)"
fi

echo "[bootstrap] Terminé."
echo
echo "Étapes suivantes :"
echo "  1. git clone https://github.com/MattTen/sideserver_website.git /opt/sideserver-prod"
echo "  2. Créer /opt/sideserver-prod/.env (CONTAINER_NAME, HOST_PORT=80, ENV_FILE=/etc/ipastore/prod.env, STORE_PATH=/srv/store-prod)"
echo "  3. cd /opt/sideserver-prod && docker compose up -d --build"
echo
echo "Le script ./website-management.sh (dans le clone) gère l'environnement prod."
echo "Utilise ./website-management.sh sans argument pour le menu interactif."
