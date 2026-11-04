"""F7 : garde de non-régression sur le schéma exposé à Gemini.

Vérifié le 2026-11-01 : `Plan.model_json_schema()` contient `$defs`/`$ref`,
mais `google.genai._transformers.t_schema` les inline avant l'envoi. Le
modèle voit donc bien les 18 propriétés de `PlanStep`, dont
`output_target_name` avec sa description.

Ce test existe pour qu'un futur refactor du provider ou du modèle Pydantic ne
casse pas silencieusement cette exposition (ce serait alors une vraie cause
d'hallucination, contrairement à ce qu'affirmait un diagnostic précédent).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import Plan

types = pytest.importorskip("google.genai.types")
transformers = pytest.importorskip("google.genai._transformers")


def _inlined_step_properties():
    schema = transformers.t_schema(None, Plan.model_json_schema())
    dumped = schema.model_dump(exclude_none=True)
    return dumped["properties"]["steps"]["items"]["properties"]


def test_step_properties_are_inlined_for_the_model():
    props = _inlined_step_properties()
    assert "output_target_name" in props
    assert "tool_args_json" in props
    assert "status" not in props
    assert "result_context" not in props


def test_output_target_name_keeps_its_description():
    props = _inlined_step_properties()
    description = props["output_target_name"].get("description", "")
    assert "output_target_name" not in description  # pas de boucle inutile
    assert "CIBLE" in description or "TARGET" in description.upper()


def test_raw_pydantic_schema_is_not_directly_a_gemini_schema():
    """Documente le piège : le dict brut n'est pas un `types.Schema` valide."""
    with pytest.raises(Exception):
        types.Schema.model_validate(Plan.model_json_schema())
