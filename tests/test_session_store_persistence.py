"""
tests/test_session_store_persistence.py
==========================================
Bug #1 / #4 (cause racine côté stockage) : la table SQLite `sessions` n'a
jamais eu de colonnes pour `insights_by_mission` ni `active_investigation_targets`.
`upsert_session` les ignorait donc silencieusement, et `get_session` ne les
renvoyait jamais — ce qui, combiné à l'écrasement inconditionnel côté
orchestrator.py, expliquait "la section insight toujours vide".

Ce test vérifie que ces deux champs survivent bien à un aller-retour
upsert_session -> get_session, exactement comme discovery_history.
"""

import os
import tempfile
import unittest

from memory.session_store import SessionStore


class TestSessionStorePersistence(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.store = SessionStore(db_path=self.db_path)

    def tearDown(self):
        os.remove(self.db_path)

    def test_insights_and_active_targets_round_trip(self):
        context_dict = {
            "goal_stack": [{"text": "ouvrir chrome", "status": "done"}],
            "unresolved_issues": [],
            "mission_history": ["m1"],
            "mood": "neutral",
            "last_mission_status": "success",
            "discovery_history": ["missions:last_mission:get_mission_details"],
            "active_investigation_targets": ["last_mission"],
            "insights_by_mission": {
                "last_mission": [{"question": "combien d'étapes ?", "answer": "7 étapes"}]
            },
        }
        self.store.upsert_session("session_1", context_dict)

        reloaded = self.store.get_session("session_1")

        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded["active_investigation_targets"], ["last_mission"])
        self.assertEqual(
            reloaded["insights_by_mission"],
            {"last_mission": [{"question": "combien d'étapes ?", "answer": "7 étapes"}]},
        )

    def test_missing_investigation_fields_default_sensibly(self):
        """Une session sans investigation en cours doit revenir avec des
        valeurs vides explicites (pas une KeyError, pas None non plus)."""
        self.store.upsert_session("session_2", {"goal_stack": []})
        reloaded = self.store.get_session("session_2")

        self.assertEqual(reloaded["active_investigation_targets"], [])
        self.assertEqual(reloaded["insights_by_mission"], {})

    def test_existing_db_without_new_columns_migrates_cleanly(self):
        """Une base créée avec l'ancien schéma (sans les 2 nouvelles colonnes)
        doit continuer à s'ouvrir et se faire migrer automatiquement, sans
        perdre les données déjà présentes."""
        import sqlite3
        # Simule une base "ancienne" : on ne crée QUE les colonnes historiques.
        old_db_path = self.db_path + ".old"
        conn = sqlite3.connect(old_db_path)
        conn.execute('''
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                goal_stack TEXT,
                unresolved_issues TEXT,
                mission_history TEXT,
                mood TEXT,
                last_mission_status TEXT,
                last_activity DATETIME,
                discovery_history TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute(
            "INSERT INTO sessions (session_id, goal_stack, discovery_history) VALUES (?, ?, ?)",
            ("legacy_session", "[]", "[]"),
        )
        conn.commit()
        conn.close()

        try:
            migrated_store = SessionStore(db_path=old_db_path)
            reloaded = migrated_store.get_session("legacy_session")
            self.assertIsNotNone(reloaded)
            self.assertEqual(reloaded["active_investigation_targets"], [])
            self.assertEqual(reloaded["insights_by_mission"], {})

            # Et on peut désormais bien écrire dans les nouvelles colonnes.
            migrated_store.upsert_session("legacy_session", {
                "active_investigation_targets": ["m42"],
                "insights_by_mission": {"m42": [{"question": "q", "answer": "a"}]},
            })
            reloaded2 = migrated_store.get_session("legacy_session")
            self.assertEqual(reloaded2["active_investigation_targets"], ["m42"])
        finally:
            os.remove(old_db_path)


if __name__ == "__main__":
    unittest.main()
