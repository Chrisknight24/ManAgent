"""A1 : les avertissements monde ne doivent JAMAIS être tronqués.

Ils sont passés en variable dédiée `world_warnings` du template convergence.md,
hors du cap `[:1500]` appliqué aux preuves (`evidence`)."""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.solver import Solver  # noqa: F401 (import lourd mais partagé)


def test_convergence_template_renders_world_warnings():
    from core.prompt_loader import get_prompt_loader
    for lang in ("base", "en", "fr"):
        out = get_prompt_loader().load(
            "convergence.md", lang=lang, step_description="d",
            expected_result="e", actual_result="r", tool_status="OK",
            world_warnings="[AVERTISSEMENT] W1",
        )
        assert "W1" in out
        assert "{{" not in out


def test_warning_survives_evidence_cap():
    s = Solver.__new__(Solver)
    s.id = "test-solver"
    s.goal = "but"
    captured = {}

    async def fake_gen(prompt, schema, tag=None, **kw):
        captured["prompt"] = prompt
        return SimpleNamespace(is_convergent=True, reason="ok")

    s.llm = SimpleNamespace(generate_structured=fake_gen)
    s.runtime_state = SimpleNamespace(
        language="en",
        mission_rum={f"k{i}": {"value": "x" * 190} for i in range(8)},
        perception_warnings_by_solver={"test-solver": ["LE MONDE A CHANGE"]},
    )
    s.variable_registry = {}
    s.executor = SimpleNamespace(_perception_trail=[])
    s.current_attempt = SimpleNamespace(nodes=[])

    res = SimpleNamespace(response="y" * 2000, final_context="tout a marché")
    asyncio.run(s._verify_mission_convergence(res))

    assert "LE MONDE A CHANGE" in captured["prompt"]
