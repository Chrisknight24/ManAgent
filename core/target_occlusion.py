"""Occultation **declaree** par l'hote, appliquee par le cerveau.

Separation des responsabilites :

- l'hote sait ce que son monde contient (fenetres, popups, z-order). C'est a
  lui d'affirmer qu'une cible est cachee, ou qu'elle en recouvre une autre ;
- le cerveau ne devine rien : aucune geometrie, aucun nom de champ, aucune
  heuristique. Il lit une **declaration** et l'applique.

Declaration (par producteur, dans le contrat) :

    "produces": [{
      "path": "data.candidates", "mode": "candidates", "scope": "ui_preset",
      "fields": {
        "ref": "id", "description": "name",
        "attributes": {
          "blocked": "covered_by",     <- publie par l'hote
          "covers":  "occludes"        <- publie par l'hote
        }
      },
      "occlusion": {
        "blocked_by": ["blocked"],     <- attribut qui dit « je suis cache »
        "covers":     ["covers"]       <- attribut qui dit « je recouvre »
      }
    }]

- `blocked_by` : si l'attribut declare est non vide/non faux, la cible est
  cachee -> refus deterministe (`mismatch`), sans appel de modele.
- `covers` : la cible recouvre autre chose que ce que son libelle annonce ->
  jamais `match` (`uncertain`), la preuve positive manque.

Aucun nom d'attribut n'est code ici : il vient de `occlusion.blocked_by` /
`occlusion.covers`. Changer de vocabulaire (autre hote, autre langue) ne
demande aucune modification du cerveau.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Tuple

VERDICT_BLOCKED = "mismatch"
VERDICT_COVERS = "uncertain"
_MAX_VALUE_CHARS = 160


@dataclass(frozen=True)
class OcclusionFinding:
    """Fait d'occultation declare, normalise pour le gate."""

    blocked: Tuple[Tuple[str, str], ...] = ()
    covers: Tuple[Tuple[str, str], ...] = ()

    @property
    def verdict(self) -> str:
        if self.blocked:
            return VERDICT_BLOCKED
        if self.covers:
            return VERDICT_COVERS
        return ""

    @property
    def reason(self) -> str:
        if self.blocked:
            return _describe(self.blocked, "cible declaree cachee")
        if self.covers:
            return _describe(self.covers, "cible declaree recouvrante")
        return ""

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self.blocked:
            out["blocked_by"] = [{"field": name, "value": value} for name, value in self.blocked]
        if self.covers:
            out["covers"] = [{"field": name, "value": value} for name, value in self.covers]
        return out


def _describe(items: Iterable[Tuple[str, str]], label: str) -> str:
    parts = [f"{name}={value}" for name, value in items]
    return f"{label} : " + ", ".join(parts[:4])


def _flatten_strings(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for item in value:
            out.extend(_flatten_strings(item))
        return out
    if isinstance(value, Mapping):
        out = []
        for item in value.values():
            out.extend(_flatten_strings(item))
        return out
    return []


def _truthy(value: Any) -> bool:
    """Une declaration non vide/non fausse est un fait, pas une opinion."""
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() not in {"false", "0", "no", "non"}
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return bool(value)


def _short(value: Any) -> str:
    text = ", ".join(_flatten_strings(value)) if not isinstance(value, str) else value
    text = " ".join(str(text).split())
    return text[:_MAX_VALUE_CHARS]


def _declared_names(producer: Any, key: str) -> Tuple[str, ...]:
    """Noms d'attributs declares sous `occlusion.<key>` (jamais ailleurs)."""
    declaration = getattr(producer, "occlusion", None)
    if not isinstance(declaration, Mapping):
        return ()
    raw = declaration.get(key) or ()
    return tuple(str(name) for name in raw if str(name).strip())


def occlusion_policy_for(contract: Any, scope: str = "") -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Fusionne les declarations d'occultation applicables a un scope.

    Un producteur est applicable s'il produit ce scope (directement ou via
    `usable_as`). Aucun producteur applicable = pas de declaration : le gate
    ne change rien.
    """
    blocked: List[str] = []
    covers: List[str] = []
    producers = tuple(getattr(contract, "producers", ()) or ())
    applicable = []
    for producer in producers:
        declared = bool(
            _declared_names(producer, "blocked_by")
            or _declared_names(producer, "covers")
        )
        if not declared:
            continue
        producer_scope = getattr(producer, "scope", None)
        usable = tuple(getattr(producer, "usable_as", ()) or ())
        if not scope or producer_scope == scope or scope in usable:
            applicable.append(producer)
    if not applicable:
        # Aucune declaration specifique a ce scope : on retombe sur les
        # producteurs qui declarent l'occultation, sans l'inventer pour autant.
        for producer in producers:
            if _declared_names(producer, "blocked_by") or _declared_names(producer, "covers"):
                applicable.append(producer)
    for producer in applicable:
        for name in _declared_names(producer, "blocked_by"):
            if name not in blocked:
                blocked.append(name)
        for name in _declared_names(producer, "covers"):
            if name not in covers:
                covers.append(name)
    return tuple(blocked), tuple(covers)


def declared_occlusion(contract: Any, entry: Any) -> OcclusionFinding:
    """Lit les attributs declares sur une cible (aucune inference)."""
    if entry is None:
        return OcclusionFinding()
    scope = str(getattr(entry, "scope", "") or "")
    blocked_names, covers_names = occlusion_policy_for(contract, scope)
    if not blocked_names and not covers_names:
        return OcclusionFinding()
    attributes = getattr(entry, "attributes", None)
    if not isinstance(attributes, Mapping):
        return OcclusionFinding()
    blocked: List[Tuple[str, str]] = []
    covers: List[Tuple[str, str]] = []
    for name in blocked_names:
        value = attributes.get(name)
        if _truthy(value):
            blocked.append((name, _short(value)))
    for name in covers_names:
        value = attributes.get(name)
        if _truthy(value):
            covers.append((name, _short(value)))
    return OcclusionFinding(blocked=tuple(blocked), covers=tuple(covers))
