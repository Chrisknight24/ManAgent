# -*- coding: utf-8 -*-
"""
core/scoped_registry.py
Registre de variables hiérarchique et scopé pour les sous-plans et l'exécution de compétences (Skills).
- Écritures : Locales au scope du skill / sous-plan (n'écrase pas les variables parentes).
- Lectures : Cherche en local, puis fait un fallback transparent sur le registre parent.
- Supporte l'accès par clé, get(), 'in', itération, items(), values() et to_full_dict().
"""

from typing import Any, Dict, Optional, Iterator


class ScopedVariableRegistry(dict):
    """
    Registre de variables hiérarchique.
    Permet à un sous-plan (ex: Skill) de lire toutes les variables de la mission
    parente tout en isolant ses écritures locales pour éviter toute pollution d'état.
    """

    def __init__(self, parent_registry: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.parent_registry: Dict[str, Any] = parent_registry if parent_registry is not None else {}

    def __getitem__(self, key: str) -> Any:
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        if key in self.parent_registry:
            return self.parent_registry[key]
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        return self.parent_registry.get(key, default)

    def __contains__(self, key: object) -> bool:
        return dict.__contains__(self, key) or (key in self.parent_registry)

    def __iter__(self) -> Iterator[str]:
        local_keys = set(dict.keys(self))
        for k in dict.keys(self):
            yield k
        for k in self.parent_registry.keys():
            if k not in local_keys:
                yield k

    def keys(self):
        return [k for k in self]

    def items(self):
        return [(k, self[k]) for k in self]

    def values(self):
        return [self[k] for k in self]

    def __len__(self) -> int:
        return len(set(dict.keys(self)) | set(self.parent_registry.keys()))

    def to_full_dict(self) -> Dict[str, Any]:
        """Vue fusionnée complète pour l'inspection et les outils dépendants du registre."""
        merged = dict(self.parent_registry)
        merged.update(self)
        return merged
