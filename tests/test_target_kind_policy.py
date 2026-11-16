"""Compatibilite declaree `type produit -> scopes d'entree` (vague 1, B1).

Le rejeu `22a92120` a perdu une tentative entiere sur une combinaison que
l'hote a refusee lui-meme (`type image` + cible UI deja extraite). Le cerveau
ne peut pas deviner cette relation : elle est donc **declaree** par l'hote
(`produces[].kind_scopes`). Ces bancs figent trois choses :

- une declaration explicite filtre les candidats AVANT tout appel de modele
  et refuse sans appeler le producteur ;
- une table muette pour ce type ne refuse rien (declaration partielle) ;
- sans table du tout, le comportement historique est inchange.

Aucun vocabulaire hote n'est code en dur : les noms de types et de scopes
viennent du contrat de chaque test.
"""

import asyncio
import json
import types

from core.target_contract import parse_target_contract
from core.target_ingress import project_target_payload
from core.target_kind import (
    KIND_COMPAT_DECLARED,
    KIND_COMPAT_EXCLUDED,
    KIND_COMPAT_MATCH,
    KIND_COMPAT_UNDECLARED,
    build_scope_to_kinds,
    kind_policy,
    mismatch_message,
)
from core.targeting import ObservedWorld, resolve_target
from core.targets import TargetRegistry


def _contract(kind_scopes=None, *, kind_param="kind"):
    producer = {
        "path": "data.target",
        "mode": "single",
        "scope": "target",
        "kind_param": kind_param,
        "fields": {"ref": "id", "description": "label"},
    }
    if kind_scopes is not None:
        producer["kind_scopes"] = kind_scopes
    raw = {"version": 1, "produces": [producer]}
    parsed = parse_target_contract(raw, tool_name="act")
    assert parsed.contract is not None, parsed.errors
    return parsed.contract


def _candidates_contract():
    parsed = parse_target_contract({
        "version": 1,
        "produces": [{
            "path": "data.candidates",
            "mode": "candidates",
            "scope": "tile",
            "fields": {"ref": "id", "description": "name"},
        }],
    }, tool_name="scan")
    assert parsed.contract is not None, parsed.errors
    return parsed.contract


def _world(contract, registry):
    projected = project_target_payload(
        registry,
        contract,
        {"result": True, "data": {"candidates": [
            {"id": "ref-1", "name": "zone libre"},
        ]}},
    )[0]
    return ObservedWorld.from_payload(registry, contract, projected)


class _Manager:
    def get_target_contract(self, name):
        return None

    def get_tool_returns(self, name):
        return []

    async def execute_tool(self, name, args):
        return json.dumps({"result": True, "data": {}})


class _ForbiddenLlm:
    """Echoue si le cerveau appelle un modele alors qu'une declaration suffit."""

    async def generate_structured(self, *args, **kwargs):
        raise AssertionError("aucun appel de modele ne doit avoir lieu")


class _SilentLlm:
    def __init__(self):
        self.calls = 0

    async def generate_structured(self, prompt, schema, tag, **kwargs):
        self.calls += 1
        if schema.__name__ == "TargetVerification":
            return schema(verdict="match", confidence="high", reason="ok")
        return schema(target_key="", no_target=True, reason="rien")


# ---------------------------------------------------------------------------
# Politique pure
# ---------------------------------------------------------------------------


def test_declaration_explicite_active_la_politique():
    contract = _contract({"ui": ["tile"], "image": []})
    policy = kind_policy(contract, {"kind": "image"})
    assert policy.active is True
    assert policy.declared is True
    assert policy.allowed_scopes == ()
    assert policy.forbids_all_candidates is True
    assert policy.compat("tile") == KIND_COMPAT_EXCLUDED


def test_type_accepte_et_type_inconnu_de_la_table():
    contract = _contract({"ui": ["tile"]})
    allowed = kind_policy(contract, {"kind": "ui"})
    assert allowed.allows("tile") is True
    assert allowed.compat("tile") == KIND_COMPAT_MATCH
    # La table existe mais ne parle pas de ce type : declaration partielle,
    # pas une declaration d'incompatibilite.
    partial = kind_policy(contract, {"kind": "other"})
    assert partial.declared is False
    assert partial.compat("tile") == KIND_COMPAT_UNDECLARED


def test_sans_table_le_comportement_est_inerte():
    contract = _contract(None)
    policy = kind_policy(contract, {"kind": "image"})
    assert policy.kind == "image"
    assert policy.declared is False
    assert policy.active is False
    assert policy.allows("n_importe_quel_scope") is True


def test_index_inverse_et_message_actionnable():
    contract = _contract({"ui": ["tile"], "image": []})
    reverse = build_scope_to_kinds(contract)
    assert reverse["tile"] == ("ui",)
    policy = kind_policy(contract, {"kind": "image"})
    message = mismatch_message(policy, ["tile"], producer_name="act")
    assert "image" in message
    assert "tile" in message
    assert "act" in message
    # L'alternative declaree est proposee, pas inventee.
    assert "ui" in message


