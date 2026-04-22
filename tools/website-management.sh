#!/usr/bin/env bash
# website-management.sh — Gestion mono-env de sideserver_website.
#
# Modele mono-env : une VM = un seul environnement (prod OU dev), distingue
# uniquement par la branche checkoutee dans /opt/sideserver-prod :
#   main / HEAD detache (tag release) -> prod (release-based)
#   dev                                -> dev  (rolling sur HEAD de la branche)
# Les chemins, nom de conteneur, fichier .env, units systemd restent
# identiques dans les deux cas. Un SEUL bootstrap existe (deploy/bootstrap.sh)
# qui deploie toujours la derniere release ; la bascule dev/prod apres coup
# se fait via `switch-dev` / `switch-prod` qui changent juste le ref checkout.
#
# Sans argument : menu interactif.
# Avec argument : execution d'une commande unique (voir --help).
#
# Les aliases `prod-update`, `prod-scinsta-build`, `prod-scinsta-cancel`
# sont conserves pour la compatibilite avec les units systemd generees
# par le bootstrap (qui utilisent l'instance %i=prod).

set -euo pipefail

# ────── Config ──────
APP_DIR="${SIDESERVER_APP_DIR:-/opt/sideserver-prod}"
GITHUB_REPO="${SIDESERVER_REPO:-MattTen/sideserver_website}"
STORE_DIR="/srv/store-prod"
CONTAINER_NAME="sidestore-website-prod"
VERSION_FILE="/etc/ipastore/prod.version"
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

# safe.directory : le clone est chowne APP_USER par le bootstrap mais les
# commandes systemd tournent aussi en APP_USER ; par contre si l'admin lance
# le script en sudo apres un bootstrap recent, on veut que git accepte.
GIT_SAFE=(-c "safe.directory=${APP_DIR}")

# ────── Helpers git ──────

# Wrapper git qui injecte le credential.helper si un token est configure
# (sinon pas d'auth, le repo est suppose public).
git_auth() {
  if [[ -r "$GIT_CREDENTIALS_FILE" ]]; then
    git "${GIT_SAFE[@]}" -c "credential.helper=store --file $GIT_CREDENTIALS_FILE" "$@"
  else
    git "${GIT_SAFE[@]}" "$@"
  fi
}

# Extrait le token GitHub de /etc/ipastore/.git-credentials si present.
github_token() {
  [[ -r "$GIT_CREDENTIALS_FILE" ]] || return 0
  sed -nE 's|^https://[^:]+:([^@]+)@github\.com.*|\1|p' "$GIT_CREDENTIALS_FILE" | head -n1
}

# Branche actuellement checkoutee dans APP_DIR (main ou dev).
current_branch() {
  git "${GIT_SAFE[@]}" -C "$APP_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown
}

# Env logique deduit de la branche courante du clone.
#   main             -> prod (cas rare : fallback sans release)
#   HEAD (detache)   -> prod (cas normal : checkout sur un tag de release)
#   dev              -> dev  (rolling)
env_from_branch() {
  case "$(current_branch)" in
    main|HEAD) echo prod ;;
    *)         echo dev ;;
  esac
}

# Renvoie le tag_name de la derniere release (ex: v1.2.3), ou vide si rien.
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
  printf '%s\n' "$1" > "$VERSION_FILE"
  chmod 644 "$VERSION_FILE" || true
}

# Met a jour IMAGE_TAG dans le .env de docker-compose pour que l'image
# resultante soit taggee avec la version deployee (ex ipastore:v0.2.0.1
# ou ipastore:rolling-dev-234eff6) plutot qu'un tag generique. A appeler
# AVANT `docker compose up -d --build`. Sanitization minimale : Docker
# accepte [a-zA-Z0-9_.-] dans les tags, donc les versions semver et nos
# `rolling-<branche>-<sha>` passent telles quelles.
set_image_tag() {
  local tag="$1"
  local env_file="$APP_DIR/.env"
  [[ -f "$env_file" ]] || { warn "$env_file absent, IMAGE_TAG non mis a jour"; return 0; }
  if grep -q '^IMAGE_TAG=' "$env_file"; then
    sed -i -E "s|^IMAGE_TAG=.*|IMAGE_TAG=${tag}|" "$env_file"
  else
    printf 'IMAGE_TAG=%s\n' "$tag" >> "$env_file"
  fi
}

