"""
tests/test_workspace_truncation.py
===================================
Bug #3 : les outils "raw fetch" (ex. get_mission_details, inspect_value)
renvoient des payloads bruts non bornés (arbre d'exécution complet, etc.).
`Workspace.add_entry` les stocke tels quels, sans aucune limite, ce qui les
fait fuiter plus loin (insights_by_mission, prompt de synthèse...).

Ce test vérifie que `add_entry` borne désormais la taille de `answer` à la
source, quelle que soit l'origine de la donnée (défense en profondeur qui
protège tous les consommateurs en aval).
"""

import unittest

from core.discovery.workspace import Workspace, MAX_ENTRY_ANSWER_CHARS


class TestWorkspaceTruncation(unittest.TestCase):
    def test_huge_raw_tool_answer_is_capped(self):
        ws = Workspace(session_id="s1")
        huge_payload = {"execution_tree": {"nodes": ["x" * 200 for _ in range(500)]}}

        ws.add_entry(
            step_id="step_1",
            question="Récupère les détails complets de la mission",
            answer=huge_payload,
            tool_name="get_mission_details",
        )

        entry = ws.get_last_entry()
        self.assertIsNotNone(entry)
        self.assertLessEqual(
            len(entry.answer), MAX_ENTRY_ANSWER_CHARS + 200,
            "La réponse stockée doit être bornée, même pour un outil qui "
            "renvoie une donnée brute non condensée."
        )
        self.assertIn("tronqué", entry.answer)

    def test_small_answer_is_untouched(self):
        ws = Workspace(session_id="s2")
        ws.add_entry(
            step_id="step_1",
            question="La mission a-t-elle réussi ?",
            answer="Oui, la mission a réussi en 3 étapes.",
            tool_name="analyze_execution_tree",
        )
        entry = ws.get_last_entry()
        self.assertEqual(entry.answer, "Oui, la mission a réussi en 3 étapes.")


if __name__ == "__main__":
    unittest.main()
