"""Tests namespaces mémoire R6 : un espace par modèle, jamais de mélange."""
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embeddings.catalog import (
    memory_namespace,
    vec_table,
    model_dim,
    precheck_download,
    HISTORIC_MODEL_ID,
)
from memory.lesson_store import LessonStore
from memory.mission_profile_store import MissionProfileStore


def test_namespace_empty_for_historic_and_none():
    assert memory_namespace(None) == ""
    assert memory_namespace(HISTORIC_MODEL_ID) == ""


def test_namespace_stable_distinct_and_sqlite_safe():
    a = memory_namespace("lite-hash")
    assert a == memory_namespace("lite-hash")
    assert a != memory_namespace("BAAI/bge-m3")
    assert re.fullmatch(r"__[0-9a-f]{8}", a)
    assert vec_table("vec_lessons", "lite-hash") == "vec_lessons" + a
    assert vec_table("vec_lessons", None) == "vec_lessons"


def test_model_dim_from_catalog():
    assert model_dim("lite-hash") == 256
    assert model_dim("BAAI/bge-m3") == 1024
    assert model_dim("unknown/model-xyz") == 384


def test_lesson_store_switches_space(tmp_path):
    store = LessonStore(str(tmp_path / "t.db"))
    assert store._vec_table == "vec_lessons"
    ns = store.use_embedding_model("lite-hash")
    assert ns != "vec_lessons" and store._vec_table == ns
    assert store._vector_dim == 256
    back = store.use_embedding_model(HISTORIC_MODEL_ID)
    assert back == "vec_lessons"


def test_mission_profile_store_switches_space(tmp_path):
    store = MissionProfileStore(str(tmp_path / "m.db"))
    assert store._vec_table == "vec_mission_profiles"
    ns = store.use_embedding_model("BAAI/bge-m3")
    assert ns != "vec_mission_profiles" and store._vector_dim == 1024


def test_precheck_lite_always_ok(tmp_path):
    r = precheck_download("lite-hash", tmp_path)
    assert r["ok"] is True and r["needed_bytes"] == 0


def test_precheck_refuses_when_disk_too_small(tmp_path):
    r = precheck_download("BAAI/bge-m3", tmp_path)
    assert r["needed_bytes"] > 2_000_000_000
    assert isinstance(r["ok"], bool) and "free_bytes" in r
