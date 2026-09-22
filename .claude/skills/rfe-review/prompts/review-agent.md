# Review Agent Instructions

You are an {ENTITY} review agent. Write a review file with assessor feedback, feasibility analysis, and frontmatter scores (plus the analysis of every other dimension this type declares). Do NOT revise the task file — revision is handled by a separate agent.

{ENTITY} ID: {ID}
Assessment result: {ASSESS_PATH}
Feasibility file: {FEASIBILITY_PATH}
Dimension files: {DIMENSION_FILES}
First pass: {FIRST_PASS}
Review rules: {RULES_PATH}
Body sections: {SECTIONS_PATH}

## Step 1: Read Inputs

Read the assessment result file and the feasibility file.
Read the assessment result file at `{ASSESS_PATH}`.
Read the feasibility file at `{FEASIBILITY_PATH}`.
Read every other dimension file listed above if it exists — a non-blocking dimension's file may be missing when that dimension was not assessed (its condition did not hold, or its agent did not complete); the review rules say what to record then.
Read the review rules at `{RULES_PATH}` and the body sections at `{SECTIONS_PATH}`.

## Step 2: Read Schema

```bash
python3 scripts/frontmatter.py schema {REVIEW_SCHEMA}
```

## Step 3: Write Review File

Write `{REVIEWS_DIR}/{ID}-review.md` with the body structure below — just
the body, no `---` frontmatter block. Step 4 creates the frontmatter. Writing the
body only avoids corruption entirely: a hand-written block breaks the moment a
value contains a colon. (`frontmatter.py set` can recover a corrupted block, but
don't rely on it.)

The body structure is the section list in `{SECTIONS_PATH}` (read in Step 1), in that order, ending with `## Revision History` ("none" on first pass).

## Step 4: Set Frontmatter

Parse the score table from the assessment result file. Determine the recommendation (submit / revise / split / reject) and `needs_attention` by the rules in `{RULES_PATH}`.

Type rules declared by the descriptor (always apply): {EXTRA_RULES}

```bash
python3 scripts/frontmatter.py set {REVIEWS_DIR}/{ID}-review.md \
    {ID_FIELD}={ID} score=<total> pass=<true/false> recommendation=<submit/revise/split/reject> \
    feasibility=<feasible/infeasible/indeterminate> needs_attention=<true/false> \
    needs_attention_reason="<reason or null>" \
    {SCORE_SET}{REVIEW_EXTRA_SET}
```

If first pass ({FIRST_PASS}=true), also set before_score and before_scores.* with the same values:

```bash
python3 scripts/frontmatter.py set {REVIEWS_DIR}/{ID}-review.md \
    before_score=<total> \
    {BEFORE_SCORE_SET}
```

If NOT first pass ({FIRST_PASS}=false), do NOT set before_score or before_scores — the orchestrator handles preserving these.

Do not return a summary. Your work is complete when the review file exists with valid frontmatter.
