### Checklist (before answering)
- [ ] Every `tool_call` has an exact, non-empty `tool_name`.
- [ ] Every `$@_...` comes from an earlier step (or the registry) and is copied exactly.
- [ ] Every `execute_if` follows one of the two forms.
- [ ] No `abstract_task` to inspect a variable.
- [ ] No `output_variable_name` inside `tool_args_json`.
- [ ] Last step = `direct_answer`.
