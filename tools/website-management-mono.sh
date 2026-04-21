#!/usr/bin/env bash
# website-management-mono.sh — Gestion mono-environnement de sideserver_website.
#
# Conçu pour un déploiement 1 machine = 1 conteneur :
#   - dev local : machine à la maison, branche 'dev' rolling
#   - prod cloud : VM cloud, release-based (tag GitHub)
#
# Les deux tournent avec le même nom de conteneur `sidestore-website` pour
# que les environnements soient strictement identiques (chemins, volumes,
# systemd units, noms docker). Le seul point de divergence est la commande
# de mise à jour utilisée (update vs update-dev).
#
# Différences vs website-management.sh historique :
#   - Un seul conteneur, un seul STORE_DIR, un seul /opt/sideserver-website
#   - Les sync prod↔dev disparaissent (les deux machines sont distinctes,
#     le cycle de promotion passe par merge dev→main + release GitHub)
#   - Nouvelle commande `db-migrate` : applique les ajouts de schéma (tables,
#     colonnes, index) à la BDD courante, s'appuyant sur app/schema_migrate.py
#     du conteneur. Utile après une mise à jour qui introduit de nouveaux
#     modèles sans passer par un rebuild (init_db() le fait déjà au boot —
#     cette commande sert de trigger manuel / de vérif `--dry-run`).
#   - reset-users : ne lit plus /etc/ipastore/.mysql.cnf, les identifiants
#     BDD proviennent du conteneur (db.json) via `docker exec`.
#
# Sans argument : menu interactif.
# Avec argument : exécution d'une commande unique (voir --help).

set -euo pipefail

# ────── Config ──────
APP_DIR="${SIDESERVER_APP_DIR:-/opt/sideserver-website}"
TOOLS_DIR="${SIDESERVER_TOOLS_DIR:-/opt/sideserver-tools}"
GITHUB_REPO="${SIDESERVER_REPO:-MattTen/sideserver_website}"
STORE_DIR="${SIDESERVER_STORE_DIR:-/srv/store}"
GIT_CREDENTIALS_FILE="/etc/ipastore/.git-credentials"
VERSION_FILE="/etc/ipastore/version"
CONTAINER_NAME="sidestore-website"

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

# ────── Helpers releases ──────

# Extrait le token GitHub de /etc/ipastore/.git-credentials (format:
# https://user:TOKEN@github.com). Renvoie vide si absent/illisible.
github_token() {
  [[ -r "$GIT_CREDENTIALS_FILE" ]] || return 0
  sed -nE 's|^https://[^:]+:([^@]+)@github\.com.*|\1|p' "$GIT_CREDENTIALS_FILE" | head -n1
}

# Wrapper git qui injecte le credential.helper pointant vers notre fichier.
# Utilisé pour tous les appels git réseau (fetch, checkout de tag…) afin
# d'éviter le prompt interactif sur les repos privés.
git_auth() {
  git -c "credential.helper=store --file $GIT_CREDENTIALS_FILE" "$@"
}

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

current_version() {
  [[ -f "$VERSION_FILE" ]] && cat "$VERSION_FILE" || true
}

# Retourne 0 si $1 > $2 (versions dotted, avec ou sans 'v' initial).
version_gt() {
  local a="${1#v}" b="${2#v}"
  [[ -z "$a" ]] && return 1
  [[ -z "$b" ]] && return 0
  [[ "$a" = "$b" ]] && return 1
  local re='^[0-9]+(\.[0-9]+)*$'
  [[ "$a" =~ $re ]] || return 1
  [[ "$b" =~ $re ]] || return 0
  [[ "$(printf '%s\n%s\n' "$a" "$b" | sort -V | tail -n1)" = "$a" ]]
}

write_version_file() {
  local version="$1"
  printf '%s\n' "$version" > "$VERSION_FILE"
  chmod 644 "$VERSION_FILE" || true
}

container_running() {
  docker ps --filter "name=^${CONTAINER_NAME}\$" --format "{{.Names}}" \
    | grep -q "^${CONTAINER_NAME}\$"
}

