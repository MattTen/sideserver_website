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
| Utilisé par | le conteneur `sidestore-website-prod` |

**Saisie initiale** : la connexion est saisie via l'UI `/setup/database` au premier démarrage (host, port, user, password, database). Les valeurs sont testées (`SELECT 1` + tentative de `CREATE TABLE`) puis persistées dans `/etc/ipastore/db.json` (mode 600, owner uid 1000, format JSON).

**Changer la connexion BDD** :
- Soit via l'UI `/settings` (si exposé)
- Soit directement : éditer `/etc/ipastore/db.json` puis `docker restart sidestore-website-prod`

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
docker restart sidestore-website-prod
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
| `secret_key` | Clé HMAC signature cookies (64 octets) | `app-user:app-user 600` |
| `prod.version` | Tag ou `rolling-<sha>` de la version déployée | `app-user:app-user 644` |
| `update-requested-prod` | Flag-file écrit par le conteneur pour déclencher une MAJ | `uid1000 644` (transient) |
| `scinsta-upload-prod.ipa` / `scinsta-build-log-prod.txt` / `scinsta-build-result-prod.json` / … | I/O pipeline SCInsta | variés |

Tous ces fichiers sont dans `/etc/ipastore/` (mode 750, owned par l'app-user) — inaccessibles aux autres utilisateurs.

---

## 6. Ce qui est public (aucune auth requise)

| Route | Contenu |
|---|---|
| `GET /source.json` | Feed SideStore (apps, URLs de téléchargement) |
| `GET /qr.svg` | QR code vers source.json |
| `GET /ipas/*` | Téléchargement des fichiers IPA |
| `GET /icons/*` | Icônes des apps |
| `GET /screenshots/*` | Screenshots |
| `GET /static/*` | CSS, JS, icône par défaut |
