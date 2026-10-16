"""Vague convergence+juge Bloc B : rendu P2, monde juge, rejected vs failed."""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType, PlanValidationDecision
from core.execution_models import PlanAttempt, FailureClass, ExecutionTree
from core.plan_validator import PlanValidator


def _p2_step():
    return PlanStep(
        id="step_1", description="saisir calcul", type=StepType.TOOL_CALL,
        tool_name="keyboard", tool_args_json='{"mode": "text", "text": "12+34="}',
        expected_result="true", step_context="Calculatrice au premier plan.",
        should_world_state_change_after_action=True, verify_with="get_world_state",
    )


def _plain_step():
    return PlanStep(
        id="step_1", description="ouvrir", type=StepType.TOOL_CALL,
        tool_name="keyboard", tool_args_json="{}",
        expected_result="true",
    )


def _plan(*steps):
    return Plan(goal="g", steps=list(steps))


def test_rendu_montre_p2_en_clair():
    v = PlanValidator.__new__(PlanValidator)
    text = PlanValidator._summarize_plan_for_prompt(v, _plan(_p2_step()))
    assert "Calculatrice au premier plan" in text
    assert "monde=oui" in text
    assert "verif=get_world_state" in text


def test_rendu_cache_p2_nul():
    v = PlanValidator.__new__(PlanValidator)
    text = PlanValidator._summarize_plan_for_prompt(v, _plan(_plain_step()))
    assert "monde=" not in text
    assert "verif=" not in text


def _attempt(num, outcome, failure_class, reason):
    return PlanAttempt(
        attempt_number=num, outcome=outcome, failure_class=failure_class,
        failure_reason=reason, proposed_plan=_plan(_plain_step()).model_dump(mode="json"),
        nodes=[],
    )


def _tree():
    tree = ExecutionTree(solver_id="s1", goal="g", depth=0, status="in_progress")
    tree.attempts.append(_attempt(1, "failed", FailureClass.EXECUTION_FAILURE, "outil false"))
    tree.attempts.append(_attempt(2, "rejected", FailureClass.PLAN_REJECTED_SUPERVISOR, "pas d'ouverture"))
    return tree


def test_historique_distingue_rejected():
    text = PlanValidator.summarize_mission_history(_tree())
    assert "[failed]" in text and "ÉCHEC" in text
    assert "[rejected]" in text and "REFUSÉ" in text
    assert "plan non exécuté" in text


def test_repetition_compte_rejected_avant_exec():
    plan = _plan(_plain_step())
    fact = PlanValidator.build_repetition_fact(plan, _tree().attempts)
    assert fact is not None
    assert "AVANT" in fact


def test_novelty_voit_rejected():
    from core.plan_validator import compare_plan_novelty
    plan = _plan(_plain_step())
    res = compare_plan_novelty(plan, _tree().attempts)
    assert res is not None


def _validator(calls):
    async def fake_gen(prompt, schema, tag=None, **kw):
        calls["prompt"] = prompt
        return PlanValidationDecision(is_conformant=True, reason="ok")
    from core.prompt_loader import PromptLoader
    return PlanValidator(
        llm=SimpleNamespace(generate_structured=fake_gen),
        prompt_loader=PromptLoader(),
        rules_text="regles",
        language="en",
        available_tools={"keyboard"},
        production_skills=set(),
        perception_tools=set(),
        tool_returns={},
        availability_summary="keyboard",
    )


def test_monde_injecte_au_juge():
    calls = {}
    v = _validator(calls)
    out = asyncio.run(v.validate(
        plan=_plan(_plain_step()), child_solver_id="s1", target_goal="g",
        previous_attempts=[], mission_history_tree=None,
        world_snapshot="Avant-plan : Calculatrice",
    ))
    assert bool(out) is True
    assert "Avant-plan : Calculatrice" in calls["prompt"]


def test_monde_absent_pas_de_bloc():
    calls = {}
    v = _validator(calls)
    asyncio.run(v.validate(
        plan=_plan(_plain_step()), child_solver_id="s1", target_goal="g",
        previous_attempts=[], mission_history_tree=None,
        world_snapshot="",
    ))
    assert "CURRENT WORLD STATE" not in calls["prompt"]


def _ref_step(sid, args_json):
    return PlanStep(
        id=sid, description="d", type=StepType.TOOL_CALL,
        tool_name="keyboard", tool_args_json=args_json,
        expected_result="true",
    )


def _tm_step(sid, args_json):
    return PlanStep(
        id=sid, description="d", type=StepType.TOOL_CALL,
        tool_name="tool_manager", tool_args_json=args_json,
        expected_result="true",
    )


def _fake_mgr_enum():
    import types

    def _validate(name, args):
        schema = {"mode": True}
        for req in ["mode"]:
            if req not in args:
                raise ValueError(f"Paramètre requis '{req}' manquant pour l'outil '{name}'")

    return types.SimpleNamespace(
        _tools={"vision": {"name": "vision",
                           "parameters": {"type": "object",
                                          "properties": {"mode": {"type": "string", "enum": ["image"]}},
                                          "required": ["mode"]}}},
        validate_tool_call=staticmethod(_validate),
    )


def test_gate_delegation_sans_subargs_refusee():
    from core.plan_validator import find_incomplete_delegated_args
    plan = _plan(_tm_step("step_1", '{"request": "voir", "source_tool": "vision"}'))
    hits = find_incomplete_delegated_args(plan, None)
    assert len(hits) == 1 and "source_args" in hits[0]


