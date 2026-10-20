# Contributing to ManAgent

## Quick start

1. `git clone https://github.com/Chrisknight24/ManAgent.git`
2. `cd ManAgent`
3. `python -m venv .venv`
4. Windows: `.venv\Scripts\activate` — Linux/macOS: `source .venv/bin/activate`
5. `pip install -r requirements-dev.txt`
   (core runtime + pytest; add `requirements-embeddings.txt` for local embeddings)
6. `pytest -q` — the whole suite must pass before a PR.

## Rules

- One branch per change: `git checkout -b fix/short-name`.
- Small commits, imperative English, scoped when useful:
  `feat(scope): ...`, `fix(scope): ...`, `docs(scope): ...`, `test(scope): ...`.
  The scope is optional; match the existing history.
- Never commit: `memory.db`, `*.dll`, `.venv/`, `__pycache__/`,
  `observability/`, `prompts_log/`.
- When you add an internal tool, provider, action, or event, update:
  - the code and its tests in `tests/`,
  - `core/constants.py` (official action/event lists),
  - `docs/PROTOCOL.md` and `docs/HOST_CONTRACT.md` (both languages).
- Keep `requirements.txt` (core) accurate. Heavy or optional dependencies go to
  `requirements-embeddings.txt`; test-only tools go to `requirements-dev.txt`.

## Checks before a PR

- `pytest -q` (green).
- Run the runtime on a real stdin/stdout exchange, or
  `python examples/minimal_host.py`:
  ```bash
  python main.py --version
  ```
  (`--help` is not implemented; use `--version` or manual stdin packets.)
- If you changed the protocol or a host-facing behavior, update the FR and EN
  docs in the same PR.

## Review checklist

- No output bridage (do not lower `LLM_STRUCTURED_MAX_OUTPUT_TOKENS`, do not add
  `max_length` guards): fix the cause, not the symptom.
- No host/OS-specific hardcoding in the brain.
- Docs match the code; links resolve with the exact file name (case matters on
  GitHub).

## Security

Do not open a public issue for a vulnerability. See [`SECURITY.md`](SECURITY.md).
