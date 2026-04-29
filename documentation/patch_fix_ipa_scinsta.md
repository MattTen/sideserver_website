# `fix_ipa_scinsta.py` — wrapper SCInsta tout-en-un

Script dédié aux IPAs SCInsta (Instagram tweaké). Applique l'intégralité des corrections nécessaires pour le sideload via SideStore et **remplace l'IPA d'origine en place** par la version patchée.

> Pour comprendre le détail technique du patch Mach-O sous-jacent, voir [`patch_fix_ipa.md`](patch_fix_ipa.md).

---

## Différence avec `fix_ipa.py`

| Étape | `fix_ipa.py` | `fix_ipa_scinsta.py` |
|---|---|---|
| Patch des Mach-O (layout __LINKEDIT, thin FAT, strip signature) | ✅ | ✅ (logique identique) |
| Suppression du dossier `Extensions/` | ❌ | ✅ |

Le cœur du patch est identique. Le wrapper ajoute la suppression de `Extensions/`, spécifique au cas SCInsta.

---

## Pourquoi supprimer `Extensions/`

iOS 15+ introduit un nouveau dossier de bundle `<App>.app/Extensions/` pour certains types d'extensions, en plus du classique `<App>.app/PlugIns/`.

**SideStore a un angle mort** : il ne réécrit les bundle identifiers que pour `PlugIns/*.appex`. Les `Extensions/*.appex` gardent leur bundle ID d'origine (ex: `com.burbn.instagram.lockscreencamera`), qui ne commence pas par le nouveau préfixe `com.burbn.instagram.<TEAMID>` appliqué au parent lors du resign.

iOS refuse alors l'install avec :

```
IXErrorDomain Code=8
Attempted to set app extension placeholder promise with bundle ID
com.burbn.instagram.lockscreencamera that does not match required prefix
of com.burbn.instagram.<TEAMID>. for parent
Mismatched bundle IDs.
```

C'est observé dans les logs minimuxer à l'étape `Installing app for bundle ID: com.burbn.instagram` après que ldid a signé proprement.

### Ce qui est perdu

Pour SCInsta v425, `Extensions/` contient uniquement `InstagramExtensionLockScreenCamera.appex` — c'est le **widget Lock Screen iOS 16+ qui ouvre la Story Camera depuis l'écran verrouillé**.

| Aspect | Impact |
|---|---|
| App principale | aucun |
| Tweak SCInsta (anti-marque sponso, screenshots stories…) | aucun |
| Stories, DMs, feed | aucun |
| Widget caméra Story sur écran verrouillé | **perdu** (raccourci de confort uniquement) |
| Risque de ban Instagram | aucun (le widget ne communique pas avec les serveurs Meta, juste un raccourci local WidgetKit) |

Les utilisateurs SCInsta cherchent les fonctions du tweak, pas un raccourci Lock Screen natif Instagram — compromis acceptable.

### Pourquoi pas une autre solution

| Solution | Pourquoi pas |
|---|---|
| Déplacer `Extensions/*.appex` → `PlugIns/` | Certaines extensions iOS 15+ (notamment WidgetKit Lock Screen) ne fonctionnent QUE depuis `Extensions/`. Le déplacement peut casser l'extension au runtime au lieu d'au moment de l'install |
| Réécrire les bundle IDs nous-mêmes | Impossible : on ne connaît pas le Team ID de l'utilisateur final, c'est SideStore qui l'assigne au moment du sideload |
| Patcher SideStore pour qu'il gère `Extensions/` | Vraie solution upstream. Pas planifiée côté mainteneurs. En attendant, suppression côté IPA |

---

## Usage

```bash
pip install lief
python3 fix_ipa_scinsta.py -s /chemin/vers/SCInsta.ipa
```

Le fichier est **remplacé en place** par la version patchée.

### Sortie attendue

```
[+] extraction de /chemin/vers/SCInsta.ipa
[+] bundle: Instagram.app
[+] Extensions/ supprime (widget Lock Screen Camera, non gere par SideStore)
    patched Instagram
    patched Frameworks/SCInsta.dylib
    ...
[+] 19 binaires patches
[+] repack...
[+] remplace en place: /chemin/vers/SCInsta.ipa
```

---

## Intégration dans IPA Magasin

Exécutable depuis l'onglet **Patch** de l'interface admin (voir [`patch_fix_ipa.md`](patch_fix_ipa.md#intégration-dans-ipa-magasin)).

---

## Limitations

Mêmes limitations que `fix_ipa.py` (voir [`patch_fix_ipa.md`](patch_fix_ipa.md#limitations-connues)), plus :

- **Conçu pour SCInsta** — la suppression aveugle de `Extensions/` n'est pas appropriée pour tous les tweaks. Pour un autre tweak où l'extension est essentielle, utiliser `fix_ipa.py`
- **Pas de backup automatique** — l'IPA d'origine est écrasé. À l'utilisateur de copier avant si besoin

## Permissions et atomicité

Même mécanisme que `fix_ipa.py` (cf. [patch_fix_ipa.md § Repack et écrasement de l'IPA d'origine](patch_fix_ipa.md#repack-et-écrasement-de-lipa-dorigine)) : `tempfile.mkstemp` dans le dossier de destination + `os.chmod(0o644)` + `os.replace` atomique. Permet de patcher en place une IPA produite par le scinsta-builder (owned `root:root`) depuis le web container (uid `ipastore`).
