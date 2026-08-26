"""
core/discovery/providers/history_provider.py
=====================================================

DataProvider pour l'historique conversationnel de la session.
Permet à l'Orchestrateur (via Progressive Disclosure) d'accéder au verbatim
intégral d'un échange passé, d'un intervalle de tours (ex: 'turns_1_4') ou de chercher
des extraits précis sans polluer son prompt initial.
"""

from typing import List, Any, Dict, Optional
import json
from pydantic import Field
from core.discovery.data_provider import DataProvider
from core.discovery.data_asset import DataAsset
from memory.history import ConversationMemory, MessageRecord

class HistoryDataAsset(DataAsset):
    """Asset représentant un échange ou une tranche de messages passés."""
    data: Dict[str, Any] = Field(default_factory=dict)

    def dump_data(self) -> str:
        messages = self.data.get("messages", [])
        if not messages:
            return "(Aucun message trouvé pour cette cible)"
        
        lines = []
        for m in messages:
            role = "Utilisateur" if m.get("role") == "user" else "Assistant"
            turn = m.get("turn") or m.get("index") or "?"
            ts = m.get("timestamp", "")
            lines.append(f"--- [Tour #{turn} | {role} | {ts}] ---")
            lines.append(m.get("content", ""))
            lines.append("")
        return "\n".join(lines).strip()


class HistoryProvider(DataProvider):
    """
    Fournit l'accès à l'historique textuel complet de la session courante.
    Cibles supportées :
    - 'turns_X_Y' (ex: 'turns_1_4', 'turns_5_8')
    - 'turn_X' (ex: 'turn_3')
    - 'recent' : les 10 derniers échanges
    - 'search:<query>' : recherche par mot-clé
    """

    def __init__(self, session_id: str, memory: ConversationMemory):
        self.session_id = session_id
        self.memory = memory

    def get_data_type(self) -> str:
        return "history"

    def get_targets(self) -> List[str]:
        targets = ["recent", "turns_all"]
        sess = self.memory._sessions.get(self.session_id) if self.memory else None
        if not sess:
            return targets

        # Ajouter les cibles de type turns_X_Y basées sur les jalons de la timeline
        for m in getattr(sess, "timeline_milestones", []):
            targets.append(f"turns_{m['from_turn']}_{m['to_turn']}")
        
        # Ajouter des tranches de tours récentes ou des tours individuels
        if sess.all_messages:
            last_turn = sess.all_messages[-1].index
            targets.append(f"turn_{last_turn}")
            if last_turn > 1:
                targets.append(f"turns_1_{last_turn}")
            # Ajouter les 3 derniers tours individuels pour un ciblage ultra-précis
            for m in sess.all_messages[-3:]:
                t_name = f"turn_{m.index}"
                if t_name not in targets:
                    targets.append(t_name)

        return list(dict.fromkeys(targets))

    def get_asset(self, target: str) -> DataAsset:
        sess = self.memory._sessions.get(self.session_id) if self.memory else None
        if not sess:
            return HistoryDataAsset(target_id=target, metadata={"target": target, "session_id": self.session_id}, data={"messages": []})

        messages = []
        metadata = {"target": target, "session_id": self.session_id}

        if target in ("recent", "turns_recent", "current_session", ""):
            raw_msgs = sess.all_messages[-10:] if sess.all_messages else []
            messages = [{"turn": m.index, "role": m.role, "content": m.content, "timestamp": m.timestamp} for m in raw_msgs]
            metadata["description"] = "10 derniers messages de la session"

        elif target == "turns_all":
            raw_msgs = sess.all_messages or []
            messages = [{"turn": m.index, "role": m.role, "content": m.content, "timestamp": m.timestamp} for m in raw_msgs]
            metadata["description"] = "Tous les messages de la session"

        elif target.startswith("turns_"):
            parts = target.replace("turns_", "").split("_")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                start_turn = int(parts[0])
                end_turn = int(parts[1])
                if hasattr(sess, "get_messages_by_range"):
                    raw_msgs = sess.get_messages_by_range(start_turn, end_turn)
                else:
                    raw_msgs = [m for m in getattr(sess, "all_messages", []) if start_turn <= getattr(m, "index", 0) <= end_turn]
                messages = [{"turn": m.index, "role": m.role, "content": m.content, "timestamp": m.timestamp} for m in raw_msgs]
                metadata["description"] = f"Échanges complets des tours #{start_turn} à #{end_turn}"
            else:
                raw_msgs = sess.all_messages[-10:] if sess.all_messages else []
                messages = [{"turn": m.index, "role": m.role, "content": m.content, "timestamp": m.timestamp} for m in raw_msgs]
                metadata["description"] = "Messages récents (format turns non reconnu)"

        elif target.startswith("turn_"):
            turn_str = target.replace("turn_", "")
            if turn_str.isdigit():
                t_idx = int(turn_str)
                if hasattr(sess, "get_messages_by_range"):
                    raw_msgs = sess.get_messages_by_range(t_idx, t_idx)
                else:
                    raw_msgs = [m for m in getattr(sess, "all_messages", []) if getattr(m, "index", 0) == t_idx]
                messages = [{"turn": m.index, "role": m.role, "content": m.content, "timestamp": m.timestamp} for m in raw_msgs]
                metadata["description"] = f"Échange complet du tour #{t_idx}"
            else:
                raw_msgs = sess.all_messages[-1:] if sess.all_messages else []
                messages = [{"turn": m.index, "role": m.role, "content": m.content, "timestamp": m.timestamp} for m in raw_msgs]
                metadata["description"] = "Dernier échange"

        elif target.startswith("search:") or target.startswith("search_"):
            query = target.replace("search:", "").replace("search_", "").strip()
            results = sess.search_messages(query=query, limit=10) if query else []
            messages = [{"turn": r["turn"], "role": r["role"], "content": r["snippet"], "timestamp": r["timestamp"]} for r in results]
            metadata["description"] = f"Recherche '{query}' dans l'historique"
        else:
            # Recherche générique ou fallback sur les messages récents
            results = sess.search_messages(query=target, limit=10) if hasattr(sess, "search_messages") else []
            if results:
                messages = [{"turn": r["turn"], "role": r["role"], "content": r["snippet"], "timestamp": r["timestamp"]} for r in results]
                metadata["description"] = f"Résultats de recherche pour '{target}'"
            else:
                raw_msgs = sess.all_messages[-10:] if sess.all_messages else []
                messages = [{"turn": m.index, "role": m.role, "content": m.content, "timestamp": m.timestamp} for m in raw_msgs]
                metadata["description"] = f"Historique récent (cible '{target}')"

        return HistoryDataAsset(target_id=target, metadata=metadata, data={"messages": messages})
