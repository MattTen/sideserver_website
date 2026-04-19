#!/usr/bin/env bash
# website-management.sh — Gestion des environnements prod/dev de sideserver_website.
#
# Sans argument : menu interactif.
# Avec argument : exécution d'une commande unique (voir --help).

set -euo pipefail

# Résolution des chemins (overridables par variables d'env)
PROD_DIR="${SIDESERVER_PROD_DIR:-/opt/sideserver-prod}"
DEV_DIR="${SIDESERVER_DEV_DIR:-/opt/sideserver-dev}"
DB_PROD="ipastore-prod"
DB_DEV="ipastore-dev"
STORE_PROD="/srv/store-prod"
STORE_DEV="/srv/store-dev"
MYSQL_DEFAULTS="/etc/ipastore/.mysql.cnf"

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
}

# ────── Mise à jour depuis GitHub ──────

cmd_update() {
  local env="$1"
  local dir="$(env_dir "$env")"
  local branch="$(env_branch "$env")"
  info "Pull branche '$branch' dans $dir"
  (cd "$dir" \
    && git fetch origin "$branch" \
    && git reset --hard "origin/$branch" \
    && docker compose up -d --build)
  ok "Mise à jour $env terminée"
  docker ps --filter "name=$(container_name "$env")" --format "  {{.Names}}  {{.Status}}"
}

# ────── Sync TOTALE prod -> dev (écrase dev) ──────

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

# ────── Reset des utilisateurs ──────

cmd_reset_users() {
  local env="$1"
  local db
  case "$env" in prod) db="$DB_PROD" ;; dev) db="$DB_DEV" ;; *) err "env ?"; return 1 ;; esac
  local container="$(container_name "$env")"

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
  status              État des deux conteneurs

$(printf "${C_BOLD}MISE À JOUR DU CODE (après push GitHub)${C_RESET}")
  prod-update         git pull 'main' + rebuild + redémarre prod
  dev-update          git pull 'dev'  + rebuild + redémarre dev

$(printf "${C_BOLD}DONNÉES${C_RESET}")
  sync                Sync TOTALE prod -> dev (écrase BDD + fichiers dev)
  prod-reset-users    Supprime tous les admins prod, en crée un nouveau
  dev-reset-users     Idem sur dev

$(printf "${C_BOLD}AIDE${C_RESET}")
  -h, --help          Cette aide

$(printf "${C_BOLD}EXEMPLES${C_RESET}")
  $(basename "$0") prod-update
  $(basename "$0") sync
  $(basename "$0") dev-reset-users
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
    printf "  ${C_DIM}État :${C_RESET}\n"
    docker ps -a --filter "name=sidestore-website" \
      --format "    ${C_CYAN}{{.Names}}${C_RESET}  {{.Status}}  ${C_DIM}{{.Ports}}${C_RESET}" \
      2>/dev/null || printf "    ${C_YELLOW}(docker indisponible)${C_RESET}\n"
    printf "\n"
    printf "  ${C_BOLD}PROD${C_RESET} (port 80, branche main)\n"
    printf "     1) Start                  2) Stop\n"
    printf "     3) Restart                4) Logs\n"
    printf "     5) Update (git pull + rebuild)\n"
    printf "     6) Reset utilisateurs\n"
    printf "\n"
    printf "  ${C_BOLD}DEV${C_RESET} (port 8080, branche dev)\n"
    printf "    11) Start                 12) Stop\n"
    printf "    13) Restart               14) Logs\n"
    printf "    15) Update (git pull + rebuild)\n"
    printf "    16) Reset utilisateurs\n"
    printf "\n"
    printf "  ${C_BOLD}DONNÉES${C_RESET}\n"
    printf "    20) Sync TOTALE prod -> dev (écrase dev)\n"
    printf "\n"
    printf "     s) Status                 h) Aide CLI\n"
    printf "     q) Quitter\n\n"
    read -r -p "  Choix : " choice
    case "$choice" in
       1) cmd_start prod ;;
       2) cmd_stop prod ;;
       3) cmd_restart prod ;;
       4) cmd_logs prod ;;
       5) cmd_update prod ;;
       6) cmd_reset_users prod ;;
      11) cmd_start dev ;;
      12) cmd_stop dev ;;
      13) cmd_restart dev ;;
      14) cmd_logs dev ;;
      15) cmd_update dev ;;
      16) cmd_reset_users dev ;;
      20) cmd_sync ;;
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
  prod-update)         cmd_update prod ;;
  prod-reset-users)    cmd_reset_users prod ;;
  dev-start)           cmd_start dev ;;
  dev-stop)            cmd_stop dev ;;
  dev-restart)         cmd_restart dev ;;
  dev-logs)            cmd_logs dev ;;
  dev-update)          cmd_update dev ;;
  dev-reset-users)     cmd_reset_users dev ;;
  status)              cmd_status ;;
  sync)                cmd_sync ;;
  *) err "Commande inconnue : $1"; echo; usage; exit 1 ;;
esac
