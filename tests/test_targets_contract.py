"""S0 : contrat target_contract, lint et helpers (sans LLM)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.executor import _REFERENCE_FIELDS
from core.target_contract import (
    RESERVED_CONSUMER_PARAMS,
    consumer_param_names,
    lint_target_payload,
    parse_target_contract,
    producer_specs,
)


SCHEMA = {
    "type": "object",
    "properties": {
        "target": {"type": "string"},
        "target2": {"type": "string"},
    },
}

VALID = {
    "version": 1,
    "consumes": [
        {"param": "target", "scope": "ui_element", "required": True},
    ],
    "produces": [
        {
            "path": "data.targets",
            "mode": "candidates",
            "scope": "ui_element",
            "fields": {
                "ref": "id",
                "description": "label",
                "correlation_id": "corr",
                "generation": "gen",
            },
            "usable_as": ["ui_element"],
        }
    ],
}


def _parse(raw, **kwargs):
    return parse_target_contract(raw, tool_name="t", parameters_schema=SCHEMA, **kwargs)


def test_reserved_names_match_executor():
    assert RESERVED_CONSUMER_PARAMS == _REFERENCE_FIELDS


def test_valid_contract_and_helpers():
    result = _parse(VALID)
    assert result.ok, result.errors
    contract = result.contract
    assert contract is not None and contract.is_effective
    assert consumer_param_names(contract) == {"target"}
    assert producer_specs(contract)[0].mode == "candidates"
    assert contract.consumer_for("target").scope == "ui_element"


def test_dynamic_scope_producer():
    raw = {
        "version": 1,
        "produces": [
            {
                "path": "targets",
                "mode": "single",
                "fields": {"ref": "id", "scope": "kind"},
            }
        ],
    }
    result = _parse(raw)
    assert result.ok, result.errors
    spec = producer_specs(result.contract)[0]
    assert spec.scope is None and spec.fields["scope"] == "kind"
    assert spec.usable_as == ()


@pytest.mark.parametrize(
    "raw, keyword",
    [
        ({"consumes": []}, "version"),
        ({"version": 2}, "unsupported"),
        ({"version": True}, "integer"),
        (
            {"version": 1, "consumes": [{"scope": "ui_element"}]},
            "param",
        ),
        (
            {"version": 1, "consumes": [{"param": "source"}]},
            "reserved",
        ),
        (
            {
                "version": 1,
                "consumes": [{"param": "target"}, {"param": "target"}],
            },
            "duplicate",
        ),
        (
            {"version": 1, "consumes": [{"param": "absent"}]},
            "absent",
        ),
        (
            {"version": 1, "produces": [{"mode": "single", "scope": "ui"}]},
            "path",
        ),
        (
            {
                "version": 1,
                "produces": [
                    {"path": "data..targets", "mode": "single", "scope": "ui"}
                ],
            },
            "path",
        ),
        (
            {
                "version": 1,
                "produces": [
                    {"path": "targets", "mode": "batch", "scope": "ui"}
                ],
            },
            "mode",
        ),
        (
            {
                "version": 1,
                "produces": [
                    {
                        "path": "targets",
                        "mode": "single",
                        "scope": "ui",
                        "fields": {"ref": "id", "scope": "kind"},
                    }
                ],
            },
            "XOR",
        ),
        (
            {
                "version": 1,
                "produces": [
                    {"path": "targets", "mode": "single", "fields": {"ref": "id"}}
                ],
            },
            "scope",
        ),
        (
            {
                "version": 1,
                "produces": [
                    {"path": "targets", "mode": "single", "scope": "ui", "fields": {}}
                ],
            },
            "fields.ref",
        ),
        (
            {
                "version": 1,
                "produces": [
                    {
                        "path": "targets",
                        "mode": "single",
                        "scope": "ui",
                        "fields": {"ref": "id"},
                        "usable_as": ["bad scope"],
                    }
                ],
            },
            "usable_as",
        ),
    ],
)
def test_invalid_contracts(raw, keyword):
    result = _parse(raw)
    assert not result.ok
    assert any(keyword in error for error in result.errors), result.errors


def test_non_object_contract_is_inert():
    result = _parse(["not", "an", "object"])
    assert not result.ok
    assert result.contract is None


def test_empty_contract_warns_but_is_not_effective():
    result = _parse({"version": 1})
    assert result.ok
    assert result.contract is not None
    assert not result.contract.is_effective
    assert any("no consumers" in warning for warning in result.warnings)


def test_unknown_field_role_is_a_warning():
    raw = {
        "version": 1,
        "produces": [
            {
                "path": "targets",
                "mode": "single",
                "scope": "ui",
                "fields": {"ref": "id", "future_role": "x"},
            }
        ],
    }
    result = _parse(raw)
    assert result.ok, result.errors
    assert any("unknown role" in warning for warning in result.warnings)


def test_consumer_enum_is_rejected_because_it_misleads_the_planner():
    schema = {
        "type": "object",
        "properties": {"target": {"type": "string", "enum": ["a", "b"]}},
    }
    result = parse_target_contract(
        {"version": 1, "consumes": [{"param": "target"}]},
        tool_name="t",
        parameters_schema=schema,
    )
    assert not result.ok
    assert any("enum" in error for error in result.errors)


def test_lint_targets_without_contract():
    warnings = lint_target_payload(
        [
            {"name": "a", "targets": [{"path": "x"}]},
            {"name": "b", "target_contract": {"version": 1}},
        ]
    )
    assert len(warnings) == 1
    assert "a" in warnings[0] and "without 'target_contract'" in warnings[0]
