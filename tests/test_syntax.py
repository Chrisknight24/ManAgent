"""Garde syntaxe : chaque .py du runtime doit parser (rapide, sans imports).

Motif : une ligne collée dans core/orchestrator.py a cassé le démarrage
hôte alors que les 109 tests passaient — aucun ne l'importait.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRS = ["core", "providers", "tools", "transport", "utils", "memory",
        "embeddings", "observability", "locale"]
SKIP_DIRS = {".venv", "__pycache__", "build", "dist", ".git"}
TOP_FILES = ["main.py"]


def _iter_py():
    for top in TOP_FILES:
        p = os.path.join(ROOT, top)
        if os.path.exists(p):
            yield p
    for d in DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)


def test_all_runtime_files_parse():
    files = sorted(_iter_py())
    assert len(files) > 50, "trop peu de fichiers, balayage cassé ?"
    bad = []
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                ast.parse(f.read(), filename=path)
        except SyntaxError as e:
            bad.append(f"{path}:{e.lineno} {e.msg}")
    assert not bad, "erreurs syntaxe :\n" + "\n".join(bad)
