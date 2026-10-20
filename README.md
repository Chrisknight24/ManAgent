# ManAgent — Universal Agent Runtime

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Protocol v1](https://img.shields.io/badge/protocol-v1-informational.svg)](docs/PROTOCOL.md)

**ManAgent is an event-driven agent runtime.** It is a black box that speaks
JSON over stdin/stdout, so a Qt app, a web backend, or a CLI can drive it
without touching Python internals.

```
Host (Qt / Web / CLI) ──JSON──▶  ManAgent  ──▶  LLM providers
                                      │
                                      └──▶  host tools (perception / action)
```

One line in = one JSON packet. One line out = one JSON packet. Logs go to
stderr, never to stdout.

## Highlights

- **Hub & Spoke** orchestrator: Planner → Executor → Solver → Validator.
- **Host tools**: the host declares perception and action tools; ManAgent
  decides, the host executes. It never fakes results.
- **Multi-provider + multi-key fallback**: OpenAI, Gemini, Anthropic, Groq,
  DeepSeek, OpenRouter.
- **Learning loop**: discovery, learner, skills (shadow → production →
  quarantine), progressive disclosure.
- **i18n FR/EN**, prompts versioned in `prompts/base|fr|en`.
- **Stable JSON contract**: see [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## Quick start

```bash
git clone https://github.com/Chrisknight24/ManAgent.git
cd ManAgent
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
# source .venv/bin/activate

pip install -r requirements.txt          # core runtime
pip install -r requirements-dev.txt      # tests (pytest)
# optional: local embeddings (torch, ~500 MB+)
# pip install -r requirements-embeddings.txt
```

Run the runtime (it waits for JSON packets on stdin):

```bash
python main.py
# or, for a frozen build:
# managent.exe [--data-dir DIR] [--version]
```

## Try it in 5 minutes (no API key, no code)

```bash
python examples/minimal_host.py
```

This minimal host starts ManAgent, registers two fake tools, prints the model
catalog and memory stats. Then read:

- [`docs/HOST_CONTRACT.md`](docs/HOST_CONTRACT.md) — the black-box contract
  ([English](docs/en/HOST_CONTRACT.md)), especially §5 (returning a tool result).
- [`docs/host.manifest.example.json`](docs/host.manifest.example.json) — a
  complete manifest to copy.
- [`docs/host.manifest.schema.json`](docs/host.manifest.schema.json) — the
  machine-readable schema.

## Documentation

| Document | English | Français |
|---|---|---|
| Host contract (start here) | [EN](docs/en/HOST_CONTRACT.md) | [FR](docs/HOST_CONTRACT.md) |
| JSON protocol (full reference) | [EN](docs/en/PROTOCOL.md) | [FR](docs/PROTOCOL.md) |
| Deploy / run | [EN](docs/en/DEPLOY.md) | [FR](docs/DEPLOY.md) |
| Code map | — | [FR](docs/FILES.md) |
| Alignment & effects | — | [FR](docs/ALIGNMENT.md) |
| Build the executable | — | [FR](docs/BUILD_EXE.md) |
| Target registry (vision) | — | [FR](docs/TARGET_REGISTRY.md) |

Full index: [`docs/README.md`](docs/README.md).

## Host integration (Qt / QProcess)

Keep the git checkout **outside** your build directory and point your host at
it without hardcoding a path:

```text
MANAGENT_PATH=C:/.../ManAgent/main.py
MANAGENT_PYTHON=C:/.../ManAgent/.venv/Scripts/python.exe
```

Your QProcess should read these variables with a fallback such as
`QCoreApplication::applicationDirPath() + "/managent/main.py"`. The host only
speaks JSON on stdin/stdout, so the checkout can move freely.

Checklist for Qt: [`docs/HOST_CONTRACT.md` §9](docs/HOST_CONTRACT.md#9-checklist-qprocess-qt).

## Layout

| Path | Role |
|---|---|
| `main.py` | runtime entry point |
| `core/` | orchestrator, planner, executor, discovery, skills |
| `providers/` | one file per LLM provider + routing |
| `embeddings/` | lite / local / remote embedding backends |
| `memory/` | SQLite stores (missions, lessons, sessions, profiles) |
| `tools/` | internal tools, build script, observability report |
| `transport/` | JSON stdin/stdout (swap point for WebSocket/HTTP) |
| `prompts/` | versioned prompts (`base`, `fr`, `en`) |
| `tests/` | pytest suite |
| `docs/` | contracts, protocol, deploy, architecture |

## Tests

```bash
pytest -q
```

The suite covers 469 tests. On Windows, `vec0.dll` must sit next to `main.py`
(it is not versioned) so the SQLite vector extension can load.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Security policy:
[`SECURITY.md`](SECURITY.md). History: [`CHANGELOG.md`](CHANGELOG.md).

## License

MIT — see [`LICENSE`](LICENSE).
