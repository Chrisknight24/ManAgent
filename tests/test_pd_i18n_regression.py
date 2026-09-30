"""Non-regression : la boucle PD ne doit jamais ecraser `_` (i18n).

Bug reel (2026-09-30, build e3d0172 chez l'hote) : `_loop_max, _, ...`
liait `_` localement dans `generate_structured`, transformant l'appel
`_(...)` quelques lignes plus bas en `TypeError: 'str' object is not callable`
et tuant chaque mission au routage. Ce test rejoue le chemin PD avec des
faux (aucune cle API, aucun reseau) et aurait echoue avant le fix.
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import Field
from core.base_schema import BaseDiscoverySchema
from core.llm import Llm


class _FakeDecision(BaseDiscoverySchema):
    reponse: str = Field(default="")


class _FakeProvider:
    model_name = "fake"

    def __init__(self):
        self.runtime_state = None
        self.call_epoch = 0

    async def generate_structured_output(self, prompt, response_schema,
                                         context=None, media_assets=None,
                                         max_output_tokens=None):
        return _FakeDecision(reponse="ok", discovery_request=None)

    def get_last_usage(self):
        return None


class _FakeProviderManager:
    def __init__(self, provider):
        self._provider = provider

    def get_provider(self, provider_id):
        return self._provider


class _FakeDataProvider:
    def get_data_type(self):
        return "bidon"

    def get_targets(self):
        return ["cible"]

    def get_scope_description(self):
        return "bac de test"


class _FakeExplorer:
    def get_available_goals(self):
        return ["lire"]


class _FakeEngine:
    def get_explorer(self, data_type):
        return _FakeExplorer()


class _FakeEntity:
    entity_id = "entitee_test"
    name = "Entitee Test"
    role = "testeur"

    def get_data_providers(self):
        return {"bidon": _FakeDataProvider()}

    def get_data_context(self):
        return None


def test_generate_structured_pd_loop_keeps_i18n_usable():
    provider = _FakeProvider()
    llm = Llm(provider_manager=_FakeProviderManager(provider),
              provider_id="faux", model_id="faux",
              system_prompt="", runtime_state=None)
    llm.enable_discovery(_FakeEngine(), _FakeEntity())
    result = asyncio.run(llm.generate_structured(
        prompt="Bonjour", schema=_FakeDecision, tag="TestPD"))
    assert isinstance(result, _FakeDecision)
    assert result.reponse == "ok"


def test_no_bare_underscore_throwaway_in_i18n_scopes():
    """Garde-fou statique : aucun `, _,` dans les modules qui appellent `_()`."""
    import re
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ("core/llm.py", "core/orchestrator.py", "core/solver.py",
                "core/planner.py", "tools/internal_tools.py",
                "core/discovery/discovery_session.py"):
        text = open(os.path.join(base, rel), encoding="utf-8").read()
        assert "from core.i18n import" in text, rel
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r"(?<![\w])_\s*=(?!=)", line):
                raise AssertionError(f"{rel}:{i} assigne `_` : {stripped}")
