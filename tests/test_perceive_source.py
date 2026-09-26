"""perceive_understand exige une source (générique, aucun hôte cité)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import PlanStep, StepType


def _step(args):
    return PlanStep(
        id="s1", description="lire", type=StepType.TOOL_CALL,
        tool_name="perceive_understand",
        tool_args_json=json.dumps(args),
        expected_result="any",
    )


def test_sans_source_rejete():
    with pytest.raises(ValueError, match="source_tool"):
        _step({"question": "que voir ?"})


def test_sans_question_rejete():
    with pytest.raises(ValueError, match="question"):
        _step({"source_tool": "n_importe_quel_outil_hote"})


def test_avec_source_tool_accepte():
    s = _step({"question": "que voir ?",
               "source_tool": "n_importe_quel_outil_hote",
               "source_args": {}})
    assert s.tool_name == "perceive_understand"


def test_avec_source_data_accepte():
    s = _step({"question": "que voir ?",
               "source_data": "$@_data_capture"})
    assert s.tool_name == "perceive_understand"


def test_autres_outils_inchanges():
    s = PlanStep(
        id="s1", description="attendre", type=StepType.TOOL_CALL,
        tool_name="pause", tool_args_json="{}",
        expected_result="true",
    )
    assert s.tool_name == "pause"
