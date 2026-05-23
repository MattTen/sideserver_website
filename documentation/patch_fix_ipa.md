# `fix_ipa.py` — patch IPA générique pour SideStore

Script Python qui corrige les IPA (iOS 15+) qui échouent à l'install sous SideStore/AltStore avec l'assertion ldid `end >= size - 0x10`.

> Pour le wrapper dédié SCInsta (qui ajoute la suppression de `Extensions/`), voir [`patch_fix_ipa_scinsta.md`](patch_fix_ipa_scinsta.md).

---

## Contexte

SCInsta, EeveeSpotify, Spotube, LiveContainer, Kodi tweaké et plein d'autres IPA modernes (iOS 15+) échouent à l'install via SideStore avec :

```
AltSign.Error 0
ldid.cpp(1461): _assert(): end >= size - 0x10
Source File: ALTSigner.mm
Source Line: 497
```

Bug référencé dans [plusieurs issues](https://github.com/altstoreio/AltStore/issues/1660) "not planned" côté mainteneurs. Les packageurs d'IPA doivent s'en débrouiller.

## Cause racine

### Le rôle de `ldid`

SideStore/AltStore utilisent `ldid` (forké par rileytestut) pour re-signer les Mach-O binaires d'une IPA avant de la sideloader. Avant de réécrire la signature, `ldid::Allocate()` valide le layout du binaire.

### La fonction fautive

Dans `ldid.cpp` lignes 1449–1464 :

```cpp
size_t size;
if (signature == NULL)
    size = mach_header.GetSize();               // taille du fichier Mach-O
else {
    size = mach_header.Swap(signature->dataoff); // décalage de la signature
    _assert(size <= mach_header.GetSize());
}

if (symtab != NULL) {
    auto end(mach_header.Swap(symtab->stroff) + mach_header.Swap(symtab->strsize));
    if (symtab->stroff != 0 || symtab->strsize != 0) {
        _assert(end <= size);                    // ligne 1460
        _assert(end >= size - 0x10);             // ligne 1461 ← ASSERT QUI PÈTE
        size = end;
    }
}
```

Traduit : **la fin du symbol string table (`stroff + strsize`) doit se trouver dans les 16 derniers octets avant la signature (ou avant la fin du fichier si pas de signature)**.

### Le vrai problème : layout `__LINKEDIT` iOS 15+

L'ABI Apple historique voulait que la section `__LINKEDIT` d'un Mach-O se termine par :

```
[ rebase/bind/lazy/export info ]
[ function starts ]
[ data in code ]
[ symbol table (nlist[]) ]
[ indirect symbol table ]
[ string table ]           ← toujours en dernier
[ code signature ]         ← si présente, à la fin du fichier
```

Dans ce layout, `stroff + strsize` pointe exactement au début de la signature (ou à la fin du fichier si non signé). L'assertion passe trivialement.

**iOS 15+ a changé ce layout.** Les nouveaux link commands `LC_DYLD_CHAINED_FIXUPS` et `LC_DYLD_EXPORTS_TRIE` (fix-up chaining pour le dyld moderne) sont **ajoutés APRÈS le string table** :

```
[ symbol table ]
[ indirect symbol table ]
[ string table ]
[ DYLD_CHAINED_FIXUPS ]    ← NOUVEAU, après strtab
[ DYLD_EXPORTS_TRIE ]      ← NOUVEAU, après strtab
[ code signature ]
```

Résultat : `stroff + strsize` finit plusieurs Ko, voire plusieurs Mo, avant la fin du fichier. Pour le binaire principal de SCInsta/Instagram v425, le gap est de **~2 Mo**.

### Pourquoi pas un bug ldid à fixer upstream

ldid est en mode maintenance, les forks SideStore/AltSign utilisent une base figée. **La fix doit venir côté IPA.**

---

## Comment le patch marche

### Stratégie : re-sérialiser les Mach-Os proprement

Au lieu de hacker le layout existant, on **re-sérialise** chaque Mach-O avec [LIEF](https://github.com/lief-project/LIEF) — une bibliothèque qui reconstruit un Mach-O canonique depuis sa représentation en mémoire.

LIEF lors du `binary.write()` :
1. Place le string table à la toute fin de `__LINKEDIT` (layout traditionnel)
2. Recalcule tous les offsets dans les load commands
3. Réduit `__LINKEDIT.filesize` au strict nécessaire
4. Aligne correctement

Résultat : `stroff + strsize == end of __LINKEDIT == end of file` (gap = 0). L'assertion ldid passe.

### Étapes du script

Pour chaque binaire Mach-O ou FAT détecté dans le bundle `.app` :

1. **Parse** avec `lief.MachO.parse()`
2. **Thin FAT → arm64** : si le binaire est un Universal Binary (2+ architectures), on garde uniquement la slice `arm64` (cpu_subtype = 0, pas arm64e). Évite l'autre bug [`ldid` ne gérant pas les FAT binaires](https://github.com/altstoreio/AltStore/issues/1584)
3. **Strip signature** : supprime le load command `LC_CODE_SIGNATURE` existant (ldid en regénère une propre au sideload)
4. **Write** : LIEF re-sérialise le Mach-O avec le layout canonique, écrase le fichier source

### Repack et écrasement de l'IPA d'origine

Le repack ZIP utilise un `tempfile.mkstemp(dir=os.path.dirname(ipa_path))` — **dans le même dossier que la destination**, pas dans `/tmp` :

```python
out_dir = os.path.dirname(ipa_path) or '.'
tmp_fd, tmp_ipa = tempfile.mkstemp(prefix='.fix-ipa-', suffix='.ipa.tmp', dir=out_dir)
os.close(tmp_fd)
repack_ipa(extract_dir, tmp_ipa)
os.chmod(tmp_ipa, 0o644)        # mkstemp cree en 0600 -> world-readable
os.replace(tmp_ipa, ipa_path)   # rename(2) atomique, meme filesystem
```

Pourquoi pas `shutil.move(/tmp/...)` :
1. `/tmp` (du conteneur) ≠ `/srv/store/ipas/` (volume monté) → `os.rename` lève `EXDEV` (cross-device link), `shutil.move` retombe sur copie non-atomique.
2. Le fallback copie ouvre la destination en `'wb'` → fail si le fichier existant a un owner différent (cas typique : IPA produite par scinsta-builder en root, patch tourne en uid `ipastore`).

`os.replace` (= `rename(2)`) sur le même filesystem est atomique ET POSIX-permissive : peut overwrite un fichier dont on n'est pas owner tant que le parent dir est writable.

### Dépendances

- **Python 3.10+**
- **LIEF** ≥ 0.17 (`pip install lief`)

LIEF est un wheel wrapping une lib C++. Pur pip, pas de compilation côté utilisateur.

---

## Usage

```bash
python3 fix_ipa.py -s /chemin/vers/app.ipa
```

Le fichier est **remplacé en place** par la version patchée. Aucune copie de sauvegarde n'est créée — fais-en une avant si besoin :

```bash
cp app.ipa app.ipa.bak
python3 fix_ipa.py -s app.ipa
```

### Sortie attendue

```
[+] extraction de /chemin/vers/app.ipa
[+] bundle: Instagram.app
    patched Instagram
    patched Frameworks/SCInsta.dylib
    ...
[+] 19 binaires patches
[+] repack...
[+] remplace en place: /chemin/vers/app.ipa
```

---

## Intégration dans IPA Magasin

Le script est exécutable depuis l'onglet **Patch** de l'interface admin. Le dossier `patch/` est scanné au boot du conteneur et chaque `.py` à la racine devient un patch sélectionnable. Le workflow depuis l'UI :

1. Onglet **Patch** → sélection du patch
2. Sélection d'une app + sa version dans la liste déroulante
3. Clic « Patcher » → le script est exécuté en subprocess sur le fichier IPA (`/srv/store-{env}/ipas/{filename}`)
4. Taille et sha256 sont recalculés et mis à jour en BDD

Contrainte : chaque patch doit respecter la signature CLI `script.py -s /chemin/vers/app.ipa` et écraser l'IPA en place.

---

## Limitations connues

### 1. Dossier `Extensions/` non géré par SideStore

iOS 15+ introduit un nouveau dossier de bundle `<App>.app/Extensions/` pour certains types d'extensions (LockScreen Camera, etc.).

SideStore ne réécrit les bundle identifiers que pour `PlugIns/*.appex`. Les `Extensions/*.appex` gardent leur bundle ID d'origine. iOS refuse alors l'install avec :

```
IXErrorDomain Code=8
Attempted to set app extension placeholder promise with bundle ID ...
that does not match required prefix of ...
Mismatched bundle IDs.
```

Ce script ne gère pas ce cas (patch générique) — voir `fix_ipa_scinsta.py` qui supprime `Extensions/` automatiquement pour SCInsta.

### 2. Thin vers arm64 pur

Si une dépendance du tweak nécessite strictement arm64e (pointer authentication, iPhone A12+), la slice thinnée arm64 peut faire crasher l'app au runtime. À date, thin arm64 marche pour la grande majorité des cas.

### 3. Pas de modification des `Info.plist`, entitlements, provisioning profiles

SideStore s'en occupe au moment du sideload. Le patch ne fait qu'assainir les Mach-Os pour que ldid puisse les re-signer.

---

## Historique des tentatives (debug log)

1. **Hypothèse 1 : FAT binaries** — certains .dylib étaient Universal. Thinnés → n'a pas suffi.
2. **Hypothèse 2 : signatures résiduelles** — stripped LC_CODE_SIGNATURE + truncated __LINKEDIT. N'a pas suffi.
3. **Diagnostic précis via source ldid** — l'assertion concerne le string table, pas la signature.
4. **Hypothèse 3 : étendre `strsize`** — passe l'assertion mais SideStore rejette (ldid écrit la signature par-dessus les chained fixups).
5. **Solution 4 : re-layout canonique via LIEF** — ✅ ldid passe, SideStore signe, l'install se lance.

---

## Références

- ldid source (rileytestut/AltSign fork) : <https://github.com/rileytestut/ldid>
- Issues connues :
  - <https://github.com/altstoreio/AltStore/issues/1284>
  - <https://github.com/altstoreio/AltStore/issues/1584>
  - <https://github.com/altstoreio/AltStore/issues/1660>
  - <https://github.com/SideStore/SideStore/issues/818>
  - <https://github.com/LiveContainer/LiveContainer/issues/134>
- LIEF : <https://lief.re/>
