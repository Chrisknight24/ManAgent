"""
Tests pour le module Entity.
"""

import pytest
from core.entity import Entity
from core.llm import Llm
from core.discovery.data_provider import DataProvider
from core.runtime_state import RuntimeState
from unittest.mock import MagicMock


class ConcreteEntity(Entity):
    async def process(self, *args, **kwargs):
        return "processed"


class MockDataProvider(DataProvider):
    def get_data_type(self):
        return "mock"
    def get_targets(self):
        return ["target1"]
    def get_data(self, target):
        return "data"
    def get_metadata(self, target):
        return {"desc": "test"}


@pytest.fixture
def runtime_state():
    rs = RuntimeState()
    rs.discovery_engine = MagicMock()
    return rs


def test_entity_creation(runtime_state):
    llm = MagicMock(spec=Llm)
    entity = ConcreteEntity(name="test", role="tester", llm=llm, parent=None)
    # On patch le runtime_state pour le test
    entity.runtime_state = runtime_state
    # Vérifier que l'ID est généré
    assert entity.entity_id is not None
    assert len(entity.entity_id) > 0


def test_entity_register_provider(runtime_state):
    llm = MagicMock(spec=Llm)
    llm._discovery_enabled = True
    entity = ConcreteEntity(name="test", role="tester", llm=llm, parent=None)
    entity.runtime_state = runtime_state
    provider = MockDataProvider()
    entity.register_data_provider("mock", provider)
    assert "mock" in entity.get_data_providers()
    # Vérifier que llm.update_discovery_providers a été appelé
    llm.update_discovery_providers.assert_called_once_with(entity.get_data_providers())