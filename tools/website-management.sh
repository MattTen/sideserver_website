#!/usr/bin/env bash
# website-management.sh — Gestion des environnements prod/dev de sideserver_website.
#
# Sans argument : menu interactif.
# Avec argument : exécution d'une commande unique (voir --help).

set -euo pipefail

# ────── Config ──────
PROD_DIR="${SIDESERVER_PROD_DIR:-/opt/sideserver-prod}"
DEV_DIR="${SIDESERVER_DEV_DIR:-/opt/sideserver-dev}"
TOOLS_DIR="${SIDESERVER_TOOLS_DIR:-/opt/sideserver-tools}"
GITHUB_REPO="${SIDESERVER_REPO:-MattTen/sideserver_website}"
DB_PROD="ipastore-prod"
DB_DEV="ipastore-dev"
STORE_PROD="/srv/store-prod"
STORE_DEV="/srv/store-dev"
MYSQL_DEFAULTS="/etc/ipastore/.mysql.cnf"
GIT_CREDENTIALS_FILE="/etc/ipastore/.git-credentials"

# Couleurs
C_CYAN='\033[1;36m'
C_GREEN='\033[1;32m'
C_YELLOW='\033[1;33m'
C_RED='\033[1;31m'
C_DIM='\033[2m'
C_BOLD='\033[1m'
C_RESET='\033[0m'

info()  { printf "${C_CYAN}[mgmt]${C_RESET} %s\n" "$*"; }
ok()    { printf "${C_GREEN}[mgmt]${C_RESET} %s\n" "$*"; }
warn()  { printf "${C_YELLOW}[mgmt]${C_RESET} %s\n" "$*" >&2; }
err()   { printf "${C_RED}[mgmt]${C_RESET} %s\n" "$*" >&2; }

env_dir() {
  case "$1" in
    prod) echo "$PROD_DIR" ;;
    dev)  echo "$DEV_DIR" ;;
    *)    err "env inconnu : $1"; exit 1 ;;
  esac
}

container_name() {
  case "$1" in
    prod) echo sidestore-website-prod ;;
    dev)  echo sidestore-website-dev ;;
  esac
}

env_branch() {
  case "$1" in
    prod) echo main ;;
    dev)  echo dev ;;
  esac
}

version_file() {
  echo "/etc/ipastore/${1}.version"
}

# ────── Helpers releases ──────

# Extrait le token GitHub de /etc/ipastore/.git-credentials (format:
# https://user:TOKEN@github.com). Renvoie vide si absent/illisible.
github_token() {
  [[ -r "$GIT_CREDENTIALS_FILE" ]] || return 0
  sed -nE 's|^https://[^:]+:([^@]+)@github\.com.*|\1|p' "$GIT_CREDENTIALS_FILE" | head -n1
}

# Renvoie le tag_name de la dernière release (ex: v1.2.3), ou vide si rien.
# Nécessaire d'être authentifié pour les repos privés.
latest_release_tag() {
  local -a curl_args=(-fsSL -H 'Accept: application/vnd.github+json')
  local token; token="$(github_token)"
  [[ -n "$token" ]] && curl_args+=(-H "Authorization: Bearer $token")
  curl "${curl_args[@]}" \
    "https://api.github.com/repos/${GITHUB_REPO}/releases/latest" 2>/dev/null \
    | grep -oE '"tag_name"[[:space:]]*:[[:space:]]*"[^"]+"' \
    | head -n1 \
    | sed -E 's/.*"([^"]+)"[[:space:]]*$/\1/'
}

# Renvoie le contenu du fichier version de l'env donné, ou vide.
current_version() {
  local f; f="$(version_file "$1")"
  [[ -f "$f" ]] && cat "$f" || true
}

# Retourne 0 si $1 > $2 (versions dotted, avec ou sans 'v' initial).
version_gt() {
  local a="${1#v}" b="${2#v}"
  [[ -z "$a" ]] && return 1
  [[ -z "$b" ]] && return 0
  [[ "$a" = "$b" ]] && return 1
  # Si a n'est pas du semver (1.2.3…), on le considère plus petit que tout.
  # Si b n'est pas du semver (ex: déploiement rolling "main-abc1234"), a gagne.
  local re='^[0-9]+(\.[0-9]+)*$'
  [[ "$a" =~ $re ]] || return 1
  [[ "$b" =~ $re ]] || return 0
  [[ "$(printf '%s\n%s\n' "$a" "$b" | sort -V | tail -n1)" = "$a" ]]
}

