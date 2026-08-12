"""Comparaison des numéros de version et résolution de la version publique.

La "version publique" d'une app est celle placée en tête du tableau `versions`
de source.json — donc celle que SideStore propose à l'installation. Deux modes :

- `App.public_version_id is None` → mode "Dernière version" : la version au
  numéro le plus élevé est publique et suit automatiquement les nouveaux uploads.
- `App.public_version_id` défini → version figée jusqu'à changement manuel.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .models import App, Version


def version_number_key(v: str) -> tuple[int, ...]:
    """Découpe un numéro de version en tuple d'entiers comparable.

    '442.0.0' -> (442, 0, 0). Les segments non numériques sont ignorés
    (ex. '1.2-beta' -> (1, 2)). Chaîne sans chiffre -> (0,) pour rester
    comparable sans lever d'exception.
    """
    parts = re.findall(r"\d+", v or "")
    return tuple(int(p) for p in parts) if parts else (0,)


def version_sort_key(v: "Version") -> tuple:
    """Clé de tri d'une Version, du plus ancien au plus récent.

    Ordonne d'abord sur le numéro de version, puis le build_version (départage
    deux builds d'une même version), puis la date d'upload (dernier recours).
    Trier avec reverse=True donne la plus récente en premier.
    """
    return (
        version_number_key(v.version),
        version_number_key(v.build_version),
        v.uploaded_at,
    )


def versions_desc(app: "App") -> list["Version"]:
    """Versions de l'app triées par numéro décroissant (affichage UI + feed)."""
    return sorted(app.versions, key=version_sort_key, reverse=True)


def public_version(app: "App") -> Optional["Version"]:
    """Version publique effective de l'app.

    - `public_version_id` défini et toujours présent → version figée.
    - sinon (mode "Dernière version", ou pin cassé par une suppression) → la
      version au numéro le plus haut.
    Retourne None si l'app n'a aucune version.
    """
    versions = list(app.versions)
    if not versions:
        return None
    if app.public_version_id is not None:
        for v in versions:
            if v.id == app.public_version_id:
                return v
        # Pin cassé (version supprimée hors FK DB) : on retombe sur la dernière.
    return max(versions, key=version_sort_key)
