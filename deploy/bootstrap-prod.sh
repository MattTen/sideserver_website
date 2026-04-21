#!/usr/bin/env bash
# bootstrap-prod.sh : deploie UNIQUEMENT le conteneur prod de zero sur une VM
# Debian/Ubuntu vierge. Concu pour etre lance via curl | bash, soit en root
# direct soit via sudo :
#
#   # en root :
#   curl -sSL https://mondomaine.com/bootstrap-prod.sh \
#     | BASE_URL=http://<ip> bash
#
#   # via sudo (les env vars doivent etre passees a sudo, pas au shell
#   # appelant, sinon elles sont purgees par secure_path) :
#   curl -sSL https://mondomaine.com/bootstrap-prod.sh \
#     | sudo BASE_URL=http://<ip> bash
#
# Variables d'environnement :
#   BASE_URL       (REQUIS) URL publique du serveur, ex http://192.168.0.202
#   BRANCH         (optionnel) branche a cloner, defaut "main"
#   GITHUB_USER    (optionnel) user git pour le clone auth, defaut "MattTen"
#   GITHUB_TOKEN   (optionnel) PAT GitHub si le repo est prive
#   HOST_PORT      (optionnel) port HTTP hote, defaut 80
#
# La configuration BDD (host/user/mdp/nom de base) est saisie depuis l'UI admin
# a la premiere connexion via /setup/database -- ce script ne cree pas de BDD
# ni d'utilisateur MySQL. L'admin doit preparer son serveur BDD de son cote.

set -euo pipefail

# Sur Debian, `su -c` n'ajoute pas /usr/sbin au PATH, donc usermod/useradd
# sont introuvables. On force un PATH complet pour couvrir tous les cas
# (curl | bash piped, su -c, login shells, etc.).
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin${PATH:+:$PATH}"

if [[ $EUID -ne 0 ]]; then
  echo "Ce script doit etre lance en root." >&2
  exit 1
fi

: "${BASE_URL:?Exporter BASE_URL (ex: http://192.168.0.202)}"
BRANCH="${BRANCH:-main}"
GITHUB_USER="${GITHUB_USER:-MattTen}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
HOST_PORT="${HOST_PORT:-80}"
GITHUB_REPO="MattTen/sideserver_website"
TARGET_DIR="/opt/sideserver-prod"

echo "[bootstrap] Installation des paquets systeme..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg rsync git

if ! command -v docker >/dev/null 2>&1; then
  echo "[bootstrap] Installation de Docker..."
  # Le repo Docker differe selon l'OS (linux/debian vs linux/ubuntu).
  # Detection via /etc/os-release ID avec fallback debian.
  DOCKER_OS="$(. /etc/os-release && echo "$ID")"
  [[ "$DOCKER_OS" == "ubuntu" || "$DOCKER_OS" == "debian" ]] || DOCKER_OS="debian"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${DOCKER_OS}/gpg" \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${DOCKER_OS} $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi

# Le conteneur tourne en uid 1000 (user 'ipastore' interne). Les units systemd
# qui exposent le script de management doivent tourner sous le user local qui
# a uid 1000 (pour acceder a docker et ecrire dans /etc/ipastore). On detecte
# ce user automatiquement ; si uid 1000 n'existe pas, on cree "ipastore".
APP_USER="$(getent passwd 1000 | cut -d: -f1 || true)"
if [[ -z "$APP_USER" ]]; then
  useradd -m -u 1000 -s /bin/bash ipastore
  APP_USER="ipastore"
fi
APP_GROUP="$(getent group $(id -g "$APP_USER") | cut -d: -f1)"
echo "[bootstrap] User applicatif (uid 1000) : ${APP_USER}:${APP_GROUP}"

# Ajoute le user au groupe docker si pas deja dedans (necessaire pour que les
# units systemd ExecStart=website-management puissent piloter docker sans sudo).
if ! id -nG "$APP_USER" | tr ' ' '\n' | grep -qx docker; then
  usermod -aG docker "$APP_USER"
