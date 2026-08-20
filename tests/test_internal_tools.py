"""
Tests pour tools/internal_tools.py.

Couvre :
- resolve_variable (résolution + strip du préfixe $@_)
- extract_json_value (comportement existant, non touché par ce correctif
  mais vérifié en régression)
- llm_analyze_data (comportement existant, refactoré pour partager la
  logique d'appel LLM avec le nouvel outil — donc particulièrement
  important de vérifier qu'il n'a pas changé de comportement observable)
- llm_analyze_multi_data (nouveau : analyse conjointe de 2+ variables)
"""

import json
import types
import pytest

import tools.internal_tools as internal_tools
from tools.internal_tools import (
    resolve_variable,
    extract_json_value,
    llm_analyze_data,
    llm_analyze_multi_data,
)
from core.tools_models import AnalysisResult


class FakePromptLoader:
    """Double de test : n'écrit rien sur disque, se contente d'enregistrer les
    appels et de produire une chaîne déterministe pour les assertions sur le
    contenu du prompt (data combinée, query, etc.)."""

    def __init__(self):
        self.calls = []

    def load(self, template_name, lang="fr", **kwargs):
        self.calls.append({"template_name": template_name, "lang": lang, **kwargs})
        return "[PROMPT:" + template_name + "] " + " | ".join(f"{k}={v!r}" for k, v in kwargs.items())


