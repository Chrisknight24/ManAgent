"""Budget de lecture du monde : configurable, 2000 par defaut.

Deux defauts corriges ici, tous deux mesures :

1. le budget n'etait pas configurable : `2000` etait ecrit en dur a chaque
   point de lecture, et `600`/`1200`/`4000` ailleurs, sans raison nommee ;
2. la valeur declaree par l'hote REMPLACAT la notre dans les deux sens
   (`limit = int(cfg.get("max_chars") or max_chars)`). Demander 600 pour un
   hote a 2000 donnait 2000 : notre reglage etait ignore. Un plafond hote ne
   doit que reduire.

Le budget est une DEMANDE ; le plafond hote est un maximum. La demande gagne
quand elle est plus basse, le plafond gagne quand il est plus bas.
"""

import asyncio
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.alignment import (
    DEFAULT_WORLD_SNAPSHOT_CHARS,
    FINAL_WORLD_SNAPSHOT_CHARS,
    MAX_WORLD_SNAPSHOT_CHARS,
    WORLD_SNAPSHOT_ENV_VAR,
    WORLD_SNAPSHOT_MIN_USEFUL,
    effective_world_snapshot_limit,
    fetch_world_state_snapshot,
    resolve_world_snapshot_budget,
)
from utils.config import resolve_world_snapshot_chars
from utils.logger import Logger


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    """Aucun test ne depend de l'environnement de la machine."""
    monkeypatch.delenv(WORLD_SNAPSHOT_ENV_VAR, raising=False)


# --------------------------------------------------------------------------
# Fonctions pures
# --------------------------------------------------------------------------

def test_le_defaut_est_2000():
    assert DEFAULT_WORLD_SNAPSHOT_CHARS == 2000
    assert resolve_world_snapshot_budget(None) == 2000


def test_le_reglage_de_session_prime_sur_le_defaut():
    rs = types.SimpleNamespace(world_snapshot_max_chars=1500)
    assert resolve_world_snapshot_budget(rs) == 1500


def test_la_demande_explicite_prime_sur_le_reglage():
    rs = types.SimpleNamespace(world_snapshot_max_chars=1500)
    assert resolve_world_snapshot_budget(rs, 700) == 700


def test_l_environnement_sert_de_repli(monkeypatch):
    monkeypatch.setenv(WORLD_SNAPSHOT_ENV_VAR, "1800")
    assert resolve_world_snapshot_budget(None) == 1800


def test_le_reglage_de_session_prime_sur_l_environnement(monkeypatch):
    monkeypatch.setenv(WORLD_SNAPSHOT_ENV_VAR, "1800")
    rs = types.SimpleNamespace(world_snapshot_max_chars=900)
    assert resolve_world_snapshot_budget(rs) == 900


@pytest.mark.parametrize("illisible", [None, "", "abc", 0, -5, True, False, {}, []])
def test_un_reglage_illisible_retombe_sur_le_defaut(illisible):
    rs = types.SimpleNamespace(world_snapshot_max_chars=illisible)
    assert resolve_world_snapshot_budget(rs) == 2000


def test_le_budget_est_borne_par_le_maximum():
    assert resolve_world_snapshot_budget(None, 99999) == MAX_WORLD_SNAPSHOT_CHARS
    assert FINAL_WORLD_SNAPSHOT_CHARS == MAX_WORLD_SNAPSHOT_CHARS


def test_le_plafond_hote_reduit():
    assert effective_world_snapshot_limit(2000, 800) == 800


def test_le_plafond_hote_n_elargit_jamais():
    """Le defaut mesure : l'hote a 2000 ne doit pas imposer 2000 a qui demande 600."""
    assert effective_world_snapshot_limit(600, 2000) == 600


def test_sans_plafond_hote_la_demande_est_respectee():
    assert effective_world_snapshot_limit(2000, None) == 2000


# --------------------------------------------------------------------------
# Lecture payload (reglage operateur)
# --------------------------------------------------------------------------

def test_payload_racine():
    assert resolve_world_snapshot_chars({"world_snapshot": {"max_chars": 1500}}) == 1500


def test_payload_runtime_configuration():
    assert resolve_world_snapshot_chars(
        {"runtime_configuration": {"world_snapshot": {"max_chars": 1200}}}
    ) == 1200


