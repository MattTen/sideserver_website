# Onglet SCInsta Builder

Doc de référence de l'onglet **SCInsta** (route `/scinsta`). Permet à l'admin de produire en un clic une IPA Instagram patchée avec [SoCuul/SCInsta](https://github.com/SoCuul/SCInsta), à partir d'une IPA Instagram officielle déposée manuellement.

---

## 1. Flux utilisateur

Organisation visuelle de la page en 4 zones :

### Carte "Instagram (decrypt.day)"
Toute l'info et les interactions liées à la **version** :
- `Version intégrée au store` — lue depuis la BDD (dernière `Version` de `com.burbn.instagram` par `uploaded_at desc`, **pas** depuis une clé settings).
- `Dernière version sur decrypt.day` — lue via `POST /scinsta/check` (curl_cffi, cf. §4).
- Un badge (`à jour` / `MAJ dispo` / `non vérifié` / `à intégrer`) compare les deux.
- Un **banner inline** affiche le résultat du dernier check : "Interrogation de decrypt.day…", "Déjà à jour.", "Nouvelle version disponible : X", ou l'erreur. Placé dans cette carte (pas dans la carte de build) parce que c'est là qu'il a du sens sémantique.
- Dernier check : timestamp + éventuelle erreur.
- **Source : `<url>` [Modifier]** — l'URL interrogée est éditable. Utile si decrypt.day change de slug, tombe, ou si l'admin veut pointer vers un miroir. Clic sur `Modifier` → input + boutons Enregistrer/Annuler.

### Carte "1. Upload de l'IPA Instagram"
Dropzone HTML5 (drag-and-drop ou clic) qui streame le fichier vers `/scinsta/upload` via XHR avec barre de progression. Le fichier est écrit atomiquement dans `/etc/ipastore/scinsta-upload-<env>.ipa` (`.tmp` puis rename). Une alerte "IPA prête : V{version}" s'affiche quand un upload est en attente (la version est lue depuis `CFBundleShortVersionString` de l'Info.plist), avec un bouton `Supprimer`.

