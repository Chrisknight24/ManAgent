"""
Tests pour core/tools_manager.py.

Deux volets :
1. Enregistrement du nouvel outil interne llm_analyze_multi_data (métadonnées,
   schéma de paramètres correct pour piloter le LLM de dispatch).
2. Bout-en-bout : ToolsManager.analyze_request route bien vers le bon outil
   selon le nombre de sources, ET mission_id dans les événements émis vient
   du execution_context scopé (régression du correctif sticky-global fait
   sur ce fichier).
"""

import json
import types
import pytest

import tools.tools_manager as tools_manager_module
import tools.internal_tools as internal_tools_module
from tools.tools_manager import ToolsManager
from core.tools_models import ToolDecision, AnalysisResult
from core.execution_context import ExecutionContext
from utils.logger import Logger


class FakePromptLoader:
    def __init__(self):
        self.calls = []

    def load(self, template_name, lang="fr", **kwargs):
        self.calls.append({"template_name": template_name, "lang": lang, **kwargs})
        return "[PROMPT:" + template_name + "] " + " | ".join(f"{k}={v!r}" for k, v in kwargs.items())


class ScriptedLlm:
    """
    LLM factice à réponses programmées PAR SCHÉMA : analyze_request appelle
    generate_structured deux fois de suite avec deux schémas différents
    (ToolDecision, puis AnalysisResult via le handler interne) — il faut
    donc pouvoir répondre différemment selon le schéma demandé.
    """

    def __init__(self):
        self.responses_by_schema = {}
        self.calls = []

    def program(self, schema, response):
        self.responses_by_schema[schema] = response

    async def generate_structured(self, prompt, schema, tag=None, **kwargs):
        self.calls.append({"prompt": prompt, "schema": schema, "tag": tag})
        resp = self.responses_by_schema.get(schema)
        if resp is None:
            raise AssertionError(f"Aucune réponse programmée pour le schéma {schema}")
        return resp


def make_runtime_state(registry=None, language="fr"):
    rs = types.SimpleNamespace()
    rs.execution_context = ExecutionContext()
    rs._solver_registry_for_tools = registry or {}
    rs.language = language
    rs._tools_llm = None
    return rs


@pytest.fixture(autouse=True)
def _fake_prompt_loader(monkeypatch):
    """
    Deux modules appellent get_prompt_loader() indépendamment :
    - tools/tools_manager.py, dans __init__ (capturé une fois sur
      self._prompt_loader) pour le prompt de décision (tools_manager_analysis.md)
    - tools/internal_tools.py, à chaque appel, pour le prompt d'analyse
      (llm_analyze_data.md) — utilisé par les handlers llm_analyze_data /
      llm_analyze_multi_data que analyze_request() dispatche.
    On patche donc les DEUX références, chacune à son point d'import exact.
    """
    fake = FakePromptLoader()
    monkeypatch.setattr(tools_manager_module, "get_prompt_loader", lambda: fake)
    monkeypatch.setattr(internal_tools_module, "get_prompt_loader", lambda: fake)
    return fake


@pytest.fixture
def captured_events(monkeypatch):
    """Capture tous les Logger.event(...) émis pendant le test, sans toucher au disque."""
    records = []
    original = Logger.event

    def fake_event(event_type, **fields):
        records.append({"event": event_type, **fields})

    monkeypatch.setattr(Logger, "event", staticmethod(fake_event))
    yield records
    monkeypatch.setattr(Logger, "event", original)


# =====================================================
# 1. Enregistrement
# =====================================================

def test_three_internal_tools_registered():
    tm = ToolsManager(runtime_state=make_runtime_state())
    assert set(tm._internal_tool_handlers.keys()) == {
        "extract_json_value", "llm_analyze_data", "llm_analyze_multi_data"
    }


def test_multi_data_tool_schema_requires_two_sources_and_query():
    tm = ToolsManager(runtime_state=make_runtime_state())
    meta = tm._internal_tools_metadata["llm_analyze_multi_data"]
    schema = meta["parameters"]
    assert schema["required"] == ["sources", "query"]
    assert schema["properties"]["sources"]["type"] == "array"
    assert schema["properties"]["sources"]["minItems"] == 2


def test_internal_tools_description_mentions_all_three_tools():
    tm = ToolsManager(runtime_state=make_runtime_state())
    desc = tm._get_internal_tools_description()
    assert "extract_json_value" in desc
    assert "llm_analyze_data" in desc
    assert "llm_analyze_multi_data" in desc


def test_unified_tool_manager_view_still_exposes_single_entry_point():
    # Le Planner ne voit toujours qu'un seul outil "tool_manager" avec un
    # paramètre 'request' en langage libre — l'ajout du 3e outil interne ne
    # doit rien changer à ce contrat externe.
    tm = ToolsManager(runtime_state=make_runtime_state())
    view = tm._get_internal_tools_view()
    assert len(view) == 1
    assert view[0]["name"] == "tool_manager"
    assert list(view[0]["parameters"]["properties"].keys()) == ["request"]


