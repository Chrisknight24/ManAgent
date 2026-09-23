# PROTOCOL — Talking to ManAgent (v2, written from the real code)

> Source of truth, written from `core/orchestrator.py`.
> Rule: one line = one JSON. Host-side, only read lines starting with `{`.
> French version: `docs/PROTOCOL.md`.

## 1. In 30 seconds

ManAgent is a separate program (black box). You start it, send JSON requests
on its stdin, it answers JSON on its stdout.

```
You (host)                        ManAgent
   │                                   │
   │── {"action":"runtime.configure",...} ──→ │  1. configure (once)
   │── {"action":"host.manifest.register",...} → │  2. introduce yourself
   │── {"action":"chat.send",...} ──→        │  3. send a mission
   │←── {"event":"response.chunk",...} ──    │  streamed answer
   │←── {"response"...} ──                   │  final answer
```

## 2. Launch

```
<python-or-managent.exe> <path-to-main.py-or-nothing>
```

- First message expected: `{"type":"event","event":"runtime.ready","payload":{}}`
- No `runtime.ready` within 15 s → kill and restart.

## 3. The 4 message shapes

**Request (you → ManAgent):**
```json
{"id": "req_001", "type": "request", "action": "chat.send", "payload": {"content": "Hello"}}
```
`id` = your tracking number (optional).

**Final response (ManAgent → you):**
```json
{"id": "req_001", "type": "response", "status": "success", "payload": {...}}
```

**Real-time event:**
```json
{"type": "event", "event": "response.chunk", "payload": {...}}
```

**Error:**
```json
{"type": "error", "message": "..."}
```

## 4. Mandatory sequence

1. `runtime.configure` — API keys + models (once at startup).
2. `host.manifest.register` — who you are: name, version, OS, tools, capabilities.
3. `chat.send` — missions. Listen to events in between.

## 5. All actions (checked against the code)

### Startup
| Action | What for | Example `payload` |
|---|---|---|
| `runtime.configure` | keys + models + embeddings | see §6 |
| `host.manifest.register` | introduce the host | `{"host_manifest": {...}}` |
| `system.warmup` | pre-warm (embeddings, cache, prompts) in background | `{}` |

### Conversation / mission
| Action | What for | Example `payload` |
|---|---|---|
| `chat.send` | send a message / mission. `forced_provider` + `forced_model` OPTIONAL: without them, auto-routing (model with `text` capability) | `{"content": "...", "forced_provider": "gemini", "forced_model": "gemini-3.5-flash-lite"}` |
| `chat.stop` | emergency stop | `{}` |
| `chat.reset` | stop everything + forget the current mission | `{}` |
| `session.delete` | erase a session (RAM + database) | `{"session_id": "..."}` |
| `tool.result` | return a host tool result | `{"call_id": "...", "result": "..."}` (see §7) |

### Embedding models
| Action | What for | Example `payload` |
|---|---|---|
| `embeddings.catalog` | model list + installed or not | `{}` |
| `embeddings.prepare` | download/preload a model (with progress). Disk pre-check first | `{"id": "...", "force": false, "set_default": false}` |
| `embeddings.cancel` | cancel a download (auto-resume on next prepare) | `{"id": "..."}` |
| `embeddings.set_default` | hot-swap models. Separate memory per model, old data preserved | `{"id": "lite-hash"}` |

### Skills (draft → shadow → production → quarantine)
| Action | What for | Example `payload` |
|---|---|---|
| `skill.list_request` | list skills | `{}` |
| `skill.payload_request` | read a skill | `{"skill_id": "...", "version": 1}` |
| `skill.set_state` | change state | `{"skill_id": "...", "state": "QUARANTINE", "version": 1}` |
| `skill.repair` | repair with an LLM (background) | `{"skill_id": "...", "version": 1}` |
| `skill.export_package` / `skill.import_package` | share skills | `{"skill_id": "..."}` / `{"package": {...}}` |

### Memory & maintenance
| Action | What for | Example `payload` |
|---|---|---|
| `learner.analyze` | re-analyze past missions (lessons) | `{"force": false}` |
| `data.stats` | counts (episodes, lessons, sessions, skills...) | `{}` |
| `data.export` | export everything | `{}` |
| `data.purge` / `system.reset_data` | erase. `target`: all, cache, episodes, lessons, skills, sessions... | `{"target": "all"}` |

Unknown action → `{"type":"error","message":"Unknown action: ..."}`. Never crashes.

## 6. `runtime.configure` in detail (real format)

```json
{
  "system_prompt": "You are a helpful assistant.",
  "language": "en",
  "protocol_version": "1",
  "environment": "simulated",
  "hitl_policy": "balanced",
  "api_keys": {"gemini": "YOUR_KEY"},
  "models_registry": {"providers": {"gemini": {"models": [
    {"id": "gemini-3.5-flash-lite", "capabilities": ["text"]}
  ]}}},
  "host_manifest": {...},
  "embeddings": {"mode": "lite"}
}
```
- `hitl_policy`: `strict` (confirm every risk), `balanced` (skip if already approved
  in this mission), `autonomous` (confirmations bypassed). Read at top level OR
  inside `runtime_configuration` (top wins), echoed in `runtime.configured`.
- Secrets: write `"env:VAR_NAME"` instead of the key
  (e.g. `"api_key": "env:MY_GEMINI_KEY"`). Lists allowed for multi-key rotation.
  Missing variable = empty string + warning.
- `environment`: `simulated` = dev/tests, `real` = production host.
- `protocol_version`: language version ("1"), echoed in `runtime.configured`.
  Missing = accepted + warning (transition). Different = loud
  `protocol version mismatch` error, no session. Bump on every breaking change.
- Answer: `{"models_count": 1, ...}`. If `0`, the model format is wrong.

## 7. Tool loop (the host works, ManAgent decides)

ManAgent never does the work itself. When it needs a host tool:
```
← {"event":"tool.requested","payload":{"call_id":"abc123","tool_name":"click","arguments":{...}}}
```
The host executes (real work) and answers:
```json
{"action": "tool.result", "payload": {"call_id": "abc123", "result": "..."}}
```
`result` is itself JSON-as-text: `{"result": true|false, "data": {...},
"error_reason": "..."}`. `call_id` = tracking ticket, verbatim.

## 8. Events to know (observability)

Health: `runtime.ready`, `runtime.configured`, `runtime.error`, `heartbeat`.
Mission: `mission.started`, `plan.generated`, `step.status_changed`,
`plan.abandoned`, `mission.failed`, `execution.completed` (end of mission,
success or failure — the host exits run mode on it).
Streaming: `thinking.started`, `response.chunk`, `response.completed`, `thinking.finished`.
Tools: `tool.requested`, `tools_manager.decision/execution/result`.
Skills: `checkpoint.reached`, `breakout.occurred`, `skill.state_changed`.
Embeddings: `embedding.download_started/finished/error/cancelled`, `embedding.active_changed`.
Learning: `learner.analyze_started/finished`.
Sequence: call `embeddings.catalog` right after `runtime.configured`.

## 9. Rules of the game

1. One line = one JSON. Ignore the rest (logs).
2. Always configure before chatting.
3. An existing action is never removed without a major version bump.
4. `rules.md` (ManAgent side) constrains plans: human confirmations, rejections, risk.
