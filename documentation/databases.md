# Bases de données — Structure et cycle de vie

## Connexion

Modèle mono-environnement : **une VM = un conteneur = une BDD**. La connexion (host/port/user/password/database) est saisie via l'UI `/setup/database` au premier démarrage et persistée dans `/etc/ipastore/db.json` (mode 600, owner uid 1000).

MySQL ou MariaDB, indifféremment. La BDD peut être sur la même VM (via `host.docker.internal` qui résout vers l'hôte depuis le réseau Docker bridge) ou sur un hôte distant.

Les tables sont créées automatiquement au démarrage du conteneur via `Base.metadata.create_all()` (SQLAlchemy).

**Migrations additives automatiques** : à chaque démarrage, `init_db()` compare les modèles SQLAlchemy à la BDD live et applique les opérations strictement additives nécessaires (`ALTER TABLE … ADD COLUMN`, `CREATE INDEX`). Aucun `DROP`, aucun `MODIFY` — les divergences de type sur colonnes existantes sont laissées telles quelles.

Pour les opérations destructives (DROP COLUMN, RENAME), ajouter le DDL dans `app/db.py::_legacy_migrate()`.

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
| `patch_display_name:{filename}` | Nom d'affichage personnalisé du patch `{filename}` (onglet Patch) | stem du fichier |
| `patch_description:{filename}` | Description libre du patch `{filename}` affichée dans la liste et éditable dans le détail | `""` |
| `scinsta_decrypt_url` | URL source interrogée pour le check version (editable dans l'UI SCInsta) | `https://decrypt.day/app/id389801252` |
| `scinsta_ig_version_latest` | Dernière version Instagram vue sur decrypt.day lors du dernier check | `""` |
| `scinsta_last_check_at` | ISO timestamp du dernier check decrypt.day | `""` |
| `scinsta_last_check_error` | Message d'erreur si le dernier check a échoué (ex: Cloudflare challenge) | `""` |
| `scinsta_last_build_at` | ISO timestamp du dernier build SCInsta terminé | `""` |
| `scinsta_last_build_status` | État du dernier build : `idle` / `requested` / `running` / `success` / `failed` | `""` |
| `scinsta_last_build_error` | Message d'erreur si le dernier build a échoué | `""` |
| `scinsta_last_build_ipa` | Nom de fichier de l'IPA produit par le dernier build réussi | `""` |
| `scinsta_last_build_patch` | Nom du patch appliqué au dernier build (vide si aucun) | `""` |
| `scinsta_last_build_scinsta_sha` | Short SHA du commit SCInsta cloné pour le dernier build | `""` |
| `scinsta_meta_name` | Nom pending pour l'App Instagram (consommé à la création depuis l'onglet SCInsta) | `""` |
| `scinsta_meta_developer_name` | Développeur pending | `""` |
| `scinsta_meta_subtitle` | Sous-titre pending | `""` |
| `scinsta_meta_description` | Description pending | `""` |
| `scinsta_meta_tint_color` | Teinte hex (sans `#`) pending | `""` |
| `scinsta_meta_category` | Catégorie pending (`aucune` par défaut à la création) | `""` |
| `scinsta_meta_changelog` | Override persistant de la Note de version des builds SCInsta. Si vide, auto-génération `Instagram <v> + SCInsta` | `""` |

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

## Promotion dev → prod

Mono-env : les VM dev et prod sont **disjointes**, il n'y a plus de sync physique BDD/store. Le cycle de promotion passe par git :

1. Développement sur la VM dev (branche `dev`, rolling via `website-management update`)
2. Validation dev OK → merge `dev` → `main` → push
3. Tag GitHub release (ex `v1.4.0`)
4. Sur la VM prod : `website-management update` détecte la release et déploie ; `init_db()` applique automatiquement les nouvelles tables/colonnes

Pour comparer deux schémas BDD à froid (utile au debug), le tool standalone `tools/schema-sync.py` prend `--source` et `--target` en argument et génère un plan SQL additif.

---

## Credentials BDD

Les mots de passe ne sont jamais dans le code ni dans git.
Voir [credentials.md](credentials.md) pour le cycle de vie et les emplacements.
