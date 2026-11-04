import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.targets import TargetNotFound, TargetRegistry
from core.constants import Events


def _records():
    seen = []

    def observer(name, payload):
        seen.append((name, payload))

    return seen, observer


def test_register_refresh_invalidate_purge_events():
    seen, observer = _records()
    registry = TargetRegistry()
    registry.set_observer(observer)

    registry.upsert(scope="desktop", ref={"id": "a"}, description="A")
    registry.upsert(scope="desktop", ref={"id": "a"}, description="A2")
    registry.invalidate(ref={"id": "a"})
    registry.purge(all=True)

    names = [name for name, _ in seen]
    assert names == [
        Events.TARGET_REGISTERED,
        Events.TARGET_REFRESHED,
        Events.TARGET_INVALIDATED,
        Events.TARGET_PURGED,
    ]
    assert seen[0][1]["local_name"] == seen[1][1]["local_name"]
    assert seen[3][1]["count"] == 1


def test_discovered_and_resolve_failed_events():
    seen, observer = _records()
    registry = TargetRegistry()
    registry.set_observer(observer)

    registry.note_discovered(scope="desktop", ref={"id": "x"}, correlation_id="c1")
    assert seen[0][0] == Events.TARGET_DISCOVERED

    try:
        registry.resolve("missing")
    except TargetNotFound:
        pass
    assert seen[-1][0] == Events.TARGET_RESOLVE_FAILED
    assert seen[-1][1]["reason"] == "unknown"


def test_observer_errors_do_not_break_registry():
    def bad(name, payload):
        raise RuntimeError("boom")

    registry = TargetRegistry()
    registry.set_observer(bad)
    entry = registry.upsert(scope="desktop", ref={"id": "a"})
    assert entry.active is True
