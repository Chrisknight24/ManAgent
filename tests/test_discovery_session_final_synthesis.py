"""
tests/test_discovery_session_final_synthesis.py
=================================================
Bug #3 (partie "RefinedContext.summary n'est pas vraiment raffiné") :
`DiscoverySession.run()` terminait toujours par une simple concaténation
brute "- question → réponse" pour CHAQUE entrée du Workspace. Or c'est
exactement cette chaîne (`refined.summary`) que `Llm.generate_structured`
injecte telle quelle dans le prompt de l'entité appelante
(`core/llm.py` : `full_prompt += f"[RÉSULTAT DE L'INVESTIGATION]\n{refined.summary}"`).
Une concaténation brute n'est bornée par rien : c'est le principal vecteur
du "gaspillage de tokens" signalé.

Ce test vérifie que :
1. Quand un LLM est disponible, `run()` produit une vraie synthèse (un appel
   LLM dédié), pas une concaténation brute.
2. Sans LLM disponible, le comportement historique (concaténation) reste
   utilisé en repli, pour ne rien casser des usages existants.
"""

import unittest
from typing import Any, Dict, List

from core.discovery.discovery_session import DiscoverySession
from core.discovery.base_explorer import BaseExplorer
from core.discovery.models import DiscoveryPlan, DiscoveryStep, StepType
from core.runtime_state import RuntimeState


class FakeExplorer(BaseExplorer):
    def get_data_type(self) -> str:
        return "missions"

    def get_available_goals(self) -> List[str]:
        return ["get_mission_details"]

    def get_tools_description(self) -> List[Dict[str, Any]]:
        return [{"name": "get_mission_details", "description": "x", "parameters": {}}]

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        # Simule un outil "raw fetch" qui renvoie un arbre d'exécution complet,
        # comme MissionHistoryExplorer._get_mission_details dans le vrai code.
        huge_tree = {"nodes": ["step_" + str(i) * 50 for i in range(200)]}
        return {"success": True, "data": huge_tree}

    def create_signature(self, targets: List[str], technical_goals: List[str]) -> str:
        return f"missions://{'_'.join(targets)}/{'_'.join(technical_goals)}"

    async def generate_plan(self, goal, technical_goal, target, llm=None, data_provider=None, data_context=None):
        raise NotImplementedError


class FakeLlm:
    def __init__(self, response: str = "Synthèse concise factice."):
        self.response = response
        self.calls: List[str] = []

    async def generate_text(self, prompt: str, tag=None) -> str:
        self.calls.append(prompt)
        return self.response


def _make_plan() -> DiscoveryPlan:
    return DiscoveryPlan(
        goal="Savoir si la dernière mission a réussi",
        steps=[
            DiscoveryStep(
                type=StepType.TOOL,
                description="Récupérer les détails de la dernière mission",
                tool_name="get_mission_details",
                tool_args={},
            )
        ],
        data_type="missions",
        targets=["last_mission"],
        technical_goals=["get_mission_details"],
    )


class TestFinalSynthesis(unittest.IsolatedAsyncioTestCase):
    async def test_summary_is_llm_synthesized_when_llm_available(self):
        llm = FakeLlm(response="La dernière mission a réussi.")
        session = DiscoverySession(
            entity_id="e1",
            plan=_make_plan(),
            explorer=FakeExplorer(RuntimeState()),
            runtime_state=RuntimeState(),
            llm=llm,
        )
        refined = await session.run()

        self.assertEqual(refined.summary, "La dernière mission a réussi.")
        self.assertGreaterEqual(len(llm.calls), 1, "Un appel dédié de synthèse doit avoir eu lieu.")
        self.assertIn("DISCOVERY SYNTHESIS", llm.calls[-1])

    async def test_summary_falls_back_to_raw_join_without_llm(self):
        session = DiscoverySession(
            entity_id="e1",
            plan=_make_plan(),
            explorer=FakeExplorer(RuntimeState()),
            runtime_state=RuntimeState(),
            llm=None,
        )
        refined = await session.run()

        # Comportement historique conservé (pas de LLM => pas de synthèse
        # possible), mais toujours borné grâce au cap de Workspace.add_entry.
        self.assertIn("Récupérer les détails de la dernière mission", refined.summary)
        self.assertLess(len(refined.summary), 6000)

    async def test_summary_never_contains_raw_huge_payload_verbatim(self):
        """Même en cas d'échec de la synthèse LLM, le résumé final ne doit
        jamais contenir la donnée brute complète non bornée."""
        class FailingLlm(FakeLlm):
            async def generate_text(self, prompt, tag=None):
                self.calls.append(prompt)
                raise RuntimeError("panne LLM")

        session = DiscoverySession(
            entity_id="e1",
            plan=_make_plan(),
            explorer=FakeExplorer(RuntimeState()),
            runtime_state=RuntimeState(),
            llm=FailingLlm(),
        )
        refined = await session.run()
        self.assertLess(len(refined.summary), 6000)


if __name__ == "__main__":
    unittest.main()
