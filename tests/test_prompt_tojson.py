"""tojson lisible : accents gardes, JSON toujours valide. Nos yeux auto."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.prompt_loader import get_prompt_loader


def test_tojson_keeps_accents():
    env = get_prompt_loader()._get_env("fr")
    out = env.from_string("{{ x | tojson }}").render(
        x={"d\u00e9tail": "\u00e7a", "liste": ["\u00e9t\u00e9"]})
    assert "d\u00e9tail" in out and "\u00e7a" in out
    assert "\\u00e9" not in out and "\\u00e7" not in out


def test_tojson_stays_valid_json_with_indent():
    env = get_prompt_loader()._get_env("fr")
    payload = {"outil": "voir_\u00e9cran", "args": {"texte": "l_\u00e9t\u00e9"}}
    out = env.from_string("{{ x | tojson(indent=2) }}").render(x=payload)
    assert json.loads(out) == payload
