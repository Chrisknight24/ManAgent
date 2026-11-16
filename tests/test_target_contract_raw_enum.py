"""`consumes[].param` + `enum` : la seule exception qui a du sens.

Un consommateur ordinaire recoit un **nom local** produit par le cerveau : un
`enum` le contredirait, et un plan pourrait croire qu'il doit envoyer une valeur
hote. Le contrat est alors refuse (tout ou rien).

Mais un consommateur `raw: allow` accepte **aussi** un litteral de l'hote, et un
`enum` decrit alors exactement ce vocabulaire ferme. Refuser cette combinaison
obligeait un hote a choisir entre « valider son vocabulaire au plan » et
« declarer le parametre comme consommateur » (rejeu 22a92120, `surface`).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.alignment import invalid_enum_value
from core.target_contract import parse_target_contract, resolve_consumer_arguments


def _schema(**properties):
    return {
        "type": "object",
        "properties": properties,
        "required": [],
    }


def _contract(raw_policy=None, *, enum=("bureau", "barre_des_taches")):
    consumer = {"param": "surface", "required": False}
    if raw_policy is not None:
        consumer["raw"] = raw_policy
    surface_schema = {"type": "string"}
    if enum is not None:
        surface_schema["enum"] = list(enum)
    return {
        "version": 1,
        "consumes": [consumer],
        "produces": [{
            "path": "data.target",
            "mode": "single",
            "scope": "target",
            "kind_param": "kind",
            "kind_scopes": {"ui": ["ui_preset"], "image": []},
            "fields": {"ref": "id", "description": "name"},
        }],
    }


def _parse(raw_policy, *, enum=("bureau", "barre_des_taches")):
    schema = _schema(surface={"type": "string"})
    if enum is not None:
        schema["properties"]["surface"]["enum"] = list(enum)
    return parse_target_contract(
        _contract(raw_policy, enum=enum),
        tool_name="target",
        parameters_schema=schema,
    )


def test_enum_sur_consommateur_ordinaire_reste_refuse():
    result = _parse(None)
    assert result.ok is False
    assert result.contract is None
    assert any("enum" in error for error in result.errors)


def test_enum_sur_consommateur_raw_allow_est_accepte():
    result = _parse("allow")
    assert result.ok is True, result.errors
    assert result.contract is not None
    consumer = result.contract.consumers[0]
    assert consumer.param == "surface"
    assert consumer.raw_allow is True
    # Le reste du contrat survit : c'est tout l'enjeu (un seul refus faisait
    # tomber `kind_scopes` et `occlusion` avec lui).
    producer = result.contract.producers[0]
    assert producer.kind_scopes["image"] == ()


def test_le_vocabulaire_ferme_reste_valide_au_plan():
    schema = _schema(surface={
        "type": "string",
        "enum": ["bureau", "barre_des_taches"],
    })
    tool = {"name": "target", "parameters": schema}
    assert invalid_enum_value(tool, {"surface": "bureau"}) is None
    message = invalid_enum_value(tool, {"surface": "n_importe_quoi"})
    assert message


def test_un_litteral_inconnu_traverse_l_egress_sans_etre_resolu():
    result = _parse("allow")
    assert result.contract is not None
    args, _display, error = resolve_consumer_arguments(
        None, result.contract, {"surface": "bureau"}
    )
    assert error is None
    assert args == {"surface": "bureau"}


def test_un_nom_local_connu_reste_resolu_meme_avec_raw_allow():
    result = _parse("allow")
    assert result.contract is not None

    class _Ref:
        value = "ref-42"

    class _Registry:
        def get(self, name):
            return {"local_name": name} if name == "zone" else None

        def resolve(self, name, scope):
            assert name == "zone"
            return _Ref()

    args, display, error = resolve_consumer_arguments(
        _Registry(), result.contract, {"surface": "zone"}
    )
    assert error is None
    assert args == {"surface": "ref-42"}
    assert display == {"surface": "zone"}


def test_enum_reste_verifie_avant_l_appel_pour_un_consommateur_raw_allow():
    """Un `enum` accepte au parsing n'est pas un `enum` ignoré : le contrôle
    d'avant-appel doit toujours refuser un littéral hors vocabulaire."""
    from tools.internal_tools import _prevalidate_external
    from tools.tools_manager import ToolsManager

    tm = ToolsManager(runtime_state=None)
    tm.register_tool(
        "target", "r", "anchor",
        {
            "type": "object",
            "properties": {
                "surface": {
                    "type": "string",
                    "enum": ["bureau", "barre_des_taches"],
                },
            },
            "required": [],
        },
        kind="utility",
        target_contract={
            "version": 1,
            "consumes": [{
                "param": "surface",
                "scope": "surface",
                "required": False,
                "raw": "allow",
            }],
            "produces": [{
                "path": "data.target",
                "mode": "single",
                "scope": "target",
                "fields": {"ref": "id", "description": "name"},
            }],
        },
    )

    assert tm.get_target_contract("target") is not None
    assert _prevalidate_external(tm, "target", {"surface": "bureau"}) is None
    message = _prevalidate_external(tm, "target", {"surface": "n_importe_quoi"})
    assert message
    assert "surface" in message
