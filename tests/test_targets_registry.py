"""S0 : registre de cibles synchrone (canonicalisation, cycle de vie)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.targets import (
    TargetInactive,
    TargetNotFound,
    TargetRef,
    TargetRegistry,
    TargetSchemaError,
    TargetScopeMismatch,
    TargetUpdate,
    canonical_ref,
)


def test_canonical_ref_is_stable_for_objects():
    left = canonical_ref({"b": 1, "a": [2, 3]})
    right = canonical_ref({"a": [2, 3], "b": 1})
    assert left == right == '{"a":[2,3],"b":1}'


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", float("nan"), float("inf"), {}, [], {1, 2}, b"x"],
)
def test_canonical_ref_rejects_non_refs(value):
    with pytest.raises(TargetSchemaError):
        canonical_ref(value)


def test_target_ref_equality_and_hash():
    first = TargetRef({"a": 1, "b": 2})
    second = TargetRef({"b": 2, "a": 1})
    assert first == second
    assert hash(first) == hash(second)


def test_new_entry_and_snapshot_never_expose_ref():
    registry = TargetRegistry()
    entry = registry.upsert(
        scope="ui_element",
        ref={"id": 42},
        description="Bouton fermer",
        correlation_id="c1",
        generation=1,
    )
    assert entry.active and registry.resolve(entry.local_name).value == {"id": 42}
    view = registry.snapshot_view()
    assert list(view) == [entry.local_name]
    meta = view[entry.local_name]
    assert meta["type"] == "target" and meta["scope"] == "ui_element"
    for forbidden in ("ref", "correlation_id", "generation", "canonical"):
        assert forbidden not in meta
    assert "42" not in str(meta)


def test_dedup_by_correlation_then_by_ref():
    registry = TargetRegistry()
    first = registry.upsert(scope="ui", ref="r1", correlation_id="c1")
    same_correlation = registry.upsert(
        scope="ui", ref="r1", description="new", correlation_id="c1"
    )
    assert same_correlation.local_name == first.local_name
    assert same_correlation.description == "new"

    other = TargetRegistry()
    first_ref = other.upsert(scope="ui", ref="r2")
    same_ref = other.upsert(scope="ui", ref="r2", description="ref")
    assert same_ref.local_name == first_ref.local_name
    assert len(other) == 1


def test_generation_wins_and_absent_never_overwrites():
    registry = TargetRegistry()
    entry = registry.upsert(
        scope="ui", ref="r1", correlation_id="c1", generation=2
    )
    older = registry.upsert(
        scope="ui", ref="r0", correlation_id="c1", generation=1
    )
    assert older.ref.canonical == TargetRef("r1").canonical
    assert older.generation == 2

    without = registry.upsert(
        scope="ui", ref="r9", correlation_id="c1", generation=None
    )
    assert without.ref.canonical == TargetRef("r1").canonical
    assert without.generation == 2

    newer = registry.upsert(
        scope="ui", ref="r3", correlation_id="c1", generation=3
    )
    assert newer.local_name == entry.local_name
    assert newer.ref.canonical == TargetRef("r3").canonical
    assert newer.generation == 3


def test_invalidate_then_aba_uses_a_new_local_name():
    registry = TargetRegistry()
    old = registry.upsert(scope="ui", ref="r1", correlation_id="c1")
    assert registry.invalidate(correlation_id="c1") == 1
    assert not registry.get(old.local_name).active
    assert registry.snapshot_view() == {}
    with pytest.raises(TargetInactive):
        registry.resolve(old.local_name)

    new = registry.upsert(scope="ui", ref="r1", correlation_id="c2")
    assert new.local_name != old.local_name
    assert registry.resolve(new.local_name).canonical == TargetRef("r1").canonical


def test_reactivation_by_correlation_keeps_the_alias():
    registry = TargetRegistry()
    old = registry.upsert(scope="ui", ref="r1", correlation_id="c1", generation=1)
    registry.invalidate(correlation_id="c1")
    revived = registry.upsert(
        scope="ui", ref="r2", correlation_id="c1", generation=2
    )
    assert revived.local_name == old.local_name
    assert revived.active and revived.generation == 2


def test_invalidate_without_generation_does_not_erase_generation():
    registry = TargetRegistry()
    entry = registry.upsert(scope="ui", ref="r1", correlation_id="c1", generation=2)
    assert registry.invalidate(correlation_id="c1", generation=None) == 0
    assert registry.get(entry.local_name).active


def test_scope_mismatch_and_missing_name():
    registry = TargetRegistry()
    entry = registry.upsert(scope="ui_element", ref="r1")
    assert registry.resolve(entry.local_name, "ui_element")
    with pytest.raises(TargetScopeMismatch):
        registry.resolve(entry.local_name, "window")
    with pytest.raises(TargetNotFound):
        registry.resolve("absent")


def test_usable_as_is_additive_but_not_transitive():
    registry = TargetRegistry()
    entry = registry.upsert(
        scope="text", ref="r1", usable_as=["text", "button"]
    )
    assert registry.resolve(entry.local_name, "button")
    with pytest.raises(TargetScopeMismatch):
        registry.resolve(entry.local_name, "window")


def test_purge_scope_inactive_and_all():
    registry = TargetRegistry()
    a = registry.upsert(scope="a", ref="a1", correlation_id="ca")
    registry.upsert(scope="b", ref="b1", correlation_id="cb")
    assert registry.purge(scope="a") == 1
    assert registry.get(a.local_name) is None
    assert len(registry) == 1
    assert registry.purge() == 0
    registry.invalidate(correlation_id="cb")
    assert registry.purge() == 1
    assert len(registry) == 0
    rest = registry.upsert(scope="c", ref="c1")
    assert registry.purge(all=True) == 1
    assert registry.get(rest.local_name) is None


def test_apply_updates_and_snapshot_limit():
    registry = TargetRegistry()
    first = registry.upsert(scope="ui", ref="r1", correlation_id="c1")
    registry.upsert(scope="ui", ref="r2", correlation_id="c2")
    assert registry.apply_updates([TargetUpdate(correlation_id="c1")]) == 1
    assert registry.snapshot_view(max_items=1)
    assert list(registry.snapshot_view(max_items=1)) == [
        registry.all()[0].local_name
    ]
    assert registry.get(first.local_name).active is False


def test_invalid_generation_and_name_are_rejected():
    registry = TargetRegistry()
    with pytest.raises(TargetSchemaError):
        registry.upsert(scope="ui", ref="r1", generation=-1)
    with pytest.raises(TargetSchemaError):
        registry.upsert(scope="ui", ref="r1", generation=True)
    with pytest.raises(TargetSchemaError):
        registry.upsert(scope="ui", ref="r1", local_name="bad name")
    with pytest.raises(TargetSchemaError):
        registry.upsert(scope="ui", ref=float("nan"))