# ────── Commandes conteneur ──────

cmd_start() {
  info "Demarrage ($APP_DIR)"
  (cd "$APP_DIR" && docker compose up -d --build)
  docker ps --filter "name=$CONTAINER_NAME" --format "  {{.Names}}  {{.Status}}  {{.Ports}}"
}

cmd_stop() {
  info "Arret"
  (cd "$APP_DIR" && docker compose down)
}

cmd_restart() {
  info "Redemarrage (force-recreate)"
  (cd "$APP_DIR" && docker compose up -d --build --force-recreate)
  docker ps --filter "name=$CONTAINER_NAME" --format "  {{.Names}}  {{.Status}}  {{.Ports}}"
}

cmd_logs() {
  info "Logs (Ctrl+C pour sortir)"
  (cd "$APP_DIR" && docker compose logs -f --tail=200)
}

cmd_status() {
  info "Conteneur"
  docker ps -a --filter "name=$CONTAINER_NAME" \
    --format "  {{.Names}}\t{{.Status}}\t{{.Ports}}"
  echo
  info "Version deployee : $(current_version 2>/dev/null || echo '<aucune>')"
  info "Branche courante : $(current_branch)"
  info "Env logique      : $(env_from_branch)"
}

# ────── Mise a jour ──────

# Apres un rebuild, attend que le conteneur soit pret (docker exec OK) puis
# lance le schema-update. Non-fatal : si le sync schema rate, le deploiement
# reste considere comme reussi (on warn juste). Appele systematiquement par
# toutes les commandes de deploiement (release, pull-dev/main, rolling) pour
# que l'ajout d'une colonne dans models.py soit applique automatiquement
# apres une MAJ declenchee depuis l'UI.
post_deploy_schema_sync() {
  info "Attente du conteneur pour appliquer le schema-update..."
  local i
  for i in {1..30}; do
    if docker exec "$CONTAINER_NAME" python -c "import app.models" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  if ! cmd_schema_update; then
    warn "schema-update a echoue (deploiement OK quand meme, a verifier manuellement)"
  fi
}

# Rolling : pull HEAD de la branche courante + rebuild. Utilise pour :
#   - dev : workflow normal (HEAD de dev)
#   - prod hotfix : bascule sur main (pas depuis HEAD detache) puis pull
cmd_pull_rolling() {
  local branch; branch="$(current_branch)"
  if [[ "$branch" == "HEAD" ]]; then
    err "HEAD detache (release). Utilise 'pull-dev'/'pull-main' pour checkout une branche."
    return 1
  fi
  info "Pull rolling branche '$branch' dans $APP_DIR"
  (cd "$APP_DIR" \
    && git_auth fetch origin "$branch" \
    && git "${GIT_SAFE[@]}" reset --hard "origin/$branch")
  local sha; sha="$(git "${GIT_SAFE[@]}" -C "$APP_DIR" rev-parse --short HEAD)"
  local tag="rolling-${branch}-${sha}"
  set_image_tag "$tag"
  (cd "$APP_DIR" && docker compose up -d --build)
  write_version_file "$tag"
  ok "Mis a jour : $tag"
  docker ps --filter "name=$CONTAINER_NAME" --format "  {{.Names}}  {{.Status}}"
  post_deploy_schema_sync
}

# pull-branch : checkout force + reset hard sur origin/<branche> + rebuild.
# Remplace l'ancien switch-dev/switch-prod en explicitant la cible.
cmd_pull_branch() {
  local branch="$1"
  info "Pull + checkout branche '$branch' dans $APP_DIR"
  (cd "$APP_DIR" \
    && git_auth fetch origin "$branch" \
    && git "${GIT_SAFE[@]}" checkout --force "$branch" \
    && git "${GIT_SAFE[@]}" reset --hard "origin/$branch")
  local sha; sha="$(git "${GIT_SAFE[@]}" -C "$APP_DIR" rev-parse --short HEAD)"
  local tag="rolling-${branch}-${sha}"
  set_image_tag "$tag"
  (cd "$APP_DIR" && docker compose up -d --build)
  write_version_file "$tag"
  ok "Deploye : $tag"
  docker ps --filter "name=$CONTAINER_NAME" --format "  {{.Names}}  {{.Status}}"
  post_deploy_schema_sync
}