def test_payload_racine_prioritaire():
    assert resolve_world_snapshot_chars({
        "world_snapshot": {"max_chars": 1500},
        "runtime_configuration": {"world_snapshot": {"max_chars": 1200}},
    }) == 1500


@pytest.mark.parametrize("payload", [None, {}, {"world_snapshot": 12},
                                     {"world_snapshot": {"max_chars": "abc"}},
                                     {"world_snapshot": {"max_chars": 0}},
                                     {"world_snapshot": {"max_chars": True}}])
def test_payload_sans_reglage_valide(payload):
    assert resolve_world_snapshot_chars(payload) is None


# --------------------------------------------------------------------------
# Bout en bout : plafond hote + signal
# --------------------------------------------------------------------------

def _runtime(max_chars=None, host_max=None, body="Monde " * 400):
    class FakeTM:
        def known_tool_names(self):
            return {"get_world_state"}

        async def execute_tool(self, name, args):
            return json.dumps({"result": True,
                               "data": {"summary_text": body,
                                        "windows": [{"title": "Fenetre"}]}})

    manifest = None
    if host_max is not None:
        manifest = types.SimpleNamespace(
            metadata={"world_snapshot": {"source_tool": "get_world_state",
                                         "max_chars": host_max}},
            environment=None,
        )
    return types.SimpleNamespace(
        tools_manager=FakeTM(),
        host_manifest=manifest,
        world_snapshot_max_chars=max_chars,
    )


def _capture_events(monkeypatch):
    seen = []
    monkeypatch.setattr(
        Logger, "event",
        staticmethod(lambda event_type, **fields: seen.append((event_type, fields))),
    )
    return seen


def test_sans_plafond_hote_le_defaut_s_applique(monkeypatch):
    events = _capture_events(monkeypatch)
    out = asyncio.run(fetch_world_state_snapshot(_runtime()))
    assert out and len(out) <= DEFAULT_WORLD_SNAPSHOT_CHARS
    snap = [f for k, f in events if k == "world_snapshot"][0]
    assert snap["budget"] == DEFAULT_WORLD_SNAPSHOT_CHARS


def test_le_plafond_hote_reduit_la_lecture(monkeypatch):
    _capture_events(monkeypatch)
    out = asyncio.run(fetch_world_state_snapshot(_runtime(host_max=300)))
    assert len(out) <= 300


def test_le_reglage_operateur_reduit_la_lecture(monkeypatch):
    _capture_events(monkeypatch)
    out = asyncio.run(fetch_world_state_snapshot(_runtime(max_chars=400)))
    assert len(out) <= 400


def test_budget_low_quand_l_hote_coupe_sous_l_utile(monkeypatch):
    events = _capture_events(monkeypatch)
    asyncio.run(fetch_world_state_snapshot(_runtime(host_max=300)))
    low = [f for k, f in events if k == "world_snapshot.budget_low"]
    assert low and low[0]["effective"] == 300
    assert low[0]["requested"] == DEFAULT_WORLD_SNAPSHOT_CHARS
    assert "host" in low[0]["cause"]


def test_pas_de_budget_low_quand_la_demande_est_deja_basse(monkeypatch):
    """Un plafond hote qui ne coupe rien n'est pas une plainte."""
    events = _capture_events(monkeypatch)
    asyncio.run(fetch_world_state_snapshot(_runtime(max_chars=400, host_max=500)))
    assert not [k for k, _ in events if k == "world_snapshot.budget_low"]


def test_pas_de_budget_low_sans_plafond_hote(monkeypatch):
    events = _capture_events(monkeypatch)
    asyncio.run(fetch_world_state_snapshot(_runtime()))
    assert not [k for k, _ in events if k == "world_snapshot.budget_low"]


def test_plafond_hote_au_seuil_utile_ne_plaint_pas(monkeypatch):
    events = _capture_events(monkeypatch)
    asyncio.run(fetch_world_state_snapshot(_runtime(host_max=WORLD_SNAPSHOT_MIN_USEFUL)))
    assert not [k for k, _ in events if k == "world_snapshot.budget_low"]


def test_le_plafond_hote_n_elargit_pas_la_lecture(monkeypatch):
    """La demande la plus basse gagne : avant, l'hote ecrasait notre reglage."""
    _capture_events(monkeypatch)
    out = asyncio.run(fetch_world_state_snapshot(_runtime(max_chars=400, host_max=4000)))
    assert out and len(out) <= 400
