"""Rejeu doré de la mission `dcc9ec4b` : les trois bugs vécus ne reviennent pas.

Le fixture `tests/fixtures/dcc9ec4b_replay.jsonl` contient les événements réels
de la mission (champs utiles seulement, aucun prompt). Il fige des faits
mesurables, pas une opinion :

- trois appels LLM tués à ~90 s par le timeout fixe, dont deux transformés en
  `TARGET_NOT_FOUND` — donc imputés au ciblage alors que le fournisseur était
  muet ;
- deux plans corrects refusés pour `$@_desktop_space`, un nom qui venait
  pourtant d'être prouvé par le gate de la même mission.

Ce banc est un garde-fou de non-régression : si un correctif futur réintroduit
un de ces comportements, il échoue ici pour une raison lisible.
"""

import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.execution_models import FailureClass
from core.plan_models import (
    Plan,
    PlanStep,
    StepType,
    classify_failure_origin,
    is_infra_streak,
    normalize_failure_signature,
)
from core.plan_validator import find_unresolved_plan_refs
from core.llm_policy import profile_for
from core.targeting import PROVIDER_UNAVAILABLE, provider_failure_code
from providers.base_provider import ProviderServiceUnavailableError

FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "dcc9ec4b_replay.jsonl",
)


def _events():
    with open(FIXTURE, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _of(name, **match):
    out = []
    for event in _events():
        if event.get("event") != name:
            continue
        if all(event.get(key) == value for key, value in match.items()):
            out.append(event)
    return out


# --------------------------------------------------------------------------
# Fait 1 : le timeout fixe de 90 s, et le faux diagnostic qui a suivi
# --------------------------------------------------------------------------

def test_trois_appels_tues_par_le_plafond_de_90s():
    stalls = [e for e in _of("llm_call") if float(e.get("duration_ms") or 0) > 90_000]
    assert len(stalls) == 3
    for stall in stalls:
        assert stall["error"], "un appel tué doit dire pourquoi"
        # Le planificateur et le juge ont droit à beaucoup plus que 90 s.
        assert profile_for(None, "default").total_s > 90.0


def test_une_panne_fournisseur_n_est_plus_une_cible_absente():
    """Le bug vécu : `llm_call` en timeout -> `TARGET_NOT_FOUND` au même pas."""
    events = _events()
    paires = []
    for index, event in enumerate(events):
        if event.get("event") != "llm_call":
            continue
        if float(event.get("duration_ms") or 0) <= 90_000:
            continue
        for suivant in events[index + 1:]:
            if suivant.get("step_id") != event.get("step_id"):
                continue
            if suivant.get("event") == "target.resolve_failed":
                paires.append((event, suivant))
            break
    assert len(paires) >= 2, "le fixture doit conserver le faux diagnostic"

    for stall, failure in paires:
        assert failure["code"] == "TARGET_NOT_FOUND"  # ce qui s'est passé
        # Ce que le code répond désormais pour la même cause :
        code = provider_failure_code(
            ProviderServiceUnavailableError(str(stall["error"]))
        )
        assert code == PROVIDER_UNAVAILABLE
        assert code != failure["code"]


def test_la_meme_cause_ne_consomme_plus_le_budget_de_convergence():
    stalls = [e for e in _of("llm_call") if float(e.get("duration_ms") or 0) > 90_000]
    raison = str(stalls[0]["error"])
    origin = classify_failure_origin(
        FailureClass.EXECUTION_FAILURE,
        raison,
        tool_name="perceive_understand",
        error_code=PROVIDER_UNAVAILABLE,
    )
    assert origin == "INFRA"

    signature = normalize_failure_signature(
        FailureClass.INFRA_FAILURE, raison, code=PROVIDER_UNAVAILABLE
    )
    assert is_infra_streak([(signature, 0)] * 2) is False
    assert is_infra_streak([(signature, 0)] * 3) is True


# --------------------------------------------------------------------------
# Fait 2 : un nom déjà prouvé n'est pas une devinette
# --------------------------------------------------------------------------

def _refs_refusees():
    refs = set()
    for decision in _of("plan_validation_decision", is_valid=False):
        raison = str(decision.get("reason") or "")
        if "référence devinée" not in raison:
            continue
        for token in raison.split():
            if token.startswith("target=$@_"):
                refs.add(token.split("=", 1)[1].replace("$@_", "", 1))
    return refs


def _noms_prouves():
    """Noms de cibles que la mission avait déjà publiés avant le refus."""
    noms = set()
    for source in _of("target_args_source"):
        for item in source.get("sources") or []:
            if str(item).startswith("target=alias:"):
                noms.add(str(item).split("alias:", 1)[1])
    return {nom for nom in noms if nom}


def test_le_fixture_contient_le_refus_et_le_nom_prouve():
    refs = _refs_refusees()
    noms = _noms_prouves()
    assert refs, "le refus « référence devinée » doit rester dans le fixture"
    assert refs <= noms, f"{refs - noms} n'a jamais été prouvé dans ce run"


def test_ces_refus_ne_se_reproduisent_plus():
    nom_prouve = sorted(_refs_refusees())[0]
    plan = Plan(
        goal="trier le bureau",
        steps=[
            PlanStep(
                id="step_1",
                description="clic droit sur le bureau",
                type=StepType.TOOL_CALL,
                tool_name="mouse",
                tool_args_json=json.dumps({"target": f"$@_{nom_prouve}"}),
                expected_result="true",
            )
        ],
    )
    assert find_unresolved_plan_refs(plan, extra_names=_noms_prouves()) == []
    # Le gate reste un gate : sans le registre, un nom inconnu est refusé.
    assert find_unresolved_plan_refs(plan) != []


# --------------------------------------------------------------------------
# Fait 3 : « Analyse terminée. » n'est pas une observation
# --------------------------------------------------------------------------

def test_le_faux_succes_narratif_est_trace_dans_le_fixture():
    observed = [e for e in _of("alignment_failure") if e.get("observed")]
    assert observed, "le fixture doit garder l'observation d'origine"
    assert any("Analyse termin" in str(e["observed"]) for e in observed)


def test_un_message_de_remplissage_n_est_pas_une_observation(monkeypatch):
    import asyncio

    from tools import internal_tools as IT
    from core.tools_models import AnalysisResult

    class _FakeLLM:
        async def generate_structured(self, **_kwargs):
            return AnalysisResult(success=False, data=None, message=None)

    async def _fake_get_llm(_state):
        return _FakeLLM()

    monkeypatch.setattr(IT, "_get_tools_llm", _fake_get_llm)
    out = asyncio.run(
        IT._run_llm_analysis(
            "brut", "que voit-on ?", SimpleNamespace(language="fr"), tag="t"
        )
    )
    assert out["result"] is False
    assert "Analyse terminée." not in str(out["message"])
    assert out["data"] is None
