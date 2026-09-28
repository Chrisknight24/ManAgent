"""Aucune adresse d'asset inventée + plafond de sortie structurée."""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_models import AssetInjection, drop_unknown_injected_assets
from core.llm import Llm
from core.plan_models import ConvergenceDecision


def _asset(uri):
    return AssetInjection(uri=uri, variable_name="data_x", description="d")


def test_fantome_jete_vrai_garde():
    ghost = _asset("inputs://turn_1")
    real = _asset("files://photo.png")
    kept, dropped = drop_unknown_injected_assets(
        [ghost, real], {"files://photo.png"})
    assert kept == [real]
    assert dropped == [ghost]


def test_vide_et_rien():
    assert drop_unknown_injected_assets([], set()) == ([], [])
    assert drop_unknown_injected_assets(None, None) == ([], [])


def _llm(calls):
    async def fake_legacy(self, prompt, schema, tag=None, mission_id=None,
                          media_assets=None, max_output_tokens=None):
        calls["cap"] = max_output_tokens
        return ConvergenceDecision(is_convergent=True, reason="ok")

    async def fake_direct(*a, **k):
        raise AssertionError("pas d'appel direct attendu")

    llm = Llm(provider_manager=SimpleNamespace(), provider_id="x",
              model_id="y", runtime_state=SimpleNamespace(language="en"))
    llm._generate_structured_legacy = fake_legacy.__get__(llm, Llm)
    llm._call_llm_with_schema = fake_direct
    return llm


def test_plafond_transmis_au_provider(monkeypatch):
    from core import llm as llm_mod
    calls = {}
    llm = _llm(calls)
    monkeypatch.setattr(Llm, "_build_discovery_section", lambda self, s, b=None: "")
    out = asyncio.run(llm.generate_structured(
        prompt="p", schema=ConvergenceDecision, tag="t",
        max_output_tokens=8192))
    assert out.is_convergent is True
    assert calls["cap"] == 8192


def test_sans_plafond_rien_change(monkeypatch):
    calls = {}
    llm = _llm(calls)
    monkeypatch.setattr(Llm, "_build_discovery_section", lambda self, s, b=None: "")
    asyncio.run(llm.generate_structured(
        prompt="p", schema=ConvergenceDecision, tag="t"))
    assert calls["cap"] is None
