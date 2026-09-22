# Initiative review rules

Inputs: read the alignment file at `{ALIGNMENT_PATH}` if it exists. If missing, alignment was not assessed (no RHAISTRAT parent or agent did not complete).

Determine recommendation:
- submit: Initiative passes (7+ with no zeros)
- revise: Initiative fails but can be improved
- split: right_sized scored 0, indicating the initiative is a grab-bag of genuinely separate initiatives — each serving a different goal and ownable by a different, unrelated team or group, not facets of one mission. BUT only if no OTHER criterion scored 0 — splitting an initiative with an unfixable problem just produces more initiatives with the same problem. Otherwise **recommend revise**.
- reject: 3+ criteria scored 0, fundamentally infeasible, or needs rethinking

Do NOT recommend split for a single overarching goal owned by one team or related group of teams (however many facets, problems, or domains it spans), for connected pieces under one theme, for individually-minor items in a deliberate container, or for **delivery-coupled** workstreams (must ship together or share a critical path) — recommend revise instead. Breadth across domains or personas is not, by itself, grounds to split.

Set `needs_attention=true` when the Initiative needs human review despite its score — e.g., feasibility is indeterminate/infeasible, references non-existent components, or has concerns the rubric doesn't capture. When true, also set `needs_attention_reason` to a concise explanation (1-2 sentences) of what needs human attention. When false, set `needs_attention_reason=null`.

Parse the alignment verdict from the alignment file's `**Alignment**:` line. If the alignment file was missing or alignment is `not_assessed`, omit the alignment field — it defaults to `not_assessed`.

If alignment is `weak`, set `needs_attention=true` (strategic misalignment requires human review).
