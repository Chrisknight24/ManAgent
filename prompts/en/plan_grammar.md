## STEP GRAMMAR

Each step N automatically produces two channels:
- `$@_bool_step_N`: True if step N ran without error, False otherwise.
- `$@_data_step_N`: the returned text / data.

### Variables
- Only cite AUTO names (`$@_data_step_N`, `$@_bool_step_N`) or REGISTRY names.
- `output_variable_name` (e.g. `data_result`) creates `$@_bool_<root>` and `$@_data_<root>`. Never double the prefix (`$@_bool_bool_...` forbidden).
- A step uses only variables from EARLIER steps.
- `output_variable_name` NEVER goes into `tool_args_json` (top-level field only).
- Copy names EXACTLY; never invent a suffix.

### Conditions (`execute_if`) — two forms only
- `$@_bool_step_N == True` (or `== False`): step N ran (or failed).
- `$@_data_step_N == "exact text"` (or `!=`): what it returned. To branch on a world fact, read it first with `perceive_understand` (`format_response="yes or no"`), then test `== "yes"` / `== "no"`.
- `and` / `or` allowed. FORBIDDEN: `.result`, `.data`, `IN`, `CONTAINS`, functions.

### Advanced step fields
- `is_crucial: true`: highlights the data in the RUM (Mission Useful Registry). Reserve it for data that ANSWERS the question (result, value, final confirmation), never for intermediate artefacts.
- `should_world_state_change_after_action: true`: you expect a visible change after the action (else absent / `false`).
- `verify_with`: name of a `[perception]` tool to check a subtle effect (default `get_world_state`).
- `waiting_ms`: 0..30000 ms. Absent = default applied (declared host, else 3000 ms). BEFORE if reading, AFTER if acting.
- `execute_skill`: only if a matching skill is listed. All its required parameters must be filled (`@$_param_...`, never empty).

### Analyzing data or an image
`llm_analyze_data` handles ONLY an already-produced variable (`source: "$@_data_xxx"`). There is no magic source (`current_screen`). For visuals: first a capture tool → `output_variable_name`, then pass `$@_data_...` to `llm_analyze_data` (declared image: `$@_data_<step>_<field>`). Never an `abstract_task` to inspect a variable: direct `tool_call`.

### Checklist (before answering)
- [ ] Every `tool_call` has an exact, non-empty `tool_name`.
- [ ] Every `$@_...` comes from an earlier step (or the registry) and is copied exactly.
- [ ] Every `execute_if` follows one of the two forms.
- [ ] No `abstract_task` to inspect a variable.
- [ ] No `output_variable_name` inside `tool_args_json`.
- [ ] Last step = `direct_answer`.
