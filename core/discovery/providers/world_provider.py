"""
core/discovery/providers/world_provider.py
==========================================
DataProvider du monde vivant (état actuel hors registre).

Pas d'inventaire : UNE cible fictive stable ("this_world" par défaut,
surchargée par l'hôte via metadata `world_alias` du manifeste) pour
satisfaire l'exigence de cible du discours PD — sans toucher aux .md.
L'entité décrit ce qu'elle cherche en langage naturel dans sa demande.
Exposition verrouillée : solver root, planner en retry et convergence
uniquement — jamais orchestrateur/presentator/learner, jamais sous-solvers.
"""
from typing import List, Any
from core.discovery.data_provider import DataProvider

DEFAULT_WORLD_TARGET = "this_world"


class WorldProvider(DataProvider):
    """Déclare la capacité 'world' pour la Progressive Disclosure."""

    def __init__(self, runtime_state=None):
        self._runtime_state = runtime_state

    def world_alias(self) -> str:
        """Nom configurable par l'hôte (metadata `world_alias`), sinon défaut."""
        try:
            rs = self._runtime_state
            manifest = getattr(rs, "host_manifest", None) if rs else None
            meta = getattr(manifest, "metadata", None) if manifest else None
            alias = (meta or {}).get("world_alias") if isinstance(meta, dict) else None
            if alias and str(alias).strip():
                return str(alias).strip()
        except Exception:
            pass
        return DEFAULT_WORLD_TARGET

    def get_data_type(self) -> str:
        return "world"

    def get_scope_description(self) -> str:
        return (
            "État actuel du monde hors registre (ce que les outils de "
            "perception verraient MAINTENANT). À demander uniquement quand "
            "les données en main ne suffisent pas — jamais systématiquement."
        )

    def get_targets(self) -> List[str]:
        # Cible fictive stable : satisfait l'exigence de cible du discours PD
        # sans inventaire (le détail se décrit en langage naturel).
        return [self.world_alias()]

    def get_asset(self, target: str) -> Any:
        raise ValueError(
            "Le monde vivant n'a pas d'asset statique : passez par une "
            "demande de découverte (inspect_state / locate_target / verify_effect)."
        )
