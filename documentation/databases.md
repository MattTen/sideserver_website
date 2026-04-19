# Bases de données — Structure et cycle de vie

## Schémas MariaDB

Deux schémas séparés sur l'hôte (port 3306), chacun géré par son propre user MySQL :

| Schéma | User MySQL | Conteneur |
|---|---|---|
| `ipastore-prod` | `ipastore-prod` | sidestore-website-prod (port 80) |
| `ipastore-dev` | `ipastore-dev` | sidestore-website-dev (port 8080) |

Le conteneur se connecte via `host.docker.internal` (résout vers l'hôte depuis le réseau Docker bridge).

Les tables sont créées automatiquement au démarrage du conteneur via `Base.metadata.create_all()` (SQLAlchemy). Il n'y a pas de migrations — les colonnes ajoutées doivent être créées manuellement sur la prod si la table existe déjà.

---

## Tables

### `users`

Comptes administrateurs de l'interface web. En pratique un seul utilisateur suffit.

| Colonne | Type | Contraintes | Description |
|---|---|---|---|
| `id` | INT | PK, autoincrement | Identifiant interne |
| `username` | VARCHAR(64) | UNIQUE, NOT NULL | Identifiant de connexion |
| `password_hash` | VARCHAR(255) | NOT NULL | Hash bcrypt (coût 12) — jamais le mot de passe en clair |
| `created_at` | DATETIME | NOT NULL | Date de création (UTC naïf) |
| `last_login` | DATETIME | NULL | Dernière connexion (NULL si jamais connecté) |

---

### `settings`

Paramètres clé/valeur du magasin, modifiables depuis l'UI `/settings` sans rebuild.

| Colonne | Type | Contraintes | Description |
|---|---|---|---|
| `key` | VARCHAR(64) | PK | Nom du paramètre |
| `value` | TEXT | NULL | Valeur du paramètre |

**Clés utilisées :**

| Clé | Description | Défaut code |
|---|---|---|
| `store_name` | Nom affiché dans SideStore | `Magasin Perso` |
| `store_subtitle` | Sous-titre | `""` |
| `store_description` | Description longue | `""` |
| `store_tint` | Couleur d'accent (hex 6 chars) | `c9a678` |
| `base_url` | URL publique du serveur (entrée dans SideStore) | `IPASTORE_BASE_URL` |
| `store_icon_file` | Nom de fichier de l'icône du store dans `ICONS_DIR` | `""` (fallback `_store.png` puis `default-app.png`) |
| `store_header_file` | Nom de fichier du header/bannière du store dans `ICONS_DIR` | `""` (optionnel) |

---

### `apps`

Métadonnées d'une application iOS. Identifiée de manière unique par `bundle_id`.

| Colonne | Type | Contraintes | Description |
|---|---|---|---|
| `id` | INT | PK, autoincrement | Identifiant interne |
| `bundle_id` | VARCHAR(255) | UNIQUE, NOT NULL, INDEX | Identifiant Apple (ex: `com.example.MonApp`) |
| `name` | VARCHAR(255) | NOT NULL | Nom affiché |
| `developer_name` | VARCHAR(255) | NOT NULL | Développeur (défaut: `Self`) |
| `subtitle` | VARCHAR(255) | NOT NULL | Sous-titre court |
| `description` | TEXT | NOT NULL | Description longue |
| `tint_color` | VARCHAR(8) | NOT NULL | Couleur hex (défaut: `833AB4`) |
| `category` | VARCHAR(64) | NOT NULL | Catégorie SideStore (défaut: `other`) |
| `icon_path` | VARCHAR(512) | NULL | Nom de fichier relatif dans `ICONS_DIR` (NULL = pas d'icône) |
| `screenshot_urls` | TEXT | NOT NULL | JSON list d'URLs absolues |
| `featured` | INT | NOT NULL | 1 = mise en avant dans SideStore |
| `created_at` | DATETIME | NOT NULL | Date de création (UTC) |
| `updated_at` | DATETIME | NOT NULL | Mise à jour automatique à chaque modification (`onupdate`) |

---

### `versions`

Une version spécifique d'une App (un IPA uploadé). Plusieurs versions par App.

| Colonne | Type | Contraintes | Description |
|---|---|---|---|
| `id` | INT | PK, autoincrement | Identifiant interne |
| `app_id` | INT | FK → `apps.id` CASCADE DELETE | App parente |
| `ipa_filename` | VARCHAR(512) | NOT NULL | Nom du fichier dans `IPAS_DIR` |
| `version` | VARCHAR(64) | NOT NULL | Version lisible (ex: `1.2.3`) |
| `build_version` | VARCHAR(64) | NOT NULL | Build number (défaut: `1`) |
| `size` | BIGINT | NOT NULL | Taille en octets (BIGINT pour > 2 Go) |
| `sha256` | VARCHAR(64) | NOT NULL | Hash SHA-256 du fichier IPA |
| `min_os_version` | VARCHAR(32) | NOT NULL | iOS minimum (défaut: `14.0`) |
| `changelog` | TEXT | NOT NULL | Notes de version |
| `uploaded_at` | DATETIME | NOT NULL | Date d'upload (UTC) |

**Contraintes :**
- `UNIQUE(app_id, version, build_version)` — empêche le double-upload d'un même build
- `INDEX(uploaded_at)` — tri efficace des versions récentes (dashboard)

---

### `news`

Articles publiés dans le feed `source.json`, affichés par SideStore au-dessus de la liste des apps.

| Colonne | Type | Contraintes | Description |
|---|---|---|---|
| `id` | INT | PK, autoincrement | Identifiant interne |
| `identifier` | VARCHAR(128) | UNIQUE, NOT NULL, INDEX | Identifiant stable — SideStore le garde en mémoire pour marquer un article comme lu et décider d'envoyer une notification |
| `title` | VARCHAR(255) | NOT NULL | Titre de l'article |
| `caption` | TEXT | NOT NULL | Corps de l'article |
| `date` | DATETIME | NOT NULL | Date de publication (UTC) |
| `tint_color` | VARCHAR(8) | NOT NULL | Couleur hex — `""` = hérite de celle du store |
| `image_path` | VARCHAR(512) | NULL | Nom de fichier relatif dans `NEWS_DIR` (NULL = pas d'image) |
| `url` | VARCHAR(512) | NOT NULL | URL externe ouverte au clic (vide si lien vers app) |
| `app_bundle_id` | VARCHAR(255) | NOT NULL | Bundle ID de l'app liée (optionnel) |
| `notify` | INT | NOT NULL | 1 = SideStore pousse une notification push |
| `created_at` | DATETIME | NOT NULL | Date de création (UTC) |

---

## Relations

```
users          (aucune relation avec les autres tables)
settings       (aucune relation)
news           (aucune relation — app_bundle_id est une référence logique non contrainte)
apps    1 ──< versions   (cascade delete : supprimer une App supprime toutes ses Version)
```

---

## Dossiers de fichiers associés

| Dossier (`STORE_DIR/`) | Servi sur | Contenu |
|---|---|---|
| `ipas/` | `/ipas/` | Binaires `.ipa` |
| `icons/` | `/icons/` | Icônes apps + icône/header du store |
| `screenshots/` | `/screenshots/` | Screenshots apps |
| `news/` | `/news-img/` | Images des articles |

---

## Synchronisation entre environnements

| Commande | Direction | Contenu |
|---|---|---|
| `website-management sync` | prod → dev | BDD complète + fichiers (`/srv/store-prod` → `/srv/store-dev`) |
| `website-management sync-to-prod` | dev → prod | BDD complète + fichiers (`/srv/store-dev` → `/srv/store-prod`) |

> **`sync-to-prod` est irréversible** : toutes les données prod sont écrasées par dev.
> Utiliser uniquement après validation complète en dev.

---

## Credentials BDD

Les mots de passe ne sont jamais dans le code ni dans git.
Voir [credentials.md](credentials.md) pour le cycle de vie et les emplacements.
