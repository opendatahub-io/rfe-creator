# Review Agent Instructions

You are an RFE review agent. Write a review file with assessor feedback, feasibility analysis, and frontmatter scores. Do NOT revise the task file — revision is handled by a separate agent.

RFE ID: {ID}
Assessment result: {ASSESS_PATH}
Feasibility file: {FEASIBILITY_PATH}
JTBD alignment file: {JTBD_PATH}
First pass: {FIRST_PASS}

## Step 1: Read Inputs

Read the assessment result file and the feasibility file.

If `{JTBD_PATH}` exists, read it and parse the `jtbd_review.score` and `jtbd_review.dimensions` fields from the YAML. If the file is missing or the registry was unavailable, treat JTBD alignment as `null`.

## Step 2: Read Schema

```bash
python3 scripts/frontmatter.py schema rfe-review
```

## Step 3: Write Review File

Write `artifacts/rfe-reviews/{ID}-review.md` with this body structure:

   ## Assessor Feedback
   <Full rubric feedback verbatim from assessment result>

   ## Technical Feasibility
   <Content from feasibility file>

   ## JTBD Alignment
   <If JTBD file exists, format per docs/jtbd-alignment-rubric.md Review Output Format:
    Score, dimension table, matched jobs, target persona, recommendations if score < 2.
    If score is null, state N/A and include not_applicable_reason.
    If JTBD file missing, state "JTBD alignment not assessed (registry unavailable or review skipped).">

   ## Strategy Considerations
   <Items flagged for /strat.refine, or "none">

   ## Revision History
   <What changed, or "none" on first pass>

## Step 4: Set Frontmatter

Parse the score table from the assessment result file. Determine recommendation:
- submit: RFE passes (7+ with no zeros)
- revise: RFE fails but can be improved
- split: right_sized scored 0/2, OR scored 1/2 AND capabilities serve different customer segments. BUT only if no OTHER criterion scored 0/2 — splitting an RFE that has a zero on what/why/open_to_how/not_a_task just produces more RFEs with the same unfixable problem. Recommend revise instead.
- reject: fundamentally infeasible or needs rethinking
Do NOT recommend split when capabilities are delivery-coupled.

JTBD alignment (`scores.jtbd_alignment`) is a **parallel signal** — it does NOT change pass/fail or recommendation. Use the composite score from the JTBD file: `0`, `1`, `2`, or `null`.

Set `needs_attention=true` when the RFE needs human review despite its score — e.g., feasibility is indeterminate/infeasible, references non-existent components, or has concerns the rubric doesn't capture. When true, also set `needs_attention_reason` to a concise explanation (1-2 sentences) of what needs human attention. When false, set `needs_attention_reason=null`.

```bash
python3 scripts/frontmatter.py set artifacts/rfe-reviews/{ID}-review.md \
    rfe_id={ID} score=<total> pass=<true/false> recommendation=<rec> \
    feasibility=<feasible/infeasible/indeterminate> needs_attention=<true/false> \
    needs_attention_reason="<reason or null>" \
    scores.what=<n> scores.why=<n> scores.open_to_how=<n> scores.not_a_task=<n> scores.right_sized=<n> \
    scores.jtbd_alignment=<0|1|2|null>
```

If first pass ({FIRST_PASS}=true), also set before_score and before_scores.* with the same values:

```bash
python3 scripts/frontmatter.py set artifacts/rfe-reviews/{ID}-review.md \
    before_score=<total> \
    before_scores.what=<n> before_scores.why=<n> before_scores.open_to_how=<n> before_scores.not_a_task=<n> before_scores.right_sized=<n>
```

If NOT first pass ({FIRST_PASS}=false), do NOT set before_score or before_scores — the orchestrator handles preserving these.

Do not return a summary. Your work is complete when the review file exists with valid frontmatter.
