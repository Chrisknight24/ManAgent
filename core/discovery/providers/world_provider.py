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

    async def answer(self, question: str):
        """Répond en langage naturel via `perceive_understand` (lecture seule).

        P5 : le routeur de scope appelle cette méthode quand il a retenu le
        scope `world`. Aucune action n'est dispatchée : uniquement une
        lecture du monde par l'outil conventionnel `get_world_state` (ou
        l'outil déclaré dans le manifeste sous `world_snapshot`).

        Retourne `None` si le monde ne peut pas être lu : le routeur ne
        transforme jamais une absence de source en réponse inventée.
        """
        rs = self._runtime_state
        if rs is None:
            return None
        question = str(question or "").strip()
        if not question:
            return None

        from core.alignment import WORLD_STATE_TOOL

        tm = getattr(rs, "tools_manager", None)
        try:
            known = set(tm.known_tool_names()) if tm is not None and hasattr(tm, "known_tool_names") else set()
        except Exception:
            known = set()

        source = WORLD_STATE_TOOL if WORLD_STATE_TOOL in known else ""
        if not source:
            # Convention de manifeste : `metadata.world_snapshot.source_tool`.
            manifest = getattr(rs, "host_manifest", None)
            for holder in (getattr(manifest, "metadata", None),
                           getattr(manifest, "environment", None)):
                if isinstance(holder, dict) and isinstance(holder.get("world_snapshot"), dict):
                    candidate = str(holder["world_snapshot"].get("source_tool") or "").strip()
                    if candidate and (not known or candidate in known):
                        source = candidate
                    break
        if not source:
            return None

        from tools.internal_tools import perceive_understand
        result = await perceive_understand(
            {"question": question, "source_tool": source, "source_args": {}},
            rs,
        )
        if not isinstance(result, dict) or not result.get("result"):
            return None
        data = result.get("data")
        text = str(data).strip() if data is not None else ""
        return text or None
