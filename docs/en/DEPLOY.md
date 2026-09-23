# DEPLOY — Where to put managent (Qt host + dev)

French version: `docs/DEPLOY.md`.

## 1. Principle: binary vs data

- Binary (read-only): `managent.exe` + `_internal/` + `vec0.dll`.
- Data (writable): `memory.db`, `rules.md` (copy), `observability/`, models.
- The exe picks its data: `--data-dir <folder>` > `MANAGENT_DATA_DIR` > current folder.
- First run creates the folder and seeds a default `rules.md`.
- `managent.exe --version` prints the version (e.g. `managent 0.1.0`).

## 2. Dev (today)

The host launches a hand-built `dist/managent/managent.exe`.
Adjustable path (never hardcoded): `MANAGENT_EXE` + `MANAGENT_DATA_DIR`
pointing at a dev folder.

## 3. Shipping (host setup)

- Copy `dist/managent/` (lite) next to the host exe (replaces any
  `universal_agent_runtime/` folder).
- Default data: `%APPDATA%/ManAgent/` (via `MANAGENT_DATA_DIR` or
  `--data-dir`). Never write next to the exe (Program Files is protected).
- Launch: `managent.exe --data-dir "%APPDATA%/ManAgent"`, any working dir.
- Health: wait for `runtime.ready` (15 s), then `runtime.configure`.
- Embedding models: setup screen → `embeddings.catalog`, download with
  progress, see `HOST_CONTRACT.md` §3b–3c.
