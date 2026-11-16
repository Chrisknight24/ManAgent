"""Faits scalaires du registre (vague 1, B3).

Run `22a92120` : le planner ne voyait qu'un « donnée masquée » pour une sortie
qui contenait, en clair, la valeur decisive (titre, booleen, compteur). Il
devait donc demander une inspection ou deviner. On publie des scalaires bornes,
sans nom de champ privilegie ni JSON complet.

Regle purement structurelle : seuls les scalaires directs sont publies, bornes
en nombre, en longueur et en total.
"""

from types import SimpleNamespace

from core.solver import Solver


def _facts(value):
    return Solver._scalar_facts(Solver.__new__(Solver), value)


def test_scalaires_directs_publies():
    out = _facts({"window": "Bureau", "focused": True, "count": 3})
    assert "window=Bureau" in out
    assert "focused=true" in out
    assert "count=3" in out


def test_structures_imbriquees_ignorees():
    out = _facts({
        "window": "Bureau",
        "rect": [10, 20, 30, 40],
        "nested": {"a": 1},
        "items": [1, 2, 3],
    })
    assert "window=Bureau" in out
    assert "rect" not in out
    assert "nested" not in out
    assert "items" not in out


def test_valeurs_vides_ignorees():
    assert _facts({"a": "", "b": None, "c": "ok"}) == "c=ok"
    assert _facts({}) == ""
    assert _facts("pas un dict") == ""


def test_bornes_de_nombre_et_de_longueur():
    many = {f"k{i:02d}": i for i in range(40)}
    out = _facts(many)
    published = out.split(" · ")
    assert len(published) <= Solver._SCALAR_MAX_FIELDS
    long_value = "x" * 500
    single = _facts({"only": long_value})
    assert len(single) <= Solver._SCALAR_MAX_CHARS + len("only=") + 1
    assert single.endswith("x" * 10)
    total = _facts({f"k{i}": "v" * 60 for i in range(20)})
    assert len(total) <= Solver._SCALAR_MAX_TOTAL + Solver._SCALAR_MAX_CHARS


def test_nouvelle_ligne_aplatie():
    out = _facts({"label": "ligne 1\nligne 2"})
    assert "\n" not in out
    assert "ligne 1 ligne 2" in out


def test_vue_registre_utilise_les_faits_et_borne_le_reste():
    solver = Solver.__new__(Solver)
    solver.variable_registry = {
        "data_step_01": {
            "value": {"window": "Bureau", "focused": False},
            "type": "data",
            "description": "sortie de l'etape",
            "source": "step_01",
        },
        "data_step_02": {
            "value": {"rect": [0, 0, 10, 10]},
            "type": "data",
            "description": "sortie non scalaire",
            "source": "step_02",
        },
    }
    solver.runtime_state = SimpleNamespace(target_registry=None)
    solver.current_attempt = None

    view = solver._get_registry_metadata_view()
    assert "window=Bureau" in view["data_step_01"]["value_hint"]
    assert "focused=false" in view["data_step_01"]["value_hint"]
    # Aucun scalaire : on retombe sur le message masque historique, inchange.
    assert "donnée masquée" in view["data_step_02"]["value_hint"]
