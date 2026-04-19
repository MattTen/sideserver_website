# Gestion des identifiants et permissions

Ce document recense tous les systèmes d'authentification du projet, leurs emplacements, leurs droits et comment les renouveler.

---

## 1. Token GitHub (lecture des releases — côté serveur)

**Rôle** : permet au serveur de lire les releases du repo privé via l'API GitHub. Utilisé par `website-management prod-check/prod-update` et par la vérification automatique 6h du conteneur.

| Champ | Valeur |
|---|---|
| Type | Fine-grained personal access token |
| Compte | MattTen |
| Repo ciblé | `MattTen/sideserver_website` uniquement |
| Permissions | Contents : Read-only · Metadata : Read-only |
| Expiration | 1 an max (fine-grained) — à renouveler |
| Emplacement sur le serveur | `/etc/ipastore/.git-credentials` |
| Format du fichier | `https://MattTen:<TOKEN>@github.com` |
| Propriétaire/droits | `altuser:altuser 600` |

**Renouveler le token** :
1. [github.com/settings/tokens](https://github.com/settings/tokens) → *Fine-grained tokens* → *Generate new token*
2. Repository access : `MattTen/sideserver_website` uniquement
3. Permissions : Contents = Read-only, Metadata = Read-only (auto)
4. Sur le serveur :
   ```bash
   printf 'https://MattTen:NOUVEAU_TOKEN@github.com\n' > /etc/ipastore/.git-credentials
   chmod 600 /etc/ipastore/.git-credentials
   # Vérifier :
   website-management prod-check
   ```

---

## 2. Utilisateurs MySQL

Trois users MySQL distincts, chacun avec un rôle précis.

### 2a. `ipastore-prod` — application prod

| Champ | Valeur |
|---|---|
| User MySQL | `ipastore-prod` |
| Mot de passe | `HmIQl5adwEjiitKlFQbQ8uk2Xrz3MHIsS9B2E-cujwY` |
| Host autorisé | `%` (depuis le conteneur Docker via `host.docker.internal`) |
| Port | `3306` |
| Base de données | `ipastore-prod` |
| Droits | `ALL PRIVILEGES ON ipastore-prod.*` |
| Fichier | `/etc/ipastore/prod.env` (`IPASTORE_DB_URL`) |
| Utilisé par | conteneur `sidestore-website-prod` |

Connection string complète :
```
mysql+pymysql://ipastore-prod:HmIQl5adwEjiitKlFQbQ8uk2Xrz3MHIsS9B2E-cujwY@host.docker.internal:3306/ipastore-prod?charset=utf8mb4
```

### 2b. `ipastore-dev` — application dev

| Champ | Valeur |
|---|---|
| User MySQL | `ipastore-dev` |
| Mot de passe | `8W2Spq9hHtGduW04lndrVRlw64-CeXhBzFyiwrZ0mfk` |
| Host autorisé | `%` (depuis le conteneur Docker via `host.docker.internal`) |
| Port | `3306` |
| Base de données | `ipastore-dev` |
| Droits | `ALL PRIVILEGES ON ipastore-dev.*` |
| Fichier | `/etc/ipastore/dev.env` (`IPASTORE_DB_URL`) |
| Utilisé par | conteneur `sidestore-website-dev` |

Connection string complète :
```
mysql+pymysql://ipastore-dev:8W2Spq9hHtGduW04lndrVRlw64-CeXhBzFyiwrZ0mfk@host.docker.internal:3306/ipastore-dev?charset=utf8mb4
```

### 2c. `ipastore-mgmt` — script de management

| Champ | Valeur |
|---|---|
| User MySQL | `ipastore-mgmt` |
| Mot de passe | `mgmt_gSZCg2WTZPgFJcEs0JDlQ` |
| Host autorisé | `localhost` uniquement |
| Droits | `ALL PRIVILEGES ON *.*` (nécessaire pour créer/supprimer des BDD lors des opérations sync/reset) |
| Fichier | `/etc/ipastore/.mysql.cnf` |
| Propriétaire/droits | `altuser:altuser 600` |
| Utilisé par | `website-management` (sync, reset-users, dev-update) |

**Contenu de `/etc/ipastore/.mysql.cnf`** :
```ini
[client]
user=ipastore-mgmt
password=mgmt_gSZCg2WTZPgFJcEs0JDlQ
host=localhost
```

**Changer le mot de passe de `ipastore-mgmt`** :
```bash
# Sur le serveur en tant que root :
NEW_PASS=$(openssl rand -base64 32 | tr -d '/+=')
mysql -u root -e "ALTER USER 'ipastore-mgmt'@'localhost' IDENTIFIED BY '${NEW_PASS}';"
# Mettre à jour le fichier :
sed -i "s/^password=.*/password=${NEW_PASS}/" /etc/ipastore/.mysql.cnf
```

---

## 3. Clé secrète de session (cookies)

**Rôle** : signe les cookies de session (`ipastore_session`) via HMAC (bibliothèque `itsdangerous`). Sans elle, toutes les sessions actives sont invalidées.

| Env | Fichier | Droits |
|---|---|---|
| prod | `/etc/ipastore/secret_key.prod` | `altuser:altuser 600` |
| dev | `/etc/ipastore/secret_key.dev` | `altuser:altuser 600` |

- Générée automatiquement au premier démarrage du conteneur si absente.
- 64 octets aléatoires (`secrets.token_bytes(64)`).
- **Ne pas supprimer** sans recréer le fichier : toutes les sessions seront invalidées et les utilisateurs devront se reconnecter.

**Régénérer** (invalide toutes les sessions actives) :
```bash
python3 -c "import secrets,sys; sys.stdout.buffer.write(secrets.token_bytes(64))" \
  > /etc/ipastore/secret_key.prod
chmod 600 /etc/ipastore/secret_key.prod
# Redémarrer le conteneur pour prendre en compte :
cd /opt/sideserver-prod && docker compose restart
```

---

## 4. Compte administrateur de l'interface web

**Rôle** : accès à l'interface d'administration (upload IPA, réglages, mise à jour).

| Champ | Détail |
|---|---|
| Stockage | table `users` dans la BDD de l'env concerné |
| Hash | bcrypt, coût 12 |
| Cookie | `ipastore_session` (HMAC signé, durée 30 jours) |
| Routes protégées | toutes sauf `/source.json`, `/qr.svg`, `/ipas/*`, `/icons/*`, `/static/*` |

**Changer ou recréer le mot de passe admin** (via le script) :
```bash
website-management prod-reset-users   # supprime tous les admins + crée un nouveau
website-management dev-reset-users    # idem sur dev
```

**Changer uniquement le mot de passe** (sans reset total) : aller dans l'interface web → *Réglages* → *Compte administrateur*.

**Premier démarrage** : si la table `users` est vide, l'interface redirige vers `/setup` pour créer le premier compte.

---

## 5. Résumé des fichiers sensibles

| Fichier | Contenu | Droits |
|---|---|---|
| `/etc/ipastore/.git-credentials` | Token GitHub | `altuser:altuser 600` |
| `/etc/ipastore/.mysql.cnf` | Creds MySQL mgmt | `altuser:altuser 600` |
| `/etc/ipastore/prod.env` | DB URL prod + vars app | `altuser:altuser 640` |
| `/etc/ipastore/dev.env` | DB URL dev + vars app | `altuser:altuser 640` |
| `/etc/ipastore/secret_key.prod` | Clé signature cookies prod | `altuser:altuser 600` |
| `/etc/ipastore/secret_key.dev` | Clé signature cookies dev | `altuser:altuser 600` |

Tous ces fichiers sont dans `/etc/ipastore/` qui appartient à `altuser` avec droits `750` — inaccessible aux autres utilisateurs du système.

---

## 6. Ce qui est public (aucune auth requise)

| Route | Contenu |
|---|---|
| `GET /source.json` | Feed SideStore (apps, URLs de téléchargement) |
| `GET /qr.svg` | QR code vers source.json |
| `GET /ipas/*` | Téléchargement des fichiers IPA |
| `GET /icons/*` | Icônes des apps |
| `GET /static/*` | CSS, JS, icône par défaut |