def test_gate_delegation_nl_pure_passe():
    from core.plan_validator import find_incomplete_delegated_args
    plan = _plan(_tm_step("step_1", '{"request": "extrais la clé x"}'))
    assert find_incomplete_delegated_args(plan, None) == []


def test_gate_delegation_enum_viole_refusee():
    from core.plan_validator import find_incomplete_delegated_args
    plan = _plan(_tm_step(
        "step_1", '{"request": "voir", "source_tool": "vision", "source_args": {"mode": "find"}}'))
    hits = find_incomplete_delegated_args(plan, _fake_mgr_enum())
    assert len(hits) == 1 and "image" in hits[0]


def test_gate_delegation_complete_passe():
    from core.plan_validator import find_incomplete_delegated_args
    plan = _plan(_tm_step(
        "step_1", '{"request": "voir", "source_tool": "vision", "source_args": {"mode": "image"}}'))
    assert find_incomplete_delegated_args(plan, _fake_mgr_enum()) == []


def test_gate_signale_sortie_declaree_inutilisee_sans_refuser():
    from core.plan_validator import find_declared_unused_outputs
    producer = _ref_step("step_1", "{}")
    producer.output_variable_name = "data_address_bar_cell"
    consumer = _ref_step("step_2", '{"cell": "$@_data_step_1"}')
    assert find_declared_unused_outputs(_plan(producer, consumer)) == [
        "step_1: data_address_bar_cell"]
    user = _ref_step("step_2", '{"cell": "$@_data_address_bar_cell"}')
    assert find_declared_unused_outputs(_plan(producer, user)) == []


def test_gate_laisse_sortie_declaree_anterieure():
    from core.plan_validator import find_unresolved_plan_refs
    producer = _ref_step("step_1", "{}")
    producer.output_variable_name = "data_telechargements_cell"
    consumer = _ref_step("step_2", '{"cell": "$@_data_telechargements_cell"}')
    assert find_unresolved_plan_refs(_plan(producer, consumer)) == []
    auto = _ref_step("step_3", '{"source": "$@_data_step_1"}')
    assert find_unresolved_plan_refs(_plan(producer, consumer, auto)) == []


def test_gate_refuse_avant_et_inconnu():
    from core.plan_validator import find_unresolved_plan_refs
    consumer = _ref_step("step_1", '{"cell": "$@_data_x_cell"}')
    producer = _ref_step("step_2", "{}")
    producer.output_variable_name = "data_x_cell"
    hits = find_unresolved_plan_refs(_plan(consumer, producer))
    assert len(hits) == 1 and "step_1" in hits[0]
    assert find_unresolved_plan_refs(_plan(
        _ref_step("step_1", '{"a": "null"}'))) != []
    assert find_unresolved_plan_refs(_plan(
        _ref_step("step_1", '{"a": "$@_data_"}'))) != []


def test_consigne_monde_dans_prompt_juge():
    calls = {}
    v = _validator(calls)
    asyncio.run(v.validate(
        plan=_plan(_plain_step()), child_solver_id="s1", target_goal="g",
        previous_attempts=[], mission_history_tree=None,
        world_snapshot="x",
    ))
    assert "REMAINDER" in calls["prompt"]
    assert "verification step" in calls["prompt"]


def test_rendu_marque_troncature_sans_mentir():
    v = PlanValidator.__new__(PlanValidator)
    long_args = '{"request": "' + "z" * 600 + '", "mode": "image"}'
    text = PlanValidator._summarize_plan_for_prompt(
        v, _plan(_ref_step("step_1", long_args)))
    assert "[tronqué à" in text
    assert '{"mod' not in text.replace("[tronqué", "")
    short = PlanValidator._summarize_plan_for_prompt(
        v, _plan(_ref_step("step_1", '{"mode": "image"}')))
    assert "[tronqué" not in short and '"mode": "image"' in short


def _perc_step(sid, args_json, out=None):
    import json as _json
    args = _json.loads(args_json)
    args.setdefault("source_tool", "vision")
    args.setdefault("source_args", {"mode": "image"})
    s = PlanStep(
        id=sid, description="lire", type=StepType.TOOL_CALL,
        tool_name="perceive_understand", tool_args_json=_json.dumps(args),
        expected_result="any", output_variable_name=out,
    )
    return s


def test_gate_perception_non_consommee_refusee():
    from core.plan_validator import find_unconsumed_perception_outputs
    perc = _perc_step("step_4", '{"question": "YouTube ?"}')
    ans = PlanStep(
        id="step_5", description="dire", type=StepType.DIRECT_ANSWER,
        expected_result="ok", response_text="YouTube affiché.",
    )
    hits = find_unconsumed_perception_outputs(_plan(perc, ans), {"vision"})
    assert len(hits) == 1 and "step_4" in hits[0]
    assert "non consommée" not in hits[0] and "Branchez" not in hits[0]


def test_gate_perception_consommee_ou_attente_passe():
    from core.plan_validator import find_unconsumed_perception_outputs
    perc = _perc_step("step_4", '{"question": "YouTube ?"}')
    use = _ref_step("step_5", '{"source": "$@_data_step_4"}')
    assert find_unconsumed_perception_outputs(_plan(perc, use), {"vision"}) == []
    wait = _ref_step("step_5", '{"mode": "wait", "wait_mode": "screen_change"}')
    assert find_unconsumed_perception_outputs(_plan(perc, wait), {"vision"}) == []
