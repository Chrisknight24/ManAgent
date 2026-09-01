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
            msg_idx = m.get("index") or m.get("turn") or "?"
            ex_idx = m.get("exchange_index") or m.get("exchange")
            if not ex_idx and str(msg_idx).isdigit():
                ex_idx = (int(msg_idx) + 1) // 2
            ts = m.get("timestamp", "")
            lines.append(f"--- [Échange #{ex_idx} | Message #{msg_idx} | {role} | {ts}] ---")
            lines.append(m.get("content", ""))
            lines.append("")
        return "\n".join(lines).strip()


class HistoryProvider(DataProvider):
    """
    Fournit l'accès à l'historique textuel complet de la session courante.
    Cibles supportées :
    - 'turns_X_Y' (ex: 'turns_1_2', 'turns_1_4')
    - 'turn_X' (ex: 'turn_1', 'turn_3')
    - 'first_message' / 'first_turn' : le premier message de la conversation
    - 'recent' : les messages récents
    - 'search:<query>' : recherche par mot-clé
    """

    def __init__(self, session_id: str, memory: ConversationMemory):
        self.session_id = session_id
        self.memory = memory

    def get_data_type(self) -> str:
        return "history"

    def get_targets(self) -> List[str]:
        targets = ["recent", "first_turn", "turns_all"]
        sess = self.memory._get_or_create_session(self.session_id) if self.memory else None
        if not sess:
            return targets

        # Ajouter les cibles de type turns_X_Y basées sur les jalons de la timeline
        for m in getattr(sess, "timeline_milestones", []):
            if "target" in m:
                targets.append(m["target"])
            elif "from_turn" in m and "to_turn" in m:
                targets.append(f"turns_{m['from_turn']}_{m['to_turn']}")
        
        # Ajouter des tranches de tours récentes ou des tours individuels
        if sess.all_messages:
            last_msg = sess.all_messages[-1]
            last_turn = last_msg.index
            targets.append(f"turn_{last_turn}")
            if last_turn > 1:
                targets.append(f"turns_1_{last_turn}")
            # Ajouter les 3 derniers messages individuels pour un ciblage précis
            for m in sess.all_messages[-3:]:
                t_name = f"turn_{m.index}"
                if t_name not in targets:
                    targets.append(t_name)

        return list(dict.fromkeys(targets))

    def _msg_to_dict(self, m: Any) -> Dict[str, Any]:
        if isinstance(m, dict):
            return {
                "index": m.get("index") or m.get("turn", 1),
                "exchange_index": m.get("exchange_index") or m.get("exchange", 1),
                "role": m.get("role", "user"),
                "content": m.get("content", ""),
                "timestamp": m.get("timestamp", "")
            }
        return {
            "index": getattr(m, "index", 1),
            "exchange_index": getattr(m, "exchange_index", 1),
            "role": getattr(m, "role", "user"),
            "content": getattr(m, "content", ""),
            "timestamp": getattr(m, "timestamp", "")
        }

    def get_asset(self, target: str) -> DataAsset:
        sess = self.memory._get_or_create_session(self.session_id) if self.memory else None
        if not sess:
            return HistoryDataAsset(target_id=target, metadata={"target": target, "session_id": self.session_id}, data={"messages": []})

        messages = []
        metadata = {"target": target, "session_id": self.session_id}

        if target in ("first_message", "first_turn", "first_exchange", "origin"):
            raw_msgs = sess.all_messages[:2] if sess.all_messages else []
            messages = [self._msg_to_dict(m) for m in raw_msgs]
            metadata["description"] = "Premier échange de la conversation"

        elif target in ("recent", "turns_recent", "current_session", ""):
            raw_msgs = sess.all_messages[-10:] if sess.all_messages else []
            messages = [self._msg_to_dict(m) for m in raw_msgs]
            metadata["description"] = "10 derniers messages de la session"

        elif target == "turns_all":
            raw_msgs = sess.all_messages or []
            messages = [self._msg_to_dict(m) for m in raw_msgs]
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
                messages = [self._msg_to_dict(m) for m in raw_msgs]
                metadata["description"] = f"Échanges #{start_turn} à #{end_turn}"
            else:
                raw_msgs = sess.all_messages[-10:] if sess.all_messages else []
                messages = [self._msg_to_dict(m) for m in raw_msgs]
                metadata["description"] = "Messages récents"

        elif target.startswith("turn_") or target.startswith("msg_"):
            turn_str = target.replace("turn_", "").replace("msg_", "")
            if turn_str.isdigit():
                t_idx = int(turn_str)
                if hasattr(sess, "get_messages_by_range"):
                    raw_msgs = sess.get_messages_by_range(t_idx, t_idx)
                else:
                    raw_msgs = [m for m in getattr(sess, "all_messages", []) if getattr(m, "index", 0) == t_idx]
                messages = [self._msg_to_dict(m) for m in raw_msgs]
                metadata["description"] = f"Échange/Message #{t_idx}"
            else:
                raw_msgs = sess.all_messages[-1:] if sess.all_messages else []
                messages = [self._msg_to_dict(m) for m in raw_msgs]
                metadata["description"] = "Dernier message"

        elif target.startswith("search:") or target.startswith("search_"):
            query = target.replace("search:", "").replace("search_", "").strip()
            results = sess.search_messages(query=query, limit=10) if query else []
            messages = [self._msg_to_dict(r) for r in results]
            metadata["description"] = f"Recherche '{query}' dans l'historique"
        else:
            results = sess.search_messages(query=target, limit=10) if hasattr(sess, "search_messages") else []
            if results:
                messages = [self._msg_to_dict(r) for r in results]
                metadata["description"] = f"Résultats de recherche pour '{target}'"
            else:
                raw_msgs = sess.all_messages[-10:] if sess.all_messages else []
                messages = [self._msg_to_dict(m) for m in raw_msgs]
                metadata["description"] = f"Historique récent (cible '{target}')"

        return HistoryDataAsset(target_id=target, metadata=metadata, data={"messages": messages})
