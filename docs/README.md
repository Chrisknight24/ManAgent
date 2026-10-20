# Documentation index

ManAgent is driven by a **JSON contract**. Most readers only need the host
contract; the rest is for maintainers.

## Start here

| You want to... | Read | Language |
|---|---|---|
| Integrate ManAgent in a host (Qt, Web, CLI) | [HOST_CONTRACT](HOST_CONTRACT.md) · [EN](en/HOST_CONTRACT.md) | FR / EN |
| Implement the JSON protocol | [PROTOCOL](PROTOCOL.md) · [EN](en/PROTOCOL.md) | FR / EN |
| Deploy or run the runtime | [DEPLOY](DEPLOY.md) · [EN](en/DEPLOY.md) | FR / EN |
| Find a file in the codebase | [FILES](FILES.md) | FR |
| Understand effects and alignment | [ALIGNMENT](ALIGNMENT.md) | FR |
| Build the Windows executable | [BUILD_EXE](BUILD_EXE.md) | FR |
| Understand the target registry design | [TARGET_REGISTRY](TARGET_REGISTRY.md) | FR |
| Follow the target registry implementation plan | [TARGET_ROADMAP](TARGET_ROADMAP.md) | FR |

## Language policy

- The **French** files under `docs/` are the primary reference.
- The **English** files under `docs/en/` cover the host-facing contract
  (contract, protocol, deploy). Keep the two versions in sync; if they differ,
  `docs/` wins.
- Internal documents (code map, alignment, build) are French-only for now.

## Contracts and schemas

- [`host.manifest.example.json`](host.manifest.example.json) — copy-paste manifest.
- [`host.manifest.schema.json`](host.manifest.schema.json) — machine-readable schema.
- [`TARGET_REGISTRY.md`](TARGET_REGISTRY.md) — Target Registry vision (design,
  not yet a shipped contract).

## Archive

- [`CHANTIER_TARGET.md`](CHANTIER_TARGET.md) — early scoping notes, superseded by
  `TARGET_REGISTRY.md`.
- [`legacy/protocol.legacy.md`](legacy/protocol.legacy.md) — pre-v1 protocol, kept
  for history only.
