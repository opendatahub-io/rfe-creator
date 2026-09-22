# RFE review rules

Inputs: read the feasibility file at `{FEASIBILITY_PATH}` — the technical feasibility analysis; the review's Feasibility section and its `feasibility` frontmatter field come from it.

Determine recommendation:
- submit: RFE passes (7+ with no zeros)
- revise: RFE fails but can be improved
- split: right_sized scored 0/2, OR scored 1/2 AND capabilities serve different customer segments. BUT only if no OTHER criterion scored 0/2 — splitting an RFE that has a zero on what/why/open_to_how/not_a_task just produces more RFEs with the same unfixable problem. Recommend revise instead.
- reject: fundamentally infeasible or needs rethinking
Do NOT recommend split when capabilities are delivery-coupled.

Set `needs_attention=true` when the RFE needs human review despite its score — e.g., feasibility is indeterminate/infeasible, references non-existent components, or has concerns the rubric doesn't capture. When true, also set `needs_attention_reason` to a concise explanation (1-2 sentences) of what needs human attention. When false, set `needs_attention_reason=null`.
