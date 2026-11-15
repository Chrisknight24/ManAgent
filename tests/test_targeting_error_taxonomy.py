"""Taxonomie honnete du ciblage : une panne ne devient jamais une cible absente.

Vecu (run dcc9ec4b) : deux timeouts de 90 s ont ete transformes en
`TARGET_NOT_FOUND`, ce qui a fait ouvrir le disjoncteur de convergence et
accuse le plan a la place du fournisseur. Ces bancs figent la frontiere.
"""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.targeting import (
    PROVIDER_QUOTA_EXHAUSTED,
    PROVIDER_UNAVAILABLE,
    ObservedWorld,
    SelectorUnavailable,
    TargetDecision,
    _ask_selector,
    provider_failure_code,
    resolve_target,
)
from providers.base_provider import (
    ProviderAuthError,
    ProviderModelNotFoundError,
    ProviderNetworkError,
    ProviderQuotaExhaustedError,
    ProviderServiceUnavailableError,
)


class _Raiser:
    def __init__(self, exc):
        self._exc = exc

    async def generate_structured(self, **_kwargs):
        raise self._exc


class _Returner:
    def __init__(self, decision):
        self._decision = decision

    async def generate_structured(self, **_kwargs):
        return self._decision


def _resolve(llm):
    return asyncio.run(
        resolve_target(llm=llm, question="la corbeille", world=ObservedWorld())
    )


# --------------------------------------------------------------------------
# Frontiere fournisseur
# --------------------------------------------------------------------------

def test_indisponibilite_service_n_est_pas_une_cible_absente():
    result = _resolve(_Raiser(ProviderServiceUnavailableError("Appel Gemini sans reponse apres 600s.")))
    assert result.ok is False
    assert result.error_code == PROVIDER_UNAVAILABLE
    assert "TARGET_NOT_FOUND" not in result.error_code
    assert "ce n'est pas une absence de cible" in result.error_message


def test_reseau_et_auth_sont_des_indisponibilites():
    for exc in (
        ProviderNetworkError("DNS"),
        ProviderAuthError("401"),
        ProviderModelNotFoundError("modele inconnu"),
        TimeoutError("delai"),
        OSError("socket"),
    ):
        assert provider_failure_code(exc) == PROVIDER_UNAVAILABLE


def test_quota_a_son_propre_code():
    result = _resolve(_Raiser(ProviderQuotaExhaustedError("toutes les cles saturees")))
    assert result.error_code == PROVIDER_QUOTA_EXHAUSTED
    assert provider_failure_code(ProviderQuotaExhaustedError("x")) == PROVIDER_QUOTA_EXHAUSTED


def test_reponse_illisible_reste_un_echec_de_selection():
    """Un schema invalide n'est pas une panne : c'est un vrai echec de selection."""
    assert provider_failure_code(ValueError("schema invalide")) == ""
    result = _resolve(_Raiser(ValueError("schema invalide")))
    assert result.error_code == "TARGET_NOT_FOUND"


def test_refus_explicite_du_selecteur_est_un_echec_de_ciblage():
    """Un refus motive reste un echec de ciblage : jamais un code d'infra."""
    result = _resolve(_Returner(TargetDecision(no_target=True, reason="aucune corbeille visible")))
    assert result.error_code.startswith("TARGET_")
    assert result.error_code not in (PROVIDER_UNAVAILABLE, PROVIDER_QUOTA_EXHAUSTED)
    assert "aucune corbeille visible" in str(result.reason)


def test_selector_unavailable_porte_le_code():
    exc = SelectorUnavailable(PROVIDER_UNAVAILABLE, "timeout")
    assert exc.code == PROVIDER_UNAVAILABLE
    assert "timeout" in str(exc)


def test_ask_selector_leve_pour_une_indisponibilite():
    try:
        asyncio.run(
            _ask_selector(
                llm=_Raiser(ProviderServiceUnavailableError("down")),
                question="q",
                world=ObservedWorld(),
                disclosure=None,
                allow_anchor=True,
                anchor_kinds=[],
                allow_probes=False,
                language="fr",
                tag="t",
            )
        )
    except SelectorUnavailable as exc:
        assert exc.code == PROVIDER_UNAVAILABLE
    else:  # pragma: no cover
        raise AssertionError("SelectorUnavailable attendue")
