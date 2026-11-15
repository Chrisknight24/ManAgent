"""Une preuve BORNEE n'est pas une preuve (et une passe vide n'est pas un fait).

Contexte reel : un hote declare une passe d'inspection limitee (compte
plancher, couverture partielle). Sans regle explicite, le verificateur lit
« 17 elements vus » comme une confirmation et conclut `match` sur la seule
geometrie — exactement ce que la regle 2 interdit.

Le prompt ne doit pas citer les cles de l'hote (`enumeration`, `truncated`,
vocabulaire de raisons) : il decrit la SEMANTIQUE du fait, pas son emballage.
Sinon on lie le cerveau a un hote.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANES = ("base", "fr", "en")

# Vocabulaire d'emballage propre a un hote : interdit dans le prompt.
HOST_KEY_PATTERNS = (
    r"\"enumeration\"",
    r"\benumeration\b",
    r"\"truncated\"",
    r"\buia_[a-z_]+\b",
    r"\bwell_known",
)


def _text(lane: str) -> str:
    return (ROOT / "prompts" / lane / "target_verification.md").read_text(
        encoding="utf-8"
    )


def test_la_regle_de_preuve_bornee_est_presente():
    for lane in LANES:
        text = _text(lane).lower()
        if lane == "en":
            assert "bounded" in text
            assert "floor count" in text
            assert "found **nothing**" in text
        else:
            assert "bornée" in text
            assert "compte plancher" in text
            assert "rien trouvé" in text


def test_la_passe_vide_est_distinguee_de_la_couverture_partielle():
    """Deux faits, deux poids : absence de mesure vs preuve limitee."""
    for lane in LANES:
        text = _text(lane).lower()
        assert "absence de mesure" in text or "absence of measurement" in text
        assert "provenance" in text


def test_aucune_cle_d_hote_dans_le_prompt():
    hits = []
    for lane in LANES:
        text = _text(lane).lower()
        for pattern in HOST_KEY_PATTERNS:
            for match in re.finditer(pattern, text):
                hits.append(f"{lane}: {match.group(0)!r}")
    assert not hits, "Prompt lie a un hote : " + "; ".join(hits)


def test_les_trois_lanes_restent_aligneés():
    """La regle vit dans les trois langues, a la meme place (avant la regle 3)."""
    for lane in LANES:
        text = _text(lane)
        marker = "bounded" if lane == "en" else "bornée"
        assert text.count("3. **") == 1
        assert text.index(marker) < text.index("3. **")
