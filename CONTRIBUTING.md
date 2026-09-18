# Contributing to ManAgent

## Quick start (début rapide)
1. `git clone https://github.com/Chrisknight24/ManAgent.git`
2. `cd ManAgent`
3. `python -m venv .venv`
4. Windows: `.venv\Scripts\activate` — Linux/macOS: `source .venv/bin/activate`
5. `pip install -r requirements.txt`
6. `pytest -q` — tout doit passer avant une PR (proposition de changement).

## Rules (règles)
- Une branche (branch) par changement : `git checkout -b fix/nom-court`.
- Petits commits (enregistrements) en anglais : `fix: ...`, `docs: ...`, `test: ...`.
- Ne jamais committer (sauvegarder sur git) : `memory.db`, `*.dll`, `.venv/`, `__pycache__/`, `observability/*.jsonl`.
- Si tu ajoutes un outil interne ou un provider, mets à jour les tests dans `tests/`.
- Docs : `docs/protocol.md` est la référence du protocole JSON.

## Checks before PR
- `pytest -q`
- `python main.py --help` (si applicable) ou test manuel stdin/stdout.
