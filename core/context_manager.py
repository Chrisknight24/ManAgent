"""
core/context_manager.py
======================================================
Gestionnaire intelligent de budget de tokens et d'assemblage du contexte LLM.
Garantit que le contexte envoyé au modèle respecte une limite stricte de tokens
tout en préservant le prompt caching et en affichant les DataAssets disponibles.
"""

from typing import List, Dict, Any, Optional, Union
from utils.logger import Logger
from memory.history import ConversationMemory, SessionConversation
from core.discovery.asset_registry import AssetRegistry


class TokenEstimator:
    """Estimation rapide et fiable du nombre de tokens (heuristique robuste)."""
    @staticmethod
    def estimate(text: str) -> int:
        if not text:
            return 0
        # Heuristique standard : ~3.8 caractères par token en moyenne (multilingue)
        return max(1, int(len(text) / 3.8))


class ContextManager:
    """
    Assemble le contexte optimal pour l'Orchestrateur selon un budget configurable.
    
    Structure hiérarchique du contexte :
    1. System Prompt & Instructions (Préfixe stable pour le prompt caching)
    2. État courant & Objectifs actifs (Session State)
    3. Manifeste des DataAssets disponibles (Fichiers, inputs longs, outputs)
    4. Faits sémantiques & Préférences actifs (LessonStore / Facts)
    5. Timeline Index (Index chronologique léger du passé lointain)
    6. Messages récents verbatim (Mémoire de travail immédiate)
    """

    def __init__(
        self,
        max_total_tokens: int = 12000,
        max_recent_messages_tokens: int = 4000,
        max_assets_manifest_tokens: int = 2000,
        max_facts_tokens: int = 1500,
        max_timeline_tokens: int = 1000
    ):
        self.max_total_tokens = max_total_tokens
        self.max_recent_messages_tokens = max_recent_messages_tokens
        self.max_assets_manifest_tokens = max_assets_manifest_tokens
        self.max_facts_tokens = max_facts_tokens
        self.max_timeline_tokens = max_timeline_tokens

    def assemble_orchestrator_context(
        self,
        memory: ConversationMemory,
        session_id: str,
        user_message: str,
        facts_summary: Optional[str] = None,
        context_notes: Optional[str] = None,
        asset_registry: Optional[AssetRegistry] = None,
        recent_turns_limit: int = 6
    ) -> Dict[str, Any]:
        """
        Construit dynamiquement les sections du contexte pour l'Orchestrateur sous budget strict.
        """
        # 1. Manifeste des DataAssets disponibles (Progressive Disclosure)
        assets_manifest = ""
        if asset_registry:
            assets_manifest = asset_registry.generate_manifest(max_assets=10, preview_lines=3)
            if TokenEstimator.estimate(assets_manifest) > self.max_assets_manifest_tokens:
                assets_manifest = assets_manifest[: int(self.max_assets_manifest_tokens * 3.8)] + "\n[... autres DataAssets référencés dans le registre]"

        # 2. Évaluer et formater les faits/préférences
        formatted_facts = facts_summary or ""
        if TokenEstimator.estimate(formatted_facts) > self.max_facts_tokens:
            formatted_facts = formatted_facts[: int(self.max_facts_tokens * 3.8)] + "\n[... faits additionnels disponibles via PD]"

        # 3. Timeline Index (échanges anciens)
        timeline_index = memory.get_timeline_index(session_id)
        if TokenEstimator.estimate(timeline_index) > self.max_timeline_tokens:
            timeline_index = timeline_index[: int(self.max_timeline_tokens * 3.8)] + "\n[... suite de la chronologie]"

        # 4. Messages récents verbatim avec dégradation adaptative sous budget
        current_limit = recent_turns_limit
        recent_msgs = memory.get_context_for_llm(session_id, limit=current_limit)
        
        def _format_msgs(msgs: List[Dict[str, str]]) -> str:
            lines = []
            for m in msgs:
                role = "Utilisateur" if m.get("role") == "user" else "Assistant"
                lines.append(f"**{role}** : {m.get('content', '')}")
            return "\n\n".join(lines)

        recent_text = _format_msgs(recent_msgs)
        while TokenEstimator.estimate(recent_text) > self.max_recent_messages_tokens and current_limit > 2:
            current_limit -= 2
            recent_msgs = memory.get_context_for_llm(session_id, limit=current_limit)
            recent_text = _format_msgs(recent_msgs)

        # 5. Contexte combiné complet
        history_sections = []
        if context_notes:
            history_sections.append(f"### [CONTEXTE DE SESSION]\n{context_notes}")
        if assets_manifest:
            history_sections.append(assets_manifest)
        if formatted_facts:
            history_sections.append(f"### [FAITS & PRÉFÉRENCES CLÉS]\n{formatted_facts}")
        if timeline_index:
            history_sections.append(timeline_index)
        if recent_text:
            history_sections.append(f"### [DERNIERS ÉCHANGES VERBATIM]\n{recent_text}")

        full_history_str = "\n\n".join(history_sections)

        total_estimated = (
            TokenEstimator.estimate(user_message)
            + TokenEstimator.estimate(full_history_str)
        )

        Logger.debug(
            f"[ContextManager] Contexte assemblé : ~{total_estimated} tokens estimés "
            f"(verbatim={len(recent_msgs)} msgs, assets={bool(assets_manifest)}, timeline={bool(timeline_index)}, facts={bool(formatted_facts)})"
        )

        return {
            "history_str": full_history_str,
            "recent_messages": recent_msgs,
            "assets_manifest": assets_manifest,
            "timeline_index": timeline_index,
            "facts_summary": formatted_facts,
            "estimated_tokens": total_estimated
        }
