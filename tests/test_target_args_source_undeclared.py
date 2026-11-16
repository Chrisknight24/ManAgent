"""Origine de TOUS les arguments consommateurs, declares ou non (vague 1, B4).

Run `22a92120` : la trace publiait `["cell=missing","preset=missing",
"target=missing"]` en omettant l'argument qui avait change le comportement de
l'outil (il n'etait pas declare comme consommateur). Un rejeu ne pouvait donc
pas voir la cause.

On trace aussi les arguments non declares (`undeclared:<origine>`), sans
jamais bloquer. Les param tres internes (`_...`) restent hors trace.
"""

from types import SimpleNamespace

from core.executor import Executor


class _FakeContract:
    def __init__(self, params):
        self._params = set(params)

    def consumer_param_names(self):
        return set(self._params)


class _FakeManager:
    def __init__(self, contract):
        self._contract = contract

    def get_target_contract(self, name):
        return self._contract


def _executor(registry=None, consumers=("target",)):
    solver = SimpleNamespace(
        variable_registry=registry or {},
        runtime_state=SimpleNamespace(
            tools_manager=_FakeManager(_FakeContract(consumers))
        ),
    )
    executor = Executor.__new__(Executor)
    executor.solver = solver
    return executor


def test_argument_declare_trace_comme_avant():
    executor = _executor({"data_a": {"value": 1}})
    sources = executor._describe_consumer_values(
        "act", {"target": "$@_data_a"}
    )
    assert sources == {"target": "alias:data_a"}


def test_argument_non_declare_est_trace():
    executor = _executor({"data_a": {"value": 1}})
    sources = executor._describe_consumer_values(
        "act", {"target": "$@_data_a", "cell": "M5"}
    )
    assert sources["target"] == "alias:data_a"
    assert sources["cell"] == "undeclared:literal"


def test_argument_non_declare_non_resolu():
    executor = _executor({"data_a": {"value": 1}})
    sources = executor._describe_consumer_values(
        "act", {"target": "$@_data_a", "preset": "$@_inconnu"}
    )
    assert sources["preset"] == "undeclared:unresolved:inconnu"


def test_arguments_internes_ignores():
    executor = _executor()
    sources = executor._describe_consumer_values(
        "act", {"target": "x", "_step_id": "s1", "_internal": 3}
    )
    assert "_step_id" not in sources
    assert "_internal" not in sources


def test_parametres_declares_manquants_restent_traces():
    executor = _executor(consumers=("target", "preset"))
    sources = executor._describe_consumer_values("act", {"cell": "A1"})
    assert sources["target"] == "missing"
    assert sources["preset"] == "missing"
    assert sources["cell"] == "undeclared:literal"


def test_sans_contrat_aucune_trace():
    solver = SimpleNamespace(
        variable_registry={},
        runtime_state=SimpleNamespace(
            tools_manager=SimpleNamespace(get_target_contract=lambda name: None)
        ),
    )
    executor = Executor.__new__(Executor)
    executor.solver = solver
    assert executor._describe_consumer_values("act", {"cell": "A1"}) == {}
