"""Politique d'appel LLM : defauts, surcharge par l'hote, bornage.

Le contrat verifie ici est celui qui a remplace le timeout fixe de 90 s :
un appel ne meurt plus a une duree arbitraire, mais selon sa classe d'usage,
et l'hote peut declarer la sienne via son manifeste sans toucher au code.
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm_policy import (
    DEFAULT_TOTALS,
    MAX_TOTAL_SECONDS,
    MIN_TOTAL_SECONDS,
    default_policy,
    policy_from_mapping,
    profile_for,
    resolve_policy,
)


def _state(policy=None, holder="metadata"):
    manifest = SimpleNamespace(metadata={}, environment={})
    if policy is not None:
        getattr(manifest, holder)["llm_policy"] = policy
    return SimpleNamespace(host_manifest=manifest)


# --------------------------------------------------------------------------
# Defauts ManAgent
# --------------------------------------------------------------------------

def test_defauts_par_classe():
    policy = default_policy()
    assert policy.source == "default"
    assert policy.profile_for("selector").total_s == DEFAULT_TOTALS["selector"]
    assert policy.profile_for("analysis").total_s == DEFAULT_TOTALS["analysis"]


def test_classe_inconnue_retombe_sur_defaut():
    policy = default_policy()
    assert policy.profile_for("planner").total_s == DEFAULT_TOTALS["default"]
    assert policy.profile_for(None).total_s == DEFAULT_TOTALS["default"]
    assert policy.profile_for("").total_s == DEFAULT_TOTALS["default"]


def test_tout_est_plus_long_que_l_ancien_timeout():
    """Aucun profil ne doit reproduire le tueur de perf (90 s)."""
    policy = default_policy()
    for kind in DEFAULT_TOTALS:
        assert policy.profile_for(kind).total_s > 90.0


# --------------------------------------------------------------------------
# Surcharge par le manifeste hote
# --------------------------------------------------------------------------

def test_manifeste_surcharge_une_classe():
    state = _state({"profiles": {"selector": 45}})
    assert profile_for(state, "selector").total_s == 45.0
    # Les autres classes gardent les defauts.
    assert profile_for(state, "analysis").total_s == DEFAULT_TOTALS["analysis"]


def test_manifeste_profil_dict():
    state = _state({"profiles": {"analysis": {"total_s": 700, "on_stall": "abort"}}})
    profile = profile_for(state, "analysis")
    assert profile.total_s == 700.0
    assert profile.on_stall == "abort"


def test_manifeste_environment_utilise_si_metadata_vide():
    state = _state({"profiles": {"analysis": 500}}, holder="environment")
    assert profile_for(state, "analysis").total_s == 500.0


def test_metadata_prioritaire_sur_environment():
    state = _state({"profiles": {"analysis": 400}}, holder="metadata")
    state.host_manifest.environment["llm_policy"] = {"profiles": {"analysis": 900}}
    assert profile_for(state, "analysis").total_s == 400.0


# --------------------------------------------------------------------------
# Tolerance : jamais de crash sur une declaration incomprise
# --------------------------------------------------------------------------

def test_borne_haute_et_basse():
    state = _state({"profiles": {"analysis": 99_999, "selector": 0}})
    assert profile_for(state, "analysis").total_s == MAX_TOTAL_SECONDS
    assert profile_for(state, "selector").total_s == MIN_TOTAL_SECONDS


def test_valeur_illisible_garde_le_defaut():
    state = _state({"profiles": {"analysis": "beaucoup"}})
    assert profile_for(state, "analysis").total_s == DEFAULT_TOTALS["analysis"]


def test_cles_inconnues_ignorees_sans_crash():
    state = _state({"profiles": {"analysis": {"total_s": 350, "futur": 1}}, "futur": 2})
    profile = profile_for(state, "analysis")
    assert profile.total_s == 350.0


def test_on_stall_inconnu_retombe_sur_abort():
    policy = policy_from_mapping({"on_stall": "hedge"})
    assert policy.on_stall == "abort"
    assert policy.profile_for("selector").on_stall == "abort"


def test_manifeste_absent_ou_illisible():
    assert resolve_policy(None).source == "default"
    assert resolve_policy(SimpleNamespace()).source == "default"
    assert resolve_policy(_state("pas un dict")).source == "default"


# --------------------------------------------------------------------------
# Injection dans le provider (le seul point de contact)
# --------------------------------------------------------------------------

def test_profil_injecte_dans_le_provider():
    from core.llm import Llm

    llm = Llm(
        provider_manager=SimpleNamespace(),
        provider_id="x",
        model_id="y",
        runtime_state=_state({"profiles": {"selector": 42}}),
    )
    fake_provider = SimpleNamespace()
    llm._apply_call_policy(fake_provider, "selector")

    assert fake_provider.call_timeout_seconds == 42.0
    assert fake_provider.failover_budget_seconds == 42.0


def test_injection_tolere_un_provider_sans_attributs():
    from core.llm import Llm

    llm = Llm(
        provider_manager=SimpleNamespace(),
        provider_id="x",
        model_id="y",
        runtime_state=_state(),
    )

    class _Verrou:
        __slots__ = ()

    # Un provider qui refuse l'attribut ne doit jamais faire echouer l'appel.
    llm._apply_call_policy(_Verrou(), "analysis")
