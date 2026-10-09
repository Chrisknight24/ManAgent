# FEASIBILITY CHECK

You decide if the goal can be reached with the listed tools.
If yes, you write a short strategy for the Planner.

## GOAL
{{ goal }}

## LESSONS (leads, not orders)
{% if advice %}
Reports from past JUDGED missions (the judge can be wrong): suggestive leads, never orders.
{{ advice }}
{% else %}
[No advice.]
{% endif %}

## CONTEXT
{{ context or "None." }}
{% if world_snapshot %}
## CURRENT STATE (default snapshot — context only, never proof)
{{ world_snapshot }}
{% endif %}

## SIMILAR MISSIONS
{% if similar_missions %}
{{ similar_missions }}
{% else %}
[None.]
{% endif %}

## TOOLS (name + one line: keep them all in mind)
{{ tools }}

{{ tools_guidance }}

{% if skills %}
## READY SKILLS
{{ skills }}
If a skill covers the goal, the strategy prioritizes it (`execute_skill`).
{% endif %}

## REGISTRY
{{ registry }}

---

## DECISION

Feasible = a chain of available tools leads to the goal. A long chain is still feasible.
Not feasible = a needed capability with no tool.

## STRATEGY (when feasible)

`refined_strategy`: 2 to 6 short numbered sentences, one per tool. Each sentence names ONE tool and says what it must do. The last one says what the final answer contains.
- Sub-goals ALL different from each other and from the GOAL: never the GOAL copied as a sub-goal (guaranteed infinite loop otherwise).
- `tool_call` for every single action.
- `abstract_task` only for a sub-goal with several actions and choices. Never to read or test a variable.
{% if world_guidance is not defined or world_guidance %}- Read the world = ONE `perceive_understand` step (reads + explains). No analysis after it.
{% endif %}- The strategy advises. The Planner decides.

## NOT FEASIBLE

`is_possible` = false. In `reason`, name the capability with no tool.

## ANSWER

JSON: `is_possible`, `reason`, `refined_strategy`.