require_container() {
  if ! container_running; then
    err "Conteneur $CONTAINER_NAME non démarré. Lance-le d'abord (start)."
    return 1
  fi
}

# ────── Commandes conteneur ──────

cmd_start() {
  info "Démarrage ($APP_DIR)"
  (cd "$APP_DIR" && docker compose up -d --build)
  docker ps --filter "name=$CONTAINER_NAME" --format "  {{.Names}}  {{.Status}}  {{.Ports}}"
}

cmd_stop() {
  info "Arrêt"
  (cd "$APP_DIR" && docker compose down)
}

cmd_restart() {
  info "Redémarrage (force-recreate)"
  (cd "$APP_DIR" && docker compose up -d --build --force-recreate)
  docker ps --filter "name=$CONTAINER_NAME" --format "  {{.Names}}  {{.Status}}  {{.Ports}}"
}

cmd_logs() {
  info "Logs (Ctrl+C pour sortir)"
  (cd "$APP_DIR" && docker compose logs -f --tail=200)
}

cmd_status() {
  info "État du conteneur"
  docker ps -a --filter "name=$CONTAINER_NAME" \
    --format "  {{.Names}}\t{{.Status}}\t{{.Ports}}"
  echo
  info "Version déployée"
  printf "  %s\n" "$(current_version || echo '<aucune>')"
}

# ────── Mise à jour du code ──────
#
# update       : release-based — checkout du tag de la dernière release GitHub
# update-dev   : rolling — pull HEAD de la branche 'dev'
# pull         : bypass d'urgence — pull direct de 'main'
#
# À chaque rebuild, init_db() s'exécute au démarrage et applique les
# migrations additives via app/schema_migrate.py. Pas besoin d'appeler
# db-migrate manuellement après une update normale.

cmd_update() {
  info "Récupération de la dernière release depuis github.com/${GITHUB_REPO}..."
  local latest current
  latest="$(latest_release_tag || true)"
  if [[ -z "$latest" ]]; then
    err "Impossible de récupérer la dernière release (pas de release publiée, ou API inaccessible)"
    return 1
  fi
  current="$(current_version || true)"
  info "Déployé actuellement : ${current:-<aucun>}"
  info "Dernière release     : $latest"

  if [[ -n "$current" ]] && ! version_gt "$latest" "$current"; then
    ok "Déjà à jour ($current)"
    return 0
  fi

  info "Checkout $latest + rebuild du conteneur"
  (cd "$APP_DIR" \
    && git_auth fetch --tags --prune origin \
    && git checkout --force "$latest" \
    && docker compose up -d --build)
  write_version_file "$latest"
  ok "Déployé : $latest"
  docker ps --filter "name=$CONTAINER_NAME" --format "  {{.Names}}  {{.Status}}"
}

cmd_update_dev() {
  info "Pull branche 'dev' dans $APP_DIR"
  (cd "$APP_DIR" \
    && git_auth fetch origin dev \
    && git reset --hard origin/dev \
    && docker compose up -d --build)
  write_version_file "rolling-dev-$(git -C "$APP_DIR" rev-parse --short HEAD)"
  ok "Mis à jour (rolling dev)"
  docker ps --filter "name=$CONTAINER_NAME" --format "  {{.Names}}  {{.Status}}"
}

# Bypass d'urgence du workflow release-based : pull direct de main + rebuild.
# A n'utiliser que pour un hotfix critique en attendant qu'une vraie release
# soit taggée.
cmd_pull() {
  warn "PULL D'URGENCE : pull direct de 'main' sans passer par une release."
  warn "À n'utiliser que pour un hotfix ; tag une release dès que possible."
  info "Pull branche 'main' dans $APP_DIR"
  (cd "$APP_DIR" \
    && git_auth fetch origin main \
    && git reset --hard origin/main \
    && docker compose up -d --build)
  write_version_file "rolling-main-$(git -C "$APP_DIR" rev-parse --short HEAD)"
  ok "Mis à jour (rolling main)"
  docker ps --filter "name=$CONTAINER_NAME" --format "  {{.Names}}  {{.Status}}"
}

