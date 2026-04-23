#!/usr/bin/env bash
# bootstrap.sh : deploie la prod (derniere release GitHub) de zero sur une VM
# Debian/Ubuntu vierge. Un SEUL bootstrap, mono-env :
#
#   curl -sSL https://raw.githubusercontent.com/MattTen/sideserver_website/main/deploy/bootstrap.sh | sudo bash
#
# Apres le bootstrap, passer en env dev se fait via le script de management :
#   website-management switch-dev    # bascule sur la branche dev (rolling)
#   website-management switch-prod   # revient sur la derniere release
#
# Le script clone main, fetch les tags, puis checkout le DERNIER TAG de release
# (HEAD detache). Si aucune release n'existe, reste sur main avec un warning.
#
# Par defaut l'app derive son URL publique des headers HTTP (request.base_url
# avec support X-Forwarded-* pour les reverse proxy), donc changer d'IP ou
# passer sur un domaine ne demande PAS de re-bootstrap.
#
# Variables d'environnement (toutes optionnelles) :
#   BASE_URL       URL publique hardcodee (ex http://192.168.0.202). Si absent,
#                  l'app utilise request.base_url dynamiquement.
#   GITHUB_USER    user git pour le clone auth, defaut "MattTen"
#   GITHUB_TOKEN   PAT GitHub si le repo est prive
#   HOST_PORT      port HTTP hote, defaut 80
#
# La configuration BDD (host/user/mdp/nom de base) est saisie depuis l'UI admin
# a la premiere connexion via /setup/database -- ce script ne cree pas de BDD
# ni d'utilisateur MySQL. L'admin doit preparer son serveur BDD de son cote.
#
# GESTION DES PERMISSIONS : le script cree un user+groupe `ipastore` dedie
# (uid/gid 1000 si libre, sinon auto) et l'ajoute au groupe docker. Les
# units systemd tournent en tant que `ipastore`. L'user qui lance sudo
# bash n'est PAS touche -- s'il veut docker sans sudo, il doit s'ajouter
# manuellement au groupe docker. C'est volontaire pour eviter de donner
# des permissions docker a un user par effet de bord.

set -euo pipefail

# Sur Debian, `su -c` n'ajoute pas /usr/sbin au PATH, donc usermod/useradd
# sont introuvables. On force un PATH complet pour couvrir tous les cas
# (curl | bash piped, su -c, login shells, etc.).
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin${PATH:+:$PATH}"

if [[ $EUID -ne 0 ]]; then
  echo "Ce script doit etre lance en root." >&2
  exit 1
fi

BASE_URL="${BASE_URL:-}"
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

# Le conteneur tourne en uid de l'user interne `ipastore`. Pour matcher les
# permissions host <-> conteneur sur les volumes montes (/etc/ipastore,
# /srv/store), on cree un user+groupe SYSTEME dedie `ipastore` (uid/gid
# dans la plage system, pas de /home, shell nologin). Les uid/gid reels
# sont ensuite passes au build Docker via build-args pour que l'user
# interne du conteneur ait les memes uid/gid que l'user host -- pas
# besoin de cibler un uid fixe (ex 1000).
#
# Important : on ne TOUCHE PAS a l'user qui lance sudo bash ($SUDO_USER).
# S'il veut utiliser docker directement, il l'ajoute lui-meme au groupe
# docker (usermod -aG docker <user>).
APP_USER="ipastore"
APP_GROUP="ipastore"

if ! getent group ipastore >/dev/null; then
  groupadd -r ipastore
fi

if ! id -u ipastore >/dev/null 2>&1; then
  # -r : user systeme (uid dans la plage system, pas de fichier a la racine home)
  # -M : pas de creation de /home/ipastore (pas besoin, c'est un user d'infra)
  # -s /usr/sbin/nologin : interdit l'ouverture de session interactive
  useradd -r -M -g ipastore -s /usr/sbin/nologin ipastore
fi

IPASTORE_UID="$(id -u ipastore)"
IPASTORE_GID="$(id -g ipastore)"
echo "[bootstrap] User applicatif : ${APP_USER}:${APP_GROUP} (uid=${IPASTORE_UID} gid=${IPASTORE_GID})"

# Le user `ipastore` doit pouvoir piloter docker car les units systemd
# (ExecStart=website-management) tournent sous cet user. L'user courant
# qui lance sudo bash n'est PAS touche.
if ! id -nG "$APP_USER" | tr ' ' '\n' | grep -qx docker; then
  usermod -aG docker "$APP_USER"
fi

