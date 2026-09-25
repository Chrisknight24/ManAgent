"""
core/discovery/providers/world_provider.py
==========================================
DataProvider du monde vivant (état actuel hors registre).

Le monde n'a pas d'inventaire : pas de cibles statiques. L'entité précise
ce qu'elle cherche dans sa demande (target libre). Exposition verrouillée :
solver root, planner en retry et convergence uniquement — jamais
orchestrateur/presentator/learner, jamais sous-solvers.
"""
from typing import List, Any
from core.discovery.data_provider import DataProvider


class WorldProvider(DataProvider):
    """Déclare la capacité 'world' pour la Progressive Disclosure."""

    def get_data_type(self) -> str:
        return "world"

    def get_scope_description(self) -> str:
        return (
            "État actuel du monde hors registre (ce que les outils de "
            "perception verraient MAINTENANT). À demander uniquement quand "
            "les données en main ne suffisent pas — jamais systématiquement."
        )

    def get_targets(self) -> List[str]:
        # Pas d'inventaire : la cible se décrit en langage naturel dans la demande.
        return []

    def get_asset(self, target: str) -> Any:
        raise ValueError(
            "Le monde vivant n'a pas d'asset statique : passez par une "
            "demande de découverte (inspect_state / locate_target / verify_effect)."
        )