fi

echo "[bootstrap] Creation des repertoires..."
mkdir -p /srv/store-prod/{ipas,icons,screenshots}
mkdir -p /etc/ipastore
mkdir -p /var/lib/ipastore-sync
# Le conteneur monte /srv/store-prod sur /srv/store et cree news/, ipas/,
# icons/... en uid 1000. Sans chown explicite, les dirs restent root:root
# et le mkdir du conteneur echoue (PermissionError).
chown -R 1000:1000 /srv/store-prod
# Idem pour /etc/ipastore : il doit etre accessible en uid 1000 pour lire
# secret_key.*, db.json et ecrire les flags.
chown 1000:1000 /etc/ipastore
chmod 750 /etc/ipastore

echo "[bootstrap] Configuration des credentials git..."
# Si GITHUB_TOKEN fourni (repo prive), on stocke le PAT pour le clone.
if [[ -n "${GITHUB_TOKEN}" ]]; then
  cat > /etc/ipastore/.git-credentials <<EOF
https://${GITHUB_USER}:${GITHUB_TOKEN}@github.com
EOF
  chown 1000:1000 /etc/ipastore/.git-credentials
  chmod 600 /etc/ipastore/.git-credentials
  GIT_CRED=(-c "credential.helper=store --file /etc/ipastore/.git-credentials")
else
  GIT_CRED=()
fi

echo "[bootstrap] Clone du repo (branche ${BRANCH}) dans ${TARGET_DIR}..."
# safe.directory=* : au re-run le dir est chowne APP_USER mais git tourne
# en root ici -> sans cette config, git refuse avec "dubious ownership".
GIT_SAFE=(-c "safe.directory=${TARGET_DIR}")
if [[ ! -d "${TARGET_DIR}/.git" ]]; then
  rm -rf "${TARGET_DIR}"
  git "${GIT_SAFE[@]}" "${GIT_CRED[@]}" \
    clone -b "${BRANCH}" "https://github.com/${GITHUB_REPO}.git" "${TARGET_DIR}"
else
  git "${GIT_SAFE[@]}" "${GIT_CRED[@]}" \
    -C "${TARGET_DIR}" fetch origin "${BRANCH}"
  git "${GIT_SAFE[@]}" -C "${TARGET_DIR}" checkout --force "${BRANCH}"
  git "${GIT_SAFE[@]}" -C "${TARGET_DIR}" reset --hard "origin/${BRANCH}"
fi

# Sparse-checkout : la doc et CLAUDE.md ne sont pas necessaires sur le serveur.
git "${GIT_SAFE[@]}" -C "${TARGET_DIR}" sparse-checkout init --no-cone 2>/dev/null || true
git "${GIT_SAFE[@]}" -C "${TARGET_DIR}" sparse-checkout set '/*' '!documentation' '!CLAUDE.md' 2>/dev/null || true
chown -R "${APP_USER}:${APP_GROUP}" "${TARGET_DIR}"

echo "[bootstrap] Ecriture du fichier d'environnement..."
# IPASTORE_DB_URL n'est PAS defini ici : la connexion BDD est saisie via
# l'UI (/setup/database) et persistee dans /etc/ipastore/db.json.
cat > /etc/ipastore/prod.env <<EOF
IPASTORE_STORE_DIR=/srv/store
IPASTORE_SECRET_FILE=/etc/ipastore/secret_key.prod
IPASTORE_BASE_URL=${BASE_URL}
IPASTORE_ENV=prod
IPASTORE_GITHUB_REPO=${GITHUB_REPO}
EOF
chmod 640 /etc/ipastore/prod.env

echo "[bootstrap] Generation de la cle de session si absente..."
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

echo "[bootstrap] Installation des units systemd (path + service templatises)..."
# Les units sont embarquees dans ce script pour que curl | bash fonctionne
# sans dependance a un clone local. User/Group substitues par ${APP_USER}
# detecte plus haut (altuser historique ou user uid 1000 existant).

