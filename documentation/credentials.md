# Gestion des identifiants et permissions

Ce document recense tous les systèmes d'authentification du projet, leurs emplacements, leurs droits et comment les renouveler.

Modèle mono-environnement : **une VM = un conteneur = un jeu de credentials**. Tous les fichiers sensibles sont dans `/etc/ipastore/` (mode 750, owner = app-user, uid 1000), monté en volume dans le conteneur. Les noms de fichiers contiennent toujours `prod` quel que soit le mode réel (dev ou prod) — c'est la branche git checkoutée qui détermine le comportement applicatif, pas le nom des fichiers.

---

## 1. Token GitHub (lecture des releases)

**Rôle** : permet au conteneur de lire les releases via l'API GitHub. Utilisé par `website-management check/update` et la vérification automatique 6h du conteneur.

**Le repo actuel est public** : le token est **optionnel**. On ne le crée que si le repo passe en privé.

| Champ | Valeur |
|---|---|
| Type | Fine-grained personal access token |
| Compte | MattTen |
| Repo ciblé | `MattTen/sideserver_website` uniquement |
| Permissions | Contents : Read-only · Metadata : Read-only |
| Expiration | 1 an max — à renouveler |
| Emplacement | `/etc/ipastore/.git-credentials` |
| Format | `https://MattTen:<TOKEN>@github.com` |
| Droits | `app-user:app-user 600` |