write_version_file() {
  local env="$1" version="$2"
  local f; f="$(version_file "$env")"
  printf '%s\n' "$version" > "$f"
  chmod 644 "$f" || true
}

# ────── Commandes conteneurs ──────

cmd_start() {
  local env="$1"
  info "Démarrage $env ($(env_dir "$env"))"
  (cd "$(env_dir "$env")" && docker compose up -d --build)
  docker ps --filter "name=$(container_name "$env")" --format "  {{.Names}}  {{.Status}}  {{.Ports}}"
}

cmd_stop() {
  local env="$1"
  info "Arrêt $env"
  (cd "$(env_dir "$env")" && docker compose down)
}

cmd_restart() {
  local env="$1"
  info "Redémarrage $env (force-recreate)"
  (cd "$(env_dir "$env")" && docker compose up -d --build --force-recreate)
  docker ps --filter "name=$(container_name "$env")" --format "  {{.Names}}  {{.Status}}  {{.Ports}}"
}

cmd_logs() {
  local env="$1"
  info "Logs $env (Ctrl+C pour sortir)"
  (cd "$(env_dir "$env")" && docker compose logs -f --tail=200)
}

cmd_status() {
  info "État des conteneurs sideserver"
  docker ps -a --filter "name=sidestore-website" \
    --format "  {{.Names}}\t{{.Status}}\t{{.Ports}}"
  echo
  info "Versions déployées"
  printf "  prod : %s\n" "$(current_version prod 2>/dev/null || echo '<aucune>')"
  printf "  dev  : %s\n" "$(current_version dev 2>/dev/null || echo '<rolling>')"
}

# ────── Mise à jour ──────
#
# prod : release-based (git checkout du tag de la dernière release).
# dev  : rolling (git pull branche dev + rebuild).

cmd_update_prod() {
  local dir; dir="$(env_dir prod)"
  info "Récupération de la dernière release depuis github.com/${GITHUB_REPO}..."
  local latest current
  latest="$(latest_release_tag || true)"
  if [[ -z "$latest" ]]; then
    err "Impossible de récupérer la dernière release (pas de release publiée, ou API inaccessible)"
    return 1
  fi
  current="$(current_version prod || true)"
  info "Déployé actuellement : ${current:-<aucun>}"
  info "Dernière release     : $latest"

  if [[ -n "$current" ]] && ! version_gt "$latest" "$current"; then
    ok "Prod déjà à jour ($current)"
    return 0
  fi

  info "Checkout $latest + rebuild du conteneur"
  (cd "$dir" \
    && git fetch --tags --prune origin \
    && git checkout --force "$latest" \
    && docker compose up -d --build)
  write_version_file prod "$latest"
  ok "Prod déployée : $latest"
  docker ps --filter "name=$(container_name prod)" --format "  {{.Names}}  {{.Status}}"
}

cmd_update_dev() {
  local dir; dir="$(env_dir dev)"
  info "Pull branche 'dev' dans $dir"
  (cd "$dir" \
    && git fetch origin dev \
    && git reset --hard origin/dev \
    && docker compose up -d --build)
  write_version_file dev "rolling-$(git -C "$dir" rev-parse --short HEAD)"
  ok "Dev mis à jour"
  docker ps --filter "name=$(container_name dev)" --format "  {{.Names}}  {{.Status}}"
}

cmd_update() {
  case "$1" in
    prod) cmd_update_prod ;;
    dev)  cmd_update_dev ;;
  esac
}

# Sortie machine-readable pour l'UI / le scheduler.
# Format : lignes "key=value". Codes de sortie : 0 ok, 1 erreur.
cmd_check_update() {
  local env="$1"
  local current latest available="0"
  current="$(current_version "$env" || true)"

  if [[ "$env" == "dev" ]]; then
    echo "env=dev"
    echo "current=${current:-unknown}"
    echo "latest="
    echo "update_available=0"
    echo "reason=dev-is-rolling"
    return 0
  fi

  latest="$(latest_release_tag || true)"
  echo "env=prod"
  echo "current=${current:-unknown}"
  echo "latest=${latest}"
  if [[ -z "$latest" ]]; then
    echo "update_available=0"
    echo "reason=no-release-or-api-error"
    return 1
  fi
  if [[ -z "$current" ]] || version_gt "$latest" "$current"; then
    available=1
  fi
  echo "update_available=${available}"
}

