#!/usr/bin/env bash
# dev.sh : gestion de l'environnement de développement (conteneur dev + sync prod->dev).
# À exécuter depuis /opt/sideserver-dev/ sur la VM.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

DB_PROD="ipastore-prod"
DB_DEV="ipastore-dev"
STORE_PROD="/srv/store-prod"
STORE_DEV="/srv/store-dev"
SYNC_STATE_DIR="/var/lib/ipastore-sync"
SYNC_STATE_FILE="$SYNC_STATE_DIR/last_sync"
MYSQL_DEFAULTS="/etc/ipastore/.mysql.cnf"   # fichier ~/.my.cnf-like (user/password root local)

color() { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
info()  { color '1;36' "[dev.sh] $*"; }
warn()  { color '1;33' "[dev.sh] $*" >&2; }
err()   { color '1;31' "[dev.sh] $*" >&2; }

cmd_start() {
  info "Démarrage du conteneur dev..."
  docker compose up -d --build
  docker compose ps
}

cmd_stop() {
  info "Arrêt du conteneur dev..."
  docker compose down
}

cmd_restart() {
  info "Redémarrage du conteneur dev..."
  docker compose up -d --build --force-recreate
  docker compose ps
}

cmd_logs() {
  docker compose logs -f --tail=200
}

cmd_status() {
  info "État des conteneurs ipastore sur l'hôte :"
  docker ps --filter "name=sidestore-website" \
    --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
}

cmd_sync() {
  mkdir -p "$SYNC_STATE_DIR"
  local since="1970-01-01 00:00:00"
  if [[ -f "$SYNC_STATE_FILE" ]]; then
    since="$(cat "$SYNC_STATE_FILE")"
  fi
  local now
  now="$(date -u +'%Y-%m-%d %H:%M:%S')"

  info "Sync incrémentale $DB_PROD -> $DB_DEV depuis $since (UTC)"

  if [[ ! -f "$MYSQL_DEFAULTS" ]]; then
    err "Fichier $MYSQL_DEFAULTS manquant. Crée-le avec [client] user=root password=..."
    exit 1
  fi

  # Tables petites : dump complet et remplacement
  info "  - users / settings : dump complet"
  mysqldump --defaults-extra-file="$MYSQL_DEFAULTS" \
    --no-create-info --replace --skip-triggers --skip-comments \
    "$DB_PROD" users settings \
    | mysql --defaults-extra-file="$MYSQL_DEFAULTS" "$DB_DEV"

  # Apps : lignes modifiées depuis last_sync
  info "  - apps : lignes updated_at > $since"
  mysqldump --defaults-extra-file="$MYSQL_DEFAULTS" \
    --no-create-info --replace --skip-triggers --skip-comments \
    --where="updated_at > '$since'" \
    "$DB_PROD" apps \
    | mysql --defaults-extra-file="$MYSQL_DEFAULTS" "$DB_DEV"

  # Versions : lignes uploaded_at > since
  info "  - versions : lignes uploaded_at > $since"
  mysqldump --defaults-extra-file="$MYSQL_DEFAULTS" \
    --no-create-info --replace --skip-triggers --skip-comments \
    --where="uploaded_at > '$since'" \
    "$DB_PROD" versions \
    | mysql --defaults-extra-file="$MYSQL_DEFAULTS" "$DB_DEV"

  # Fichiers : rsync incrémental (mtime + taille)
  info "  - fichiers $STORE_PROD -> $STORE_DEV (rsync --update)"
  mkdir -p "$STORE_DEV"/{ipas,icons,screenshots}
  rsync -a --update "$STORE_PROD/" "$STORE_DEV/"

  echo "$now" > "$SYNC_STATE_FILE"
  info "Sync terminée. Nouveau repère : $now (UTC)"
}

cmd_reset_sync() {
  warn "Reset du marqueur de sync (prochaine sync = full)"
  rm -f "$SYNC_STATE_FILE"
}

usage() {
  cat <<EOF
Usage: $(basename "$0") <commande>

Commandes :
  start       Démarre le conteneur dev (port 8080)
  stop        Arrête le conteneur dev
  restart     Rebuild + redémarre
  logs        Suit les logs
  status      Affiche l'état des conteneurs
  sync        Sync incrémentale $DB_PROD -> $DB_DEV + fichiers
  reset-sync  Efface le marqueur de sync (prochaine sync = tout)

Exemples :
  ./dev.sh start
  ./dev.sh sync
  ./dev.sh logs
EOF
}

case "${1:-}" in
  start)       cmd_start ;;
  stop)        cmd_stop ;;
  restart)     cmd_restart ;;
  logs)        cmd_logs ;;
  status)      cmd_status ;;
  sync)        cmd_sync ;;
  reset-sync)  cmd_reset_sync ;;
  -h|--help|help|"") usage ;;
  *) err "Commande inconnue : $1"; usage; exit 1 ;;
esac