**Créer/renouveler** :
1. [github.com/settings/tokens](https://github.com/settings/tokens) → *Fine-grained tokens* → *Generate new token*
2. Repository access : `MattTen/sideserver_website` uniquement
3. Permissions : Contents = Read-only, Metadata = Read-only
4. Sur la VM :
   ```bash
   printf 'https://MattTen:NOUVEAU_TOKEN@github.com\n' | sudo tee /etc/ipastore/.git-credentials
   sudo chmod 600 /etc/ipastore/.git-credentials
   website-management check
   ```

---

## 2. Utilisateur MySQL de l'application

Un seul user MySQL par VM — celui qu'utilise le conteneur pour lire/écrire sa BDD.

| Champ | Valeur |
|---|---|
| User MySQL | libre (ex: `ipastore`) |
| Host autorisé | tout host depuis lequel le conteneur se connecte (`%` ou `host.docker.internal` côté Docker, `localhost` si la BDD est sur la même VM) |
| Port | `3306` (défaut MySQL/MariaDB) |
| Base de données | libre (ex: `ipastore`) |
| Droits | `ALL PRIVILEGES ON <db>.*` |
| Où trouver le mot de passe | `/etc/ipastore/db.json` (champ `password`) |
| Utilisé par | le conteneur `ipastore-website` |

**Saisie initiale** : la connexion est saisie via l'UI `/setup/database` au premier démarrage (host, port, user, password, database). Les valeurs sont testées (`SELECT 1` + tentative de `CREATE TABLE`) puis persistées dans `/etc/ipastore/db.json` (mode 600, owner uid 1000, format JSON).

**Changer la connexion BDD** :
- Soit via l'UI `/settings` (si exposé)
- Soit directement : éditer `/etc/ipastore/db.json` puis `docker restart ipastore-website`

Il n'y a **plus** de user `ipastore-mgmt` ni de fichier `.mysql.cnf` — les opérations administratives (reset-users) passent par `docker exec` dans le conteneur, qui utilise la connexion applicative déjà configurée.

---

## 3. Clé secrète de session (cookies)

**Rôle** : signe les cookies de session (`ipastore_session`) via HMAC (bibliothèque `itsdangerous`). Sans elle, toutes les sessions actives sont invalidées.

| Fichier | Droits |
|---|---|
| `/etc/ipastore/secret_key` (défaut) ou `/etc/ipastore/secret_key.prod` (si `IPASTORE_SECRET_FILE` pointe dessus) | `app-user:app-user 600` |

- Générée automatiquement au premier démarrage du conteneur si absente.
- 64 octets aléatoires (`secrets.token_bytes(64)`).
- **Ne pas supprimer** sans recréer le fichier : toutes les sessions seront invalidées.

**Régénérer** (invalide toutes les sessions) :
```bash
sudo python3 -c "import secrets,sys; sys.stdout.buffer.write(secrets.token_bytes(64))" \
  | sudo tee /etc/ipastore/secret_key > /dev/null
sudo chmod 600 /etc/ipastore/secret_key
docker restart ipastore-website
```

---

## 4. Compte administrateur de l'interface web

**Rôle** : accès à l'interface d'administration (upload IPA, réglages, mise à jour).

| Champ | Détail |
|---|---|
| Stockage | table `users` dans la BDD |
| Hash | bcrypt, coût 12 |
| Cookie | `ipastore_session` (HMAC signé, durée 30 jours) |
| Routes protégées | toutes sauf `/source.json`, `/qr.svg`, `/ipas/*`, `/icons/*`, `/static/*`, `/setup*` |

**Changer ou recréer le mot de passe admin** :
```bash
website-management reset-users   # supprime tous les admins + prompt création
```

**Changer uniquement le mot de passe** (sans reset total) : interface web → *Réglages* → *Compte administrateur*.

**Premier démarrage** : si la table `users` est vide, l'interface redirige vers `/setup` pour créer le premier compte.

---

## 5. Résumé des fichiers sensibles dans `/etc/ipastore/`

| Fichier | Contenu | Droits |
|---|---|---|
| `.git-credentials` | Token GitHub (optionnel, repo public) | `app-user:app-user 600` |
| `db.json` | Connexion BDD (host/port/user/password/database) | `uid1000 600` |
| `prod.env` | Vars app (`IPASTORE_STORE_DIR`, `IPASTORE_SECRET_FILE`, `IPASTORE_ENV`, `IPASTORE_GITHUB_REPO`, éventuellement `IPASTORE_BASE_URL`) — **pas** la connexion BDD | `app-user:app-user 640` |
| `secret_key` (ou `secret_key.prod`) | Clé HMAC signature cookies (64 octets) | `app-user:app-user 600` |
| `prod.version` | Tag ou `rolling-<sha>` de la version déployée | `app-user:app-user 644` |
| `app.log` | Logs applicatifs (file handler dans `app/main._configure_logging`) — affiché via Réglages → Logs | `app-user:app-user 644` |
| `update-requested-prod` | Flag-file MAJ (créé par l'UI, supprimé par `ExecStartPre` du service) | `uid1000 644` (transient) |
| `scinsta-upload-prod.ipa` | IPA Instagram source uploadée (manuelle ou via URL) | `uid1000 600` (transient, supprimée après build success) |
| `scinsta-build-requested-prod` | Flag-file build (JSON `{patch, requested_at}`) — supprimé par `build.py` au démarrage, fallback `trap` + `ExecStopPost` | `uid1000 644` (transient) |
| `scinsta-build-cancel-prod` | Flag-file cancel — supprimé par `cmd_scinsta_cancel` après `docker stop` | `uid1000 644` (transient) |
| `scinsta-build-progress-prod` | JSON d'étape (`{step}`) écrit par `build.py`, supprimé en fin | `uid1000 644` (transient) |
| `scinsta-build-result-prod` | JSON résultat consommé+supprimé par le watcher lifespan | `uid1000 644` (transient) |
| `scinsta-build-log-prod.txt` | Tee stdout/stderr du build (lu via `/scinsta/logs?offset=N`) | `uid1000 644` |

Tous ces fichiers sont dans `/etc/ipastore/` (mode 750, owned par l'app-user) — inaccessibles aux autres utilisateurs.

---

## 6. Source token (protection optionnelle du dépôt)

**Rôle** : limite l'accès à `/source.json` et `/qr.svg` aux personnes connaissant un secret en query string. Empêche les bots de scraping de découvrir la liste des IPAs.

| Champ | Valeur |
|---|---|
| Activation | Réglages → Sécurité → toggle "Protéger l'accès au dépôt d'IPA" |
| Stockage | clés `settings.source_token_enabled` (`'1'` / `''`) + `settings.source_token_value` (256 chars hex) |
| Cache RAM | `app/source_token.py` — `threading.Lock` + refresh depuis BDD au boot et après modif |
| Format jeton | 256 caractères alphanumériques (`secrets.token_urlsafe`) |
| Comportement | Sans `?t=...` correct, `/source.json` et `/qr.svg` renvoient `404` (volontairement opaque pour les bots) |

**Régénérer un jeton** (invalide tous les liens partagés précédemment) : Réglages → Sécurité → bouton **Régénérer**. Confirmation côté UI.

**Désactiver** : toggle off → le jeton reste en BDD (peut être réactivé tel quel) mais n'est plus exigé.

L'URL affichée dans le dashboard et le QR code intègrent automatiquement le jeton quand actif. C'est un **secret long en query string** plutôt qu'une vraie auth car SideStore ne sait pas envoyer de header custom — seul `GET ?token=...` est utilisable côté client iOS.

---

## 7. Ce qui est public (aucune auth requise)

| Route | Contenu |
|---|---|
| `GET /source.json` | Feed SideStore (apps, URLs de téléchargement) — **404 si protection par jeton active et `?t=` absent** |
| `GET /qr.svg` | QR code vers source.json — **404 si protection active et `?t=` absent** |
| `GET /healthz` | Liveness check Docker — toujours `200 ok` (pas de BDD, pas de jeton) |
| `GET /ipas/*` | Téléchargement des fichiers IPA |
| `GET /icons/*` | Icônes des apps |
| `GET /screenshots/*` | Screenshots |
| `GET /static/*` | CSS, JS, icône par défaut |