echo "[bootstrap] Creation des repertoires..."
mkdir -p /srv/store-prod/{ipas,icons,screenshots}
mkdir -p /etc/ipastore
mkdir -p /var/lib/ipastore-sync
# Le conteneur monte /srv/store-prod sur /srv/store et cree news/, ipas/,
# icons/... en uid de l'user interne `ipastore`. Sans chown explicite,
# les dirs restent root:root et le mkdir du conteneur echoue.
chown -R "${IPASTORE_UID}:${IPASTORE_GID}" /srv/store-prod
# Idem pour /etc/ipastore : il doit etre accessible en uid ipastore pour
# lire secret_key.*, db.json et ecrire les flags.
chown "${IPASTORE_UID}:${IPASTORE_GID}" /etc/ipastore
chmod 750 /etc/ipastore

echo "[bootstrap] Configuration des credentials git..."
# Si GITHUB_TOKEN fourni (repo prive), on stocke le PAT pour le clone.
if [[ -n "${GITHUB_TOKEN}" ]]; then
  cat > /etc/ipastore/.git-credentials <<EOF
https://${GITHUB_USER}:${GITHUB_TOKEN}@github.com
EOF
  chown "${IPASTORE_UID}:${IPASTORE_GID}" /etc/ipastore/.git-credentials
  chmod 600 /etc/ipastore/.git-credentials
  GIT_CRED=(-c "credential.helper=store --file /etc/ipastore/.git-credentials")
else
  GIT_CRED=()
fi

echo "[bootstrap] Clone du repo dans ${TARGET_DIR}..."
# safe.directory=* : au re-run le dir est chowne APP_USER mais git tourne
# en root ici -> sans cette config, git refuse avec "dubious ownership".
GIT_SAFE=(-c "safe.directory=${TARGET_DIR}")
if [[ ! -d "${TARGET_DIR}/.git" ]]; then
  rm -rf "${TARGET_DIR}"
  git "${GIT_SAFE[@]}" "${GIT_CRED[@]}" \
    clone "https://github.com/${GITHUB_REPO}.git" "${TARGET_DIR}"
else
  git "${GIT_SAFE[@]}" "${GIT_CRED[@]}" \
    -C "${TARGET_DIR}" fetch origin
fi

# On doit avoir tous les tags pour pouvoir checkout la derniere release.
git "${GIT_SAFE[@]}" "${GIT_CRED[@]}" -C "${TARGET_DIR}" fetch --tags --prune origin

# Recupere le tag de la derniere release via l'API GitHub.
echo "[bootstrap] Recuperation de la derniere release..."
API_CURL=(-fsSL -H 'Accept: application/vnd.github+json')
[[ -n "${GITHUB_TOKEN}" ]] && API_CURL+=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
LATEST_TAG="$(
  curl "${API_CURL[@]}" "https://api.github.com/repos/${GITHUB_REPO}/releases/latest" 2>/dev/null \
    | grep -oE '"tag_name"[[:space:]]*:[[:space:]]*"[^"]+"' \
    | head -n1 \
    | sed -E 's/.*"([^"]+)"[[:space:]]*$/\1/' \
    || true
)"

if [[ -n "${LATEST_TAG}" ]]; then
  echo "[bootstrap] Checkout release ${LATEST_TAG} (HEAD detache)"
  git "${GIT_SAFE[@]}" -C "${TARGET_DIR}" checkout --force "${LATEST_TAG}"
  DEPLOYED_VERSION="${LATEST_TAG}"
else
  echo "[bootstrap] WARNING : aucune release publiee -- fallback sur la branche main"
  git "${GIT_SAFE[@]}" -C "${TARGET_DIR}" checkout --force main
  git "${GIT_SAFE[@]}" -C "${TARGET_DIR}" reset --hard origin/main
  DEPLOYED_VERSION="rolling-main-$(git "${GIT_SAFE[@]}" -C "${TARGET_DIR}" rev-parse --short HEAD)"
fi

# Sparse-checkout : la doc et CLAUDE.md ne sont pas necessaires sur le serveur.
git "${GIT_SAFE[@]}" -C "${TARGET_DIR}" sparse-checkout init --no-cone 2>/dev/null || true
git "${GIT_SAFE[@]}" -C "${TARGET_DIR}" sparse-checkout set '/*' '!documentation' '!CLAUDE.md' 2>/dev/null || true
chown -R "${APP_USER}:${APP_GROUP}" "${TARGET_DIR}"

