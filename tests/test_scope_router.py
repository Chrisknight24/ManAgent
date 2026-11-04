"""P5 — routeur de scope en langage naturel (DETTE-3).

L'entité ne choisit pas un `scope` technique : elle pose une question.
Un méta-LLM choisit les providers, puis on agrège leurs réponses. Aucune
action n'est dispatchée. Le refus est une réponse normale.
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import BaseModel

from core.discovery.data_provider import DataProvider
from core.llm import Llm
from core.scope_router import ScopeRoute, ScopeRouter
from tools.internal_tools import investigate
from tools.tools_manager import ToolsManager


# --------------------------------------------------------------------------
# Doublures
# --------------------------------------------------------------------------

class _Provider(DataProvider):
    def __init__(self, data_type, scope, answer_text=None):
        self._data_type = data_type
        self._scope = scope
        self._answer_text = answer_text
        self.calls = []

    def get_data_type(self):
        return self._data_type

    def get_targets(self):
        return ["target"]

    def get_scope_description(self):
        return self._scope

    def get_asset(self, target):
        return None

    async def answer(self, question):
        self.calls.append(question)
        return self._answer_text


class _Entity:
    entity_id = "ent-test"

    def __init__(self, providers):
        self._providers = providers

    def get_data_providers(self):
        return self._providers


class _RouteLlm:
    def __init__(self, providers, reason=""):
        self._providers = providers
        self._reason = reason
        self.calls = []

    async def generate_structured(self, prompt, schema, tag=None, **kwargs):
        self.calls.append({"prompt": prompt, "tag": tag, "kwargs": kwargs})
        return ScopeRoute(providers=list(self._providers), reason=self._reason)


class _RecordingTools:
    def __init__(self):
        self.calls = []

    async def execute_tool(self, name, args):
        self.calls.append((name, args))
        return '{"result": true, "data": null}'


class _FakeSchema(BaseModel):
    reponse: str = ""


class _FakeExplorer:
    def get_available_goals(self):
        return ["lire"]


class _FakeEngine:
    def get_explorer(self, data_type):
        return _FakeExplorer()


class _FakePdProvider:
    def __init__(self, data_type):
        self._data_type = data_type

    def get_data_type(self):
        return self._data_type

    def get_targets(self):
        return ["cible"]

    def get_scope_description(self):
        return "périmètre de test"


def _pd_llm(runtime_state):
    llm = Llm(provider_manager=None, provider_id="f", model_id="f",
              system_prompt="", runtime_state=runtime_state)
    llm.enable_discovery(
        _FakeEngine(),
        _Entity({
            "world": _FakePdProvider("world"),
            "files": _FakePdProvider("files"),
        }),
    )
    return llm


# --------------------------------------------------------------------------
# Routage
# --------------------------------------------------------------------------

def test_natural_language_routes_to_provider():
    world = _Provider("world", "état actuel du monde", "Paint est ouvert.")
    files = _Provider("files", "contenu des fichiers", "a.txt")
    tools = _RecordingTools()
    rs = types.SimpleNamespace(tools_manager=tools, language="fr")
    router = ScopeRouter(
        rs,
        llm=_RouteLlm(["world"]),
        entity=_Entity({"world": world, "files": files}),
    )

    out = asyncio.run(router.answer("Est-ce que Paint est ouvert ?"))

    assert out["result"] is True
    assert "Paint est ouvert" in out["data"]
    assert out["sources"] == ["world"]
    assert world.calls == ["Est-ce que Paint est ouvert ?"]
    assert files.calls == [], "le provider non retenu ne doit pas être interrogé"
    assert tools.calls == [], "le routeur ne dispatche aucune action"


def test_no_provider_returns_explicit_refusal():
    router = ScopeRouter(
        types.SimpleNamespace(tools_manager=_RecordingTools(), language="fr"),
        llm=_RouteLlm([]),
        entity=_Entity({"world": _Provider("world", "monde")}),
    )

    out = asyncio.run(router.answer("Est-ce que Paint est ouvert ?"))

    assert out["result"] is False
    assert "Aucun scope ne peut traiter cette demande" in out["error_reason"]
    assert "Est-ce que Paint est ouvert ?" in out["error_reason"]


def test_selected_provider_without_answer_is_not_invented():
    class _NoAnswer(_Provider):
        answer = None

    router = ScopeRouter(
        types.SimpleNamespace(tools_manager=_RecordingTools(), language="fr"),
        llm=_RouteLlm(["files"]),
        entity=_Entity({"files": _NoAnswer("files", "fichiers")}),
    )

    out = asyncio.run(router.answer("Quel fichier est ouvert ?"))

    assert out["result"] is False
    assert "n'ont pas pu répondre" in out["error_reason"]


def test_data_provider_default_answer_is_none():
    # Le contrat par défaut de l'interface reste None (aucune obligation).
    class _Minimal(DataProvider):
        def get_data_type(self):
            return "x"

        def get_targets(self):
            return []

        def get_asset(self, target):
            return None

    assert _Minimal().answer("question") is None


# --------------------------------------------------------------------------
# Flag : off = PD strictement inchangée ; on = une ligne
# --------------------------------------------------------------------------

def test_flag_off_keeps_pd_byte_identical():
    rs_off = types.SimpleNamespace(enable_scope_router=False, language="fr")
    rs_absent = types.SimpleNamespace(language="fr")

    off = _pd_llm(rs_off)._build_discovery_section(_FakeSchema, set())
    absent = _pd_llm(rs_absent)._build_discovery_section(_FakeSchema, set())

    assert off == absent, "flag off doit produire exactement la PD d'avant P5"
    assert "Scopes disponibles" not in off
    assert "world" in off and "files" in off


def test_flag_on_reduces_pd_to_one_line():
    rs_off = types.SimpleNamespace(enable_scope_router=False, language="fr")
    rs_on = types.SimpleNamespace(enable_scope_router=True, language="fr")

    off = _pd_llm(rs_off)._build_discovery_section(_FakeSchema, set())
    on = _pd_llm(rs_on)._build_discovery_section(_FakeSchema, set())

    lines = [line for line in on.splitlines() if line.strip()]
    assert len(lines) == 1
    assert on.startswith("Scopes disponibles")
    assert "investigate" in on
    assert len(on) < len(off)


# --------------------------------------------------------------------------
# Outil `investigate` + exposition conditionnelle
# --------------------------------------------------------------------------

def test_investigate_refuses_when_flag_off():
    rs = types.SimpleNamespace(enable_scope_router=False)
    out = asyncio.run(investigate({"question": "Paint ?"}, rs))
    assert out["result"] is False
    assert "désactivé" in out["error_reason"]


def test_investigate_routes_when_flag_on():
    world = _Provider("world", "monde", "Paint est ouvert.")
    rs = types.SimpleNamespace(
        enable_scope_router=True,
        language="fr",
        _tools_llm=_RouteLlm(["world"]),
        _scope_router_entity=_Entity({"world": world}),
        tools_manager=_RecordingTools(),
    )

    out = asyncio.run(investigate({"question": "Paint ?"}, rs))

    assert out["result"] is True
    assert "Paint est ouvert" in out["data"]
    assert out["sources"] == ["world"]


def test_tools_manager_exposes_investigate_only_when_enabled():
    rs = types.SimpleNamespace(enable_scope_router=False)
    tm = ToolsManager(runtime_state=rs)

    assert "investigate" not in tm.known_tool_names()
    assert not any(t["name"] == "investigate" for t in asyncio.run(tm.get_tools_view()))

    rs.enable_scope_router = True
    assert "investigate" in tm.known_tool_names()
    view = asyncio.run(tm.get_tools_view())
    entry = next(t for t in view if t["name"] == "investigate")
    assert entry["kind"] == "perception"
    assert "question" in entry["parameters"]["properties"]

    rs.enable_scope_router = False
    assert "investigate" not in tm.known_tool_names()
