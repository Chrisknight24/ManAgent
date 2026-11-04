You analyze the failure of a specific entity in an agentic engine.

RESPONSIBLE ENTITY: {{ entity_type }}
ROLE OF THIS ENTITY: {{ role_description }}

SUB-TASK GOAL: {{ goal }}
ERROR TYPE: {{ failure_class }}
ERROR DETAIL: {{ failure_reason }}

EXECUTION SEQUENCE (pruned):
{{ pruned_attempt }}

Instructions:
1. Identify a STABLE, narrow scope keyword summarizing the precise SITUATION at the origin of the failure (e.g. 'keyboard_run_dialog_focus_loss'). It is not necessarily an application.
2. Propose 5 to 8 BROAD, varied keywords (applications, actions, synonyms, involved tools) so this lesson can be found from a different mission goal.
3. Write a short actionable tip (1-2 sentences) addressed to {{ entity_type }}, as a lead for avoiding this kind of error in the future, considering its role above. Prefer wording such as "it is often preferable to…" or "one lead is to…" over "you must…". Note: Skills self-heal through their own registry (SkillRegistry); a rule that forbade or discouraged general use of the `execute_skill` tool or Skills would probably be counterproductive.
4. ANTI-PERSISTENCE: if the failure comes from a LACK of tools or skills (nothing available in the environment), a useful lead is to report the gap and stop quickly rather than insist. Conversely, an absolute rule such as "never refuse" would probably be excessive.
