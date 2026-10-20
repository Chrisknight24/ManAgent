# Changelog

All notable changes to ManAgent are recorded here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Target Registry vision document (`docs/TARGET_REGISTRY.md`).
- Repository docs index (`docs/README.md`).
- Separate dependency sets: core, dev, optional local embeddings.
- PR/issue templates and security policy.

### Changed
- README rewritten as an English entry point, with a French mirror
  (`README.fr.md`) and accurate install/run/docs sections.
- `HOST_CONTRACT` FR/EN are now aligned on the same section numbers; the
  English version gained the missing perception meta-tool, world alias, and
  QProcess checklist sections.
- `requirements.txt` now contains only core runtime dependencies.
- Single pytest configuration (in `pyproject.toml`).
- Legacy protocol and image moved under `docs/legacy/`.

### Fixed
- Broken case-sensitive links to `docs/PROTOCOL.md`.
- Removed the reference to a deploy script that no longer exists.
- Removed the dangling `VERSION` reference (the version lives in `pyproject.toml`).

## [0.1.0]

### Added
- Event-driven Hub & Spoke runtime (Planner → Executor → Solver → Validator).
- JSON stdin/stdout protocol (v1) for Qt, Web, and CLI hosts.
- Host manifests with perception/action tools, typed outputs, effects,
  world state, active wait, and skill governance.
- Multi-provider routing (OpenAI, Gemini, Anthropic, Groq, DeepSeek, OpenRouter)
  with multi-key fallback.
- Embeddings: `lite` (hash), `local` (sentence-transformers), `remote` (API).
- Learning loop: discovery, lessons, skills, progressive disclosure.
- Observability events, token usage stats, and prompt recording (`MANAGENT_RECORD_PROMPTS`).
- Windows executable build (`managent.spec`, `tools/build_exe.py`).