cat > /etc/systemd/system/ipastore-update@.path <<EOF
[Unit]
Description=Watch /etc/ipastore/update-requested-%i flag (triggers update of %i container)

[Path]
PathExists=/etc/ipastore/update-requested-%i
Unit=ipastore-update@%i.service

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/ipastore-update@.service <<EOF
[Unit]
Description=Apply update to %i environment (triggered by flag file)
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
User=${APP_USER}
Group=${APP_GROUP}
# On enleve TOUT DE SUITE le flag pour eviter les re-triggers en boucle.
ExecStartPre=/bin/rm -f /etc/ipastore/update-requested-%i
ExecStart=/usr/local/bin/website-management %i-update
StandardOutput=journal
StandardError=journal
TimeoutStartSec=600
EOF

cat > /etc/systemd/system/ipastore-scinsta-build@.path <<EOF
[Unit]
Description=Watch /etc/ipastore/scinsta-build-requested-%i flag

[Path]
PathExists=/etc/ipastore/scinsta-build-requested-%i
Unit=ipastore-scinsta-build@%i.service

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/ipastore-scinsta-build@.service <<EOF
[Unit]
Description=Build SCInsta IPA (Instagram + SCInsta main clone) pour %i
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
User=${APP_USER}
Group=${APP_GROUP}
# Le flag est lu PUIS supprime par build.py (read_flag_payload) : ne PAS le
# supprimer ici sinon le payload JSON est perdu avant lecture.
ExecStart=/usr/local/bin/website-management %i-scinsta-build
StandardOutput=journal
StandardError=journal
# Clone SCInsta + Theos build + cyan + ipapatch : 5-15 min selon la VM.
TimeoutStartSec=1800
EOF

cat > /etc/systemd/system/ipastore-scinsta-cancel@.path <<EOF
[Unit]
Description=Watch /etc/ipastore/scinsta-build-cancel-%i flag (abort running build)

[Path]
PathExists=/etc/ipastore/scinsta-build-cancel-%i
Unit=ipastore-scinsta-cancel@%i.service

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/ipastore-scinsta-cancel@.service <<EOF
[Unit]
Description=Abort a running SCInsta build for %i
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
User=${APP_USER}
Group=${APP_GROUP}
ExecStart=/usr/local/bin/website-management %i-scinsta-cancel
StandardOutput=journal
StandardError=journal
TimeoutStartSec=30
EOF

systemctl daemon-reload
systemctl enable --now \
  ipastore-update@prod.path \
  ipastore-scinsta-build@prod.path \
  ipastore-scinsta-cancel@prod.path

echo "[bootstrap] Symlink website-management..."
if [[ -f "${TARGET_DIR}/tools/website-management.sh" ]]; then
  ln -sf "${TARGET_DIR}/tools/website-management.sh" /usr/local/bin/website-management
  chmod +x "${TARGET_DIR}/tools/website-management.sh"
fi

echo "[bootstrap] Ecriture du .env docker-compose..."
cat > "${TARGET_DIR}/.env" <<EOF
CONTAINER_NAME=sidestore-website-prod
HOST_PORT=${HOST_PORT}
ENV_FILE=/etc/ipastore/prod.env
STORE_PATH=/srv/store-prod
IMAGE_TAG=prod
EOF
chown "${APP_USER}:${APP_GROUP}" "${TARGET_DIR}/.env"

echo "[bootstrap] Build + start du conteneur..."
( cd "${TARGET_DIR}" && docker compose up -d --build )

echo
echo "[bootstrap] Termine."
echo "  URL admin      : ${BASE_URL}"
echo "  Premier acces  : /setup/database pour configurer la connexion MySQL/MariaDB"
echo "  Puis           : /setup pour creer l'admin"
echo
echo "Management CLI   : /usr/local/bin/website-management"
echo "  (sans argument = menu interactif)"