# Sortie machine-readable pour l'UI / le scheduler.
cmd_check_update() {
  local current latest available="0"
  current="$(current_version || true)"
  latest="$(latest_release_tag || true)"
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
  (cd "$TOOLS_DIR" && git_auth fetch origin main && git reset --hard origin/main)
  ok "Script à jour : $(git -C "$TOOLS_DIR" log -1 --format='%h %s')"
}

# ────── Migration de schéma BDD ──────
#
# S'appuie sur app/schema_migrate.py du conteneur pour appliquer les DDL
# additifs (CREATE TABLE, ADD COLUMN, CREATE INDEX) reflétant les modèles
# SQLAlchemy courants. Strictement additif : aucun DROP, aucun MODIFY.
#
# Usage typique : après avoir tiré une version dev qui introduit de nouvelles
# tables ou colonnes, pour forcer la migration sans rebuild complet (le rebuild
# la fait déjà au boot via init_db, mais l'opération manuelle est utile pour
# inspection / dry-run / environnement où le redémarrage est coûteux).

cmd_db_migrate() {
  require_container || return 1
  info "Dry-run : liste des opérations que la migration appliquerait"
  docker exec -i "$CONTAINER_NAME" python -m app.schema_migrate --dry-run || {
    err "Dry-run échoué — la BDD est-elle configurée ?"
    return 1
  }
  echo
  read -r -p "Appliquer ces opérations ? [y/N] " yn
  [[ "$yn" =~ ^[yYoO]$ ]] || { info "Annulé"; return 1; }
  docker exec -i "$CONTAINER_NAME" python -m app.schema_migrate
  ok "Migration terminée."
}

cmd_db_migrate_check() {
  # Version non-interactive : dry-run pur, sortie brute.
  require_container || return 1
  docker exec -i "$CONTAINER_NAME" python -m app.schema_migrate --dry-run
}

# ────── Reset users ──────
#
# Supprime tous les utilisateurs et crée un nouvel admin. Utilise bcrypt
# + SQLAlchemy du conteneur (qui lit lui-même db.json), donc pas besoin
# de credentials MySQL côté hôte.

cmd_reset_users() {
  require_container || return 1

  warn "Cette opération SUPPRIME tous les utilisateurs de la BDD courante."
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

  info "Purge users + insertion du nouvel admin"
  # Le username et le mot de passe sont passés via stdin (JSON) pour éviter
  # toute expansion shell / problème de quoting avec des caractères spéciaux.
  printf '%s\n' "$(python3 -c '
import json, sys
print(json.dumps({"u": sys.argv[1], "p": sys.argv[2]}))
' "$new_user" "$new_pass1")" | docker exec -i "$CONTAINER_NAME" python - <<'PY'
import json, sys
import bcrypt
from app.db import SessionLocal
from app.models import User

data = json.loads(sys.stdin.read())
username = data["u"]
password = data["p"].encode()

hashed = bcrypt.hashpw(password, bcrypt.gensalt(rounds=12)).decode()

db = SessionLocal()
try:
    db.query(User).delete()
    db.add(User(username=username, password_hash=hashed))
    db.commit()
    print(f"OK: admin '{username}' créé.")
finally:
    db.close()
PY

  ok "Admin '$new_user' recréé. Connecte-toi avec ces identifiants."
}

# ────── SCInsta builder ──────

SCINSTA_BUILDER_IMAGE="scinsta-builder:latest"