### Carte "2. Patch optionnel"
`<select>` peuplé dynamiquement depuis les scripts auto-découverts dans `patch/` (mêmes que l'onglet Patch). Si sélectionné, le script sera appliqué par le builder **après** l'injection SCInsta.

### Carte "3. Lancer le build"
Bouton `Builder maintenant`. Désactivé tant qu'aucun upload n'est prêt ou qu'un build est déjà en cours. Le statut affiche la progression (`clone`, `build`, `inject`, `patch`, `deploy`) via polling `/scinsta/status` toutes les 3s.

Bouton `Annuler le build` (visible uniquement pendant un build, style `btn-danger`). Écrit `/etc/ipastore/scinsta-build-cancel-<env>`, déclenche le path unit `ipastore-scinsta-cancel@<env>.path` → service host-side qui `docker kill --signal=SIGTERM scinsta-builder-<env>` puis écrit un `scinsta-build-result-<env>` avec status `failed` et message "Build annule par l'admin". Le watcher lifespan bascule alors le state en `failed` côté UI. Utilisé en cas de build qui boucle indéfiniment (Theos bloqué, réseau mort…).

Un `<pre>` sous le bouton affiche la **sortie temps réel** du conteneur builder (git clone, Theos, cyan, ipapatch, patch). Implémentation :
- Côté builder (`tools/scinsta-builder/build.py`) : `_install_log_tee()` ouvre `/etc/ipastore/scinsta-build-log-<env>.txt` en line-buffered et redirige `sys.stdout`/`sys.stderr` vers un `_Tee(sys.__stdout__, fh)`. Chaque ligne arrive dans le fichier dès qu'elle est imprimée, même pendant `bash ./build.sh sideload` (qui tourne plusieurs minutes).
- Côté web : poll `GET /scinsta/logs?offset=N` toutes les 1.5s pendant un build, envoie `next_offset` pour ne recevoir que le delta, append au `<pre>` côté client. Option `auto-scroll` cochée par défaut.
- Au chargement, un poll unique récupère le log du **dernier build** (success/failed) : reste consultable après coup.

---

## 2. Pipeline complet

```
[UI] POST /scinsta/build (patch form field)
   ↓ ecrit /etc/ipastore/scinsta-build-requested-<env>   (flag JSON)
[hote] ipastore-scinsta-build@<env>.path  (systemd PathExists=)
   ↓
[hote] ipastore-scinsta-build@<env>.service
   ↓ lance `website-management <env>-scinsta-build`
   ↓ docker build + docker run --rm avec mounts :
      -v /etc/ipastore:/etc/ipastore
      -v /srv/store-<env>:/srv/store
      -v /opt/sideserver-<env>:/opt/source:ro   (pour patch/*.py)
[conteneur builder] tools/scinsta-builder/build.py :
   1. Clone fresh `SoCuul/SCInsta` main (--recursive --depth 1)
   2. Copie upload en `packages/com.burbn.instagram.ipa`
   3. `bash ./build.sh sideload` (Theos compile + cyan inject + ipapatch)
   4. Si patch choisi : `python3 patch/<file>.py -s <ipa_out>`
   5. Renomme en `SCInsta-ig<ver>-sc<sha>-<ts>.ipa` → /srv/store/ipas/
   6. Ecrit scinsta-build-result-<env> (JSON)
[conteneur web] _scinsta_result_loop (poll 5s dans lifespan) :
   - Consomme le result file
   - integrate_build_result : cree l'App si absente + Version (pas d'article news auto)
   - Supprime l'upload consume
```

Toutes les communications hôte ↔ conteneur passent par `/etc/ipastore/` (monté en volume dans les deux).

---

## 3. Fichiers partagés

Dans `/etc/ipastore/` :

| Fichier                              | Direction     | Contenu                                                  |
|--------------------------------------|---------------|----------------------------------------------------------|
| `scinsta-upload-<env>.ipa`           | web → builder | IPA Instagram officielle (rename atomique via `.tmp`)    |
| `scinsta-build-requested-<env>`      | web → systemd | Flag JSON `{requested_at, patch}`                        |
| `scinsta-build-progress-<env>`       | builder → web | JSON `{step: "clone|build|inject|patch|deploy"}`         |
| `scinsta-build-result-<env>`         | builder → web | JSON final (consommé puis supprimé par le watcher)       |
| `scinsta-build-log-<env>.txt`        | builder → web | Tee stdout/stderr du builder (lu incrémentalement via `/scinsta/logs?offset=N`) |
| `scinsta-build-cancel-<env>`         | web → systemd | Flag JSON déclenchant `ipastore-scinsta-cancel@<env>.service` qui `docker kill` le conteneur |

---

## 4. Bypass Cloudflare (check de version)

decrypt.day est protégé par Cloudflare. Constat au déploiement :

- `urllib` avec User-Agent Chrome → **HTTP 403** systématique.
- `cloudscraper` → HTTP 403 (les challenges JS ne suffisent pas quand le TLS est reconnu comme Python).
- `curl_cffi` avec `impersonate="chrome"` → **HTTP 200**, microdata `softwareVersion` accessible.

Cloudflare fingerprint le **TLS ClientHello** (JA3/JA4) : Python+OpenSSL produit une signature distinctive, Chrome en produit une autre, et le WAF filtre sur la base de cette empreinte, avant même de regarder les headers applicatifs. `curl_cffi` utilise `libcurl-impersonate` qui réplique exactement le ClientHello de Chrome — indistinguable au niveau réseau.

### Implémentation (`app/scinsta.py`)

Chaîne de fallback avec détection de challenge affinée :

1. `_fetch_with_curl_cffi(url)` — essaie `chrome`, `chrome131`, `safari17_0`, `firefox133` dans l'ordre. Résiste aux MAJ WAF qui pourraient un jour flag une impersonation précise.
2. `_fetch_with_urllib(url)` — fallback seulement si `curl_cffi` n'est pas installé (dev local sans rebuild).
3. Détection de challenge par markers : `just a moment`, `attention required`, `checking your browser`, `cf-chl-bypass`. Les markers `challenge-platform` et `cf-beacon` sont **exclus** volontairement car ils apparaissent aussi dans des réponses 200 légitimes (scripts CF embarqués pour la télémétrie).
4. Extraction du numéro de version : regex `itemprop=["\']softwareVersion["\'][^>]*>([^<]+)<` (microdata schema.org).

### Limite connue

Seule la **page HTML** (numéro de version) est récupérée automatiquement. Le **bouton de téléchargement** de l'IPA sur decrypt.day est derrière un challenge interactif Turnstile (vrai JS challenge, pas juste TLS) — non bypass-able proprement. D'où le flux "upload manuel" : l'admin fait le téléchargement dans un vrai navigateur puis dépose dans le dropzone.

---

## 5. URL source modifiable

Valeur par défaut : `https://decrypt.day/app/id389801252` (Instagram).

L'admin peut la remplacer à chaud via l'UI (bouton `Modifier` à côté de "Source :"). Stockée dans la clé settings `scinsta_decrypt_url`. Lue par :
- `run_check(db)` qui passe l'URL à `fetch_instagram_version_online(url)`
- `get_state(db)` qui expose `decrypt_url` dans le JSON `/scinsta/status`

Validation côté route : schéma `http://` ou `https://` obligatoire, rien d'autre (on laisse l'admin assumer — la page cible doit contenir le microdata `softwareVersion` pour que le parser trouve la version).

