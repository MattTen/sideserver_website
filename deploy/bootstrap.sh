#!/usr/bin/env bash
# bootstrap.sh : prépare une VM Debian vierge pour héberger les deux conteneurs.
# À exécuter UNE FOIS en root sur la VM.
#
# La configuration BDD (host/user/mdp/nom de base) est saisie depuis l'UI admin
# à la première connexion via /setup/database — ce script ne crée plus de BDD
# ni d'utilisateur MySQL. L'admin doit préparer ses schémas de son côté.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Ce script doit être lancé en root." >&2
  exit 1
fi

: "${BASE_URL:?Exporter BASE_URL (ex: http://192.168.1.100)}"

echo "[bootstrap] Installation des paquets..."
apt-get update
apt-get install -y ca-certificates curl gnupg rsync git

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

echo "[bootstrap] Écriture des fichiers d'environnement..."
# IPASTORE_DB_URL n'est plus défini ici : la connexion BDD est saisie via
# l'UI (/setup/database) et persistée dans /etc/ipastore/db.json.
cat > /etc/ipastore/prod.env <<EOF
IPASTORE_STORE_DIR=/srv/store
IPASTORE_SECRET_FILE=/etc/ipastore/secret_key.prod
IPASTORE_BASE_URL=${BASE_URL}
IPASTORE_ENV=prod
IPASTORE_GITHUB_REPO=MattTen/sideserver_website
EOF
chmod 640 /etc/ipastore/prod.env

cat > /etc/ipastore/dev.env <<EOF
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
    ipastore-update@prod.path         ipastore-update@dev.path \
    ipastore-scinsta-build@prod.path  ipastore-scinsta-build@dev.path \
    ipastore-scinsta-cancel@prod.path ipastore-scinsta-cancel@dev.path
  echo "  units activées :"
  echo "    ipastore-update@{prod,dev}.path"
  echo "    ipastore-scinsta-build@{prod,dev}.path"
  echo "    ipastore-scinsta-cancel@{prod,dev}.path"
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