cmd_scinsta_build() {
  local dir="$APP_DIR/tools/scinsta-builder"
  [[ -d "$dir" ]] || { err "Builder introuvable : $dir (lance update ?)"; exit 1; }

  # Tee toute la sortie (docker build + docker run + messages info) vers le
  # fichier log poll par l'UI. Sans ça, l'utilisateur ne voit rien tant que
  # build.py ne démarre pas.
  # Note : on conserve le suffixe -<env> dans le log file pour compatibilité
  # avec l'UI. L'env est lu depuis IPASTORE_ENV côté conteneur.
  local env="${IPASTORE_ENV:-prod}"
  local log_file="/etc/ipastore/scinsta-build-log-${env}.txt"
  : > "$log_file"
  exec > >(tee -a "$log_file") 2>&1

  info "Build image Docker $SCINSTA_BUILDER_IMAGE (idempotent, cache actif)"
  docker build --progress=plain -t "$SCINSTA_BUILDER_IMAGE" "$dir"

  local cname="scinsta-builder"
  info "Run scinsta-builder (container=$cname)"
  # --name déterministe : permet à cmd_scinsta_cancel de le killer
  # proprement (docker stop <name>) si l'admin veut stopper.
  docker run --rm --name "$cname" \
    -e IPASTORE_ENV="$env" \
    -v /etc/ipastore:/etc/ipastore \
    -v "${STORE_DIR}:/srv/store" \
    -v "${APP_DIR}:/opt/sideserver-website:ro" \
    --network host \
    "$SCINSTA_BUILDER_IMAGE"
  ok "Build terminé"
}

cmd_scinsta_cancel() {
  # Stoppe un build en cours : kill le conteneur + écrit un result failed
  # pour que le watcher web bascule le state en "failed" et débloque l'UI.
  local env="${IPASTORE_ENV:-prod}"
  local cname="scinsta-builder"
  local flag="/etc/ipastore/scinsta-build-cancel-${env}"
  local req_flag="/etc/ipastore/scinsta-build-requested-${env}"
  local progress="/etc/ipastore/scinsta-build-progress-${env}"
  local result="/etc/ipastore/scinsta-build-result-${env}"

  info "Cancel demandé"
  # On supprime le flag de demande AVANT de kill — évite que le path unit
  # retrigger un nouveau build juste après qu'on ait tué le conteneur.
  rm -f "$flag" "$req_flag"

  if docker ps --filter "name=^${cname}\$" --format "{{.Names}}" | grep -q "^${cname}\$"; then
    info "Stop du conteneur $cname (SIGTERM -t2 puis SIGKILL, bloquant)"
    # docker stop : SIGTERM, attend 2s, puis SIGKILL. build.py est PID 1 dans
    # le conteneur ; sans handler explicite, le kernel ignore SIGTERM pour
    # PID 1 — seul SIGKILL termine le process.
    docker stop -t 2 "$cname" || true
  else
    warn "Conteneur $cname non trouvé (déjà terminé ?)"
  fi

  local now
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  cat > "$result" <<JSON
{"status":"failed","finished_at":"$now","error":"Build annulé"}
JSON
  rm -f "$progress"
  ok "Build marqué comme annulé."
}

# ────── Aide ──────

