"""
tests/test_llm_discovery_end_to_end.py
=========================================
Test d'intégration de bout en bout, avec le VRAI `core/llm.py` (non modifié)
et le VRAI pipeline Discovery (DiscoveryEngine -> DiscoverySession ->
Workspace), pour vérifier ce qui se passe exactement dans le chemin décrit
par l'utilisateur :

    Llm.generate_structured()
      -> le LLM demande une discovery_request
      -> _execute_discovery() lance une DiscoverySession dont un des steps
         appelle un outil qui renvoie un ÉNORME payload brut (comme
         get_mission_details / inspect_value dans le vrai code)
      -> refined.summary est injecté tel quel dans le prompt :
             full_prompt += f"[RÉSULTAT DE L'INVESTIGATION]\n{refined.summary}"
      -> le LLM est rappelé avec ce nouveau prompt.

Avant le correctif, `refined.summary` pouvait faire plusieurs milliers de
caractères de JSON brut (l'exact "gaspillage de tokens" rapporté). Ce test
vérifie qu'après le correctif (Workspace.add_entry borné + synthèse LLM
finale), le prompt réinjecté au deuxième appel reste raisonnable.
"""

import unittest
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core.llm import Llm
from core.runtime_state import RuntimeState
from core.discovery.discovery_engine import DiscoveryEngine
from core.discovery.base_explorer import BaseExplorer
from core.discovery.data_provider import DataProvider
from core.discovery.models import DiscoveryPlan, DiscoveryStep, StepType, DiscoveryRequest


# --- Schéma de décision minimal (équivalent simplifié d'OrchestratorDecision) ---
class FakeDecision(BaseModel):
    type: str
    output: str
    discovery_request: Optional[DiscoveryRequest] = Field(default=None)


class HugeDataProvider(DataProvider):
    def get_data_type(self) -> str:
        return "missions"

    def get_targets(self) -> List[str]:
        return ["last_mission"]

    def get_data(self, target: str) -> Any:
        return {"mission_id": "abc123"}

    def get_metadata(self, target: str) -> Dict[str, Any]:
        return {"goal": "ouvrir chrome"}


class HugeDataExplorer(BaseExplorer):
    """Explorer factice dont l'outil renvoie un arbre d'exécution complet non
    condensé, exactement comme MissionHistoryExplorer._get_mission_details
    dans le vrai code."""

    def get_data_type(self) -> str:
        return "missions"

    def get_available_goals(self) -> List[str]:
        return ["get_mission_details"]

    def get_tools_description(self) -> List[Dict[str, Any]]:
        return [{"name": "get_mission_details", "description": "x", "parameters": {}}]

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        huge_tree = {"nodes": [f"node_{i}_" + ("x" * 100) for i in range(300)]}
        return {"success": True, "data": huge_tree}

    def create_signature(self, targets: List[str], technical_goals: List[str]) -> str:
        return f"missions://{'_'.join(targets)}/{'_'.join(technical_goals)}"

    async def generate_plan(self, goal, technical_goal=None, target=None, llm=None,
                             data_provider=None, data_context=None,
                             targets=None, technical_goals=None) -> DiscoveryPlan:
        targets = targets or [target]
        technical_goals = technical_goals or [technical_goal]
        return DiscoveryPlan(
            goal=goal,
            steps=[
                DiscoveryStep(
                    type=StepType.TOOL,
                    description="Récupérer les détails de la dernière mission",
                    tool_name="get_mission_details",
                    tool_args={},
                )
            ],
            data_type="missions",
            targets=targets,
            technical_goals=technical_goals,
        )


class FakeEntity:
    entity_id = "entity_1"
    name = "TestEntity"
    role = "orchestrator"

    def __init__(self, provider: HugeDataProvider):
        self._provider = provider

    def get_data_context(self):
        return None

    def get_data_providers(self) -> Dict[str, DataProvider]:
        return {"missions": self._provider}

    def get_data_provider(self, data_type: str) -> Optional[DataProvider]:
        return self._provider if data_type == "missions" else None


class ScriptedProvider:
    """Provider factice pilotable : renvoie les réponses scriptées dans l'ordre,
    et enregistre le prompt complet de chaque appel pour inspection."""

    def __init__(self, responses: List[BaseModel]):
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []
        self.model_name = None

    async def generate_structured_output(self, prompt: str, response_schema, context):
        self.calls.append({"prompt": prompt, "context": context})
        return self._responses.pop(0)

    async def generate_response(self, user_message: str) -> str:
        return "n/a"


class FakeProviderManager:
    def __init__(self, provider):
        self._provider = provider

    def get_provider(self, provider_id: str):
        return self._provider


class TestDiscoveryEndToEndPromptBudget(unittest.IsolatedAsyncioTestCase):
    async def test_investigation_result_injected_into_prompt_stays_bounded(self):
        runtime_state = RuntimeState()
        engine = DiscoveryEngine(runtime_state)
        runtime_state.set_discovery_engine(engine)

        explorer = HugeDataExplorer(runtime_state)
        engine.register_explorer(explorer)

        discovery_req = DiscoveryRequest(
            goal="Savoir si la dernière mission a réussi",
            data_type="missions",
            targets=["last_mission"],
            technical_goals=["get_mission_details"],
        )
        first_response = FakeDecision(
            type="request",
            output="Je recherche le statut de la dernière mission.",
            discovery_request=discovery_req,
        )
        final_response = FakeDecision(
            type="direct",
            output="La dernière mission a réussi.",
            discovery_request=None,
        )

        provider = ScriptedProvider(responses=[first_response, final_response])
        provider_manager = FakeProviderManager(provider)

        llm = Llm(
            provider_manager=provider_manager,
            provider_id="p1",
            model_id="m1",
            system_prompt="Tu es l'orchestrateur.",
            runtime_state=runtime_state,
        )
        # generate_text est utilisé par l'étape de synthèse finale de la
        # DiscoverySession : on lui fait renvoyer une phrase courte, comme le
        # ferait un vrai LLM correctement guidé par discovery_synthesis.md.
        async def fake_generate_text(prompt, tag=None):
            return "La dernière mission (abc123) a réussi."
        llm.generate_text = fake_generate_text

        llm.enable_discovery(engine, FakeEntity(HugeDataProvider()))

        result = await llm.generate_structured(
            prompt="L'utilisateur demande : est-ce que la dernière mission a réussi ?",
            schema=FakeDecision,
            tag="orchestrator_routing",
        )

        self.assertEqual(result.type, "direct")
        self.assertEqual(len(provider.calls), 2, "Une découverte puis une réponse finale : 2 appels LLM.")

        second_call_prompt = provider.calls[1]["prompt"]
        self.assertIn("[RÉSULTAT DE L'INVESTIGATION]", second_call_prompt)
        self.assertNotIn("node_0_", second_call_prompt,
                          "Le prompt réinjecté ne doit pas contenir la donnée brute de l'outil.")
        self.assertLess(
            len(second_call_prompt), 2000,
            "Le prompt réinjecté après l'investigation doit rester borné, même "
            "quand l'outil sous-jacent renvoie un payload énorme (~30 000 "
            "caractères ici) : c'est exactement le 'gaspillage de tokens' "
            "signalé."
        )


if __name__ == "__main__":
    unittest.main()
