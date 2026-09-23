# HOST_CONTRACT — How to use ManAgent without reading its code

ManAgent is a black box: you use it without looking inside.
The host (Qt, Web, CLI) NEVER touches the `.py` files. It starts a process,
sends JSON on stdin, reads JSON on stdout.

Full reference: `docs/en/PROTOCOL.md`. This contract is the stable summary
for integrators. French version: `docs/HOST_CONTRACT.md`.

## 1. Launch

Command:
```
<MANAGENT_PYTHON> <MANAGENT_PATH>
# dev example: C:/.../ManAgent/.venv/Scripts/python.exe C:/.../ManAgent/main.py
# prod: managent.exe [--data-dir DIR] [--version]
```

- `MANAGENT_PATH`: path to `main.py`. `MANAGENT_PYTHON`: ManAgent's own
  `.venv` python (never the system one).
- Working dir: the ManAgent folder (for `prompts/`, `rules.md`).
- Protocol: one line = one JSON. Only JSON on stdout, logs go to stderr.
- First signal to wait for:
  `{"type":"event","event":"runtime.ready","payload":{}}`
- Version handshake: `runtime.configure` sends `"protocol_version": "1"`
  (top level); `runtime.configured` echoes it. Mismatch = loud error, no session.

Healthcheck: no `runtime.ready` within 15 s → kill and restart.

## 2. Packets

Request (host → ManAgent):
```json
{"id":"req_001","type":"request","action":"chat.send","payload":{"content":"Hello"}}
```

Final response (ManAgent → host):
```json
{"id":"req_001","type":"response","status":"success","payload":{}}
```

Real-time event (streaming, observability):
```json
{"type":"event","event":"response.chunk","payload":{"text":"Hel..."}}
```

Error:
```json
{"type":"error","message":"Provider unavailable"}
```

Rule: `action` is always lowercase with a dot (`runtime.configure`, `tool.result`).

## 3. Mandatory sequence

1. `runtime.configure` — providers + keys + system prompt. Always first.
2. `host.manifest.register` — introduce the host (see `docs/host.manifest.example.json`).
3. `chat.send` — send missions (`content` + optional `forced_provider`/`forced_model`,
   without them the brain auto-routes by capabilities).
4. Listen to `response.chunk` / `response.completed` + `plan.generated` / `execution.completed`.

## 3b. Embeddings (lite / local / remote)

The host picks the mode in `runtime.configure`:
```json
{"embeddings":{"mode":"lite"}}
```
- `"lite"` (default): 0 MB, offline, lower accuracy. Enough to start.
- `"local"`: e.g. `"model":"sentence-transformers/all-MiniLM-L6-v2"`. Accurate,
  ~90–500 MB downloaded on first run into `%APPDATA%/ManAgent/models`.
- `"remote"`: API-based (`model`, `api_key`, `base_url`). Accurate, 0 MB local, billed per use.

`runtime.configured` echoes `embeddings_mode` + `active_embedding_model`.

## 3c. Model management (setup UI — query, never hardcode)

1. `embeddings.catalog` → list with sizes + installed flags. Never hardcode a model host-side.
2. Download button → `embeddings.prepare {"id":...}` → `embedding.download_started/finished/error`.
   `force:true` re-downloads, `set_default:true` activates immediately.
3. Default button → `embeddings.set_default` → hot swap (+ `embedding.active_changed`).
   Clear error if not installed. One memory space per model, old data preserved.
4. Cancel button → `embeddings.cancel` (auto-resume on next prepare). Disk space is
   checked before downloading, with a clear error if insufficient.

## 4. Supported actions

`runtime.configure`, `host.manifest.register`, `chat.send`,
`chat.stop` / `chat.reset`, `tool.result`, `skill.*` (list, payload, set_state,
repair, export/import), `learner.analyze`, `system.warmup` / `system.reset_data` /
`data.*`, `embeddings.*` (catalog, prepare, set_default, cancel).

## 5. Tool loop (the host works, ManAgent decides)

ManAgent never does the work itself. It asks (`tool.requested`) and the host
does REAL work (no fakes), then always answers:
```json
{"type":"request","action":"tool.result","payload":{"call_id":"...","result":"..."}}
```
`result` is itself JSON-as-text with 3 simple keys:
- `result`: `true` (worked) or `false` (failed).
- `data`: the useful result (optional: text, object, list).
- `error_reason`: why it failed, in words (long text OK).

Success example:
```json
{"call_id":"abc123","result":"{\"result\": true, \"data\": {\"total\": 42}}"}
```
Honest failure example:
```json
{"call_id":"abc123","result":"{\"result\": false, \"error_reason\": \"button not found on screen\"}"}
```
`call_id` = the tracking ticket (mandatory, verbatim). Large payloads
(> ~3000 chars) are stored aside and paged, no panic.

## 6. Observability

Stable events to log host-side: `plan.generated`, `tools_manager.decision/execution/result`,
`checkpoint.reached`, `breakout.occurred`, `execution.completed`, `learner.analyze_finished`.

## 7. Secrets

Never hardcode keys: `"api_key": "env:MY_KEY_VAR"` (lists supported for rotation).
Missing variable = empty string + warning, never a crash.

## 8. Compatibility

Only optional fields are added; an existing action is never renamed without a
major version bump. `protocol_version` mismatch = loud error, no session.
