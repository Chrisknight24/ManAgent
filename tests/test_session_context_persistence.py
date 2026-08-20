"""
tests/test_session_context_persistence.py
===========================================
Bug #1 : `active_investigation_targets` / `insights_by_mission` étaient
recalculés correctement après chaque Progressive Disclosure, mais
`_load_session_context()` (orchestrator.py) les écrasait inconditionnellement
au tour suivant avec la valeur venant de SQLite — laquelle ne contenait
jamais ces champs (colonnes absentes du schéma). Résultat : la section
"INVESTIGATION EN COURS" du prompt de l'Orchestrateur était toujours vide.

Ce test vérifie le nouveau contrat centralisé sur SessionContext :
- `to_persistable_dict()` expose tous les champs qui doivent survivre à un
  rechargement de session (y compris les deux champs manquants).
- `restore_from_dict()` ne touche QUE les clés effectivement présentes dans
  les données rechargées — un store incomplet ou plus ancien ne doit jamais
  effacer un état déjà en mémoire.
"""

import unittest

from memory.session_memory import SessionContext


class TestSessionContextPersistence(unittest.TestCase):
    def test_to_persistable_dict_includes_investigation_fields(self):
        ctx = SessionContext(session_id="s1")
        ctx.active_investigation_targets = ["abc123"]
        ctx.insights_by_mission = {"abc123": [{"question": "q", "answer": "a"}]}

        data = ctx.to_persistable_dict()

        self.assertIn("active_investigation_targets", data)
        self.assertIn("insights_by_mission", data)
        self.assertEqual(data["active_investigation_targets"], ["abc123"])
        self.assertEqual(data["insights_by_mission"], {"abc123": [{"question": "q", "answer": "a"}]})

    def test_restore_from_dict_round_trip(self):
        ctx = SessionContext(session_id="s1")
        ctx.active_investigation_targets = ["abc123"]
        ctx.insights_by_mission = {"abc123": [{"question": "q", "answer": "a"}]}
        ctx.goal_stack = [{"text": "ouvrir chrome", "status": "done"}]
        persisted = ctx.to_persistable_dict()

        fresh = SessionContext(session_id="s1")
        fresh.restore_from_dict(persisted)

        self.assertEqual(fresh.active_investigation_targets, ["abc123"])
        self.assertEqual(fresh.insights_by_mission, {"abc123": [{"question": "q", "answer": "a"}]})
        self.assertEqual(fresh.goal_stack, [{"text": "ouvrir chrome", "status": "done"}])

    def test_restore_from_dict_does_not_wipe_missing_keys(self):
        """C'est LA régression clé : un store qui ne connaît pas encore (ou
        plus) `active_investigation_targets` (ex: SQLite avant migration, ou
        une ligne plus ancienne) ne doit jamais écraser la valeur déjà
        présente en mémoire par un défaut vide."""
        ctx = SessionContext(session_id="s1")
        ctx.active_investigation_targets = ["abc123"]
        ctx.insights_by_mission = {"abc123": [{"question": "q", "answer": "a"}]}

        # Simule des données persistées incomplètes (comme le faisait l'ancien
        # SessionStore, qui ne sauvegardait pas du tout ces deux champs).
        incomplete_persisted_data = {
            "goal_stack": [],
            "unresolved_issues": [],
            "mission_history": [],
            "mood": None,
            "last_mission_status": None,
            "discovery_history": [],
            # "active_investigation_targets" et "insights_by_mission" absents !
        }

        ctx.restore_from_dict(incomplete_persisted_data)

        self.assertEqual(
            ctx.active_investigation_targets, ["abc123"],
            "Une clé absente des données persistées ne doit pas écraser l'état en mémoire."
        )
        self.assertEqual(ctx.insights_by_mission, {"abc123": [{"question": "q", "answer": "a"}]})


if __name__ == "__main__":
    unittest.main()
