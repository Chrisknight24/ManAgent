"""Occultation **declaree** par l'hote, appliquee par le cerveau (vague 1, B2).

Le rejeu `22a92120` a produit un `match` sur une cible mensongere : la
description annoncait une chose, le monde en contenait une autre. Le cerveau
n'a aucun moyen fiable de le deviner (pas de geometrie, pas de nom de champ) :
c'est a l'hote d'affirmer qu'une cible est cachee ou qu'elle en recouvre une
autre. Ces bancs figent trois garanties :

- une cible declaree cachee est refusee sans appel de modele ;
- une cible declaree recouvrante n'est jamais `match` (au mieux `uncertain`) ;
- sans declaration, ou avec un attribut muet, le comportement est inchange.
"""

import asyncio

from core.target_contract import parse_target_contract
from core.target_ingress import project_target_payload
from core.target_occlusion import (
    OcclusionFinding,
    declared_occlusion,
    occlusion_policy_for,
)
from core.targeting import _verify_target
from core.targets import TargetRegistry


def _contract(occlusion=None, attributes=None, *, tool_name="scan"):
    producer = {
        "path": "data.candidates",
        "mode": "candidates",
        "scope": "tile",
        "fields": {"ref": "id", "description": "name"},
    }
    if attributes is not None:
        producer["fields"]["attributes"] = attributes
    if occlusion is not None:
        producer["occlusion"] = occlusion
    parsed = parse_target_contract(
        {"version": 1, "produces": [producer]}, tool_name=tool_name
    )
    assert parsed.contract is not None, parsed.errors
    return parsed.contract, parsed.warnings


def _entry(contract, registry, item):
    project_target_payload(
        registry, contract, {"result": True, "data": {"candidates": [item]}}
    )
    entry = registry.find_by_ref(item["id"])
    assert entry is not None, "la cible de test doit etre projetee"
    return entry


class _ForbiddenLlm:
    """Un fait declare doit suffire : aucun appel de modele."""

    async def generate_structured(self, *args, **kwargs):
        raise AssertionError("aucun appel de modele ne doit avoir lieu")


class _CountingLlm:
    def __init__(self, verdict="uncertain"):
        self.calls = 0
        self._verdict = verdict

    async def generate_structured(self, prompt, schema, tag, **kwargs):
        self.calls += 1
        return schema(verdict=self._verdict, confidence="low", reason="scripte")


# ---------------------------------------------------------------------------
# Lecture de la declaration (pure)
# ---------------------------------------------------------------------------


def test_sans_declaration_aucun_fait():
    contract, _ = _contract()
    registry = TargetRegistry()
    entry = _entry(
        contract, registry,
        {"id": "t1", "name": "zone libre", "covered_by": "menu"},
    )
    assert declared_occlusion(contract, entry) == OcclusionFinding()


def test_attribut_cache_declare_produit_un_refus():
    contract, warnings = _contract(
        occlusion={"blocked_by": ["blocked"]},
        attributes={"blocked": "covered_by"},
    )
    assert warnings == []
    registry = TargetRegistry()
    entry = _entry(
        contract, registry,
        {"id": "t1", "name": "zone libre", "covered_by": "menu contextuel"},
    )
    finding = declared_occlusion(contract, entry)
    assert finding.verdict == "mismatch"
    assert "blocked" in finding.reason
    assert "menu contextuel" in finding.reason


def test_attribut_muet_ne_declenche_rien():
    contract, _ = _contract(
        occlusion={"blocked_by": ["blocked"], "covers": ["covers"]},
        attributes={"blocked": "covered_by", "covers": "occludes"},
    )
    registry = TargetRegistry()
    entry = _entry(
        contract, registry,
        {"id": "t1", "name": "zone libre", "covered_by": "", "occludes": []},
    )
    assert declared_occlusion(contract, entry) == OcclusionFinding()


def test_attribut_recouvrant_ne_produit_jamais_un_match():
    contract, _ = _contract(
        occlusion={"covers": ["covers"]},
        attributes={"covers": "occludes"},
    )
    registry = TargetRegistry()
    entry = _entry(
        contract, registry,
        {"id": "t1", "name": "Bureau", "occludes": ["fenetre etrangere"]},
    )
    finding = declared_occlusion(contract, entry)
    assert finding.verdict == "uncertain"
    assert "covers" in finding.reason


def test_declaration_non_publiee_est_signalee():
    contract, warnings = _contract(
        occlusion={"blocked_by": ["blocked"]},
        attributes={},
    )
    assert any("unpublished attribute" in w for w in warnings)
    # La declaration reste lue telle quelle : le cerveau ne devine pas que
    # l'attribut est absent du monde, il constate simplement qu'il est muet.
    assert occlusion_policy_for(contract, "tile") == (("blocked",), ())


# ---------------------------------------------------------------------------
# Gate de verification : la declaration court-circuite le modele
# ---------------------------------------------------------------------------


def _verify(contract, entry, llm, question="une zone libre"):
    return asyncio.run(_verify_target(
        llm=llm,
        question=question,
        entry=entry,
        language="fr",
        tag="t",
        contract=contract,
    ))


def test_gate_refuse_sans_modele_une_cible_cachee():
    contract, _ = _contract(
        occlusion={"blocked_by": ["blocked"]},
        attributes={"blocked": "covered_by"},
    )
    registry = TargetRegistry()
    entry = _entry(
        contract, registry,
        {"id": "t1", "name": "zone libre", "covered_by": "menu contextuel"},
    )
    outcome = _verify(contract, entry, _ForbiddenLlm())
    assert outcome.verdict == "mismatch"
    assert outcome.next == "replan"
    assert outcome.verified is True
    assert outcome.coverage.get("blocked_by")


def test_gate_ne_prouve_jamais_une_cible_recouvrante():
    contract, _ = _contract(
        occlusion={"covers": ["covers"]},
        attributes={"covers": "occludes"},
    )
    registry = TargetRegistry()
    entry = _entry(
        contract, registry,
        {"id": "t1", "name": "Bureau", "occludes": ["autre proprietaire"]},
    )
    outcome = _verify(contract, entry, _ForbiddenLlm())
    assert outcome.verdict == "uncertain"
    assert outcome.verdict != "match"
    assert outcome.next == "accept"
    assert outcome.coverage.get("covers")


def test_gate_sans_declaration_appelle_le_verificateur():
    contract, _ = _contract()
    registry = TargetRegistry()
    entry = _entry(contract, registry, {"id": "t1", "name": "zone libre"})
    llm = _CountingLlm(verdict="match")
    outcome = _verify(contract, entry, llm)
    assert llm.calls == 1
    assert outcome.verdict == "match"


def test_gate_avec_attribut_muet_appelle_le_verificateur():
    contract, _ = _contract(
        occlusion={"blocked_by": ["blocked"]},
        attributes={"blocked": "covered_by"},
    )
    registry = TargetRegistry()
    entry = _entry(
        contract, registry,
        {"id": "t1", "name": "zone libre", "covered_by": ""},
    )
    llm = _CountingLlm(verdict="match")
    outcome = _verify(contract, entry, llm)
    assert llm.calls == 1
    assert outcome.verdict == "match"
