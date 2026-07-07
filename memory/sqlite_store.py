"""
sqlite_store.py
===============

Moteur de persistance local.
"""

import sqlite3
import os
import json
from datetime import datetime
from utils.logger import Logger

class SQLiteStore:

    def __init__(self, db_path: str = "memory.db"):
        self.db_path = db_path
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
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        provider_id TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_session_id ON messages(session_id)')
                conn.commit()
                Logger.info(f"SQLite database ready at {self.db_path}")
        except Exception as e:
            Logger.error(f"Database initialization failed: {str(e)}")

    def save_message(self, session_id: str, role: str, content: str, provider_id: str = None):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''INSERT INTO messages (session_id, role, content, provider_id) VALUES (?, ?, ?, ?)''',
                    (session_id, role, content, provider_id)
                )
                conn.commit()
        except Exception as e:
            Logger.error(f"Failed to save message to DB: {str(e)}")

    def get_session_history(self, session_id: str, limit: int = 50) -> list:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''SELECT role, content FROM messages WHERE session_id = ? ORDER BY timestamp DESC, id DESC LIMIT ?''',
                    (session_id, limit)
                )
                rows = cursor.fetchall()
                history = [{"role": row[0], "content": row[1]} for row in rows]
                history.reverse()
                return history
        except Exception as e:
            Logger.error(f"Failed to retrieve history: {str(e)}")
            return []

    def delete_session(self, session_id: str):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM messages WHERE session_id = ?', (session_id,))
                conn.commit()
        except Exception as e:
            Logger.error(f"Failed to delete session: {str(e)}")