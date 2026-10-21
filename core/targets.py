"""Registre synchrone de cibles opaques.

Le LLM ne voit que des noms locaux ; les references hote restent ici et ne
sortent jamais vers les artefacts LLM-facing. Ce module est volontairement
sans dependance lourde : il peut etre importe par RuntimeState sans cycle.
"""

from __future__ import annotations

import json
import math
import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple


MAX_DESCRIPTION_LENGTH = 200
MAX_LOCAL_NAME_LENGTH = 64

_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_LOCAL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

TARGET_ERROR_CODES = frozenset({
    "TARGET_NOT_FOUND",
    "TARGET_INACTIVE",
    "TARGET_SCOPE_MISMATCH",
    "TARGET_STALE_REF",
    "TARGET_SCHEMA_INVALID",
})


class TargetError(ValueError):
    """Erreur de cible avec un code ferme optionnel."""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message or code
        super().__init__(f"{code}: {self.message}" if message else code)


class TargetSchemaError(TargetError):
    def __init__(self, message: str):
        super().__init__("TARGET_SCHEMA_INVALID", message)


class TargetNotFound(TargetError):
    def __init__(self, local_name: str):
        super().__init__("TARGET_NOT_FOUND", str(local_name))


class TargetInactive(TargetError):
    def __init__(self, local_name: str):
        super().__init__("TARGET_INACTIVE", str(local_name))


class TargetScopeMismatch(TargetError):
    def __init__(self, local_name: str, scope: str):
        super().__init__(
            "TARGET_SCOPE_MISMATCH",
            f"{local_name} (attendu: {scope})",
        )


def canonical_ref(value: Any) -> str:
    """Canonicalise une reference hote en JSON trie, sans NaN/Infinity."""

    if value is None:
        raise TargetSchemaError("ref absente")
    if isinstance(value, str) and not value.strip():
        raise TargetSchemaError("ref vide")
    try:
        with_nan = isinstance(value, float) and not math.isfinite(value)
    except Exception:
        with_nan = False
    if with_nan:
        raise TargetSchemaError("ref non finie")
    if isinstance(value, (dict, list)) and not value:
        raise TargetSchemaError("ref vide")
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise TargetSchemaError(f"ref non serialisable: {exc}") from exc


@dataclass(frozen=True, eq=False)
class TargetRef:
    """Reference opaque, comparee par sa forme canonique."""

    value: Any
    canonical: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "canonical", canonical_ref(self.value))

    @classmethod
    def from_value(cls, value: Any) -> "TargetRef":
        if isinstance(value, cls):
            return value
        return cls(value)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, TargetRef) and self.canonical == other.canonical

    def __hash__(self) -> int:
        return hash(self.canonical)

    def __str__(self) -> str:
        return self.canonical


@dataclass
class TargetEntry:
    """Liaison nom local <-> reference, avec cycle de vie."""

    local_name: str
    scope: str
    ref: TargetRef
    description: str = ""
    correlation_id: Optional[str] = None
    generation: Optional[int] = None
    active: bool = True
    usable_as: Tuple[str, ...] = ()

    @property
    def effective_usable_as(self) -> Tuple[str, ...]:
        if self.usable_as:
            return self.usable_as
        return (self.scope,)

    def accepts_scope(self, scope: str) -> bool:
        return scope == self.scope or scope in self.effective_usable_as

    def snapshot_meta(self) -> Dict[str, Any]:
        return {
            "type": "target",
            "scope": self.scope,
            "description": self.description,
        }


@dataclass(frozen=True)
class TargetUpdate:
    """Mise a jour recue de l'hote (invalidation pour la v1)."""

    correlation_id: Optional[str] = None
    ref: Optional[TargetRef] = None
    op: str = "invalidate"
    generation: Optional[int] = None

    def __post_init__(self) -> None:
        if self.op != "invalidate":
            raise TargetSchemaError(f"op inconnue: {self.op}")
        if self.correlation_id is None and self.ref is None:
            raise TargetSchemaError("update sans correlation_id ni ref")
        if self.generation is not None and (
            isinstance(self.generation, bool) or not isinstance(self.generation, int)
        ):
            raise TargetSchemaError("generation non entiere")