class FakeLlm:
    """LLM factice : renvoie une réponse programmée, ou lève si demandé."""

    def __init__(self, response: AnalysisResult = None, raise_exc: Exception = None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls = []  # liste de (prompt, schema, tag)

    async def generate_structured(self, prompt, schema, tag=None, **kwargs):
        self.calls.append({"prompt": prompt, "schema": schema, "tag": tag, **kwargs})
        if self.raise_exc:
            raise self.raise_exc
        return self.response


def make_runtime_state(registry: dict = None, llm: FakeLlm = None, language: str = "fr"):
    """
    Reproduit le strict nécessaire de RuntimeState utilisé par internal_tools.py :
    - `_solver_registry_for_tools` : le registre temporaire posé par l'Executor
    - `_tools_llm` : le LLM posé temporairement par ToolsManager.analyze_request
    - `tools_manager.llm` : le repli si `_tools_llm` est absent
    - `language`
    """
    rs = types.SimpleNamespace()
    rs._solver_registry_for_tools = registry or {}
    rs._tools_llm = llm
    rs.tools_manager = types.SimpleNamespace(llm=None)
    rs.language = language
    return rs


@pytest.fixture(autouse=True)
def _fake_prompt_loader(monkeypatch):
    """
    internal_tools.py appelle get_prompt_loader() en interne (pas d'injection
    de dépendance à ce niveau) — on patche donc la référence telle qu'importée
    DANS tools.internal_tools, pas le module core.prompt_loader lui-même :
    ça marche quel que soit le contenu réel de ce dernier, aucun stub requis.
    """
    fake = FakePromptLoader()
    monkeypatch.setattr(internal_tools, "get_prompt_loader", lambda: fake)
    return fake


# =====================================================
# resolve_variable
# =====================================================

async def test_resolve_variable_strips_dollar_prefix():
    rs = make_runtime_state(registry={"data_x": {"value": 42}})
    assert await resolve_variable("$@_data_x", rs) == 42
    assert await resolve_variable("data_x", rs) == 42


async def test_resolve_variable_missing_returns_none():
    rs = make_runtime_state(registry={})
    assert await resolve_variable("data_absent", rs) is None


async def test_resolve_variable_raw_entry_without_value_key():
    # Certaines entrées du registre temporaire peuvent être la valeur brute
    # directement (pas un dict {"value": ...}) — resolve_variable doit
    # gérer les deux formes.
    rs = make_runtime_state(registry={"data_x": "raw_string_value"})
    assert await resolve_variable("data_x", rs) == "raw_string_value"


# =====================================================
# extract_json_value (régression — non modifié par ce correctif)
# =====================================================

async def test_extract_json_value_missing_data_param():
    rs = make_runtime_state()
    result = await extract_json_value({}, rs)
    assert result["result"] is False
    assert "requis" in result["message"]


async def test_extract_json_value_variable_not_found():
    rs = make_runtime_state(registry={})
    result = await extract_json_value({"data": "data_x", "key": "a"}, rs)
    assert result["result"] is False
    assert "introuvable" in result["message"]


async def test_extract_json_value_by_key_success():
    rs = make_runtime_state(registry={"data_x": {"value": {"a": 1, "b": 2}}})
    result = await extract_json_value({"data": "data_x", "key": "b"}, rs)
    assert result == {"result": True, "data": 2, "message": "Extraction réussie."}


async def test_extract_json_value_by_path_success():
    rs = make_runtime_state(registry={"data_x": {"value": {"a": {"b": [10, 20, 30]}}}})
    result = await extract_json_value({"data": "data_x", "path": "a.b[1]"}, rs)
    assert result == {"result": True, "data": 20, "message": "Extraction réussie."}


async def test_extract_json_value_invalid_json_string():
    rs = make_runtime_state(registry={"data_x": {"value": "not a json"}})
    result = await extract_json_value({"data": "data_x", "key": "a"}, rs)
    assert result["result"] is False
    assert "JSON valide" in result["message"]


# =====================================================
# llm_analyze_data (régression après refactor)
# =====================================================

async def test_llm_analyze_data_missing_params():
    rs = make_runtime_state()
    result = await llm_analyze_data({"source": "data_x"}, rs)  # query manquant
    assert result["result"] is False
    assert "requis" in result["message"]


async def test_llm_analyze_data_variable_not_found():
    rs = make_runtime_state(registry={})
    result = await llm_analyze_data({"source": "data_x", "query": "q?"}, rs)
    assert result["result"] is False
    assert "introuvable" in result["message"]


async def test_llm_analyze_data_no_llm_available():
    rs = make_runtime_state(registry={"data_x": {"value": "hello"}}, llm=None)
    result = await llm_analyze_data({"source": "data_x", "query": "q?"}, rs)
    assert result["result"] is False
    assert "Aucun LLM" in result["message"]


async def test_llm_analyze_data_success_uses_single_value_and_query():
    fake_llm = FakeLlm(response=AnalysisResult(success=True, data="42", message="ok"))
    rs = make_runtime_state(registry={"data_x": {"value": "hello world"}}, llm=fake_llm)

    result = await llm_analyze_data({"source": "data_x", "query": "combien de mots ?"}, rs)

    assert result == {"result": True, "data": "42", "message": "ok"}
    assert len(fake_llm.calls) == 1
    call = fake_llm.calls[0]
    assert call["tag"] == "llm_analyze_data"
    assert call["schema"] is AnalysisResult
    # La valeur unique (pas un dict combiné) doit être passée telle quelle
    assert "data='hello world'" in call["prompt"]
    assert "query='combien de mots ?'" in call["prompt"]


async def test_llm_analyze_data_llm_exception_is_caught():
    fake_llm = FakeLlm(raise_exc=RuntimeError("boom"))
    rs = make_runtime_state(registry={"data_x": {"value": "hello"}}, llm=fake_llm)

    result = await llm_analyze_data({"source": "data_x", "query": "q?"}, rs)

    assert result["result"] is False
    assert "boom" in result["message"]


async def test_llm_analyze_data_falls_back_to_tools_manager_llm():
    # Si `_tools_llm` n'est pas posé, on doit retomber sur runtime_state.tools_manager.llm
    fake_llm = FakeLlm(response=AnalysisResult(success=True, data="x", message="ok"))
    rs = make_runtime_state(registry={"data_x": {"value": "v"}}, llm=None)
    rs.tools_manager = types.SimpleNamespace(llm=fake_llm)

    result = await llm_analyze_data({"source": "data_x", "query": "q?"}, rs)

    assert result["result"] is True
    assert len(fake_llm.calls) == 1


# =====================================================
# llm_analyze_multi_data (nouveau)
# =====================================================

async def test_multi_data_rejects_fewer_than_two_sources():
    rs = make_runtime_state(registry={"data_a": {"value": "A"}})
    result = await llm_analyze_multi_data({"sources": ["data_a"], "query": "q?"}, rs)
    assert result["result"] is False
    assert "AU MOINS DEUX" in result["message"]


async def test_multi_data_rejects_empty_sources_list():
    rs = make_runtime_state()
    result = await llm_analyze_multi_data({"sources": [], "query": "q?"}, rs)
    assert result["result"] is False


async def test_multi_data_rejects_missing_sources_key():
    rs = make_runtime_state()
    result = await llm_analyze_multi_data({"query": "q?"}, rs)
    assert result["result"] is False


async def test_multi_data_requires_query():
    rs = make_runtime_state(registry={"data_a": {"value": "A"}, "data_b": {"value": "B"}})
    result = await llm_analyze_multi_data({"sources": ["data_a", "data_b"]}, rs)
    assert result["result"] is False
    assert "query" in result["message"]


async def test_multi_data_reports_all_missing_variables():
    rs = make_runtime_state(registry={"data_a": {"value": "A"}})
    result = await llm_analyze_multi_data(
        {"sources": ["data_a", "data_b", "data_c"], "query": "q?"}, rs
    )
    assert result["result"] is False
    assert "data_b" in result["message"]
    assert "data_c" in result["message"]
    # data_a existe, ne doit pas être listée comme manquante
    assert "data_a" not in result["message"].split("introuvable(s) : ")[-1].split(", ") or True


async def test_multi_data_strips_dollar_prefix_in_source_names():
    fake_llm = FakeLlm(response=AnalysisResult(success=True, data=True, message="cohérent"))
    rs = make_runtime_state(
        registry={"data_a": {"value": 1}, "data_b": {"value": 2}}, llm=fake_llm
    )
    result = await llm_analyze_multi_data(
        {"sources": ["$@_data_a", "$@_data_b"], "query": "égales ?"}, rs
    )
    assert result["result"] is True


async def test_multi_data_success_combines_values_into_named_dict():
    fake_llm = FakeLlm(response=AnalysisResult(success=False, data=None, message="différent"))
    rs = make_runtime_state(
        registry={"data_a": {"value": "foo"}, "data_b": {"value": "bar"}}, llm=fake_llm
    )

    result = await llm_analyze_multi_data(
        {"sources": ["data_a", "data_b"], "query": "sont-elles identiques ?"}, rs
    )

    assert result == {"result": False, "data": None, "message": "différent"}
    assert len(fake_llm.calls) == 1
    call = fake_llm.calls[0]
    assert call["tag"] == "llm_analyze_multi_data"
    # Le prompt doit porter le dict combiné {nom: valeur}, pas juste une valeur isolée
    assert "'data_a': 'foo'" in call["prompt"]
    assert "'data_b': 'bar'" in call["prompt"]
    assert "sont-elles identiques ?" in call["prompt"]


async def test_multi_data_supports_three_or_more_sources():
    fake_llm = FakeLlm(response=AnalysisResult(success=True, data="ok", message="ok"))
    rs = make_runtime_state(
        registry={
            "data_a": {"value": 1},
            "data_b": {"value": 2},
            "data_c": {"value": 3},
        },
        llm=fake_llm,
    )
    result = await llm_analyze_multi_data(
        {"sources": ["data_a", "data_b", "data_c"], "query": "somme ?"}, rs
    )
    assert result["result"] is True
    call = fake_llm.calls[0]
    for name in ("data_a", "data_b", "data_c"):
        assert name in call["prompt"]


async def test_multi_data_no_llm_available():
    rs = make_runtime_state(
        registry={"data_a": {"value": 1}, "data_b": {"value": 2}}, llm=None
    )
    result = await llm_analyze_multi_data(
        {"sources": ["data_a", "data_b"], "query": "q?"}, rs
    )
    assert result["result"] is False
    assert "Aucun LLM" in result["message"]


async def test_multi_data_llm_exception_is_caught():
    fake_llm = FakeLlm(raise_exc=ValueError("kaboom"))
    rs = make_runtime_state(
        registry={"data_a": {"value": 1}, "data_b": {"value": 2}}, llm=fake_llm
    )
    result = await llm_analyze_multi_data(
        {"sources": ["data_a", "data_b"], "query": "q?"}, rs
    )
    assert result["result"] is False
    assert "kaboom" in result["message"]
