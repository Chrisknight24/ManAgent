"""Contrat `target_contract` declare par un outil hote.

Le parseur est pur : il ne journalise pas et ne leve pas pour un manifeste
invalide. Il renvoie un contrat valide ou des diagnostics, afin que l'outil
reste utilisable mais inerte quand le contrat est invalide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


RESERVED_CONSUMER_PARAMS = frozenset({"source_data", "source", "sources"})
SUPPORTED_VERSION = 1
MODES = frozenset({"single", "candidates"})
FIELD_ROLES = frozenset(
    {"ref", "description", "scope", "correlation_id", "generation"}
)

_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")


@dataclass(frozen=True)
class ConsumerSpec:
    param: str
    scope: Optional[str] = None
    required: bool = True


@dataclass(frozen=True)
class ProducerSpec:
    path: str
    mode: str
    scope: Optional[str] = None
    fields: Dict[str, str] = field(default_factory=dict)
    usable_as: Tuple[str, ...] = ()

    @property
    def has_static_scope(self) -> bool:
        return self.scope is not None


@dataclass(frozen=True)
class TargetContract:
    version: int
    consumers: Tuple[ConsumerSpec, ...] = ()
    producers: Tuple[ProducerSpec, ...] = ()
    tool_name: str = ""

    @property
    def is_effective(self) -> bool:
        return bool(self.consumers or self.producers)

    def consumer_param_names(self) -> set:
        return {consumer.param for consumer in self.consumers}

    def producer_specs(self) -> Tuple[ProducerSpec, ...]:
        return self.producers

    def consumer_for(self, param: str) -> Optional[ConsumerSpec]:
        for consumer in self.consumers:
            if consumer.param == param:
                return consumer
        return None


@dataclass
class TargetContractResult:
    contract: Optional[TargetContract] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def consumer_param_names(contract: TargetContract) -> set:
    return contract.consumer_param_names()


def producer_specs(contract: TargetContract) -> Tuple[ProducerSpec, ...]:
    return contract.producer_specs()


def lint_target_payload(tools_payload: Any) -> List[str]:
    """Warnings de lint d'un payload d'outils (sans modifier ni lever)."""

    warnings: List[str] = []
    if not isinstance(tools_payload, (list, tuple)):
        return warnings
    for index, tool in enumerate(tools_payload):
        if not isinstance(tool, dict):
            continue
        if tool.get("targets") and not tool.get("target_contract"):
            name = tool.get("name") or f"index {index}"
            warnings.append(
                f"tool '{name}': 'targets' declared without 'target_contract'; "
                "targets stay inert"
            )
    return warnings


def parse_target_contract(
    raw: Any,
    *,
    tool_name: str = "",
    parameters_schema: Optional[Dict[str, Any]] = None,
) -> TargetContractResult:
    prefix = f"tool '{tool_name}': " if tool_name else ""
    errors: List[str] = []
    warnings: List[str] = []

    def error(message: str) -> None:
        errors.append(prefix + message)

    def warning(message: str) -> None:
        warnings.append(prefix + message)

    if not isinstance(raw, dict):
        error("target_contract must be an object")
        return TargetContractResult(errors=errors, warnings=warnings)

    unknown_keys = set(raw) - {"version", "consumes", "produces"}
    for key in sorted(unknown_keys):
        warning(f"unknown target_contract key: {key}")

    version = raw.get("version")
    if version is None:
        error("version is required")
    elif isinstance(version, bool) or not isinstance(version, int):
        error("version must be an integer")
    elif version != SUPPORTED_VERSION:
        error(f"unsupported version: {version}")

    consumers: List[ConsumerSpec] = []
    consumers_raw = raw.get("consumes", [])
    if consumers_raw is None:
        consumers_raw = []
    if not isinstance(consumers_raw, list):
        error("consumes must be a list")
    else:
        seen_params: set = set()
        for index, item in enumerate(consumers_raw):
            spec = _parse_consumer(
                item,
                index=index,
                parameters_schema=parameters_schema,
                seen_params=seen_params,
                error=error,
            )
            if spec is not None:
                consumers.append(spec)

    producers: List[ProducerSpec] = []
    producers_raw = raw.get("produces", [])
    if producers_raw is None:
        producers_raw = []
    if not isinstance(producers_raw, list):
        error("produces must be a list")
    else:
        for index, item in enumerate(producers_raw):
            spec = _parse_producer(
                item,
                index=index,
                error=error,
                warning=warning,
            )
            if spec is not None:
                producers.append(spec)

    if version == SUPPORTED_VERSION and not consumers and not producers:
        warning("contract has no consumers or producers; it stays inert")

    if errors:
        return TargetContractResult(errors=errors, warnings=warnings)

    contract = TargetContract(
        version=SUPPORTED_VERSION,
        consumers=tuple(consumers),
        producers=tuple(producers),
        tool_name=tool_name,
    )
    return TargetContractResult(contract=contract, warnings=warnings)


def _valid_scope(value: Any) -> bool:
    return isinstance(value, str) and bool(_SCOPE_RE.fullmatch(value.strip()))


def _parse_consumer(
    item: Any,
    *,
    index: int,
    parameters_schema: Optional[Dict[str, Any]],
    seen_params: set,
    error,
) -> Optional[ConsumerSpec]:
    if not isinstance(item, dict):
        error(f"consumes[{index}] must be an object")
        return None

    param = item.get("param")
    if not isinstance(param, str) or not param.strip():
        error(f"consumes[{index}].param is required")
        return None
    param = param.strip()
    if param in RESERVED_CONSUMER_PARAMS:
        error(f"consumer parameter '{param}' is reserved")
        return None
    if param in seen_params:
        error(f"duplicate consumer parameter '{param}'")
        return None
    seen_params.add(param)

    scope = item.get("scope")
    if scope is not None:
        if not _valid_scope(scope):
            error(f"consumes[{index}].scope is invalid: {scope!r}")
        else:
            scope = scope.strip()

    required = item.get("required", True)
    if not isinstance(required, bool):
        error(f"consumes[{index}].required must be a boolean")
        required = True

    if isinstance(parameters_schema, dict):
        properties = parameters_schema.get("properties")
        if isinstance(properties, dict):
            if param not in properties:
                error(
                    f"consumer parameter '{param}' is absent from the tool schema"
                )
            else:
                spec = properties.get(param)
                if isinstance(spec, dict):
                    enum = spec.get("enum")
                    if isinstance(enum, (list, tuple)) and enum:
                        error(
                            f"consumer parameter '{param}' must not declare enum"
                        )

    return ConsumerSpec(param=param, scope=scope, required=required)


def _parse_producer(
    item: Any,
    *,
    index: int,
    error,
    warning,
) -> Optional[ProducerSpec]:
    if not isinstance(item, dict):
        error(f"produces[{index}] must be an object")
        return None

    path = item.get("path")
    if not isinstance(path, str) or not path.strip():
        error(f"produces[{index}].path is required")
        return None
    path = path.strip()
    if not _PATH_RE.fullmatch(path):
        error(f"produces[{index}].path is invalid: {path!r}")
        return None

    mode = item.get("mode")
    if mode not in MODES:
        error(f"produces[{index}].mode must be one of {sorted(MODES)}")
        return None

    scope = item.get("scope")
    if scope is not None:
        if not _valid_scope(scope):
            error(f"produces[{index}].scope is invalid: {scope!r}")
            return None
        scope = scope.strip()

    fields = item.get("fields")
    if not isinstance(fields, dict):
        error(f"produces[{index}].fields must be an object")
        return None
    if not isinstance(fields.get("ref"), str) or not fields.get("ref").strip():
        error(f"produces[{index}].fields.ref is required")
        return None

    dynamic_scope = fields.get("scope")
    if dynamic_scope is not None:
        if not isinstance(dynamic_scope, str) or not dynamic_scope.strip():
            error(f"produces[{index}].fields.scope is invalid")
            return None
        dynamic_scope = dynamic_scope.strip()
    if scope is not None and dynamic_scope is not None:
        error(f"produces[{index}]: scope XOR fields.scope")
        return None
    if scope is None and dynamic_scope is None:
        error(f"produces[{index}]: scope or fields.scope is required")
        return None

    cleaned_fields: Dict[str, str] = {}
    for role, field_name in fields.items():
        if role not in FIELD_ROLES:
            warning(f"produces[{index}].fields has unknown role: {role}")
            continue
        if not isinstance(field_name, str) or not field_name.strip():
            error(f"produces[{index}].fields.{role} is invalid")
            continue
        cleaned_fields[role] = field_name.strip()

    usable_raw = item.get("usable_as")
    if usable_raw is None:
        usable: Tuple[str, ...] = (scope,) if scope is not None else ()
    else:
        if not isinstance(usable_raw, list):
            error(f"produces[{index}].usable_as must be a list")
            return None
        cleaned_usable: List[str] = []
        for value in usable_raw:
            if not _valid_scope(value):
                error(f"produces[{index}].usable_as has invalid scope: {value!r}")
                return None
            normalized = value.strip()
            if normalized not in cleaned_usable:
                cleaned_usable.append(normalized)
        usable = tuple(cleaned_usable)
        if scope is not None and scope not in usable:
            warning(
                f"produces[{index}].usable_as does not include its own scope "
                f"'{scope}'"
            )

    return ProducerSpec(
        path=path,
        mode=mode,
        scope=scope,
        fields=cleaned_fields,
        usable_as=usable,
    )
