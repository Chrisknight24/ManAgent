## What does this PR do?

<!-- One or two sentences. Link the issue if any: "Closes #123". -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor (no behavior change)
- [ ] Docs only
- [ ] Build / CI

## Checklist

- [ ] `pytest -q` passes.
- [ ] I ran a real stdin/stdout exchange (or `python examples/minimal_host.py`).
- [ ] No output bridage (no lowered `LLM_STRUCTURED_MAX_OUTPUT_TOKENS`, no `max_length`).
- [ ] No host/OS-specific hardcoding in the brain.
- [ ] Protocol/host-facing changes are documented in **both** FR and EN.
- [ ] No secret, `memory.db`, `.dll`, `observability/`, or `prompts_log/` is committed.
- [ ] Markdown links resolve with the exact file name (case matters on GitHub).

## Notes for reviewers

<!-- Trade-offs, follow-ups, out-of-scope items. -->
