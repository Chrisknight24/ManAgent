"""Cooldown court (5s) + failover que sur du configuré (rapide, sans LLM)."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers.base_provider import (
    BaseProvider,
    RATE_LIMIT_BASE_COOLDOWN,
    RATE_LIMIT_MAX_COOLDOWN,
)


class _ConcreteProvider(BaseProvider):
    async def initialize(self):
        pass

    async def generate_response(self, user_message: str) -> str:
        return ""

    async def stream_response(self, message: str, context=None, tools=None):
        return
        yield

    async def is_available(self) -> bool:
        return True

    async def generate_structured_output(self, prompt, response_schema, context=None,
                                         media_assets=None, max_output_tokens=None):
        return response_schema()


def _provider(pid="test"):
    p = _ConcreteProvider.__new__(_ConcreteProvider)
    p.provider_id = pid
    p.provider_name = pid
    p.system_prompt = ""
    p.api_keys_pool = [{"key": "k1", "is_active": True}]
    p.active_key_index = 0
    p._key_cooldowns = {}
    p._rate_limit_streak = 0
    p._pool_cooldown_until = 0.0
    return p


def test_base_courte_5s():
    assert RATE_LIMIT_BASE_COOLDOWN == 5.0
    assert RATE_LIMIT_MAX_COOLDOWN == 60.0


def test_cooldown_incremental_5_10_20_40_plafond_60():
    p = _provider()
    before = time.time()
    p.note_rate_limit("k1")
    d1 = p._key_cooldowns["k1"] - before
    assert 4.0 <= d1 <= 7.0
    p._pool_cooldown_until = 0.0
    p._key_cooldowns = {}
    p.note_rate_limit("k1")
    p._pool_cooldown_until = 0.0
    p._key_cooldowns = {}
    p.note_rate_limit("k1")
    p.note_rate_limit("k1")
    d3 = p._key_cooldowns["k1"] - time.time()
    assert 35.0 <= d3 <= 45.0  # 4e appel de la rafale : 5*2^3 = 40s
    for _ in range(6):
        p._pool_cooldown_until = 0.0
        p._key_cooldowns = {}
        p.note_rate_limit("k1")
    # streak monte : 5,10,20,40,60(plafond),60...
    p2 = _provider()
    cds = []
    for _ in range(6):
        p2._pool_cooldown_until = 0.0
        p2._key_cooldowns = {}
        t0 = time.time()
        p2.note_rate_limit("k1")
        cds.append(p2._key_cooldowns["k1"] - t0)
    assert cds[0] < cds[1] < cds[2] < cds[3]
    assert max(cds) <= 61.0


def test_succes_efface_le_pool():
    p = _provider()
    p.note_rate_limit("k1")
    assert p.has_available_keys() is False
    p.note_provider_success()
    assert p._pool_cooldown_until == 0.0


def test_cooldown_remaining_zero_quand_dispo():
    assert _provider().cooldown_remaining() == 0.0


def test_cooldown_remaining_annonce_l_attente():
    p = _provider()
    p.note_rate_limit("k1")
    assert 0.0 < p.cooldown_remaining() <= 6.0
    p.note_provider_success()
    p._key_cooldowns = {}
    assert p.cooldown_remaining() == 0.0


def test_message_quota_lisible_sans_traceback():
    from providers.base_provider import quota_friendly_message
    short = quota_friendly_message(45)
    assert "Traceback" not in short and "quota" in short.lower()
    long = quota_friendly_message(300)
    assert "minute" in long.lower()


def test_election_ignore_provider_sans_cles():
    from providers.provider_manager import ProviderManager, ModelMetadata, ModelRequirement

    mgr = ProviderManager()
    p = _provider("groq")
    p.api_keys_pool = []  # configuré sans clés = indisponible
    mgr.register_provider(p)
    mgr.register_model_metadata(ModelMetadata(
        model_id="vide-1", provider_id="groq", display_name="vide",
        capabilities=["structured_output"],
    ))
    req = ModelRequirement(role_name="t", required_capabilities=["structured_output"])
    assert mgr.find_best_model_for_requirement(req) is None


def test_election_prend_provider_configure_avec_cles():
    from providers.provider_manager import ProviderManager, ModelMetadata, ModelRequirement

    mgr = ProviderManager()
    p = _provider("groq")
    mgr.register_provider(p)
    mgr.register_model_metadata(ModelMetadata(
        model_id="ok-1", provider_id="groq", display_name="ok",
        capabilities=["structured_output"],
    ))
    req = ModelRequirement(role_name="t", required_capabilities=["structured_output"])
    best = mgr.find_best_model_for_requirement(req)
    assert best == ("groq", "ok-1")