@dataclass(frozen=True)
class TargetSelection:
    """Resultat de selection pour les producteurs (S3)."""

    kind: str
    entries: Tuple[TargetEntry, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        if self.kind not in {"single", "candidates", "none"}:
            raise TargetSchemaError(f"selection inconnue: {self.kind}")


class CandidateIndex:
    """Index interne : correlation_id et ref canonique vers nom local."""

    def __init__(self) -> None:
        self._by_correlation: Dict[str, str] = {}
        self._by_ref: Dict[str, str] = {}

    def get_by_correlation(self, correlation_id: Optional[str]) -> Optional[str]:
        if correlation_id is None:
            return None
        return self._by_correlation.get(correlation_id)

    def get_by_ref(self, canonical: str) -> Optional[str]:
        return self._by_ref.get(canonical)

    def set_correlation(self, correlation_id: Optional[str], local_name: str) -> None:
        if correlation_id is not None:
            self._by_correlation[correlation_id] = local_name

    def set_ref(self, canonical: str, local_name: str) -> None:
        self._by_ref[canonical] = local_name

    def remove_ref(self, canonical: str) -> None:
        self._by_ref.pop(canonical, None)

    def remove_correlation(self, correlation_id: str) -> None:
        self._by_correlation.pop(correlation_id, None)

    def remove(self, entry: TargetEntry) -> None:
        if entry.correlation_id is not None:
            if self._by_correlation.get(entry.correlation_id) == entry.local_name:
                self._by_correlation.pop(entry.correlation_id, None)
        if self._by_ref.get(entry.ref.canonical) == entry.local_name:
            self._by_ref.pop(entry.ref.canonical, None)

    def clear(self) -> None:
        self._by_correlation.clear()
        self._by_ref.clear()


def _clean_scope(scope: Any) -> str:
    if not isinstance(scope, str):
        raise TargetSchemaError("scope manquant")
    value = scope.strip()
    if not value or not _SCOPE_RE.fullmatch(value):
        raise TargetSchemaError(f"scope invalide: {scope!r}")
    return value


def _clean_local_name(local_name: Any) -> str:
    if not isinstance(local_name, str):
        raise TargetSchemaError("nom local invalide")
    value = local_name.strip()
    if not value or len(value) > MAX_LOCAL_NAME_LENGTH:
        raise TargetSchemaError(f"nom local invalide: {local_name!r}")
    if not _LOCAL_NAME_RE.fullmatch(value):
        raise TargetSchemaError(f"nom local invalide: {local_name!r}")
    return value


def _clean_correlation_id(correlation_id: Any) -> Optional[str]:
    if correlation_id is None:
        return None
    if isinstance(correlation_id, bool):
        raise TargetSchemaError("correlation_id invalide")
    if isinstance(correlation_id, (int, float)):
        correlation_id = str(correlation_id)
    if not isinstance(correlation_id, str):
        raise TargetSchemaError("correlation_id invalide")
    value = correlation_id.strip()
    if not value:
        raise TargetSchemaError("correlation_id vide")
    return value


def _clean_generation(generation: Any) -> Optional[int]:
    if generation is None:
        return None
    if isinstance(generation, bool) or not isinstance(generation, int):
        raise TargetSchemaError("generation non entiere")
    if generation < 0:
        raise TargetSchemaError("generation negative")
    return generation


def _clean_description(description: Any) -> str:
    if not isinstance(description, str):
        return ""
    return description.strip()[:MAX_DESCRIPTION_LENGTH]


def _clean_usable_as(usable_as: Any, scope: str) -> Tuple[str, ...]:
    if usable_as is None:
        return (scope,)
    if isinstance(usable_as, str):
        items: Iterable[Any] = (usable_as,)
    else:
        try:
            items = tuple(usable_as)
        except TypeError as exc:
            raise TargetSchemaError("usable_as invalide") from exc
    cleaned: List[str] = []
    for item in items:
        value = _clean_scope(item)
        if value not in cleaned:
            cleaned.append(value)
    if scope not in cleaned:
        cleaned.insert(0, scope)
    return tuple(cleaned)


class TargetRegistry:
    """Registre synchrone, sans coroutine, partage par le runtime."""

    def __init__(self) -> None:
        self._entries: Dict[str, TargetEntry] = {}
        self._index = CandidateIndex()

    def __len__(self) -> int:
        return sum(1 for entry in self._entries.values() if entry.active)

    def __contains__(self, local_name: object) -> bool:
        entry = self._entries.get(str(local_name))
        return bool(entry and entry.active)

    def get(self, local_name: str) -> Optional[TargetEntry]:
        return self._entries.get(local_name)

    def find_by_correlation(self, correlation_id: str) -> Optional[TargetEntry]:
        name = self._index.get_by_correlation(correlation_id)
        return self._entries.get(name) if name is not None else None

    def find_by_ref(self, ref: Any) -> Optional[TargetEntry]:
        try:
            canonical = TargetRef.from_value(ref).canonical
        except TargetSchemaError:
            return None
        name = self._index.get_by_ref(canonical)
        return self._entries.get(name) if name is not None else None

    def all(self, include_inactive: bool = False) -> List[TargetEntry]:
        entries = [
            entry for entry in self._entries.values()
            if include_inactive or entry.active
        ]
        return sorted(entries, key=lambda entry: entry.local_name)

    def entries_for_scope(self, scope: str) -> List[TargetEntry]:
        return [entry for entry in self.all() if entry.accepts_scope(scope)]

    def resolve(self, local_name: str, scope: Optional[str] = None) -> TargetRef:
        entry = self._entries.get(local_name)
        if entry is None:
            raise TargetNotFound(local_name)
        if not entry.active:
            raise TargetInactive(local_name)
        if scope is not None and not entry.accepts_scope(scope):
            raise TargetScopeMismatch(local_name, scope)
        return entry.ref

    def upsert(
        self,
        *,
        scope: str,
        ref: Any,
        description: str = "",
        correlation_id: Optional[str] = None,
        generation: Optional[int] = None,
        local_name: Optional[str] = None,
        usable_as: Optional[Iterable[str]] = None,
    ) -> TargetEntry:
        scope_value = _clean_scope(scope)
        ref_value = TargetRef.from_value(ref)
        corr = _clean_correlation_id(correlation_id)
        gen = _clean_generation(generation)
        desc = _clean_description(description)
        usable = _clean_usable_as(usable_as, scope_value)
        requested_name = _clean_local_name(local_name) if local_name is not None else None

        existing: Optional[TargetEntry] = None
        found_by_correlation = False
        if corr is not None:
            name = self._index.get_by_correlation(corr)
            if name is not None:
                existing = self._entries.get(name)
                found_by_correlation = existing is not None
                if existing is None:
                    self._index.remove_correlation(corr)
        if existing is None:
            name = self._index.get_by_ref(ref_value.canonical)
            if name is not None:
                existing = self._entries.get(name)
                if existing is None:
                    self._index.remove_ref(ref_value.canonical)

        if existing is not None:
            if requested_name is not None and requested_name != existing.local_name:
                raise TargetSchemaError(
                    f"nom local deja lie: {existing.local_name} != {requested_name}"
                )
            if not self._generation_allows(gen, existing.generation):
                return existing
            if not existing.active and not found_by_correlation:
                existing = None
            else:
                old_canonical = existing.ref.canonical
                existing.ref = ref_value
                existing.scope = scope_value
                if desc:
                    existing.description = desc
                existing.generation = gen if gen is not None else existing.generation
                existing.usable_as = usable
                if corr is not None:
                    existing.correlation_id = corr
                existing.active = True
                self._index.remove_ref(old_canonical)
                self._index.set_ref(ref_value.canonical, existing.local_name)
                self._index.set_correlation(existing.correlation_id, existing.local_name)
                return existing

        name = requested_name or self._mint_name(scope_value)
        if name in self._entries:
            raise TargetSchemaError(f"nom local deja utilise: {name}")
        entry = TargetEntry(
            local_name=name,
            scope=scope_value,
            ref=ref_value,
            description=desc,
            correlation_id=corr,
            generation=gen,
            active=True,
            usable_as=usable,
        )
        self._entries[name] = entry
        self._index.set_ref(ref_value.canonical, name)
        self._index.set_correlation(corr, name)
        return entry

    def invalidate(
        self,
        *,
        correlation_id: Optional[str] = None,
        ref: Any = None,
        generation: Optional[int] = None,
    ) -> int:
        corr = _clean_correlation_id(correlation_id)
        gen = _clean_generation(generation)
        entry: Optional[TargetEntry] = None
        if corr is not None:
            name = self._index.get_by_correlation(corr)
            if name is not None:
                entry = self._entries.get(name)
        if entry is None and ref is not None:
            ref_value = TargetRef.from_value(ref)
            name = self._index.get_by_ref(ref_value.canonical)
            if name is not None:
                entry = self._entries.get(name)
        if entry is None or not entry.active:
            return 0
        if not self._generation_allows(gen, entry.generation):
            return 0
        entry.active = False
        self._index.remove_ref(entry.ref.canonical)
        return 1

    def apply_updates(self, updates: Iterable[TargetUpdate]) -> int:
        applied = 0
        for update in updates:
            applied += self.invalidate(
                correlation_id=update.correlation_id,
                ref=update.ref,
                generation=update.generation,
            )
        return applied

    def purge(self, *, all: bool = False, scope: Optional[str] = None) -> int:
        if all:
            count = len(self._entries)
            self._entries.clear()
            self._index.clear()
            return count
        if scope is not None:
            scope_value = _clean_scope(scope)
            doomed = [
                name for name, entry in self._entries.items()
                if entry.scope == scope_value
            ]
        else:
            doomed = [
                name for name, entry in self._entries.items()
                if not entry.active
            ]
        for name in doomed:
            entry = self._entries.pop(name, None)
            if entry is not None:
                self._index.remove(entry)
        return len(doomed)

    def clear(self) -> int:
        return self.purge(all=True)

    def snapshot_view(self, max_items: int = 50) -> Dict[str, Dict[str, Any]]:
        entries = self.all()
        if max_items > 0:
            entries = entries[:max_items]
        return {entry.local_name: entry.snapshot_meta() for entry in entries}

    @staticmethod
    def _generation_allows(
        incoming: Optional[int],
        existing: Optional[int],
    ) -> bool:
        if existing is None:
            return True
        if incoming is None:
            return False
        return incoming >= existing

    def _mint_name(self, scope: str) -> str:
        base = re.sub(r"[^a-z0-9_]+", "_", scope.lower()).strip("_") or "item"
        for _ in range(100):
            name = f"target_{base}_{secrets.token_hex(4)}"
            if name not in self._entries:
                return name
        raise TargetSchemaError("impossible de generer un nom local unique")
