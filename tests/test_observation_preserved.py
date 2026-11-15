"""Observation immortelle : ce qui a ete vu survit a l'echec de l'etape.

Vecu (run dcc9ec4b) : l'analyse « Trier par… cellule M5 » a abouti, puis une
etape ulterieure a echoue et l'information a disparu du prompt de replan. Le
planner a alors replanifie a l'aveugle, deux fois.
"""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import internal_tools as IT
from core.tools_models import AnalysisResult


class _FakeLLM:
    def __init__(self, result):
        self._result = result
        self.prompts = []

    async def generate_structured(self, prompt=None, schema=None, tag="", **kwargs):
        self.prompts.append(prompt)
        return self._result


def _state():
    return SimpleNamespace(tools_manager=None, language="fr")


def _run(monkeypatch, result):
    llm = _FakeLLM(result)

    async def _fake_get_llm(_state):
        return llm

    monkeypatch.setattr(IT, "_get_tools_llm", _fake_get_llm)
    out = asyncio.run(
        IT._run_llm_analysis("brut", "que voit-on ?", _state(), tag="llm_analyze_data")
    )
    return out, llm


# --------------------------------------------------------------------------
# Jamais de faux succes narratif
# --------------------------------------------------------------------------

def test_succes_sans_detail_ne_prétend_pas_avoir_analyse(monkeypatch):
    out, _ = _run(monkeypatch, AnalysisResult(success=True, data=None, message=None))
    assert out["result"] is True
    assert out["message"] != "Analyse terminée."
    assert "aucun détail" in out["message"].lower()
    # Aucune donnee inventee : un message de remplissage n'est pas une observation.
    assert out["data"] is None


def test_echec_sans_detail_n_invente_pas_un_succes(monkeypatch):
    out, _ = _run(monkeypatch, AnalysisResult(success=False, data=None, message=None))
    assert out["result"] is False
    assert "chec" in out["message"].lower()


# --------------------------------------------------------------------------
# La vraie information survit
# --------------------------------------------------------------------------

def test_observation_survit_meme_sans_payload_structure(monkeypatch):
    out, _ = _run(
        monkeypatch,
        AnalysisResult(
            success=True,
            data=None,
            message="Menu contextuel ouvert ; entree « Trier par… » en cellule M5.",
        ),
    )
    assert "M5" in str(out["data"])
    assert "M5" in out["message"]


def test_payload_structure_prioritaire_sur_le_message(monkeypatch):
    out, _ = _run(
        monkeypatch,
        AnalysisResult(success=True, data={"cell": "M5"}, message="vu"),
    )
    assert out["data"] == {"cell": "M5"}


def test_echec_conserve_l_observation_partielle(monkeypatch):
    out, _ = _run(
        monkeypatch,
        AnalysisResult(
            success=False,
            data=None,
            gap="le menu s'est referme",
            message="Le bureau est affiche, aucune corbeille visible.",
        ),
    )
    assert out["result"] is False
    assert "corbeille" in str(out["data"])
    assert "corbeille" in out["message"]