def test_parametre_de_type_non_standard():
    contract = _contract({"ui": ["tile"]}, kind_param="target_type")
    policy = kind_policy(contract, {"target_type": "ui"})
    assert policy.param == "target_type"
    assert policy.active is True
    # Convention `kind` absente du contrat : ne doit pas activer la politique.
    assert kind_policy(contract, {"kind": "ui"}).active is False


# ---------------------------------------------------------------------------
# Integration resolve_target : refus avant tout appel
# ---------------------------------------------------------------------------


def test_refus_avant_selection_quand_aucun_candidat_ne_produit_le_type():
    producer = _contract({"ui": ["tile"], "image": []})
    source = _candidates_contract()
    registry = TargetRegistry()
    world = _world(source, registry)
    policy = kind_policy(producer, {"kind": "image"})

    result = asyncio.run(resolve_target(
        llm=_ForbiddenLlm(),
        question="une zone libre",
        world=world,
        contract=source,
        registry=registry,
        tools_mgr=_Manager(),
        allow_anchor=False,
        max_rounds=0,
        kind_policy=policy,
        producer_name="act",
    ))

    assert not result.ok
    assert result.error_code == "TARGET_KIND_MISMATCH"
    assert "image" in result.error_message
    assert result.entry is None


def test_type_compatible_laisse_le_choix_au_selecteur():
    producer = _contract({"ui": ["tile"]})
    source = _candidates_contract()
    registry = TargetRegistry()
    world = _world(source, registry)
    llm = _SilentLlm()

    result = asyncio.run(resolve_target(
        llm=llm,
        question="une zone libre",
        world=world,
        contract=source,
        registry=registry,
        tools_mgr=_Manager(),
        allow_anchor=False,
        max_rounds=0,
        kind_policy=kind_policy(producer, {"kind": "ui"}),
        producer_name="act",
    ))

    assert llm.calls >= 1
    # Le modele n'a rien choisi : l'echec est un « pas de cible », jamais un
    # refus de compatibilite.
    assert result.error_code != "TARGET_KIND_MISMATCH"


def test_sans_declaration_aucun_filtre_ni_refus():
    producer = _contract(None)
    source = _candidates_contract()
    registry = TargetRegistry()
    world = _world(source, registry)
    llm = _SilentLlm()

    result = asyncio.run(resolve_target(
        llm=llm,
        question="une zone libre",
        world=world,
        contract=source,
        registry=registry,
        tools_mgr=_Manager(),
        allow_anchor=False,
        max_rounds=0,
        kind_policy=kind_policy(producer, {"kind": "image"}),
        producer_name="act",
    ))

    assert llm.calls >= 1
    assert result.error_code != "TARGET_KIND_MISMATCH"
    assert world.candidates  # le monde n'a pas ete vide par la politique


def test_ancre_brute_disponible_empeche_le_refus_anticipe():
    """`kind_scopes` ne parle que des cibles locales : une ancre brute reste
    une entree possible, donc on filtre sans fermer la porte."""
    producer = _contract({"ui": ["tile"], "image": []})
    source = _candidates_contract()
    registry = TargetRegistry()
    world = _world(source, registry)
    llm = _SilentLlm()

    result = asyncio.run(resolve_target(
        llm=llm,
        question="une zone libre",
        world=world,
        contract=source,
        registry=registry,
        tools_mgr=_Manager(),
        allow_anchor=True,
        anchor_kinds=["raw_cell"],
        max_rounds=0,
        kind_policy=kind_policy(producer, {"kind": "image"}),
        producer_name="act",
    ))

    assert llm.calls >= 1
    assert result.error_code != "TARGET_KIND_MISMATCH"


def test_scope_dynamique_empeche_le_refus_anticipe():
    """Un producteur a scope lu par item peut reveler un scope accepte via une
    inspection : refuser avant le selecteur serait une conclusion prematuree."""
    source = _dynamic_scope_contract()
    producer = _contract({"ui": ["other"], "image": []})
    registry = TargetRegistry()
    projected = project_target_payload(
        registry,
        source,
        {"result": True, "data": {"candidates": [
            {"id": "ref-1", "name": "zone libre", "scope": "tile"},
        ]}},
    )[0]
    world = ObservedWorld.from_payload(registry, source, projected)
    llm = _SilentLlm()

    result = asyncio.run(resolve_target(
        llm=llm,
        question="une zone libre",
        world=world,
        contract=source,
        registry=registry,
        tools_mgr=_Manager(),
        allow_anchor=False,
        max_rounds=0,
        kind_policy=kind_policy(producer, {"kind": "image"}),
        producer_name="act",
    ))

    assert llm.calls >= 1
    assert result.error_code != "TARGET_KIND_MISMATCH"
    assert world.candidates == []  # le candidat exclu reste ecarte


def _dynamic_scope_contract():
    parsed = parse_target_contract({
        "version": 1,
        "produces": [{
            "path": "data.candidates",
            "mode": "candidates",
            "fields": {
                "ref": "id",
                "description": "name",
                "scope": "scope",
            },
        }],
    }, tool_name="scan")
    assert parsed.contract is not None, parsed.errors
    return parsed.contract