# Liste les N dernieres releases GitHub et permet d'en choisir une a checkout.
cmd_choose_release() {
  local -a curl_args=(-fsSL -H 'Accept: application/vnd.github+json')
  local token; token="$(github_token)"
  [[ -n "$token" ]] && curl_args+=(-H "Authorization: Bearer $token")

  info "Recuperation des releases depuis github.com/${GITHUB_REPO}"
  local json
  json="$(curl "${curl_args[@]}" \
    "https://api.github.com/repos/${GITHUB_REPO}/releases?per_page=15" 2>/dev/null || true)"
  [[ -n "$json" ]] || { err "API GitHub inaccessible"; return 1; }

  # Parse les tag_name via grep (pas de dep jq). Ordre API = plus recent d'abord.
  local -a tags=()
  while IFS= read -r tag; do
    tags+=("$tag")
  done < <(printf '%s' "$json" \
    | grep -oE '"tag_name"[[:space:]]*:[[:space:]]*"[^"]+"' \
    | sed -E 's/.*"([^"]+)"[[:space:]]*$/\1/')

  [[ ${#tags[@]} -gt 0 ]] || { err "Aucune release trouvee sur ce repo"; return 1; }

  echo
  printf "  ${C_BOLD}Releases disponibles${C_RESET} (plus recente en haut) :\n"
  local i=1
  for tag in "${tags[@]}"; do
    printf "    %2d) %s\n" "$i" "$tag"
    ((i++))
  done
  echo
  read -r -p "  Choix (numero, vide pour annuler) : " choice
  [[ -n "$choice" ]] || { info "Annule"; return 0; }
  [[ "$choice" =~ ^[0-9]+$ ]] || { err "Choix invalide"; return 1; }
  (( choice >= 1 && choice <= ${#tags[@]} )) || { err "Hors de la plage"; return 1; }

  local target="${tags[$((choice-1))]}"
  info "Checkout $target + rebuild"
  (cd "$APP_DIR" \
    && git_auth fetch --tags --prune origin \
    && git "${GIT_SAFE[@]}" checkout --force "$target")
  set_image_tag "$target"
  (cd "$APP_DIR" && docker compose up -d --build)
  write_version_file "$target"
  ok "Deploye : $target"
  docker ps --filter "name=$CONTAINER_NAME" --format "  {{.Names}}  {{.Status}}"
  post_deploy_schema_sync
}

# self-update-dev : recupere uniquement ce script depuis la branche dev,
# sans toucher au reste du clone (pas de rebuild conteneur). Utile pour
# tester une nouvelle version du script sans bousculer le code applicatif.
cmd_self_update_dev() {
  info "Fetch origin dev + checkout tools/website-management.sh uniquement"
  (cd "$APP_DIR" \
    && git_auth fetch origin dev \
    && git "${GIT_SAFE[@]}" checkout "origin/dev" -- tools/website-management.sh)
  chmod +x "$APP_DIR/tools/website-management.sh" || true
  ok "Script de management mis a jour depuis origin/dev (reste du clone inchange)"
}

# schema-update : aligne la BDD sur Base.metadata (app/models.py) via
# docker exec dans le conteneur. Ne fait QUE de l'additif :
#   - CREATE TABLE pour les tables absentes
#   - ALTER TABLE ADD COLUMN pour les colonnes absentes
# Ne modifie PAS les colonnes existantes (type/nullability), ne supprime
# rien. Les changements destructifs restent a appliquer manuellement.
cmd_schema_update() {
  if ! docker ps --filter "name=$CONTAINER_NAME" --format "{{.Names}}" | grep -q "^$CONTAINER_NAME$"; then
    err "Conteneur $CONTAINER_NAME non demarre. Lance-le d'abord."
    return 1
  fi

  info "Comparaison Base.metadata (app/models.py) vs BDD configuree"
  local py_script='
import sys
from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateColumn

import app.models  # noqa: F401  force limport de tous les modeles
from app.db import Base, get_engine
from app.db_config import is_configured

if not is_configured():
    print("BDD non configuree (ouvre /setup/database dabord)", file=sys.stderr)
    sys.exit(1)

engine = get_engine()
insp = inspect(engine)
existing = set(insp.get_table_names())

missing_tables = [t for t in Base.metadata.sorted_tables if t.name not in existing]
missing_columns = []
for table in Base.metadata.sorted_tables:
    if table.name not in existing:
        continue
    db_cols = {c["name"] for c in insp.get_columns(table.name)}
    for col in table.columns:
        if col.name not in db_cols:
            missing_columns.append((table, col))

if not missing_tables and not missing_columns:
    print("Schema deja a jour -- aucune action.")
    sys.exit(0)

print("Plan :")
for t in missing_tables:
    print(f"  + CREATE TABLE {t.name}")
for t, c in missing_columns:
    print(f"  + ALTER TABLE {t.name} ADD COLUMN {c.name}")

if missing_tables:
    Base.metadata.create_all(engine, tables=missing_tables)
    print(f"OK: {len(missing_tables)} table(s) creee(s)")

rc = 0
for table, col in missing_columns:
    ddl = str(CreateColumn(col).compile(dialect=engine.dialect))
    sql = f"ALTER TABLE `{table.name}` ADD COLUMN {ddl}"
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
        print(f"OK: {table.name}.{col.name} ajoutee")
    except Exception as e:
        print(f"ERREUR: {table.name}.{col.name} -- {e}", file=sys.stderr)
        rc = 2
sys.exit(rc)
'
  if docker exec -i "$CONTAINER_NAME" python -c "$py_script"; then
    ok "Synchronisation schema terminee"
  else
    err "Synchronisation schema echouee (voir erreurs ci-dessus)"
    return 1
  fi
}

# Release-based : checkout du tag de la derniere release GitHub. Utilise
# uniquement pour la prod (branche main). Idempotent si deja a jour.
cmd_update_release() {
  info "Recuperation de la derniere release depuis github.com/${GITHUB_REPO}"
  local latest current
  latest="$(latest_release_tag || true)"
  if [[ -z "$latest" ]]; then
    err "Impossible de recuperer la derniere release (pas de release publiee, ou API inaccessible)"
    return 1
  fi
  current="$(current_version || true)"
  info "Deploye actuellement : ${current:-<aucun>}"
  info "Derniere release     : $latest"

  if [[ -n "$current" ]] && ! version_gt "$latest" "$current"; then
    ok "Deja a jour ($current)"
    return 0
  fi

  info "Checkout $latest + rebuild du conteneur"
  (cd "$APP_DIR" \
    && git_auth fetch --tags --prune origin \
    && git "${GIT_SAFE[@]}" checkout --force "$latest")
  set_image_tag "$latest"
  (cd "$APP_DIR" && docker compose up -d --build)
  write_version_file "$latest"
  ok "Deploye : $latest"
  docker ps --filter "name=$CONTAINER_NAME" --format "  {{.Names}}  {{.Status}}"
  post_deploy_schema_sync
}

# update : dispatcher selon le mode (branche).
#   prod (main ou HEAD detache sur un tag) -> release-based (latest tag)
#   dev                                    -> rolling (HEAD de la branche)
cmd_update() {
  case "$(env_from_branch)" in
    prod) cmd_update_release ;;
    dev)  cmd_pull_rolling ;;
  esac
}

# Sortie machine-readable pour l'UI admin.
cmd_check_update() {
  local current latest available="0"
  current="$(current_version || true)"
  local env; env="$(env_from_branch)"

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

# self-update : mono-env, plus de clone tools separe. On pull simplement
# la branche courante du clone applicatif — ce script en fait partie.
# Sur HEAD detache (prod sur tag), on passe par `switch-prod` qui refera
# un checkout propre sur la derniere release plutot que de tenter un pull.
cmd_self_update() {
  local branch; branch="$(current_branch)"
  if [[ "$branch" == "HEAD" ]]; then
    info "HEAD detache -> bascule sur la derniere release (equivalent switch-prod --no-rebuild)"
    local latest
    latest="$(latest_release_tag || true)"
    [[ -z "$latest" ]] && { err "Aucune release disponible"; return 1; }
    (cd "$APP_DIR" \
      && git_auth fetch --tags --prune origin \
      && git "${GIT_SAFE[@]}" checkout --force "$latest")
  else
    info "Pull self-update (branche $branch) dans $APP_DIR"
    (cd "$APP_DIR" \
      && git_auth fetch origin "$branch" \
      && git "${GIT_SAFE[@]}" reset --hard "origin/$branch")
  fi
  ok "Script a jour : $(git "${GIT_SAFE[@]}" -C "$APP_DIR" log -1 --format='%h %s')"
}

# ────── Reset users ──────

# Purge tous les users admin et en insere un nouveau. Utilise le
# Python du conteneur (SQLAlchemy + bcrypt + db.json), donc pas besoin
# de client mysql ni de .mysql.cnf hote — la connexion est celle configuree
# via /setup/database.
cmd_reset_users() {
  warn "Cette operation SUPPRIME tous les utilisateurs admin."
  read -r -p "Confirmer ? [y/N] " yn
  [[ "$yn" =~ ^[yYoO]$ ]] || { info "Annule"; return 1; }

  local new_user new_pass1 new_pass2
  read -r -p "Nouveau login admin : " new_user
  [[ -n "$new_user" ]] || { err "Login vide"; return 1; }
  [[ ${#new_user} -ge 3 ]] || { err "Login trop court (3 min)"; return 1; }

  read -r -s -p "Nouveau mot de passe : " new_pass1; echo
  read -r -s -p "Confirmation         : " new_pass2; echo
  [[ "$new_pass1" == "$new_pass2" ]] || { err "Les mots de passe ne correspondent pas"; return 1; }
  [[ ${#new_pass1} -ge 8 ]] || { err "Mot de passe trop court (8 min)"; return 1; }

  if ! docker ps --filter "name=$CONTAINER_NAME" --format "{{.Names}}" | grep -q "^$CONTAINER_NAME$"; then
    err "Conteneur $CONTAINER_NAME non demarre. Lance-le d'abord (option Start)."
    return 1
  fi

  info "Purge + reinsertion via le conteneur (Python + SQLAlchemy + bcrypt)"
  # Le password est passe via stdin (pas via arg) pour eviter qu'il apparaisse
  # dans la table process ou les logs docker.
  local py_script='
import sys
import bcrypt
from sqlalchemy import text
from app.db import get_engine, init_db
from app.db_config import is_configured

if not is_configured():
    print("BDD non configuree (ouvre /setup/database dabord)", file=sys.stderr)
    sys.exit(1)

username = sys.argv[1]
password = sys.stdin.buffer.read()
pw_hash = bcrypt.hashpw(password, bcrypt.gensalt(rounds=12)).decode()

init_db()
with get_engine().begin() as conn:
    conn.execute(text("DELETE FROM users"))
    conn.execute(
        text("INSERT INTO users (username, password_hash, created_at) VALUES (:u, :h, UTC_TIMESTAMP())"),
        {"u": username, "h": pw_hash},
    )
print("OK")
'
  if printf '%s' "$new_pass1" | docker exec -i "$CONTAINER_NAME" \
      python -c "$py_script" "$new_user"; then
    ok "Admin '$new_user' cree. Connecte-toi avec ces identifiants."
  else
    err "Echec de la reinsertion"
    return 1
  fi
}

# ────── SCInsta builder ──────

SCINSTA_BUILDER_IMAGE="scinsta-builder:latest"

cmd_scinsta_build() {
  local env="${1:-$(env_from_branch)}"
  local dir="$APP_DIR/tools/scinsta-builder"
  [[ -d "$dir" ]] || { err "Builder introuvable : $dir (relance 'update' ?)"; exit 1; }

  # Tee toute la sortie vers le fichier log poll par l'UI.
  local log_file="/etc/ipastore/scinsta-build-log-${env}.txt"
  : > "$log_file"
  exec > >(tee -a "$log_file") 2>&1

  info "Build image Docker $SCINSTA_BUILDER_IMAGE"
  docker build --progress=plain -t "$SCINSTA_BUILDER_IMAGE" "$dir"

  local cname="scinsta-builder-${env}"
  info "Run scinsta-builder env=$env (container=$cname)"
  docker run --rm --name "$cname" \
    -e IPASTORE_ENV="$env" \
    -v /etc/ipastore:/etc/ipastore \
    -v "${STORE_DIR}:/srv/store" \
    -v "${APP_DIR}:/opt/sideserver-${env}:ro" \
    --network host \
    "$SCINSTA_BUILDER_IMAGE"
  ok "Build termine (env=$env)"
}

cmd_scinsta_cancel() {
  local env="${1:-$(env_from_branch)}"
  local cname="scinsta-builder-${env}"
  local flag="/etc/ipastore/scinsta-build-cancel-${env}"
  local req_flag="/etc/ipastore/scinsta-build-requested-${env}"
  local progress="/etc/ipastore/scinsta-build-progress-${env}"
  local result="/etc/ipastore/scinsta-build-result-${env}"

  info "Cancel demande pour env=$env"
  rm -f "$flag" "$req_flag"

  if docker ps --filter "name=^${cname}\$" --format "{{.Names}}" | grep -q "^${cname}\$"; then
    info "Stop du conteneur $cname (SIGTERM -t2 puis SIGKILL, bloquant)"
    docker stop -t 2 "$cname" || true
  else
    warn "Conteneur $cname non trouve (deja termine ?)"
  fi

  local now
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  cat > "$result" <<JSON
{"status":"failed","finished_at":"$now","error":"Build annule"}
JSON
  rm -f "$progress"
  ok "Build $env marque comme annule."
}

# ────── Aide ──────

usage() {
  cat <<EOF
$(printf "${C_BOLD}website-management.sh${C_RESET}") — gestion mono-env de sideserver_website

$(printf "${C_BOLD}USAGE${C_RESET}")
  $(basename "$0")                   # menu interactif
  $(basename "$0") <commande>        # execution directe

$(printf "${C_BOLD}CONTENEUR${C_RESET}")
  start               Demarre le conteneur
  stop                Arrete le conteneur
  restart             Rebuild + redemarre
  logs                Suit les logs
  status              Conteneur + version + branche

$(printf "${C_BOLD}MISE A JOUR DU CODE${C_RESET}")
  update              Checkout de la derniere release GitHub + rebuild
  choose-release      Liste les 15 dernieres releases et permet d'en choisir une
  pull-dev            Checkout + pull origin/dev + rebuild
  pull-main           Checkout + pull origin/main + rebuild
  self-update-dev     Recupere uniquement ce script depuis origin/dev (pas de rebuild)
  check               Machine-readable : current / latest / update_available
  pull                Force pull HEAD de la branche courante (dev uniquement)
  self-update         Pull ce script (git pull / re-checkout de APP_DIR)

$(printf "${C_BOLD}BASCULE D'ENVIRONNEMENT (alias retro-compat)${C_RESET}")
  switch-dev          Alias de pull-dev
  switch-prod         Alias de update (derniere release)

$(printf "${C_BOLD}ADMIN${C_RESET}")
  reset-users         Supprime tous les admins et en cree un nouveau

$(printf "${C_BOLD}DB${C_RESET}")
  schema-update       Aligne la BDD sur app/models.py (CREATE TABLE + ADD COLUMN
                      additif uniquement, pas de modif/suppression)

$(printf "${C_BOLD}SCINSTA BUILDER${C_RESET}")
  scinsta-build       Lance le pipeline SCInsta + Instagram
  scinsta-cancel      Stoppe un build SCInsta en cours

$(printf "${C_BOLD}COMPAT SYSTEMD${C_RESET}")
  prod-update / prod-scinsta-build / prod-scinsta-cancel
  (aliases pour les units ipastore-*@prod.service, identiques au mono-env)

$(printf "${C_BOLD}AIDE${C_RESET}")
  -h, --help          Cette aide
EOF
}

# ────── Menu interactif ──────

pause_menu() {
  printf "\n${C_DIM}Appuie sur Entree pour revenir au menu...${C_RESET}"
  read -r _
}

menu() {
  while true; do
    clear
    printf "${C_BOLD}╔═══════════════════════════════════════════════╗${C_RESET}\n"
    printf "${C_BOLD}║  SideServer Website — Gestion mono-env        ║${C_RESET}\n"
    printf "${C_BOLD}╚═══════════════════════════════════════════════╝${C_RESET}\n\n"
    local branch env
    branch="$(current_branch)"
    env="$(env_from_branch)"
    printf "  ${C_DIM}Conteneur :${C_RESET}\n"
    docker ps -a --filter "name=$CONTAINER_NAME" \
      --format "    ${C_CYAN}{{.Names}}${C_RESET}  {{.Status}}  ${C_DIM}{{.Ports}}${C_RESET}" \
      2>/dev/null || printf "    ${C_YELLOW}(docker indisponible)${C_RESET}\n"
    printf "  ${C_DIM}Version :${C_RESET}  ${C_GREEN}%s${C_RESET}\n" "$(current_version 2>/dev/null || echo '<aucune>')"
    printf "  ${C_DIM}Branche :${C_RESET}  ${C_GREEN}%s${C_RESET}  ${C_DIM}(env=%s)${C_RESET}\n" "$branch" "$env"
    printf "\n"
    printf "  ${C_BOLD}CONTENEUR${C_RESET}\n"
    printf "     1) Start                  2) Stop\n"
    printf "     3) Restart                4) Logs\n"
    printf "\n"
    printf "  ${C_BOLD}CODE${C_RESET}\n"
    printf "     5) Update (derniere release GitHub)\n"
    printf "     6) Choose a release\n"
    printf "     7) Pull dev branch\n"
    printf "     8) Pull main branch\n"
    printf "     9) Self-update for dev (script seul)\n"
    printf "\n"
    printf "  ${C_BOLD}ADMIN${C_RESET}\n"
    printf "    10) Reset utilisateurs\n"
    printf "\n"
    printf "  ${C_BOLD}DB${C_RESET}\n"
    printf "    11) Update schema (Tables & Keys)\n"
    printf "\n"
    printf "     s) Status                 h) Aide CLI\n"
    printf "     q) Quitter\n\n"
    read -r -p "  Choix : " choice
    case "$choice" in
       1) cmd_start ;;
       2) cmd_stop ;;
       3) cmd_restart ;;
       4) cmd_logs ;;
       5) cmd_update_release ;;
       6) cmd_choose_release ;;
       7) cmd_pull_branch dev ;;
       8) cmd_pull_branch main ;;
       9) cmd_self_update_dev ;;
      10) cmd_reset_users ;;
      11) cmd_schema_update ;;
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
  update)              cmd_update_release ;;
  choose-release)      cmd_choose_release ;;
  pull-dev)            cmd_pull_branch dev ;;
  pull-main)           cmd_pull_branch main ;;
  self-update-dev)     cmd_self_update_dev ;;
  pull)                cmd_pull_rolling ;;
  check)               cmd_check_update ;;
  self-update)         cmd_self_update ;;
  switch-dev)          cmd_pull_branch dev ;;
  switch-prod)         cmd_update_release ;;
  reset-users)         cmd_reset_users ;;
  schema-update)       cmd_schema_update ;;
  scinsta-build)       cmd_scinsta_build "$(env_from_branch)" ;;
  scinsta-cancel)      cmd_scinsta_cancel "$(env_from_branch)" ;;
  # Aliases systemd : ipastore-update@prod.service appelle "prod-update".
  # Comme l'env physique est toujours "prod" dans le nouveau modele, on
  # map ces commandes aux operations mono-env et on ignore l'instance.
  prod-update)         cmd_update ;;
  prod-scinsta-build)  cmd_scinsta_build prod ;;
  prod-scinsta-cancel) cmd_scinsta_cancel prod ;;
  *) err "Commande inconnue : $1"; echo; usage; exit 1 ;;
esac
