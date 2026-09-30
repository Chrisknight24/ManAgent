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

## 3d. Rigid tool descriptions (mandatory, imposed by ManAgent)

The brain only sees your descriptions. Vague = mechanical hallucination,
not bad will. Absolute rules for every declared tool:

1. **Ref provenance**: every reference-type param (IDs, windows, targets,
   cells) must say where the value comes from (output of a previous tool,
   verbatim, never invented) and name the refusal error otherwise.
   Ex: `target_id` comes from a `perceive` output of this mission,
   invented = `STALE_REF` failure.
2. **Chaining**: if a tool needs another one before it
   (perceive before waiting, capture before clicking), write it down.
3. **Explicit danger**: every ban carries its sanction
   (`Never guess: without a valid ID, TARGET_NOT_FOUND failure`).
   A permission without a guard (`no need to have perceived it`) will be
   read as an authorization to invent.
4. **Usage example if ambiguous**: if a param reads poorly
   (ID format, quotes, case), add a full call example in the description.
5. **Adaptation guardrails**: your tools must tolerate brain data
   (spaces, stray quotes, case) and adapt silently when risk-free,
   or refuse with the exact reason otherwise.
   The brain follows descriptions as best it can; if nothing is clear,
   it guesses — mechanically.

## 4. Supported actions

`runtime.configure`, `host.manifest.register`, `chat.send`,
`chat.stop` / `chat.reset`, `tool.result`, `skill.*` (list, payload, set_state,
repair, export/import), `learner.analyze`, `system.warmup` / `system.reset_data` /
`data.*`, `embeddings.*` (catalog, prepare, set_default, cancel),
`stats.get`, `rules.get` / `rules.set`.

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

## 5a. Typed outputs (optional, imposed by ManAgent)

By default, `data` is treated as text. If a tool returns a non-textual
payload (image, audio, PDF...), declare it in the manifest, per tool,
`returns` field (list). Imposed vocabulary: standard MIME types
(`image/jpeg`, `image/png`, `application/pdf`...), never proper nouns.
Your tool and field names stay free.
```json
{"name": "capture_image", "kind": "perception",
 "parameters": {"type": "object", "properties": {}, "required": []},
 "returns": [
   {"field": "image_base64", "asset": "image/jpeg",
    "description": "Base64-encoded screen photo."},
   {"field": "frame", "asset": "application/json",
    "description": "Detected structure."}
 ]}
```
- `field`: dotted path inside `data` (e.g. `image_base64`, or `preview.thumb`).
- `asset`: imposed type. Plain `image` is enough: ManAgent checks the bytes
  (magic signature) and labels the true type. A full MIME (`image/jpeg`...)
  stays recommended but content wins. Only non-text (`image`, `image/*`,
  `video/*`, `audio/*`, `application/pdf`) is extracted into assets. The rest stays inline.
- ManAgent registers each payload as a typed asset and exposes its address
  (`outputs://...`) in the registry. Image analysis uses the address,
  never base64. Without a `returns` declaration, behavior is unchanged (text).

Handled types, end to end (declaration → model):

| Family | Examples | Actual handling |
| `image/*` | jpeg, png, webp, gif, bmp | pixels to the model, if the model has vision, else clear refusal |
| `application/pdf` | pdf | document to the model |
| `video/*`, `audio/*` | mp4, mp3 | stored as assets, not yet sent to the model |
| text, JSON, CSV | the rest | inline + forage by slices |

## 6. Observability

Stable events to log host-side: `plan.generated`, `tools_manager.decision/execution/result`,
`checkpoint.reached`, `breakout.occurred`, `execution.completed`, `learner.analyze_finished`.

Token usage: every `llm_call` carries `usage = {prompt_tokens, completion_tokens, total_tokens, source}`
with `source` = `real` (provider figure) or `estimated` (local ~4 chars = 1 token). Never invented.
Query: `stats.get {"mission_id":"m123"}` → totals, or `{}` → total + by_mission. Unknown mission → `found:false` + zeros.

Rules: file `rules.md` for dev (edit + restart). Prod (exe): `runtime.configure {"rules_text":"..."}` once,
or `rules.set` live + `rules.get` to read back. Memory override wins over file.

## 6b. Skills: who decides the numbers

Defaults: automation after **2** consecutive successes, 1 shadow trial before prod, quarantine after **3** failures, max **3** repairs. These are displayed defaults — you decide for your context.

| Setting | Default | Plain meaning |
|---|---|---|
| `discovery_threshold` | 2 | consecutive successes before creating the automation |
| `shadow_success_threshold` | 1 | successful shadow trials before prod |
| `shadow_mismatch_threshold` | 3 | mismatches before quarantine |
| `circuit_breaker_max_failures` | 3 | consecutive failures before quarantine |
| `max_repairs` | 3 | repairs before retirement (`null` = infinite, yours to assume) |
| `champion_margin` | 0.05 | lead required to replace the prod version |

3 ways, broadest to narrowest (narrowest wins, every decision logs its source in events):
1. **Global**: `"skill_governance": {"circuit_breaker_max_failures": 1}` in the manifest.
2. **Tool sensitivity**: `"sensitivity": "high"` on a tool (bank transfer) → the automation becomes strict by itself (thresholds halved). `"low"` → relaxed (×2). Default = standard.
3. **Per skill**: `"skill_governance": {"skills": {"skill.transfer.send": {"circuit_breaker_max_failures": 1}}}` (IDs come from `skill.list_request`).

Bank (strict): `{"tools": [{"name": "transfer.send", "sensitivity": "high", "requires_env": ["session_auth"]}], "skill_governance": {"max_repairs": 1}}`.
Demo (relaxed): `{"skill_governance": {"circuit_breaker_max_failures": 5, "max_repairs": null}}`.
Golden rule: an imported automation starts as shadow (never instant trust). A v2 replaces v1 only with more proof.

## 6c. Exploration (tunable limits)

When the brain lacks info, it explores (defaults: 5 back-and-forths, 10 steps). Hitting the ceiling emits `discovery.ceiling_hit`. To explore more:
```json
{"type":"request","action":"runtime.configure","payload":{"discovery":{"max_iterations":8,"max_session_steps":12}}}
```
Clamped 1..20, defaults otherwise. Hard ceiling: 20 (no infinite loops).

## 6d. Embeddings: who uses your model, how much RAM

Your chosen model serves EVERYTHING: mission memory, lessons (write AND read), skill profiles. One space at a time, never mixed. Lessons from another model are skipped with a log (dimension guard).

Approximate RAM: `lite` (default) 0 MB offline · MiniLM/e5 ~500 MB · BGE-M3 ~2.3 GB · `remote` 0 MB local, billed per use. 1 GB+ in dev with BGE-M3 is normal (torch + weights). To slim down: `lite` or `remote`.

## 7. Secrets

Never hardcode keys: `"api_key": "env:MY_KEY_VAR"` (lists supported for rotation).
Missing variable = empty string + warning, never a crash.

## 8. Compatibility

Only optional fields are added; an existing action is never renamed without a
major version bump. `protocol_version` mismatch = loud error, no session.