usage() {
  cat <<EOF
$(printf "${C_BOLD}website-management-mono.sh${C_RESET}") — gestion mono-env de sideserver_website

$(printf "${C_BOLD}USAGE${C_RESET}")
  $(basename "$0")                   # menu interactif
  $(basename "$0") <commande>        # exécution directe

$(printf "${C_BOLD}CONTENEUR${C_RESET}")
  start               Démarre le conteneur
  stop                Arrête le conteneur
  restart             Rebuild + redémarre le conteneur
  logs                Suit les logs
  status              État + version déployée

$(printf "${C_BOLD}MISE À JOUR DU CODE${C_RESET}")
  update              Déploie la dernière RELEASE GitHub (mode prod)
  update-dev          git pull 'dev' + rebuild (mode dev rolling)
  pull                URGENCE : pull direct de 'main' + rebuild (bypass release)
  check               Affiche current / latest / update_available
  self-update         Met à jour ce script depuis /opt/sideserver-tools

$(printf "${C_BOLD}BASE DE DONNÉES${C_RESET}")
  db-migrate          Applique les migrations additives de schéma (interactif)
  db-migrate-check    Dry-run : liste les opérations sans les appliquer

$(printf "${C_BOLD}ADMIN${C_RESET}")
  reset-users         Supprime tous les admins, en crée un nouveau

$(printf "${C_BOLD}SCINSTA BUILDER${C_RESET}")
  scinsta-build       Lance le pipeline SCInsta + Instagram (IPA uploadée requise)
  scinsta-cancel      Stoppe un build SCInsta en cours (docker stop + result failed)

$(printf "${C_BOLD}AIDE${C_RESET}")
  -h, --help          Cette aide

$(printf "${C_BOLD}EXEMPLES${C_RESET}")
  $(basename "$0") update
  $(basename "$0") check
  $(basename "$0") db-migrate-check
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
    printf "${C_BOLD}║  SideServer Website — Gestion                 ║${C_RESET}\n"
    printf "${C_BOLD}╚═══════════════════════════════════════════════╝${C_RESET}\n\n"
    printf "  ${C_DIM}Conteneur :${C_RESET}\n"
    docker ps -a --filter "name=$CONTAINER_NAME" \
      --format "    ${C_CYAN}{{.Names}}${C_RESET}  {{.Status}}  ${C_DIM}{{.Ports}}${C_RESET}" \
      2>/dev/null || printf "    ${C_YELLOW}(docker indisponible)${C_RESET}\n"
    printf "  ${C_DIM}Version :${C_RESET}\n"
    printf "    ${C_GREEN}%s${C_RESET}\n" "$(current_version 2>/dev/null || echo '<aucune>')"
    printf "\n"
    printf "  ${C_BOLD}CONTENEUR${C_RESET}\n"
    printf "     1) Start                  2) Stop\n"
    printf "     3) Restart                4) Logs\n"
    printf "\n"
    printf "  ${C_BOLD}MISE À JOUR DU CODE${C_RESET}\n"
    printf "     5) Update (dernière release — prod)\n"
    printf "     6) Update dev (rolling branche dev)\n"
    printf "     7) Pull d'urgence (main direct, bypass release)\n"
    printf "     8) Check update\n"
    printf "\n"
    printf "  ${C_BOLD}BASE DE DONNÉES${C_RESET}\n"
    printf "    10) Mettre à jour la BDD (schéma : tables/colonnes/index manquants)\n"
    printf "    11) Dry-run migration (liste les opérations sans appliquer)\n"
    printf "\n"
    printf "  ${C_BOLD}ADMIN${C_RESET}\n"
    printf "    15) Reset utilisateurs\n"
    printf "\n"
    printf "  ${C_BOLD}SCINSTA${C_RESET}\n"
    printf "    20) Build IPA Instagram + SCInsta\n"
    printf "    21) Annuler build en cours\n"
    printf "\n"
    printf "  ${C_BOLD}OUTILS${C_RESET}\n"
    printf "    30) Self-update (pull ce script)\n"
    printf "\n"
    printf "     s) Status                 h) Aide CLI\n"
    printf "     q) Quitter\n\n"
    read -r -p "  Choix : " choice
    case "$choice" in
       1) cmd_start ;;
       2) cmd_stop ;;
       3) cmd_restart ;;
       4) cmd_logs ;;
       5) cmd_update ;;
       6) cmd_update_dev ;;
       7) cmd_pull ;;
       8) cmd_check_update ;;
      10) cmd_db_migrate ;;
      11) cmd_db_migrate_check ;;
      15) cmd_reset_users ;;
      20) cmd_scinsta_build ;;
      21) cmd_scinsta_cancel ;;
      30) cmd_self_update ;;
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
  start)               cmd_start ;;
  stop)                cmd_stop ;;
  restart)             cmd_restart ;;
  logs)                cmd_logs ;;
  status)              cmd_status ;;
  update)              cmd_update ;;
  update-dev)          cmd_update_dev ;;
  pull)                cmd_pull ;;
  check)               cmd_check_update ;;
  self-update)         cmd_self_update ;;
  db-migrate)          cmd_db_migrate ;;
  db-migrate-check)    cmd_db_migrate_check ;;
  reset-users)         cmd_reset_users ;;
  scinsta-build)       cmd_scinsta_build ;;
  scinsta-cancel)      cmd_scinsta_cancel ;;
  *) err "Commande inconnue : $1"; echo; usage; exit 1 ;;
esac
