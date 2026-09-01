"""
memory/history.py
======================================================
Gestion de l'historique conversationnel avec persistance immuable (Source of Truth).
Aucun message n'est supprimé : tous les tours d'échanges sont conservés intégralement
pour permettre l'indexation, la recherche sémantique et la Progressive Disclosure.
"""

import sqlite3
import json
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime
from utils.logger import Logger


class MessageRecord(BaseModel):
    index: int
    exchange_index: int = 1
    role: str
    content: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SessionConversation:
    """Conserve l'historique immuable d'une session donnée."""

    def __init__(self, session_id: str, db_saver: Optional[Any] = None):
        self.session_id = session_id
        self.all_messages: List[MessageRecord] = []
        self.timeline_milestones: List[Dict[str, Any]] = []
        self.db_saver = db_saver

    def add_message(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None, save_to_db: bool = True) -> MessageRecord:
        idx = len(self.all_messages) + 1
        # Calcul du numéro d'échange (1 échange = question utilisateur + réponse assistant)
        user_count = sum(1 for m in self.all_messages if m.role == "user")
        if role == "user":
            exchange_idx = user_count + 1
        else:
            exchange_idx = max(1, user_count)

        record = MessageRecord(
            index=idx,
            exchange_index=exchange_idx,
            role=role,
            content=content,
            metadata=metadata or {}
        )
        self.all_messages.append(record)
        self._update_timeline_if_needed()

        if save_to_db and self.db_saver:
            try:
                self.db_saver(self.session_id, record)
            except Exception as e:
                Logger.error(f"[SessionConversation] Erreur lors de la sauvegarde du message en DB : {e}")

        return record

    def get_messages(self) -> List[Dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in self.all_messages]

    def get_recent_messages(self, count: int = 6) -> List[Dict[str, str]]:
        recent = self.all_messages[-count:] if len(self.all_messages) > count else self.all_messages
        return [{"role": m.role, "content": m.content} for m in recent]

    def get_messages_by_range(self, from_turn: int, to_turn: int) -> List[MessageRecord]:
        """
        Retourne la liste des messages inclus dans l'intervalle.
        Supporte à la fois les numéros d'échanges (ex: échanges 1 à 4)
        et les index de messages individuels (ex: messages 1 à 4).
        """
        by_exchange = [m for m in self.all_messages if from_turn <= m.exchange_index <= to_turn]
        by_index = [m for m in self.all_messages if from_turn <= m.index <= to_turn]
        combined = {m.index: m for m in (by_exchange + by_index)}
        return [combined[k] for k in sorted(combined.keys())]

    def search_messages(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        q_lower = query.lower()
        results = []
        for msg in reversed(self.all_messages):
            if q_lower in msg.content.lower():
                results.append({
                    "turn": msg.index,
                    "exchange": msg.exchange_index,
                    "role": msg.role,
                    "timestamp": msg.timestamp,
                    "snippet": msg.content[:250] + ("..." if len(msg.content) > 250 else "")
                })
                if len(results) >= limit:
                    break
        return results

    def _update_timeline_if_needed(self) -> None:
        # Regroupement par échange pour une chronologie claire
        exchanges: Dict[int, List[MessageRecord]] = {}
        for m in self.all_messages:
            exchanges.setdefault(m.exchange_index, []).append(m)
        
        total_exchanges = len(exchanges)
        chunk_size = 2  # 2 échanges par jalon de timeline
        existing_milestones_count = len(self.timeline_milestones)
        covered_exchanges = existing_milestones_count * chunk_size
        
        while total_exchanges - 2 >= covered_exchanges + chunk_size:
            start_ex = covered_exchanges + 1
            end_ex = covered_exchanges + chunk_size
            
            chunk_msgs = [m for m in self.all_messages if start_ex <= m.exchange_index <= end_ex]
            if not chunk_msgs:
                break

            min_msg_idx = min(m.index for m in chunk_msgs)
            max_msg_idx = max(m.index for m in chunk_msgs)
            
            previews = []
            for m in chunk_msgs:
                prefix = "Utilisateur" if m.role == "user" else "Assistant"
                clean_text = m.content.strip().replace("\n", " ")
                preview = clean_text[:50] + ("..." if len(clean_text) > 50 else "")
                previews.append(f"{prefix} (msg #{m.index}): \"{preview}\"")

            summary_label = " ; ".join(previews)
            self.timeline_milestones.append({
                "from_exchange": start_ex,
                "to_exchange": end_ex,
                "from_turn": min_msg_idx,
                "to_turn": max_msg_idx,
                "target": f"turns_{start_ex}_{end_ex}",
                "summary": summary_label
            })
            existing_milestones_count = len(self.timeline_milestones)
            covered_exchanges = existing_milestones_count * chunk_size

    def get_timeline_index(self) -> str:
        if not self.timeline_milestones:
            return ""
        lines = [
            "### [CHRONOLOGIE DES ÉCHANGES PASSÉS] (Utilisez Progressive Disclosure 'history:turns_X_Y' pour le verbatim complet)"
        ]
        for m in self.timeline_milestones:
            from_ex = m.get('from_exchange', m.get('from_turn', 1))
            to_ex = m.get('to_exchange', m.get('to_turn', 1))
            lines.append(
                f"- **Échanges #{from_ex}..#{to_ex} (Messages #{m['from_turn']}..#{m['to_turn']})** "
                f"[Cible: `turns_{from_ex}_{to_ex}`] : {m['summary']}"
            )
        return "\n".join(lines)


class ConversationMemory:
    """
    Gestionnaire central de la mémoire conversationnelle pour l'Orchestrateur.
    Supporte le multi-sessions, l'immuabilité et la persistance SQLite (memory.db).
    """

    def __init__(self, db_path: str = "memory.db"):
        self.db_path = db_path
        self._sessions: Dict[str, SessionConversation] = {}
        self._initialize_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _initialize_db(self):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        idx INTEGER NOT NULL,
                        exchange_index INTEGER NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        metadata TEXT DEFAULT '{}',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)')
                conn.commit()
                Logger.info("[ConversationMemory] Table 'messages' prête.")
        except Exception as e:
            Logger.error(f"[ConversationMemory] Erreur init DB : {e}")

    def _save_message_to_db(self, session_id: str, record: MessageRecord) -> None:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO messages (session_id, idx, exchange_index, role, content, timestamp, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    session_id,
                    record.index,
                    record.exchange_index,
                    record.role,
                    record.content,
                    record.timestamp,
                    json.dumps(record.metadata or {}, ensure_ascii=False)
                ))
                conn.commit()
        except Exception as e:
            Logger.error(f"[ConversationMemory] Erreur sauvegarde message DB : {e}")

    def _load_session_from_db(self, session_id: str) -> SessionConversation:
        sess = SessionConversation(session_id=session_id, db_saver=self._save_message_to_db)
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT idx, exchange_index, role, content, timestamp, metadata FROM messages WHERE session_id = ? ORDER BY idx ASC",
                    (session_id,)
                )
                rows = cursor.fetchall()
                for r in rows:
                    meta = json.loads(r["metadata"] or "{}")
                    record = MessageRecord(
                        index=r["idx"],
                        exchange_index=r["exchange_index"],
                        role=r["role"],
                        content=r["content"],
                        timestamp=r["timestamp"],
                        metadata=meta
                    )
                    sess.all_messages.append(record)
                sess._update_timeline_if_needed()
                if rows:
                    Logger.info(f"[ConversationMemory] Chargé {len(rows)} message(s) depuis la DB pour session {session_id}")
        except Exception as e:
            Logger.error(f"[ConversationMemory] Erreur chargement session {session_id} depuis la DB : {e}")
        return sess

    def _get_or_create_session(self, session_id: str) -> SessionConversation:
        if session_id not in self._sessions:
            self._sessions[session_id] = self._load_session_from_db(session_id)
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
        sess = self._get_or_create_session(session_id)
        return sess.get_recent_messages(count=limit)

    def get_timeline_index(self, session_id: str) -> str:
        sess = self._get_or_create_session(session_id)
        return sess.get_timeline_index()

    def search_session_messages(self, session_id: str, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        sess = self._get_or_create_session(session_id)
        return sess.search_messages(query=query, limit=limit)

    def clear_session(self, session_id: str) -> None:
        if session_id in self._sessions:
            del self._sessions[session_id]
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
                conn.commit()
                Logger.info(f"[ConversationMemory] Messages purgés de la DB pour session : {session_id}")
        except Exception as e:
            Logger.error(f"[ConversationMemory] Erreur suppression messages DB {session_id} : {e}")

