# ALIGNMENT — knowing whether the world matches the plan

ManAgent is agnostic (host, transport, models). Some hosts are deterministic
(an action always has the effect it reports). Some are not (the reported
success and the real effect can differ). The brain must handle both without
hardcoding any host, tool, or application.

## 1. Sensor vs judge

`perceive_understand` is a **sensor**: it reads the world through a host
perception tool and reports what it understood. Its `result` means
"I could read", never "the world matches the goal". A reading of "no" is
still a successful reading (`result=true, data="no"`).

Alignment ("does the world match?") is decided elsewhere:

- the planner branching on readings (`data == "yes"`),
- the final mission convergence (which may re-perceive through PD),
- never the sensor alone.

No new sensor tool is needed. What is missing is a **nudge** telling the
reasoning entities how much uncertainty a plan carries.

## 2. Declaring tool effects (optional, host side)

Each manifest tool may declare `effects`:

- `"deterministic"` — the reported result always equals the real effect.
- `"uncertain"` — the reported result and the real effect may differ.

Defaults (cautious, do not punish deterministic hosts that say nothing):

- `[action]` tools default to `"uncertain"`.
- `[perception]` and `[utility]` tools carry no uncertainty.

A deterministic host declares `"deterministic"` on its actions once and
pays zero extra perception. A volatile host leaves the default. Open
vocabulary, like `kind`. See `HOST_CONTRACT.md`.

## 3. Nudge points (incentive, never sanction)

Three reasoning entities see uncertainty. Each sees it differently.
The plan validator is **deliberately excluded** (see §5).

### 3a. Solver — general nudge

The solver has no plan yet but sees the available tools. Its prompt carries
one computed fact: the share of uncertain actions among available tools.
High share → prefer strategies with checkpoints (read before deciding,
verify after acting). Low share → no overhead. Strategy-level only.

### 3b. Planner — concrete nudge, with credit

The planner sees the tools it actually uses. Its prompt carries one
computed fact: how many uncertain steps the draft plan holds and whether
any perception covers them. The planner is trusted: it writes the
verification or owns the risk. No automatic refusal follows.

### 3c. Final convergence — the catcher

The final mission convergence receives the computed risk fact (same
pattern as repetition/novelty facts: the code computes, the LLM judges).
It may re-perceive through PD and refuse unproven alignment. This is the
only judge of alignment. One judge, not two.

## 4. Risk fact (deterministic, pure function)

Computed over `tool_call` steps only. Sub-tasks are handled recursively
through the execution tree, never flattened into the parent score.

Definitions:

- `U` = uncertain action steps in scope.
- `P` = last perception step index in scope (none = -1).
- `uncovered` = uncertain steps positioned after `P`.

Risk signal (schematic):

- `uncovered == 0` → low ("every uncertain step is covered by a later reading").
- `uncovered > 0` → proportional ("N uncertain steps since the last reading").

The prompt sentence is indicative, never blocking:

> "This plan holds N uncertain steps since the last world reading.
> A perception check through PD may be needed to confirm alignment."

## 5. Why the validator stays out

Refusing a plan costs a full attempt plus tokens, while the convergence
behind it can catch the same problem for free. A validator refusal on
"missing verification" would punish reasonable plans (deterministic hosts,
harmless sequences) and double-punish the rest. The validator keeps only
existence gates (unknown tool, unreadable args, unknown source): syntax,
never meaning.

## 6. Schematic example (abstract, no host)

Scope holds five steps: three uncertain actions, one perception, one
uncertain action, in this order:

1. uncertain action
2. uncertain action
3. perception ("is the expected state present?", strict yes/no)
4. uncertain action
5. final answer branching on the reading (`== "yes"` / `== "no"`)

Fact: "1 uncertain step since the last world reading." Low risk.
The same steps without step 3 → "3 uncertain steps, no world reading."
High risk → the planner is nudged to add a check; the final convergence
may re-perceive before accepting.

## 7. Explicitly out of scope

- No hard refusal on missing verification (a later mission may add it).
- No new sensor tool (the existing reader plus branching covers it).
- No host, tool, or application names anywhere in prompts or docs
  (placeholders only — concrete names would bias future test missions).