# =====================================================
# 2. Dispatch bout-en-bout + régression mission_id
# =====================================================

async def test_analyze_request_routes_to_multi_data_tool_with_scoped_mission_id(captured_events):
    rs = make_runtime_state(registry={"data_a": {"value": "foo"}, "data_b": {"value": "bar"}})
    tm = ToolsManager(runtime_state=rs, llm=None)
    rs.tools_manager = tm  # repli utilisé par le handler si besoin

    llm = ScriptedLlm()
    llm.program(ToolDecision, ToolDecision(
        success=True,
        tool_name="llm_analyze_multi_data",
        tool_args_json=json.dumps({"sources": ["data_a", "data_b"], "query": "identiques ?"})
    ))
    llm.program(AnalysisResult, AnalysisResult(success=True, data=False, message="différentes"))

    # Un tour de mission ouvrirait normalement ce scope autour de toute
    # l'exécution ; on le simule ici pour vérifier que mission_id est bien
    # lu depuis LE CONTEXTE, pas depuis un ancien attribut global.
    with rs.execution_context.scope(mission_id="mission-42", solver_id="solver-1"):
        result = await tm.analyze_request("compare data_a et data_b", context={}, llm=llm)

    assert result == {"result": True, "data": False, "message": "différentes"}
    assert len(llm.calls) == 2
    assert llm.calls[0]["tag"] == "tools_manager_decision"
    assert llm.calls[1]["tag"] == "llm_analyze_multi_data"

    decision_events = [e for e in captured_events if e["event"] == "tools_manager.decision"]
    assert len(decision_events) == 1
    assert decision_events[0]["mission_id"] == "mission-42"
    assert decision_events[0]["solver_id"] == "solver-1"
    assert decision_events[0]["tool_name"] == "llm_analyze_multi_data"

    result_events = [e for e in captured_events if e["event"] == "tools_manager.result"]
    assert result_events[0]["mission_id"] == "mission-42"


async def test_analyze_request_still_routes_single_source_requests_correctly():
    rs = make_runtime_state(registry={"data_a": {"value": "hello"}})
    tm = ToolsManager(runtime_state=rs, llm=None)

    llm = ScriptedLlm()
    llm.program(ToolDecision, ToolDecision(
        success=True,
        tool_name="llm_analyze_data",
        tool_args_json=json.dumps({"source": "data_a", "query": "combien de mots ?"})
    ))
    llm.program(AnalysisResult, AnalysisResult(success=True, data=1, message="ok"))

    result = await tm.analyze_request("compte les mots de data_a", context={}, llm=llm)

    assert result == {"result": True, "data": 1, "message": "ok"}


async def test_analyze_request_mission_id_absent_outside_any_scope(captured_events):
    # Reproduit un appel de type "tour direct" : aucune mission n'est en
    # cours, donc aucun scope(mission_id=...) n'est ouvert. mission_id doit
    # être absent (None), et surtout ne PAS hériter d'une mission précédente
    # — c'était exactement le bug corrigé sur ce fichier.
    rs = make_runtime_state(registry={"data_a": {"value": "hello"}, "data_b": {"value": "world"}})
    tm = ToolsManager(runtime_state=rs, llm=None)

    llm = ScriptedLlm()
    llm.program(ToolDecision, ToolDecision(success=False, tool_name=None, tool_args_json="{}"))

    # Une mission précédente (dans un autre test / tour) aurait pu laisser
    # une trace si le code lisait encore un attribut global sticky — on
    # vérifie ici qu'un appel FRAIS, hors de tout scope, n'a bien AUCUN
    # mission_id.
    await tm.analyze_request("une requête sans rapport avec une mission", context={}, llm=llm)

    decision_events = [e for e in captured_events if e["event"] == "tools_manager.decision"]
    assert len(decision_events) == 1
    assert decision_events[0]["mission_id"] is None


async def test_analyze_request_no_llm_available():
    rs = make_runtime_state()
    tm = ToolsManager(runtime_state=rs, llm=None)
    result = await tm.analyze_request("peu importe", context={}, llm=None)
    assert result["result"] is False
    assert "Aucun LLM" in result["message"]


async def test_analyze_request_unknown_tool_name_rejected():
    rs = make_runtime_state(registry={"data_a": {"value": "x"}})
    tm = ToolsManager(runtime_state=rs, llm=None)

    llm = ScriptedLlm()
    llm.program(ToolDecision, ToolDecision(
        success=True, tool_name="outil_qui_n_existe_pas", tool_args_json="{}"
    ))

    result = await tm.analyze_request("requete", context={}, llm=llm)
    assert result["result"] is False
    assert "n'existe pas" in result["message"]
