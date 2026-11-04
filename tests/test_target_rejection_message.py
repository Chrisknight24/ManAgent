"""P1.3 — message de rejet des références inconnues : groupé, non redondant.

Bug réel (mission `4a810e6e`, 29/10) : 3 consommateurs `target` sans
`output_target_name` produisaient 5 blocs quasi identiques (~3 560 chars),
dont 5 fois la même liste « Variables disponibles ». Le modèle ne peut pas
exploiter un mur de texte répété : une seule piste groupée suffit.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan, PlanStep, StepType
from core.planner import Planner


def _target(sid, label, target_name=None):
    return PlanStep(
        id=sid,
        description=f"créer la cible {label}",
        type=StepType.TOOL_CALL,
        tool_name="target",
        tool_args_json=json.dumps({"cell": "F2", "label": label}),
        expected_result="true",
        output_target_name=target_name,
    )


def _mouse(sid, ref):
    return PlanStep(
        id=sid,
        description="cliquer sur la cible",
        type=StepType.TOOL_CALL,
        tool_name="mouse",
        tool_args_json=json.dumps({"action": "click", "target": f"$@_{ref}"}),
        expected_result="true",
    )


def _write(sid, ref):
    return PlanStep(
        id=sid,
        description="écrire un fichier",
        type=StepType.TOOL_CALL,
        tool_name="write_file",
        tool_args_json=json.dumps({"path": f"$@_{ref}"}),
        expected_result="true",
    )


def _paint_like_plan():
    return Plan(goal="peindre", steps=[
        _target("step_1", "outil rectangle"),
        _target("step_3", "départ carré"),
        _target("step_5", "arrivée carré"),
        _mouse("step_2", "data_outil_rectangle"),
        _mouse("step_4", "data_depart_carre"),
        _mouse("step_6", "data_arrivee_carre"),
    ])


def test_target_hint_emitted_once():
    ok, errors = Planner._validate_plan(None, _paint_like_plan(), {})
    assert ok is False
    joined = "\n".join(errors)
    assert joined.count("output_target_name") == 1, joined
    for name in ("outil_rectangle", "depart_carre", "arrivee_carre"):
        assert name in joined


def test_unknown_vars_grouped_in_one_hint():
    plan = Plan(goal="g", steps=[
        _write("step_1", "unknown_path_a"),
        _write("step_2", "unknown_path_b"),
        _write("step_3", "unknown_path_c"),
        _write("step_4", "unknown_path_d"),
        _write("step_5", "unknown_path_e"),
    ])
    ok, errors = Planner._validate_plan(None, plan, {})
    assert ok is False
    joined = "\n".join(errors)
    assert joined.lower().count("disponibles") == 1, joined


def test_message_budget_under_1200_chars_for_5_refs():
    plan = Plan(goal="g", steps=[
        _write("step_1", "unknown_path_a"),
        _write("step_2", "unknown_path_b"),
        _write("step_3", "unknown_path_c"),
        _write("step_4", "unknown_path_d"),
        _write("step_5", "unknown_path_e"),
    ])
    ok, errors = Planner._validate_plan(None, plan, {})
    assert ok is False
    joined = "\n".join(errors)
    assert len(joined) < 1200, (len(joined), joined)


def test_target_plan_budget_under_1200_chars():
    ok, errors = Planner._validate_plan(None, _paint_like_plan(), {})
    assert ok is False
    joined = "\n".join(errors)
    assert len(joined) < 1200, (len(joined), joined)


def test_no_target_hint_without_target_arg():
    plan = Plan(goal="g", steps=[_write("step_1", "unknown_path")])
    ok, errors = Planner._validate_plan(None, plan, {})
    assert ok is False
    joined = "\n".join(errors)
    assert "output_target_name" not in joined
    assert "unknown_path" in joined