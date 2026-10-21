"""S8 : concurrence. Le registre reste synchrone : aucune coroutine."""

import inspect

from core.targets import TargetRegistry


PUBLIC_METHODS = (
    "get",
    "find_by_correlation",
    "find_by_ref",
    "all",
    "entries_for_scope",
    "resolve",
    "upsert",
    "invalidate",
    "apply_updates",
    "purge",
    "clear",
    "snapshot_view",
)


def test_s8_t29_registre_synchrone_sans_coroutine():
    for name in PUBLIC_METHODS:
        method = getattr(TargetRegistry, name)
        assert not inspect.iscoroutinefunction(method), name
        assert not inspect.isasyncgenfunction(method), name
