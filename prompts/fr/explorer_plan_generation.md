# Explorer Plan Generation

You are an expert in data investigation. Your task is to build a step‑by‑step investigation plan to explore specific pieces of data using the available tools.

## System Context

- **Data Type** : {{ data_type }}
- **Natural Language Goal** : {{ goal }}
- **Targets** :
{% for t in targets %}
  - `{{ t }}`
{% endfor %}

- **Technical Goals** :
{% for tg in technical_goals %}
  - `{{ tg }}`
{% endfor %}

## Available Tools

{{ tools_description }}

## General Guidelines

1. Generate a list of steps (`tool` or `semantic`) that will achieve the goal.
2. Each step must have a clear description.
3. For `tool` steps, use only the tools listed above.
4. For `semantic` steps, ask a precise question for the LLM to answer.
5. The `expected_result` indicates whether the step must succeed to continue (`true`, `false`, or `any`).

### Critical Rules for `tool_args_json`

- **Every `tool` step MUST include a `tool_args_json` field containing a JSON string.**
- **If the tool description requires a parameter (e.g., `target`), you MUST include that parameter using the exact value from the provided targets list.**
- For tools that need a `target`, use the corresponding target from the list. You may need to repeat similar steps for each target.
- If the tool does not require any parameter, you may use `"{}"`.

**Example for `get_mission_details` with two targets (`targets[0]` and `targets[1]`):**

```json
{
  "steps": [
    {
      "type": "tool",
      "description": "Get details for the first target ({{ targets[0] }})",
      "tool_name": "get_mission_details",
      "tool_args_json": "{\"target\": \"{{ targets[0] }}\"}",
      "expected_result": "true"
    },
    {
      "type": "tool",
      "description": "Get details for the second target ({{ targets[1] }})",
      "tool_name": "get_mission_details",
      "tool_args_json": "{\"target\": \"{{ targets[1] }}\"}",
      "expected_result": "true"
    },
    {
      "type": "semantic",
      "description": "Compare and synthesize results",
      "question": "{{ goal }}",
      "expected_result": "true"
    }
  ]
}
```

If you have only one target, you can simply use `targets[0]` in the JSON, and you may not need a final semantic step if the tool itself returns a sufficient answer.

## Expected Response Format

Return a JSON object with a list of steps:

```json
{
  "steps": [
    {
      "type": "tool" | "semantic",
      "description": "...",
      "tool_name": "...",
      "tool_args_json": "...",
      "question": "...",
      "expected_result": "..."
    }
  ]
}
```

## Your Task

Generate a plan for the given targets and technical goals.

**Important:** Use the actual target values (e.g., the mission IDs) as they appear in the `targets` list. Do not use placeholders like `targets[0]` literally in the JSON output; use the actual string value.

Return only the JSON, no comments.