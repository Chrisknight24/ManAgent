"""
Tests unitaires pour le Discovery Framework.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.runtime_state import RuntimeState
from core.discovery.models import DiscoveryPlan, RefinedContext, ExitPolicy
from core.discovery.explorers.registry_explorer import RegistryExplorer
from core.discovery.discovery_engine import DiscoveryEngine
from core.llm import Llm


@pytest.fixture
def runtime_state():
    rs = RuntimeState()
    rs.discovery_engine = None
    rs.cache_manager = MagicMock()
    rs.cache_manager.get = AsyncMock(return_value=None)
    rs.cache_manager.set = AsyncMock()
    rs.variable_registry = {"test_target": {"value": "test_value"}}
    return rs


@pytest.fixture
def registry_explorer(runtime_state):
    llm = MagicMock(spec=Llm)
    llm.generate_structured = AsyncMock(return_value=MagicMock(steps=[]))
    explorer = RegistryExplorer(runtime_state, llm=llm)
    return explorer


@pytest.fixture
def discovery_engine(runtime_state, registry_explorer):
    engine = DiscoveryEngine(runtime_state)
    engine.register_explorer(registry_explorer)
    # On mocke store_refined_context pour pouvoir vérifier l'appel
    engine.store_refined_context = AsyncMock()
    return engine


@pytest.mark.asyncio
async def test_discovery_cache_hit(discovery_engine, runtime_state):
    signature = "registry://test_target/list_keys"
    refined = RefinedContext(
        signature=signature,
        data_type="registry",
        target="test_target",
        goal="Lister les clés",
        technical_goal="list_keys",
        summary="Cache hit summary",
        exit_policy=ExitPolicy.PLAN_COMPLETED
    )
    discovery_engine.get_refined_context = AsyncMock(return_value=refined)

    plan = DiscoveryPlan(
        goal="Lister les clés",
        steps=[],
        data_type="registry",
        target="test_target",
        technical_goal="list_keys",
        signature=signature
    )

    result = await discovery_engine.start_discovery("test_entity", plan)
    assert result.signature == signature
    assert result.summary == "Cache hit summary"
    # Vérifier que store_refined_context n'a pas été appelé (cache hit)
    discovery_engine.store_refined_context.assert_not_called()


@pytest.mark.asyncio
async def test_discovery_miss_and_execution(discovery_engine, runtime_state, registry_explorer):
    discovery_engine.get_refined_context = AsyncMock(return_value=None)

    # Mock validate_target pour qu'elle retourne True
    registry_explorer.validate_target = MagicMock(return_value=True)

    with patch("core.discovery.discovery_session.DiscoverySession.run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = RefinedContext(
            signature="registry://test_target/list_keys",
            data_type="registry",
            target="test_target",
            goal="Lister les clés",
            technical_goal="list_keys",
            summary="Résultat de la session",
            exit_policy=ExitPolicy.PLAN_COMPLETED
        )
        plan = DiscoveryPlan(
            goal="Lister les clés",
            steps=[],
            data_type="registry",
            target="test_target",
            technical_goal="list_keys"
        )
        result = await discovery_engine.start_discovery("test_entity", plan)
        assert result.summary == "Résultat de la session"
        # Vérifier que store_refined_context a été appelé une fois (cache miss)
        discovery_engine.store_refined_context.assert_called_once()