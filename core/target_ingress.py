"""Membrane ingress : references hote -> noms locaux avant stockage.

La projection remplace chaque cible produite par sa forme publique
(`key`, `description`, `scope`) et retire `target_updates` du payload
stocke. Sans contrat, rien n'est appele : le comportement reste inchange.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from core.target_contract import ProducerSpec, TargetContract
from core.targets import TargetRegistry, TargetSchemaError, TargetUpdate


_MISSING = object()


def _read_path(obj: Any, path: Optional[str]) -> Any:
    if not isinstance(path, str) or not path:
        return _MISSING
    current = obj
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _set_path(obj: Any, path: str, value: Any) -> bool:
    parts = path.split(".")
    current = obj
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    if not isinstance(current, dict):
        return False
    current[parts[-1]] = value
    return True


def _clean_generation(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _canonical_entry(entry: Any) -> Dict[str, str]:
    return {
        "key": entry.local_name,
        "description": entry.description,
        "scope": entry.scope,
    }


def _lookup(registry: TargetRegistry, correlation_id: Any, ref: Any) -> Any:
    entry = None
    if isinstance(correlation_id, str) and correlation_id.strip():
        entry = registry.find_by_correlation(correlation_id.strip())
    if entry is None and ref is not _MISSING:
        entry = registry.find_by_ref(ref)
    if entry is None or not entry.active:
        return None
    return entry


def _project_item(
    registry: TargetRegistry,
    spec: ProducerSpec,
    item: Any,
    *,
    register: bool,
) -> Any:
    if not isinstance(item, dict):
        return None

    fields = spec.fields
    ref_raw = _read_path(item, fields.get("ref"))
    if ref_raw is _MISSING or ref_raw is None:
        return None

    scope = spec.scope
    if scope is None:
        scope_raw = _read_path(item, fields.get("scope"))
        if not isinstance(scope_raw, str) or not scope_raw.strip():
            return None
        scope = scope_raw.strip()

    correlation_raw = _read_path(item, fields.get("correlation_id"))
    if correlation_raw is _MISSING:
        correlation_raw = None
    if isinstance(correlation_raw, bool):
        correlation_raw = None

    generation_raw = _read_path(item, fields.get("generation"))
    generation = _clean_generation(
        None if generation_raw is _MISSING else generation_raw
    )

    description_raw = _read_path(item, fields.get("description"))
    description = description_raw if isinstance(description_raw, str) else ""

    if not register:
        return _lookup(registry, correlation_raw, ref_raw)

    try:
        return registry.upsert(
            scope=scope,
            ref=ref_raw,
            description=description,
            correlation_id=correlation_raw,
            generation=generation,
            usable_as=list(spec.usable_as) or None,
        )
    except TargetSchemaError:
        return None


def project_target_payload(
    registry: Optional[TargetRegistry],
    contract: Optional[TargetContract],
    payload: Any,
    *,
    register: bool = True,
) -> Tuple[Any, int]:
    """Projette les producteurs de cibles d'un payload hote.

    `register=True` : enregistre les cibles (ingress normal).
    `register=False` : projection seule, aucune inscription (snapshot monde).
    Retourne `(payload_projete, nombre_de_cibles_projetees)`.
    """
    if registry is None or contract is None or not contract.producers:
        return payload, 0
    if not isinstance(payload, dict):
        return payload, 0

    projected = copy.deepcopy(payload)
    count = 0
    for spec in contract.producers:
        current = _read_path(projected, spec.path)
        if current is _MISSING:
            continue

        if spec.mode == "single":
            entry = _project_item(registry, spec, current, register=register)
            projected_value = _canonical_entry(entry) if entry is not None else None
            if entry is not None:
                count += 1
        else:
            items = current if isinstance(current, list) else []
            projected_items: List[Dict[str, str]] = []
            for item in items:
                entry = _project_item(registry, spec, item, register=register)
                if entry is not None:
                    projected_items.append(_canonical_entry(entry))
            projected_value = projected_items
            count += len(projected_items)

        _set_path(projected, spec.path, projected_value)

    return projected, count


def apply_target_updates(
    registry: Optional[TargetRegistry],
    payload: Any,
) -> Tuple[Any, int]:
    """Applique puis retire `target_updates` du payload stocke."""
    if not isinstance(payload, dict) or "target_updates" not in payload:
        return payload, 0

    projected = copy.deepcopy(payload)
    raw_updates = projected.pop("target_updates")
    if not isinstance(raw_updates, list):
        raw_updates = [raw_updates]

    updates: List[TargetUpdate] = []
    for item in raw_updates:
        if not isinstance(item, dict):
            continue
        if (item.get("op") or "invalidate") != "invalidate":
            continue
        try:
            updates.append(TargetUpdate(
                correlation_id=item.get("correlation_id"),
                ref=item.get("ref"),
                generation=_clean_generation(item.get("generation")),
                op="invalidate",
            ))
        except TargetSchemaError:
            continue

    applied = registry.apply_updates(updates) if registry is not None else 0
    return projected, applied


def strip_target_updates(payload: Any) -> Any:
    """Retire `target_updates` sans appliquer (lecture passive)."""
    if not isinstance(payload, dict) or "target_updates" not in payload:
        return payload
    projected = copy.deepcopy(payload)
    projected.pop("target_updates", None)
    return projected