---

## 6. Routes API

| Méthode | Path                  | Rôle                                                         |
|---------|-----------------------|--------------------------------------------------------------|
| GET     | `/scinsta`            | Page principale (rendu Jinja avec state initial)             |
| GET     | `/scinsta/status`     | État JSON (appel lors du polling pendant un build)           |
| GET     | `/scinsta/logs?offset=N` | Delta du log builder à partir de l'offset (poll temps réel) |
| POST    | `/scinsta/check`      | Lance le check version (curl_cffi vers URL source)           |
| POST    | `/scinsta/source`     | Met à jour `scinsta_decrypt_url` (form field `url`)          |
| POST    | `/scinsta/upload`     | Stream l'IPA vers `scinsta-upload-<env>.ipa`                 |
| POST    | `/scinsta/clear-upload` | Supprime l'upload en attente                               |
| POST    | `/scinsta/build`      | Écrit le flag-file de build (form field `patch` optionnel)   |
| POST    | `/scinsta/cancel`     | Écrit le flag-file de cancel (kill le conteneur builder)     |

Toutes les routes (sauf l'éventuelle visite) exigent auth admin via `Depends(require_user)`.

---

## 7. Clés settings utilisées

Stockées en BDD dans la table `settings` (voir [databases.md](databases.md)). Aucune migration requise, valeurs créées à la volée.

| Clé                                | Valeur                                                          |
|------------------------------------|-----------------------------------------------------------------|
| `scinsta_decrypt_url`              | URL source pour le check version (défaut decrypt.day IG)        |
| `scinsta_ig_version_latest`        | Dernière version vue sur decrypt.day                            |
| `scinsta_last_check_at`            | ISO timestamp du dernier check                                  |
| `scinsta_last_check_error`         | Message si check échoué                                         |
| `scinsta_last_build_at`            | ISO timestamp du dernier build terminé                          |
| `scinsta_last_build_status`        | `idle` / `requested` / `running` / `success` / `failed`         |
| `scinsta_last_build_error`         | Message si build échoué                                         |
| `scinsta_last_build_ipa`           | Filename du dernier IPA produit                                 |
| `scinsta_last_build_patch`         | Filename du patch appliqué                                      |
| `scinsta_last_build_scinsta_sha`   | Short SHA du commit SCInsta cloné                               |

> `scinsta_ig_version_deployed` n'existe **pas** — la version déployée est toujours lue depuis la table `versions` directement (source de vérité unique : la BDD).

---

## 8. Intégration post-build

`integrate_build_result(db, result)` dans `app/scinsta.py` :

1. Crée l'App `com.burbn.instagram` **si absente uniquement** (tint `E1306C`, catégorie `social`, icône extraite de l'Info.plist de l'IPA). Si l'App existe déjà, ses métadonnées (`developer_name`, `description`, `subtitle`, icône…) sont **figées** et ne sont **pas** ré-écrites à chaque build — l'admin reste maître du contenu saisi dans l'onglet Applications.
2. Insère (ou met à jour en place si déjà présente) la `Version` avec :
   - `version = <IG version>` (ex: `425.0.0`) — lu depuis `CFBundleShortVersionString` de l'Info.plist.
   - `build_version = <CFBundleVersion>` (ex: `933996394`) — lu depuis l'Info.plist de l'IPA final. **Doit** correspondre au `CFBundleVersion` réel ; sinon SideStore refuse l'install avec un mismatch "version trouvée dans l'IPA ≠ version du store". Le short SHA SCInsta n'est **plus** utilisé comme `build_version` (cassait l'install), il reste tracé dans le setting `scinsta_last_build_scinsta_sha`.
   - `changelog = "Instagram <v> + SCInsta"` (sobre ; rien sur le patch ou le SHA pour ne pas polluer l'affichage SideStore).
   - **Rebuilds du même CFBundleVersion** : la ligne Version existante est remplacée en place (nouveau `ipa_filename`, `size`, `sha256`, `uploaded_at` bumpé) et l'ancien IPA est supprimé du disque.
   - **Purge des lignes obsolètes** : toutes les autres Versions pour la même App + `version` IG mais avec un `build_version` différent (ex : lignes historiques créées avec short SHA, ou upload vanille pré-SCInsta) sont supprimées, IPAs inclus — un seul IPA par `(app, version, CFBundleVersion)` reste en BDD.
3. **Aucun article news automatique** — l'admin rédige manuellement depuis l'onglet News s'il veut annoncer le build (et déclencher la notif push via `notify=1`).
4. Supprime `scinsta-upload-<env>.ipa` pour que l'UI n'affiche plus "upload en attente".

---

## 9. Fichiers du repo

| Fichier                                           | Rôle                                                 |
|---------------------------------------------------|------------------------------------------------------|
| `app/scinsta.py`                                  | Logique : check version, upload, flag, intégration  |
| `app/routes/scinsta.py`                           | Routes FastAPI                                       |
| `templates/scinsta.html`                          | UI (Jinja + JS vanilla)                              |
| `templates/_icons/instagram.svg`                  | Icône de la nav sidebar                              |
| `tools/scinsta-builder/Dockerfile`                | Image Theos + cyan + ipapatch + lief                 |
| `tools/scinsta-builder/build.py`                  | Pipeline du conteneur one-shot                       |
| `tools/scinsta-builder/README.md`                 | Doc I/O du conteneur                                 |
| `deploy/systemd/ipastore-scinsta-build@.path`     | Watcher du flag de build                             |
| `deploy/systemd/ipastore-scinsta-build@.service`  | Runner du build                                      |
| `deploy/systemd/ipastore-scinsta-cancel@.path`    | Watcher du flag de cancel                            |
| `deploy/systemd/ipastore-scinsta-cancel@.service` | Runner du cancel (docker kill + result failed)       |

---

## 10. Dépendances Python

Dans `requirements.txt` :

- `curl_cffi>=0.7.4` — **indispensable**. Sans ça, le check de version est cassé (Cloudflare block systématique).
- `lief>=0.16` — utilisé par les scripts `patch/*.py` (post-injection).
