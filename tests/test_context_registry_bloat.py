"""Contexte planner/presentator : le bloc REGISTRE ne doit pas dupliquer le registre.

Run Paint du 28/10 (mission b39a11ae) :
- 5e plan : 8 723 des 12 467 caractères du contexte « Already tried + errors »
  étaient le bloc `--- REGISTRE (MÉTADONNÉES) ---` ;
- prompt Presentator : 14 472 des 26 910 caractères ;
- or les prompts ont déjà une section registre compacte (`registry_compact`).

Le bloc complet (description + source + value_hint par variable) est donc
redondant et croît à chaque retry. On attend un résumé borné.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.executor import Executor


def _registry_view(n: int = 40) -> dict:
    return {
        f"var_{i:02d}": {
            "description": "Résultat détaillé de l'étape " * 20,
            "source": "mission_b39a11ae",
            "timestamp": "2026-10-28T20:47:00",
            "type": "data",
            "value_hint": "(objet JSON avec clés: cell, generation, kind)",
        }
        for i in range(n)
    }


def _format(view: dict) -> str:
    # _format_registry_view est pure : aucun solver/runtime nécessaire.
    return object.__new__(Executor)._format_registry_view(view)


def test_bloc_registre_borne():
    out = _format(_registry_view(40))
    assert len(out) < 500, f"bloc registre trop long : {len(out)} caractères"
    assert "40" in out
    assert "section registre" in out.lower()


def test_bloc_registre_ne_duplique_pas_les_details():
    out = _format(_registry_view(40))
    assert "hint:" not in out
    assert "source:" not in out
    assert "Résultat détaillé de l'étape" not in out
    assert "objet JSON avec clés" not in out


def test_bloc_registre_apercu_borne():
    out = _format(_registry_view(40))
    # L'aperçu ne doit pas re-lister les 40 noms.
    assert out.count("var_") <= 12
    assert "…" in out


def test_bloc_registre_vide():
    out = _format({})
    assert "vide" in out.lower()


def test_bloc_registre_repartition_par_type():
    view = _registry_view(3)
    view["cible"] = {"type": "target", "description": "cible"}
    out = _format(view)
    assert "target" in out
    assert "data" in out