echo "[bootstrap] Ecriture du fichier d'environnement..."
# IPASTORE_DB_URL n'est PAS defini ici : la connexion BDD est saisie via
# l'UI (/setup/database) et persistee dans /etc/ipastore/db.json.
# Si BASE_URL n'est pas fourni, on omet IPASTORE_BASE_URL : l'app retombe
# sur request.base_url (uvicorn lit X-Forwarded-* via --proxy-headers) et
# l'admin peut acceder via n'importe quelle IP/domaine sans re-bootstrap.
{
  echo "IPASTORE_STORE_DIR=/srv/store"
  echo "IPASTORE_SECRET_FILE=/etc/ipastore/secret_key.prod"
  [[ -n "${BASE_URL}" ]] && echo "IPASTORE_BASE_URL=${BASE_URL}"
  echo "IPASTORE_ENV=prod"
  echo "IPASTORE_GITHUB_REPO=${GITHUB_REPO}"
} > /etc/ipastore/prod.env
# chown ipastore : les units systemd tournent en user `ipastore` et
# docker-compose doit pouvoir lire ce fichier (env_file) pour le passer au
# conteneur. Sans chown, le fichier reste root:root et docker-compose
# echoue avec "open /etc/ipastore/prod.env: permission denied".
chown "${IPASTORE_UID}:${IPASTORE_GID}" /etc/ipastore/prod.env
chmod 640 /etc/ipastore/prod.env

echo "[bootstrap] Generation de la cle de session si absente..."
f=/etc/ipastore/secret_key.prod
if [[ ! -f "$f" ]]; then
  head -c 64 /dev/urandom > "$f"
fi
chown "${IPASTORE_UID}:${IPASTORE_GID}" "$f"
chmod 600 "$f"

echo "[bootstrap] Ecriture du fichier version (${DEPLOYED_VERSION})..."
f="/etc/ipastore/prod.version"
printf '%s\n' "${DEPLOYED_VERSION}" > "$f"
# chown ipastore : website-management tourne en user `ipastore` et doit
# pouvoir ecrire ce fichier pour mettre a jour la version deployee apres
# chaque update/pull.
chown "${IPASTORE_UID}:${IPASTORE_GID}" "$f"
chmod 644 "$f"

echo "[bootstrap] Installation des units systemd (path + service templatises)..."
# Les units sont embarquees dans ce script pour que curl | bash fonctionne
# sans dependance a un clone local. User/Group substitues par ${APP_USER}
# detecte plus haut (user uid 1000 existant).

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
# IPASTORE_UID/GID sont passees au Dockerfile via build-args dans
# docker-compose.yml : l'user interne du conteneur est ainsi cree avec
# les memes uid/gid que l'user host `ipastore`, ce qui garantit que les
# fichiers ecrits via volumes montes ont les bonnes permissions.
cat > "${TARGET_DIR}/.env" <<EOF
CONTAINER_NAME=ipastore-website
HOST_PORT=${HOST_PORT}
ENV_FILE=/etc/ipastore/prod.env
STORE_PATH=/srv/store-prod
IMAGE_TAG=${DEPLOYED_VERSION}
IPASTORE_UID=${IPASTORE_UID}
IPASTORE_GID=${IPASTORE_GID}
EOF
chown "${APP_USER}:${APP_GROUP}" "${TARGET_DIR}/.env"

echo "[bootstrap] Build + start du conteneur..."
( cd "${TARGET_DIR}" && docker compose up -d --build )

echo
echo "[bootstrap] Termine."
echo "  Version deployee : ${DEPLOYED_VERSION}"
if [[ -n "${BASE_URL}" ]]; then
  echo "  URL admin        : ${BASE_URL}"
else
  echo "  URL admin        : http://<ip-de-cette-vm>:${HOST_PORT}"
fi
echo "  Premier acces    : /setup/database pour configurer la connexion MySQL/MariaDB"
echo "  Puis             : /setup pour creer l'admin"
echo
echo "Management CLI    : /usr/local/bin/website-management"
echo "  (sans argument = menu interactif)"
echo "  switch-dev      : bascule en env dev (branche dev, rolling)"
echo "  switch-prod     : revient en prod (derniere release)"
echo "  update          : dispatch auto selon env courant"
echo
echo "Gestion des permissions :"
echo "  - L'user applicatif '${APP_USER}' a ete cree avec uid ${IPASTORE_UID}"
echo "    et ajoute au groupe docker (necessaire pour les units systemd)."
echo "  - Ton user courant (\$SUDO_USER=${SUDO_USER:-root}) n'a PAS ete modifie."
echo "  - Si tu veux utiliser 'docker ps' directement sans sudo, ajoute-toi"
echo "    manuellement : sudo usermod -aG docker \$USER && newgrp docker"
echo "  - Sinon, prefere 'sudo docker ps' ou 'website-management status'."
