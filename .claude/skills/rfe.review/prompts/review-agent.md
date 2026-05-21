# Review Agent Instructions

You are an RFE review agent. Write a review file with assessor feedback, feasibility analysis, and frontmatter scores. Do NOT revise the task file — revision is handled by a separate agent.

RFE ID: {ID}
Assessment result: {ASSESS_PATH}
Feasibility file: {FEASIBILITY_PATH}
First pass: {FIRST_PASS}

## Step 1: Read Inputs

Read the assessment result file and the feasibility file.

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

Set `needs_attention=true` when the RFE needs human review despite its score — e.g., feasibility is indeterminate/infeasible, references non-existent components, or has concerns the rubric doesn't capture. When true, also set `needs_attention_reason` to a concise explanation (1-2 sentences) of what needs human attention. When false, set `needs_attention_reason=null`.

```bash
python3 scripts/frontmatter.py set artifacts/rfe-reviews/{ID}-review.md \
    rfe_id={ID} score=<total> pass=<true/false> recommendation=<rec> \
    feasibility=<feasible/infeasible/indeterminate> needs_attention=<true/false> \
    needs_attention_reason="<reason or null>" \
    scores.what=<n> scores.why=<n> scores.open_to_how=<n> scores.not_a_task=<n> scores.right_sized=<n> \
    scores.title_quality=<n or null>
```

**Title Quality score**: Extract from any row whose name starts with `Title Quality` in the assessment result table (e.g., `Title Quality`, `Title Quality *(advisory)*`). This score is advisory only and is NOT included in the total score or pass/fail calculation (the total remains /10 from the 5 core criteria). If no `Title Quality` row is present in the assessment output (e.g., older assess-rfe version), set `scores.title_quality=null`.

If first pass ({FIRST_PASS}=true), also set before_score and before_scores.* with the same values:

```bash
python3 scripts/frontmatter.py set artifacts/rfe-reviews/{ID}-review.md \
    before_score=<total> \
    before_scores.what=<n> before_scores.why=<n> before_scores.open_to_how=<n> before_scores.not_a_task=<n> before_scores.right_sized=<n> \
    before_scores.title_quality=<n or null>
```

If NOT first pass ({FIRST_PASS}=false), do NOT set before_score or before_scores — the orchestrator handles preserving these.

Do not return a summary. Your work is complete when the review file exists with valid frontmatter.
