"""Compatibilite declaree entre un type de cible produit et ses entrees.

Le cerveau ne connait aucune semantique de monde : il ne sait pas ce qu'un
`kind` veut dire, ni quels scopes vont ensemble. Cette relation est donc
**declaree par l'hote** dans le contrat (`produces[].kind_scopes`), et ce
module se contente de l'appliquer :

    "produces": [{
      "path": "data.target", "mode": "single", "scope": "target",
      "kind_param": "kind",
      "kind_scopes": {"ui": ["ui_preset"], "image": []}
    }]

- `kind_scopes[k]` liste les scopes d'entree qui peuvent produire un type `k`.
- Une liste vide est une declaration forte : aucun candidat ne peut produire
  ce type (c'est le cas d'un type purement geometrique, produit depuis une
  ancre brute, jamais depuis une cible deja matérialisee).
- Absent = non declare : le cerveau ne filtre rien, il trace seulement
  `kind_compat="undeclared"`. Aucune regression pour les hotes qui n'ont pas
  encore declare la table.

Regle de securite : on ne refuse **que** sur une declaration explicite. Une
absence de declaration n'autorise ni refus ni substitution silencieuse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# Etats de compatibilite publies dans la trace (jamais des noms d'hote).
KIND_COMPAT_DECLARED = "declared"
KIND_COMPAT_UNDECLARED = "undeclared"
KIND_COMPAT_NO_KIND = "no_kind"
KIND_COMPAT_MATCH = "match"
KIND_COMPAT_EXCLUDED = "excluded"


@dataclass(frozen=True)
class KindPolicy:
    """Politique de compatibilite pour UN appel de producteur.

    `kind` est la valeur declaree par le plan (vocabulaire hote, vide si
    aucune). `allowed_scopes` vient de la table declaree. `active` est vrai
    seulement quand l'hote a declare la relation pour ce `kind` : sans cela,
    le cerveau ne filtre pas.
    """

    kind: str = ""
    param: str = "kind"
    declared: bool = False
    allowed_scopes: Tuple[str, ...] = ()
    scope_to_kinds: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return bool(self.kind) and self.declared

    @property
    def forbids_all_candidates(self) -> bool:
        """Vrai si l'hote declare qu'aucun candidat ne produit ce type."""
        return self.active and not self.allowed_scopes

    def allows(self, scope: Any) -> bool:
        if not self.active:
            return True
        value = str(scope or "").strip()
        return value in self.allowed_scopes

    def compat(self, scope: Any) -> str:
        if not self.kind:
            return KIND_COMPAT_NO_KIND
        if not self.declared:
            return KIND_COMPAT_UNDECLARED
        return KIND_COMPAT_MATCH if self.allows(scope) else KIND_COMPAT_EXCLUDED

    def kinds_for_scope(self, scope: Any) -> Tuple[str, ...]:
        value = str(scope or "").strip()
        return tuple(self.scope_to_kinds.get(value, ()))

    def as_trace(self, scope: Any = "") -> Dict[str, Any]:
        trace: Dict[str, Any] = {
            "kind": self.kind,
            "param": self.param,
            "status": self.compat(scope),
            "declared": self.declared,
        }
        if scope:
            trace["scope"] = str(scope)
        if self.declared:
            trace["allowed_scopes"] = list(self.allowed_scopes)
        return trace


def _producer_for_kind_param(contract: Any, args: Mapping[str, Any]) -> Any:
    """Producteur dont le `kind_param` est effectivement present dans les args.

    Un contrat peut declarer plusieurs producteurs (candidats + sortie
    unique). On retient celui qui porte le parametre reellement fourni, sinon
    le premier qui declare une table : jamais de choix arbitraire silencieux.
    """
    producers = tuple(getattr(contract, "producers", ()) or ())
    if not producers:
        return None
    for producer in producers:
        param = getattr(producer, "kind_param", None) or "kind"
        if isinstance(args, Mapping) and args.get(param) not in (None, ""):
            return producer
    for producer in producers:
        if getattr(producer, "kind_scopes", None):
            return producer
    return producers[0]


def build_scope_to_kinds(contract: Any) -> Dict[str, Tuple[str, ...]]:
    """Index inverse `scope -> types qui l'acceptent`, lu dans le contrat."""
    index: Dict[str, List[str]] = {}
    for producer in tuple(getattr(contract, "producers", ()) or ()):
        table = getattr(producer, "kind_scopes", None)
        if not isinstance(table, Mapping):
            continue
        for kind_name, scopes in table.items():
            for scope in scopes:
                bucket = index.setdefault(str(scope), [])
                if str(kind_name) not in bucket:
                    bucket.append(str(kind_name))
    return {scope: tuple(kinds) for scope, kinds in index.items()}


def kind_policy(contract: Any, args: Any) -> KindPolicy:
    """Politique applicable a un appel de producteur (pure, sans journal).

    Retourne une politique inerte (`active == False`) quand l'appel ne
    declare aucun type, ou quand le contrat ne declare pas la table : le
    comportement historique est preserve a l'identique.
    """
    if contract is None or not isinstance(args, Mapping):
        return KindPolicy()
    producer = _producer_for_kind_param(contract, args)
    if producer is None:
        return KindPolicy()
    param = getattr(producer, "kind_param", None) or "kind"
    raw_kind = args.get(param)
    kind = raw_kind.strip() if isinstance(raw_kind, str) else ""
    if not kind:
        return KindPolicy(param=param)
    table = getattr(producer, "kind_scopes", None)
    scope_to_kinds = build_scope_to_kinds(contract)
    if not isinstance(table, Mapping) or not table:
        return KindPolicy(kind=kind, param=param, declared=False,
                          scope_to_kinds=scope_to_kinds)
    declared_scopes: Optional[Sequence[str]] = None
    for name, scopes in table.items():
        if str(name).strip().lower() == kind.lower():
            declared_scopes = tuple(scopes)
            break
    if declared_scopes is None:
        # La table existe mais ne parle pas de ce type : ce n'est pas une
        # declaration d'incompatibilite, c'est une declaration partielle.
        # On reste inerte plutot que de refuser sur une table muette.
        return KindPolicy(kind=kind, param=param, declared=False,
                          scope_to_kinds=scope_to_kinds)
    return KindPolicy(
        kind=kind,
        param=param,
        declared=True,
        allowed_scopes=tuple(declared_scopes),
        scope_to_kinds=scope_to_kinds,
    )


def filter_candidates(
    policy: KindPolicy, candidates: Iterable[Any]
) -> Tuple[List[Any], List[Any]]:
    """Separe les candidats compatibles des autres (ordre preserve)."""
    items = list(candidates or [])
    if not policy.active:
        return items, []
    kept: List[Any] = []
    excluded: List[Any] = []
    for item in items:
        scope = getattr(item, "scope", None)
        (kept if policy.allows(scope) else excluded).append(item)
    return kept, excluded


def mismatch_message(
    policy: KindPolicy,
    observed_scopes: Sequence[str] = (),
    *,
    producer_name: str = "",
) -> str:
    """Message actionnable : quoi est refuse, et quoi utiliser a la place.

    Le message ne nomme aucun outil ni aucune technologie : uniquement les
    valeurs declarees (`kind`, scopes, types alternatifs).
    """
    kind = policy.kind or "?"
    param = policy.param or "kind"
    observed = [s for s in dict.fromkeys(str(s) for s in observed_scopes if s)]
    lines = [
        "Type de cible incoherent : "
        f"{param}='{kind}' n'accepte aucun des candidats observes."
    ]
    if observed:
        lines.append("Scopes observes : " + ", ".join(observed) + ".")
    if policy.declared:
        allowed = ", ".join(policy.allowed_scopes) or "(aucun)"
        lines.append(f"Scopes acceptes pour '{kind}' : {allowed}.")
    alternatives: List[str] = []
    for scope in observed:
        kinds = policy.kinds_for_scope(scope)
        if kinds:
            alternatives.append(
                f"'{scope}' est accepte par {param}={list(kinds)}"
            )
    if alternatives:
        lines.append("; ".join(alternatives) + ".")
    if producer_name:
        lines.append(
            f"Choisissez {param} parmi les types acceptes, ou retirez "
            f"'{param}' pour laisser l'outil '{producer_name}' decider."
        )
    else:
        lines.append(
            f"Choisissez {param} parmi les types acceptes, ou retirez-le "
            "pour laisser l'outil decider."
        )
    return " ".join(lines)
