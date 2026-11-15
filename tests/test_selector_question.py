"""Question du selecteur : intention ET contexte, jamais l'un au prix de l'autre.

`target_description or question` appauvrissait le prompt des que l'ecran
grossissait (liste de cibles) : le selecteur perdait l'intention.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.internal_tools import _SELECTION_CONTEXT_LIMIT, _selection_question


def test_les_deux_informations_arrivent():
    out = _selection_question("clique sur le menu du bureau", "icone corbeille")
    assert "menu du bureau" in out
    assert "icone corbeille" in out


def test_intention_en_tete():
    out = _selection_question("ouvrir la corbeille", "icone poubelle")
    assert out.startswith("ouvrir la corbeille")


def test_sans_contexte_inchange():
    assert _selection_question("ouvrir la corbeille", "") == "ouvrir la corbeille"


def test_sans_intention_garde_le_contexte():
    assert _selection_question("", "icone poubelle") == "icone poubelle"


def test_pas_de_duplication_quand_identique():
    assert _selection_question("idem", "idem") == "idem"


def test_contexte_borne():
    out = _selection_question("q", "x" * 5000)
    assert len(out) <= _SELECTION_CONTEXT_LIMIT + len("q") + 40


def test_contexte_deja_present_dans_l_intention():
    question = "clique sur la corbeille (icone poubelle)"
    assert _selection_question(question, "icone poubelle") == question
