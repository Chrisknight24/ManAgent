"""
tests/test_discovery_session_semantic_grounding.py
====================================================
Bug #2 : l'étape "semantic" d'une DiscoverySession construisait un prompt
(`discovery_semantic.md`) à partir du seul nom du data_type et de la cible,
sans jamais injecter les résultats des étapes précédentes du même plan
(les WorkspaceEntry déjà collectées). L'étape ne pouvait donc que broder,
faute de toute donnée réelle sur laquelle s'appuyer.

Ce test vérifie que le prompt envoyé au LLM pour une étape sémantique
contient bien le contenu des étapes précédentes.
"""

import unittest
from typing import Any, Dict, List, Optional

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
        return []

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"success": True, "data": ""}

    def create_signature(self, targets: List[str], technical_goals: List[str]) -> str:
        return f"missions://{'_'.join(targets)}/{'_'.join(technical_goals)}"

    async def generate_plan(self, goal, technical_goal, target, llm=None, data_provider=None, data_context=None):
        raise NotImplementedError


class FakeLlm:
    """Capture le dernier prompt reçu par generate_text pour permettre
    d'inspecter précisément ce que le LLM a vu (ou pas)."""

    def __init__(self, response: str = "Réponse factice."):
        self.response = response
        self.last_prompt: Optional[str] = None
        self.calls: List[str] = []

    async def generate_text(self, prompt: str, tag: Optional[str] = None) -> str:
        self.last_prompt = prompt
        self.calls.append(prompt)
        return self.response


class TestSemanticStepGrounding(unittest.IsolatedAsyncioTestCase):
    def _build_session(self, llm) -> DiscoverySession:
        plan = DiscoveryPlan(
            goal="Savoir combien d'étapes a exécuté la dernière mission",
            steps=[],
            data_type="missions",
            targets=["last_mission"],
            technical_goals=["get_mission_details"],
        )
        return DiscoverySession(
            entity_id="entity_1",
            plan=plan,
            explorer=FakeExplorer(RuntimeState()),
            runtime_state=RuntimeState(),
            llm=llm,
        )

    async def test_semantic_step_prompt_includes_prior_tool_results(self):
        llm = FakeLlm(response="La mission a exécuté 7 étapes.")
        session = self._build_session(llm)

        # Simule une étape TOOL déjà exécutée plus tôt dans le même plan
        # (ex: get_mission_details) qui a livré la donnée nécessaire.
        session.workspace.add_entry(
            step_id="step_1",
            question="Récupérer les détails de la dernière mission",
            answer="mission_id=abc123, steps_count=7, status=success",
            tool_name="get_mission_details",
        )

        semantic_step = DiscoveryStep(
            type=StepType.SEMANTIC,
            description="Combien d'étapes ?",
            question="Combien d'étapes la mission a-t-elle exécutées ?",
        )

        await session._execute_semantic_step(semantic_step)

        self.assertIsNotNone(llm.last_prompt, "L'étape sémantique doit appeler le LLM.")
        self.assertIn(
            "steps_count=7", llm.last_prompt,
            "Le prompt de l'étape sémantique doit contenir les données déjà "
            "collectées par les étapes précédentes du plan (ici le résultat "
            "de l'étape 'tool' get_mission_details) — sinon le LLM ne peut "
            "que broder sans aucune donnée réelle."
        )

    async def test_semantic_step_with_no_prior_data_says_so_explicitly(self):
        llm = FakeLlm(response="Je ne sais pas.")
        session = self._build_session(llm)

        semantic_step = DiscoveryStep(
            type=StepType.SEMANTIC,
            description="Combien d'étapes ?",
            question="Combien d'étapes la mission a-t-elle exécutées ?",
        )
        await session._execute_semantic_step(semantic_step)

        self.assertIsNotNone(llm.last_prompt)
        # Le prompt doit être honnête sur l'absence de données plutôt que de
        # laisser croire au LLM qu'il "consulte" une donnée qu'il n'a pas.
        self.assertTrue(
            "Aucune donnée" in llm.last_prompt or "aucune donnée" in llm.last_prompt
        )


if __name__ == "__main__":
    unittest.main()
