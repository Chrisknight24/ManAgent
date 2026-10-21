"""S7 : recul, snapshot frais et registre perime.

T13 : un recul refait une lecture du monde et les alias de cibles mortes
(invalidees ou purgees) ne reapparaissent pas dans les vues de prompt.
"""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.plan_models import FeasibilityDecision, Plan, PlanStep, StepType
from core.planner import Planner
from core.solver import Solver
from core.targets import TargetRegistry, target_is_live
from host_sim import HostSim


def test_s7_target_is_live_suit_invalidation_et_purge():
    registry = TargetRegistry()
    live = registry.upsert(scope="ui_element", ref="ref-live")
    dead = registry.upsert(scope="ui_element", ref="ref-dead")

    assert target_is_live(registry, live.local_name) is True
    assert target_is_live(registry, "inconnue") is False
    assert target_is_live(None, live.local_name) is False

    registry.invalidate(ref="ref-dead")
    assert target_is_live(registry, dead.local_name) is False

    registry.purge(all=True)
    assert target_is_live(registry, live.local_name) is False


def test_s7_registre_perime_absent_de_la_vue_solver():
    registry = TargetRegistry()
    live = registry.upsert(scope="ui_element", ref="ref-live")
    dead = registry.upsert(scope="ui_element", ref="ref-dead")
    registry.invalidate(ref="ref-dead")

    solver = Solver.__new__(Solver)
    solver.runtime_state = SimpleNamespace(target_registry=registry)
    solver.variable_registry = {
        "btn_vivant": {
            "value": live.local_name,
            "type": "target",
            "description": "Vivant",
        },
        "btn_mort": {
            "value": dead.local_name,
            "type": "target",
            "description": "Mort",
        },
        "ordinaire": {
            "value": 42,
            "type": "data",
            "description": "Donnee",
        },
    }

    view = solver._get_registry_metadata_view()

    assert "btn_vivant" in view
    assert "btn_mort" not in view
    assert "ordinaire" in view


def _manifest_with_target_contract():
    return {
        "host_name": "hostsim-s7",
        "host_version": "1.0.0",
        "tools": [{
            "name": "click",
            "kind": "action",
            "description": "Clique une cible.",
            "parameters": {
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
            },
            "target_contract": {
                "version": 1,
                "consumes": [{"param": "target", "scope": "ui_element"}],
            },
        }],
    }


class _FakeProviderManager:
    def get_model_metadata(self, model_id):
        return None

    def has_model_capability(self, model_id, capability):
        return False


class _PromptCaptureLlm:
    def __init__(self, answer):
        self.model_id = "fake"
        self.provider_manager = _FakeProviderManager()
        self._discovery_enabled = False
        self.answer = answer
        self.prompts = []

    async def generate_structured(self, **kwargs):
        self.prompts.append(kwargs["prompt"])
        return self.answer


def test_s7_registre_perime_absent_du_prompt_planner():
    async def scenario():
        sim = HostSim()
        await sim.register_manifest(_manifest_with_target_contract())
        live = sim.runtime_state.target_registry.upsert(
            scope="ui_element", ref="ref-live", description="Vivant")
        dead = sim.runtime_state.target_registry.upsert(
            scope="ui_element", ref="ref-dead", description="Mort")
        sim.runtime_state.target_registry.invalidate(ref="ref-dead")

        planner = Planner.__new__(Planner)
        planner.name = "planner-s7"
        planner.runtime_state = sim.runtime_state
        planner.llm = _PromptCaptureLlm(Plan(
            goal="g",
            steps=[PlanStep(
                id="step_1",
                description="conclure",
                type=StepType.DIRECT_ANSWER,
                response_text="ok",
                expected_result="any",
            )],
        ))
        planner._cached_advice = ""
        planner._last_proposed_plan = None

        variables = {
            "btn_vivant": {
                "value": live.local_name,
                "type": "target",
                "description": "Vivant",
            },
            "btn_mort": {
                "value": dead.local_name,
                "type": "target",
                "description": "Mort",
            },
        }
        await planner.propose_plan("g", "c", "s", variables)
        return planner.llm.prompts[-1]

    prompt = asyncio.run(scenario())

    assert "btn_vivant" in prompt
    assert "btn_mort" not in prompt


def _manifest_with_world_state():
    return {
        "host_name": "hostsim-s7-world",
        "host_version": "1.0.0",
        "tools": [{
            "name": "get_world_state",
            "kind": "utility",
            "description": "Photo du monde.",
            "parameters": {"type": "object", "properties": {}},
        }],
    }


class _FeasibilityCaptureLlm(_PromptCaptureLlm):
    def __init__(self):
        super().__init__(FeasibilityDecision(
            is_possible=True, reason="ok", refined_strategy="s"))
        self.mission_ids = []

    async def generate_structured(self, **kwargs):
        self.prompts.append(kwargs["prompt"])
        self.mission_ids.append(kwargs.get("mission_id"))
        return self.answer


def test_s7_t13_recul_refait_un_snapshot_frais():
    async def scenario():
        sim = HostSim(results={
            "get_world_state": [
                {"result": True, "data": {"fenetre": "monde-A"}},
                {"result": True, "data": {"fenetre": "monde-B"}},
            ],
        })
        await sim.register_manifest(_manifest_with_world_state())

        solver = Solver.__new__(Solver)
        solver.id = "s7-recul"
        solver.goal = "verifier le monde"
        solver.context = ""
        solver.runtime_state = sim.runtime_state
        solver.llm = _FeasibilityCaptureLlm()
        solver.variable_registry = {}
        solver._candidate_skills = []
        solver._cached_advice = ""
        solver._format_candidate_skills = lambda skills: ""

        async def noop(*args, **kwargs):
            return None

        solver.propagate_event = noop

        await solver._check_feasibility()
        await solver._check_feasibility()
        return sim, solver.llm

    sim, llm = asyncio.run(scenario())

    assert len(sim.host_calls) == 2
    assert "monde-A" in llm.prompts[0]
    assert "monde-B" in llm.prompts[1]
    assert "monde-A" not in llm.prompts[1]
