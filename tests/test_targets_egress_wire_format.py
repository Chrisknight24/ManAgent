"""Egress cible : la valeur envoyee respecte le type declare au manifeste.

Bug reel (mission `008bcdba`, 02:58:12 / 02:59:24 / 03:00:51) : l'hote
declare `target` en `type: "string"`, ManAgent lui envoyait l'objet brut
`{"kind": "img", "id": "..."}` ; le gate C++ `hasTargetArg`
(`rpahosttools.cpp:857-859`) teste `.toString()` sur un `QJsonObject`
(-> `""`) et refuse avec `TARGET_REQUIRED`.

Regle : si le schema du parametre consommateur declare `type: "string"`,
la reference est serialisee en `"<kind>:<id>"` (forme acceptee par
`s_parseTargetRef`, `rpahosttools.cpp:290-298`). Tout autre cas est
laisse tel quel (aucune regression sur les hotes qui attendent l'objet).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.target_contract import parse_target_contract, resolve_consumer_arguments
from core.targets import TargetRegistry

_REF = {"kind": "img", "id": "6d70afbb-3e36-4204-9f33-21237512c37b"}


def _registry(ref=None, local_name="loc_rect"):
    registry = TargetRegistry()
    registry.upsert(
        scope="target",
        ref=_REF if ref is None else ref,
        description="outil rectangle",
        local_name=local_name,
    )
    return registry


def _contract(param_type="string"):
    schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "target": ({"type": param_type} if param_type else {}),
        },
        "required": ["action", "target"],
    }
    result = parse_target_contract(
        {"version": 1, "consumes": [{"param": "target", "scope": "target"}]},
        tool_name="mouse",
        parameters_schema=schema,
    )
    assert result.ok, result.errors
    return result.contract


def test_object_ref_is_serialized_when_schema_declares_string():
    resolved, display, error = resolve_consumer_arguments(
        _registry(), _contract("string"), {"action": "click", "target": "loc_rect"}
    )
    assert error is None
    assert resolved["target"] == "img:6d70afbb-3e36-4204-9f33-21237512c37b"
    # L'affichage garde le nom local : aucune reference brute dans les logs LLM.
    assert display["target"] == "loc_rect"


def test_object_ref_is_kept_when_schema_declares_object():
    resolved, _display, error = resolve_consumer_arguments(
        _registry(), _contract("object"), {"action": "click", "target": "loc_rect"}
    )
    assert error is None
    assert resolved["target"] == _REF


def test_object_ref_is_kept_when_schema_has_no_type():
    resolved, _display, error = resolve_consumer_arguments(
        _registry(), _contract(None), {"action": "click", "target": "loc_rect"}
    )
    assert error is None
    assert resolved["target"] == _REF


def test_string_ref_is_unchanged_with_string_schema():
    resolved, _display, error = resolve_consumer_arguments(
        _registry(ref="ref-e4-start"),
        _contract("string"),
        {"action": "click", "target": "loc_rect"},
    )
    assert error is None
    assert resolved["target"] == "ref-e4-start"


def test_unknown_object_shape_falls_back_to_json_string():
    weird = {"foo": "bar", "n": 1}
    resolved, _display, error = resolve_consumer_arguments(
        _registry(ref=weird), _contract("string"), {"action": "click", "target": "loc_rect"}
    )
    assert error is None
    assert isinstance(resolved["target"], str)
    assert json.loads(resolved["target"]) == weird


def test_unresolved_alias_is_still_rejected_deterministically():
    resolved, display, error = resolve_consumer_arguments(
        _registry(), _contract("string"), {"action": "click", "target": "$@_jamais_publie"}
    )
    assert error is not None
    assert error["code"] == "TARGET_NOT_FOUND"
    assert resolved["target"] == "$@_jamais_publie"
    assert display is None
