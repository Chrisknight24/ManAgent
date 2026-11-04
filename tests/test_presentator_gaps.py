"""Rapport final honnete : les lacunes du plan atteignent le presentateur.

Bug reel (mission `ee848a24`, 02:32:10) : le plan etait partiel (2 lacunes),
la convergence a dit « Mission accomplie. » et le presentateur a produit
« ### Mission accomplie » — sans jamais recevoir les lacunes
(`prompts_log/0011_presentator_output.md.en.md.vars.txt` : ni `plan_gaps`,
ni `is_partial`, ni `convergence_reason`).
"""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.presentator import Presentator

_GAPS = [
    "l'outil de dessin n'est pas encore selectionne",
    "le carre complet n'est pas encore dessine",
]


def _presentator(captured, *, boom=False):
    async def fake_gen(prompt, schema, tag=None, **kw):
        captured["prompt"] = prompt
        if boom:
            raise RuntimeError("service indisponible")
        return SimpleNamespace(user_report="rapport", summary="resume")

    pres = Presentator.__new__(Presentator)
    pres.runtime_state = SimpleNamespace(
        mission_rum={},
        language="fr",
        presentator_detail_level="brief",
    )
    pres.llm = SimpleNamespace(generate_structured=fake_gen)
    pres._discovery_activated = False
    return pres


def _run(pres, **overrides):
    kwargs = dict(
        goal="dessiner un carre dans Paint",
        final_context="Paint est ouvert.",
        variable_registry={},
        accumulated_response="Paint est ouvert.",
        mission_status="success",
    )
    kwargs.update(overrides)
    return asyncio.run(pres.generate_mission_output(**kwargs))


def test_presentator_prompt_receives_gaps_and_convergence_reason():
    captured = {}
    _run(
        _presentator(captured),
        plan_gaps=list(_GAPS),
        is_partial=True,
        convergence_reason="lacunes non prouvees",
    )
    prompt = captured["prompt"]
    for gap in _GAPS:
        assert gap in prompt
    assert "lacunes non prouvees" in prompt
    assert "partiel" in prompt.lower()


def test_presentator_prompt_has_no_gap_section_without_gaps():
    captured = {}
    _run(_presentator(captured))
    prompt = captured["prompt"]
    assert _GAPS[0] not in prompt
    assert "LACUNES CONNUES" not in prompt


def test_fallback_never_claims_full_success_when_gaps():
    captured = {}
    out = _run(
        _presentator(captured, boom=True),
        plan_gaps=list(_GAPS),
        is_partial=True,
        convergence_reason="lacunes non prouvees",
    )
    text = out.user_report.lower()
    assert "accomplie" not in text
    assert "partiel" in text


def test_process_forwards_gaps_to_the_prompt():
    captured = {}
    pres = _presentator(captured)
    asyncio.run(pres.process(
        goal="dessiner un carre dans Paint",
        final_context="Paint est ouvert.",
        variable_registry={},
        accumulated_response="Paint est ouvert.",
        mission_status="success",
        plan_gaps=list(_GAPS),
        is_partial=True,
        convergence_reason="lacunes non prouvees",
    ))
    prompt = captured["prompt"]
    for gap in _GAPS:
        assert gap in prompt
    assert "partiel" in prompt.lower()


def test_collect_plan_gaps_reads_the_solver_store():
    runtime = SimpleNamespace(
        plan_warnings_by_solver={"solver-1": list(_GAPS)},
    )
    assert Presentator.collect_plan_gaps(runtime, "solver-1", "mission-1") == _GAPS
    # Repli sur l'id de mission quand la cle solveur n'existe pas.
    assert Presentator.collect_plan_gaps(runtime, "inconnu", "solver-1") == _GAPS
    assert Presentator.collect_plan_gaps(runtime, "inconnu", "mission-x") == []
    assert Presentator.collect_plan_gaps(SimpleNamespace(), "s", "m") == []