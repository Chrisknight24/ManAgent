"""BUG F bloc 4 — REGISTRE compact (planner + presentator).

La vue registre du presentator était rendue comme un repr de dict Python
(timestamp + value_hint + quotes = bruit/tokens) ; celle du planner répétait
`source`. On rend une ligne compacte par variable et on retire le bruit.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.prompt_loader import get_prompt_loader, registry_compact

_META = {
    "data_step_1": {
        "description": "Contenu du fichier apollo." * 10,
        "source": "step_1",
        "timestamp": "2026-01-01T00:00:00",
        "type": "data",
        "value_hint": "(donnée masquée – utilisez Progressive Disclosure pour inspecter)",
    },
    "bool_step_1": {
        "description": "Etape 1 OK",
        "source": "step_1",
        "timestamp": "2026-01-01T00:00:00",
        "type": "bool",
        "value_hint": "(bool: true)",
    },
}


def test_compact_une_ligne_par_var_et_drop_timestamp():
    out = registry_compact(_META, prefix="$@_")
    assert out.count("\n") == 1
    assert "$@_data_step_1 (data)" in out
    assert "timestamp" not in out
    assert "2026-01-01" not in out
    assert "(bool: true)" in out


def test_compact_tronque_la_description():
    out = registry_compact(_META)
    assert "…" in out
    assert len(max(out.splitlines(), key=len)) < 240


def test_compact_vide():
    assert registry_compact({}) == ""
    assert registry_compact(None) == ""


def test_planner_registre_compact():
    out = get_prompt_loader().load(
        "planner.md", lang="base", goal="g", strategy="s", context="c",
        advice="", skills="", model_id="m", supported_modalities=[],
        unsupported_modalities=[], tools=[],
        variable_registry={"data_step_1": {"description": "d", "source": "step_1"}})
    assert "$@_data_step_1" in out
    assert "source :" not in out


def test_presentator_registre_pas_repr_dict():
    out = get_prompt_loader().load(
        "presentator_output.md", lang="base", goal="g", final_context="x",
        accumulated_response="", mission_status="success", error_reason="",
        session_mood="neutre", detail_level="brief", variable_registry=_META)
    assert "$@_data_step_1" not in out          # pas de prefix cote presentator
    assert "data_step_1 (data)" in out
    assert "'value_hint'" not in out            # plus de repr de dict
    assert "{'" not in out
