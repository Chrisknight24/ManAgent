"""
history.py
==========

Gestionnaire d'abstraction de la mémoire épisodique.
"""

import json
from memory.sqlite_store import SQLiteStore
from utils.logger import Logger

class ConversationMemory:

    def __init__(self):
        self.store = SQLiteStore()
        # On baisse la limite par défaut pour laisser une marge de sécurité de ~3500 tokens
        # pour le prompt système, les schemas de validation et les outils (La taxe Agent).
        self.DEFAULT_MAX_CONTEXT_TOKENS = 8000

    # =====================================================
    # GESTION GRANULAIRE DES ROLES
    # =====================================================

    def add_interaction(self, session_id: str, user_msg: str, ai_msg: str, provider_id: str = "unknown"):
        """
        Méthode de compatibilité : Sauvegarde intelligemment ce qui n'est pas vide.
        """
        if not session_id: return

        if user_msg:
            self.store.save_message(session_id, "user", user_msg)
        if ai_msg:
            self.store.save_message(session_id, "assistant", ai_msg, provider_id)

    def add_tool_call(self, session_id: str, tool_name: str, args: dict, provider_id: str = "unknown"):
        """Sauvegarde l'intention de l'IA (La demande d'outil)."""
        content = json.dumps({"call": tool_name, "args": args})
        self.store.save_message(session_id, "tool_call", content, provider_id)

    def add_tool_result(self, session_id: str, tool_name: str, result: str):
        """Sauvegarde le retour physique du C++."""
        # On préfixe pour que le LLM comprenne de quel outil vient la donnée
        content = f"[{tool_name} Result]: {result}"
        self.store.save_message(session_id, "tool", content)

    # =====================================================
    # EXTRACTION ET ÉLAGAGE
    # =====================================================

    def _estimate_tokens(self, text: str) -> int:
        # Estimation standard sûre (1 token ≈ 4 caractères)
        return len(text) // 4 if text else 0

    def get_context_for_llm(self, session_id: str, max_tokens: int = None) -> list:
        """
        Récupère l'historique glissant. 
        Permet de passer une limite personnalisée (ex: plus petite pour Groq, plus grande pour Gemini).
        """
        if not session_id: return []

        # On utilise la limite spécifiée ou celle par défaut
        limit_threshold = max_tokens if max_tokens is not None else self.DEFAULT_MAX_CONTEXT_TOKENS

        # On augmente un peu le limit global de lignes pour donner de la matière à l'élagage au besoin
        raw_history = self.store.get_session_history(session_id, limit=50)
        if not raw_history: return []

        pruned_history = []
        current_token_count = 0

        # Ton algorithme inversé excellent
        for msg in reversed(raw_history):
            msg_tokens = self._estimate_tokens(msg["content"])
            
            # Si le message courant fait déborder la jauge allouée à l'historique
            if current_token_count + msg_tokens > limit_threshold:
                Logger.info(f"[Memory Pruning] Budget de contexte atteint ({current_token_count} tokens chargés). Élagage des messages plus anciens.")
                break
                
            pruned_history.append(msg)
            current_token_count += msg_tokens

        pruned_history.reverse()
        return pruned_history

    def clear_session(self, session_id: str):
        if session_id: 
            self.store.delete_session(session_id)