cmd_self_update() {
  info "Pull du script depuis $TOOLS_DIR (branche main, sparse-checkout)"
  (cd "$TOOLS_DIR" && git fetch origin main && git reset --hard origin/main)
  ok "Script à jour : $(git -C "$TOOLS_DIR" log -1 --format='%h %s')"
}

# ────── Sync TOTALE prod -> dev ──────

cmd_sync() {
  warn "Sync TOTALE $DB_PROD -> $DB_DEV : toutes les modifs dev seront perdues."
  read -r -p "Confirmer ? [y/N] " yn
  [[ "$yn" =~ ^[yYoO]$ ]] || { info "Annulé"; return 1; }

  if [[ ! -f "$MYSQL_DEFAULTS" ]]; then
    err "Fichier $MYSQL_DEFAULTS manquant. Crée-le avec [client] user=root password=..."
    return 1
  fi

  info "Drop + recreate $DB_DEV"
  mysql --defaults-extra-file="$MYSQL_DEFAULTS" <<SQL
DROP DATABASE IF EXISTS \`$DB_DEV\`;
CREATE DATABASE \`$DB_DEV\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL PRIVILEGES ON \`$DB_DEV\`.* TO 'ipastore-dev'@'%';
FLUSH PRIVILEGES;
SQL

  info "Dump + restore de $DB_PROD"
  # --single-transaction : snapshot cohérent InnoDB sans poser de verrou table.
  # --quick : streame les lignes au lieu de les bufferiser en mémoire (gros volumes).
  mysqldump --defaults-extra-file="$MYSQL_DEFAULTS" \
    --routines --triggers --events \
    --single-transaction --quick \
    "$DB_PROD" \
    | mysql --defaults-extra-file="$MYSQL_DEFAULTS" "$DB_DEV"

  info "Mirror $STORE_PROD -> $STORE_DEV (rsync --delete)"
  mkdir -p "$STORE_DEV"/{ipas,icons,screenshots}
  rsync -a --delete "$STORE_PROD/" "$STORE_DEV/"

  info "Restart conteneur dev"
  (cd "$DEV_DIR" && docker compose restart)

  ok "Sync terminée. La BDD et les fichiers dev reflètent la prod."
}

cmd_sync_to_prod() {
  # Opération IRRÉVERSIBLE : toutes les données prod sont écrasées par dev.
  # Double confirmation exigée pour éviter toute manipulation accidentelle.
  printf "${C_RED}╔══════════════════════════════════════════════════════╗${C_RESET}\n"
  printf "${C_RED}║  ATTENTION : SYNC DEV -> PROD                        ║${C_RESET}\n"
  printf "${C_RED}║  Toutes les données PROD seront écrasées par DEV.    ║${C_RESET}\n"
  printf "${C_RED}║  Cette opération est IRRÉVERSIBLE.                   ║${C_RESET}\n"
  printf "${C_RED}╚══════════════════════════════════════════════════════╝${C_RESET}\n"
  read -r -p "Tape 'CONFIRMER' pour continuer : " confirmation
  [[ "$confirmation" == "CONFIRMER" ]] || { info "Annulé"; return 1; }

  if [[ ! -f "$MYSQL_DEFAULTS" ]]; then
    err "Fichier $MYSQL_DEFAULTS manquant."
    return 1
  fi

  info "Arrêt conteneur prod (évite les écritures concurrentes pendant la restauration)"
  (cd "$PROD_DIR" && docker compose stop)

  info "Drop + recreate $DB_PROD"
  mysql --defaults-extra-file="$MYSQL_DEFAULTS" <<SQL
DROP DATABASE IF EXISTS \`$DB_PROD\`;
CREATE DATABASE \`$DB_PROD\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL PRIVILEGES ON \`$DB_PROD\`.* TO 'ipastore-prod'@'%';
FLUSH PRIVILEGES;
SQL

  info "Dump $DB_DEV -> restore $DB_PROD"
  mysqldump --defaults-extra-file="$MYSQL_DEFAULTS" \
    --routines --triggers --events \
    --single-transaction --quick \
    "$DB_DEV" \
    | mysql --defaults-extra-file="$MYSQL_DEFAULTS" "$DB_PROD"

  info "Mirror $STORE_DEV -> $STORE_PROD (rsync --delete)"
  mkdir -p "$STORE_PROD"/{ipas,icons,screenshots}
  rsync -a --delete "$STORE_DEV/" "$STORE_PROD/"

  info "Redémarrage conteneur prod"
  (cd "$PROD_DIR" && docker compose start)

  ok "Sync dev -> prod terminée. La BDD et les fichiers prod reflètent dev."
}

# ────── Reset users ──────

cmd_reset_users() {
  local env="$1"
  local db
  case "$env" in prod) db="$DB_PROD" ;; dev) db="$DB_DEV" ;; *) err "env ?"; return 1 ;; esac
  local container; container="$(container_name "$env")"

  warn "Cette opération SUPPRIME tous les utilisateurs de '$db' ($env)."
  read -r -p "Confirmer ? [y/N] " yn
  [[ "$yn" =~ ^[yYoO]$ ]] || { info "Annulé"; return 1; }

  local new_user new_pass1 new_pass2
  read -r -p "Nouveau login admin : " new_user
  [[ -n "$new_user" ]] || { err "Login vide"; return 1; }
  [[ ${#new_user} -ge 3 ]] || { err "Login trop court (3 min)"; return 1; }

  read -r -s -p "Nouveau mot de passe : " new_pass1; echo
  read -r -s -p "Confirmation         : " new_pass2; echo
  [[ "$new_pass1" == "$new_pass2" ]] || { err "Les mots de passe ne correspondent pas"; return 1; }
  [[ ${#new_pass1} -ge 8 ]] || { err "Mot de passe trop court (8 min)"; return 1; }

  if ! docker ps --filter "name=$container" --format "{{.Names}}" | grep -q "^$container$"; then
    err "Conteneur $container non démarré. Lance-le d'abord ($env-start)."
    return 1
  fi

  info "Hash bcrypt du mot de passe (via conteneur $container)"
  local hash
  hash="$(printf '%s' "$new_pass1" | docker exec -i "$container" python -c \
    'import sys,bcrypt; print(bcrypt.hashpw(sys.stdin.buffer.read(),bcrypt.gensalt(rounds=12)).decode())')"
  [[ -n "$hash" ]] || { err "Échec du hash"; return 1; }

  info "Purge users + insertion du nouvel admin dans '$db'"
  # sed "s/'/''/g" : échappe les apostrophes dans le username pour éviter
  # toute injection SQL (le hash bcrypt ne contient que des chars alphanumériques).
  mysql --defaults-extra-file="$MYSQL_DEFAULTS" "$db" <<SQL
DELETE FROM users;
INSERT INTO users (username, password_hash, created_at)
VALUES ('$(printf '%s' "$new_user" | sed "s/'/''/g")', '$hash', UTC_TIMESTAMP());
SQL

  ok "Admin '$new_user' recréé sur $env. Connecte-toi avec ces identifiants."
}

# ────── Aide ──────

usage() {
  cat <<EOF
$(printf "${C_BOLD}website-management.sh${C_RESET}") — gestion des environnements prod/dev

$(printf "${C_BOLD}USAGE${C_RESET}")
  $(basename "$0")                   # menu interactif
  $(basename "$0") <commande>        # exécution directe

$(printf "${C_BOLD}CONTENEURS${C_RESET}")
  prod-start          Démarre le conteneur prod (port 80)
  prod-stop           Arrête prod
  prod-restart        Rebuild + redémarre prod
  prod-logs           Suit les logs prod
  dev-start           Démarre le conteneur dev (port 8080)
  dev-stop            Arrête dev
  dev-restart         Rebuild + redémarre dev
  dev-logs            Suit les logs dev
  status              État + versions déployées

$(printf "${C_BOLD}MISE À JOUR DU CODE${C_RESET}")
  prod-update         Déploie la dernière RELEASE GitHub (si > version actuelle)
  prod-check          Affiche current / latest / update_available (machine-readable)
  dev-update          git pull 'dev' + rebuild (rolling)
  dev-check           Retourne update_available=0 (dev est rolling)
  self-update         Met à jour ce script depuis /opt/sideserver-tools

$(printf "${C_BOLD}DONNÉES${C_RESET}")
  sync                Sync TOTALE prod -> dev (écrase BDD + fichiers dev)
  sync-to-prod        Sync TOTALE dev -> prod (écrase BDD + fichiers prod — IRRÉVERSIBLE)
  prod-reset-users    Supprime tous les admins prod, en crée un nouveau
  dev-reset-users     Idem sur dev

$(printf "${C_BOLD}AIDE${C_RESET}")
  -h, --help          Cette aide

$(printf "${C_BOLD}EXEMPLES${C_RESET}")
  $(basename "$0") prod-update
  $(basename "$0") prod-check
  $(basename "$0") sync
EOF
}

# ────── Menu interactif ──────

pause_menu() {
  printf "\n${C_DIM}Appuie sur Entrée pour revenir au menu...${C_RESET}"
  read -r _
}

menu() {
  while true; do
    clear
    printf "${C_BOLD}╔═══════════════════════════════════════════════╗${C_RESET}\n"
    printf "${C_BOLD}║  SideServer Website — Gestion prod / dev      ║${C_RESET}\n"
    printf "${C_BOLD}╚═══════════════════════════════════════════════╝${C_RESET}\n\n"
    printf "  ${C_DIM}Conteneurs :${C_RESET}\n"
    docker ps -a --filter "name=sidestore-website" \
      --format "    ${C_CYAN}{{.Names}}${C_RESET}  {{.Status}}  ${C_DIM}{{.Ports}}${C_RESET}" \
      2>/dev/null || printf "    ${C_YELLOW}(docker indisponible)${C_RESET}\n"
    printf "  ${C_DIM}Versions :${C_RESET}\n"
    printf "    prod : ${C_GREEN}%s${C_RESET}\n" "$(current_version prod 2>/dev/null || echo '<aucune>')"
    printf "    dev  : ${C_GREEN}%s${C_RESET}\n" "$(current_version dev  2>/dev/null || echo '<rolling>')"
    printf "\n"
    printf "  ${C_BOLD}PROD${C_RESET} (port 80, release-based)\n"
    printf "     1) Start                  2) Stop\n"
    printf "     3) Restart                4) Logs\n"
    printf "     5) Update (dernière release)\n"
    printf "     6) Reset utilisateurs\n"
    printf "\n"
    printf "  ${C_BOLD}DEV${C_RESET} (port 8080, rolling branche dev)\n"
    printf "    11) Start                 12) Stop\n"
    printf "    13) Restart               14) Logs\n"
    printf "    15) Update (git pull + rebuild)\n"
    printf "    16) Reset utilisateurs\n"
    printf "\n"
    printf "  ${C_BOLD}DONNÉES${C_RESET}\n"
    printf "    20) Sync TOTALE prod -> dev (écrase dev)\n"
    printf "    25) Sync TOTALE dev -> prod (écrase prod — IRRÉVERSIBLE)\n"
    printf "    21) Self-update (pull ce script)\n"
    printf "    22) Check update prod     23) Check update dev\n"
    printf "\n"
    printf "     s) Status                 h) Aide CLI\n"
    printf "     q) Quitter\n\n"
    read -r -p "  Choix : " choice
    case "$choice" in
       1) cmd_start prod ;;
       2) cmd_stop prod ;;
       3) cmd_restart prod ;;
       4) cmd_logs prod ;;
       5) cmd_update_prod ;;
       6) cmd_reset_users prod ;;
      11) cmd_start dev ;;
      12) cmd_stop dev ;;
      13) cmd_restart dev ;;
      14) cmd_logs dev ;;
      15) cmd_update_dev ;;
      16) cmd_reset_users dev ;;
      20) cmd_sync ;;
      25) cmd_sync_to_prod ;;
      21) cmd_self_update ;;
      22) cmd_check_update prod ;;
      23) cmd_check_update dev ;;
      s|S) cmd_status ;;
      h|H) usage ;;
      q|Q|exit|quit) exit 0 ;;
      *) warn "Choix invalide : $choice" ;;
    esac
    pause_menu
  done
}

# ────── Dispatch ──────

case "${1:-}" in
  "")                  menu ;;
  -h|--help|help)      usage ;;
  prod-start)          cmd_start prod ;;
  prod-stop)           cmd_stop prod ;;
  prod-restart)        cmd_restart prod ;;
  prod-logs)           cmd_logs prod ;;
  prod-update)         cmd_update_prod ;;
  prod-check)          cmd_check_update prod ;;
  prod-reset-users)    cmd_reset_users prod ;;
  dev-start)           cmd_start dev ;;
  dev-stop)            cmd_stop dev ;;
  dev-restart)         cmd_restart dev ;;
  dev-logs)            cmd_logs dev ;;
  dev-update)          cmd_update_dev ;;
  dev-check)           cmd_check_update dev ;;
  dev-reset-users)     cmd_reset_users dev ;;
  self-update)         cmd_self_update ;;
  status)              cmd_status ;;
  sync)                cmd_sync ;;
  sync-to-prod)        cmd_sync_to_prod ;;
  *) err "Commande inconnue : $1"; echo; usage; exit 1 ;;
esac
