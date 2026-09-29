"""Rules à chaud : contrat léger, sans import lourd. Nos yeux auto."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _pick_rules(file_text: str, override=None) -> str:
    """Miroir de la priorité Orchestrator._load_rules_md : override non vide gagne."""
    if isinstance(override, str) and override.strip():
        return override
    return file_text


def test_actions_exist():
    from core.constants import Actions
    assert Actions.STATS_GET == "stats.get"
    assert Actions.RULES_GET == "rules.get"
    assert Actions.RULES_SET == "rules.set"


def test_file_used_without_override():
    assert _pick_rules("FILE-RULES", None) == "FILE-RULES"


def test_override_wins_over_file():
    assert _pick_rules("FILE-RULES", "HOST-RULES") == "HOST-RULES"


def test_empty_override_falls_back_to_file():
    assert _pick_rules("FILE-RULES", "   ") == "FILE-RULES"


def test_docs_mention_new_actions():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fr = open(os.path.join(base, "docs", "HOST_CONTRACT.md"), encoding="utf-8").read()
    en = open(os.path.join(base, "docs", "en", "HOST_CONTRACT.md"), encoding="utf-8").read()
    assert "stats.get" in fr and "rules.set" in fr
    assert "stats.get" in en and "rules.set" in en
