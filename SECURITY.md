# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | yes |

## Reporting a vulnerability

Do **not** open a public issue for a security problem.

Use GitHub private reporting:
<https://github.com/Chrisknight24/ManAgent/security/advisories/new>

Please include:
- a short description and impact,
- steps to reproduce (minimal JSON packets if relevant),
- affected version / commit,
- any suggested fix.

You will get an acknowledgement as soon as possible. Please give us time to
ship a fix before any public disclosure.

## Secrets

- Never commit API keys. Use environment references: `"api_key": "env:MY_KEY"`.
- Logs, prompts, and `prompts_log/` must never contain secret values.
- `memory.db`, `observability/`, and `prompts_log/` are local runtime data and
  must not be committed.
