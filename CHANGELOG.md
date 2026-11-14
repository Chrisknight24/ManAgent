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
- Proof gate for produced targets (`target.gate` event: `proven`, `unproven`,
  `contradicted`), including a post-producer gate for `single` mode on both
  the perception meta-tool and the direct plan producer path.

### Changed
- Target verification is now strict by default: `uncertain` and `mismatch`
  block any binding or action (`TARGET_UNPROVEN` / `TARGET_MISMATCH`).
  The former fail-open-with-warning behaviour remains available only for
  benches that do not enable the gate.
- An anchor consumed by a producer is treated as a locator: when the host
  declares an inspection probe for that anchor kind, it runs before the anchor
  is consumed and the selector re-decides once with the observed world.
- `target_selection` / `target_verification` prompts (base/fr/en) state
  explicitly that an anchor is not a proof and that a doubt never authorizes
  an action, and that host-declared facts in `attributes`/`invariants`
  (context, proof scope, enumerability, replaced candidate, truncated value)
  constrain the verdict instead of decorating it.
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
- A target produced in `single` mode could be bound on its mere uniqueness,
  without any proof of its nature; it is now verified against the question
  with the source contract's evidence before any binding.

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
