"""Premiers tests reconstruits from scratch : catalogue embeddings agnostique."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embeddings.catalog import get_catalog, detect_installed, catalog_status
from embeddings.providers import create_embedding_provider


def test_catalog_loads_and_host_can_extend():
    base = get_catalog()
    assert any(e["id"] == "lite-hash" for e in base)
    extended = get_catalog([{"id": "custom/mine", "type": "sentence-transformer"}])
    assert any(e["id"] == "custom/mine" for e in extended)
    assert len(extended) == len(base) + 1


def test_detect_installed_on_fake_cache(tmp_path):
    snap = tmp_path / "models--org--m1" / "snapshots" / "rev1"
    snap.mkdir(parents=True)
    (snap / "model.safetensors").write_bytes(b"x" * 64)
    (tmp_path / "models--org--m2").mkdir()
    st = detect_installed(["org/m1", "org/m2", "org/missing", "lite-hash"], tmp_path)
    assert st["org/m1"]["installed"] is True
    assert st["org/m1"]["size_bytes"] >= 64
    assert st["org/m2"]["installed"] is False
    assert st["org/missing"]["installed"] is False
    assert st["lite-hash"]["installed"] is True


def test_factory_maps_all_provider_types():
    assert create_embedding_provider({"type": "hash"}).model_name == "lite-hash"
    remote = create_embedding_provider(
        {"type": "remote", "id": "text-embedding-3-small", "api_key": "k"}
    )
    assert remote.model_name == "remote:text-embedding-3-small"
    local = create_embedding_provider({"id": "sentence-transformers/all-MiniLM-L6-v2"})
    assert local.model_name == "sentence-transformers/all-MiniLM-L6-v2"


def test_catalog_status_flags_active():
    rows = catalog_status(active_id="lite-hash")
    lite = [r for r in rows if r["id"] == "lite-hash"][0]
    assert lite["active"] is True
    assert all(r["active"] is False for r in rows if r["id"] != "lite-hash")
