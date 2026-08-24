"""
memory/history.py
======================================================
Gestion de l'historique conversationnel avec persistance immuable (Source of Truth).
Aucun message n'est supprimé : tous les tours d'échanges sont conservés intégralement
pour permettre l'indexation, la recherche sémantique et la Progressive Disclosure.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime


class MessageRecord(BaseModel):
    index: int
    role: str
    content: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SessionConversation:
    """Conserve l'historique immuable d'une session donnée."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.all_messages: List[MessageRecord] = []
        self.timeline_milestones: List[Dict[str, Any]] = []

    def add_message(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> MessageRecord:
        idx = len(self.all_messages) + 1
        record = MessageRecord(
            index=idx,
            role=role,
            content=content,
            metadata=metadata or {}
        )
        self.all_messages.append(record)
        self._update_timeline_if_needed()
        return record

    def get_messages(self) -> List[Dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in self.all_messages]

    def get_recent_messages(self, count: int = 6) -> List[Dict[str, str]]:
        recent = self.all_messages[-count:] if len(self.all_messages) > count else self.all_messages
        return [{"role": m.role, "content": m.content} for m in recent]

    def search_messages(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        q_lower = query.lower()
        results = []
        for msg in reversed(self.all_messages):
            if q_lower in msg.content.lower():
                results.append({
                    "turn": msg.index,
                    "role": msg.role,
                    "timestamp": msg.timestamp,
                    "snippet": msg.content[:250] + ("..." if len(msg.content) > 250 else "")
                })
                if len(results) >= limit:
                    break
        return results

    def _update_timeline_if_needed(self) -> None:
        total = len(self.all_messages)
        chunk_size = 4
        existing_covered = len(self.timeline_milestones) * chunk_size
        
        if total - 6 >= existing_covered + chunk_size:
            start_idx = existing_covered
            end_idx = start_idx + chunk_size
            chunk = self.all_messages[start_idx:end_idx]
            
            user_topics = [m.content[:80].strip() for m in chunk if m.role == "user"]
            summary_label = " ; ".join(user_topics)
            self.timeline_milestones.append({
                "from_turn": start_idx + 1,
                "to_turn": end_idx,
                "summary": f"Échanges #{start_idx+1}-#{end_idx} : {summary_label}"
            })

    def get_timeline_index(self) -> str:
        if not self.timeline_milestones:
            return ""
        lines = ["### [CHRONOLOGIE DES ÉCHANGES ANTÉRIEURS]"]
        for m in self.timeline_milestones:
            lines.append(f"- **Tours {m['from_turn']}..{m['to_turn']}** : {m['summary']}")
        return "\n".join(lines)


class ConversationMemory:
    """
    Gestionnaire central de la mémoire conversationnelle pour l'Orchestrateur.
    Supporte le multi-sessions, l'immuabilité et la compatibilité totale
    avec add_interaction, get_context_for_llm et clear_session.
    """

    def __init__(self):
        self._sessions: Dict[str, SessionConversation] = {}

    def _get_or_create_session(self, session_id: str) -> SessionConversation:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionConversation(session_id=session_id)
        return self._sessions[session_id]

    def add_interaction(
        self,
        session_id: str,
        user_msg: str,
        ai_msg: str,
        provider_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Ajoute une paire d'échanges utilisateur / assistant de façon immuable."""
        sess = self._get_or_create_session(session_id)
        meta = metadata or {}
        if provider_id:
            meta["provider_id"] = provider_id
        sess.add_message("user", user_msg, metadata=meta)
        sess.add_message("assistant", ai_msg, metadata=meta)

    def add_user_message(self, session_id: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> MessageRecord:
        sess = self._get_or_create_session(session_id)
        return sess.add_message("user", content, metadata=metadata)

    def add_assistant_message(self, session_id: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> MessageRecord:
        sess = self._get_or_create_session(session_id)
        return sess.add_message("assistant", content, metadata=metadata)

    def get_context_for_llm(self, session_id: str, limit: int = 10) -> List[Dict[str, str]]:
        """Retourne la liste des messages récents pour le prompt LLM."""
        if session_id not in self._sessions:
            return []
        return self._sessions[session_id].get_recent_messages(count=limit)

    def get_timeline_index(self, session_id: str) -> str:
        if session_id not in self._sessions:
            return ""
        return self._sessions[session_id].get_timeline_index()

    def search_session_messages(self, session_id: str, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        if session_id not in self._sessions:
            return []
        return self._sessions[session_id].search_messages(query=query, limit=limit)

    def clear_session(self, session_id: str) -> None:
        if session_id in self._sessions:
            del self._sessions[session_id]
