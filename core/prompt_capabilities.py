"""Calcul unique des capacites qui pilotent la surface des prompts.

Fonctions pures, sans journalisation ni mutation. Tout nouveau bloc de prompt
doit etre conditionne par un flag calcule ici, avec un defaut OFF.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Set


CAP_TARGETS = "targets"
CAP_WORLD = "world"
CAP_TYPED_ASSETS = "typed_assets"
CAP_SKILLS = "skills"
CAP_DISCOVERY = "discovery"


def _read(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _tools_list(tools_view: Any) -> list:
    return [tool for tool in (tools_view or []) if isinstance(tool, dict)]


def prompt_capabilities(
    runtime_state: Any = None,
    tools_view: Any = None,
    *,
    skills_text: Any = "",
    discovery: Optional[bool] = None,
) -> Set[str]:
    """Retourne les capacites actives. Absent ou invalide = OFF."""

    caps: Set[str] = set()
    tools = _tools_list(tools_view)

    manager = _read(runtime_state, "tools_manager")
    has_contracts = _read(manager, "has_target_contracts")
    targets = False
    if callable(has_contracts):
        try:
            targets = bool(has_contracts())
        except Exception:
            targets = False
    if not targets:
        targets = any(tool.get("target_contract") for tool in tools)
    if targets:
        caps.add(CAP_TARGETS)

    try:
        from core.alignment import world_guidance_visible

        if world_guidance_visible(tools):
            caps.add(CAP_WORLD)
    except Exception:
        pass

    if any(tool.get("returns") for tool in tools):
        caps.add(CAP_TYPED_ASSETS)

    if str(skills_text or "").strip():
        caps.add(CAP_SKILLS)

    if discovery is None:
        discovery = _read(runtime_state, "discovery_engine") is not None
    if discovery:
        caps.add(CAP_DISCOVERY)

    return caps


def _has_candidate_producer(runtime_state: Any) -> bool:
    manager = _read(runtime_state, "tools_manager")
    checker = _read(manager, "has_candidate_target_producers")
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    contracts = _read(manager, "_target_contracts", {}) or {}
    try:
        return any(
            producer.mode == "candidates"
            for contract in contracts.values()
            for producer in getattr(contract, "producers", ())
        )
    except Exception:
        return False


def target_prompt_flags(
    runtime_state: Any = None,
    tools_view: Any = None,
    *,
    skills_text: Any = "",
    discovery: Optional[bool] = None,
) -> Dict[str, bool]:
    """Flags de rendu, tous derives du meme calcul."""

    caps = prompt_capabilities(
        runtime_state,
        tools_view,
        skills_text=skills_text,
        discovery=discovery,
    )
    targets_enabled = CAP_TARGETS in caps
    return {
        "targets_enabled": targets_enabled,
        "target_selection_enabled": targets_enabled
        and _has_candidate_producer(runtime_state),
        "world_guidance": CAP_WORLD in caps,
        "typed_assets_enabled": CAP_TYPED_ASSETS in caps,
        "skills_enabled": CAP_SKILLS in caps,
        "discovery_enabled": CAP_DISCOVERY in caps,
    }
