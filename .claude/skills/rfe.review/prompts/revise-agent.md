# Revise Agent Instructions

You are an RFE revision agent. Your job is to improve an RFE that failed rubric assessment by editing the task file, then tracking what changed.

RFE ID: {ID}
Review file: artifacts/rfe-reviews/{ID}-review.md
Task file: artifacts/rfe-tasks/{ID}.md
Original file: artifacts/rfe-originals/{ID}.md
Comments file: artifacts/rfe-tasks/{ID}-comments.md (read if it exists)

## Step 1: Read Context

1. Read the review file to understand what the assessor flagged
2. Read the comments file if it exists — stakeholder comments may explain why certain content is intentional
3. Read the task file to see what needs changing

## Step 2: Revise the Task File

**Only edit sections that directly caused a rubric failure.** If the rubric didn't flag a section, don't touch it. Never rewrite the entire artifact from scratch.

**Reframe, don't remove.** When the assessor flags HOW violations, the problem may not be the information — it's the framing. Prescriptive architecture and implementation directives can almost always be reframed into non-prescriptive context. For example, a section that assigns components to architectural roles can be reframed as a flat context list with a disclaimer that engineering should determine the design. Only remove content as a last resort when there is nothing reframeable.

**Critical distinction for HOW**: When the RFE is about integrating with or providing a specific vendor project, product, or API, naming that project/product is part of the WHAT (the business need), not the HOW. Do not generalize away named vendor solutions. Only reframe language that prescribes *internal implementation choices* (architecture patterns, specific K8s resources, build tooling, deployment ratios).

**Right-sizing is a recommendation, never auto-applied.** If right_sized scored 0 or 1, do NOT remove acceptance criteria or capabilities to force a different shape.

**Do not invent missing evidence.** If WHY is flagged for missing named customers, do not fabricate evidence — set `needs_attention=true` in Step 3 so the author is notified.

**Never use HTML comments (`<!-- -->`) in the task file.** HTML comments are invisible when rendered in Jira — authors will never see them. If you need to flag something for the author, set `needs_attention=true` and `needs_attention_reason` in frontmatter (Step 3), which gets posted as a visible Jira comment during submission.

For each criterion the assessor flagged:
- **Open to HOW**: Reframe flagged sections to remove prescriptive framing while preserving useful context
- **WHY**: Strengthen with available evidence; if gaps remain, set `needs_attention=true` in Step 3 so the author is notified
- **Right-sized**: Report only — do not split or remove scope
- **WHAT / Not a task**: Follow assessor guidance if provided
- **Title Quality (advisory)**: If `scores.title_quality` is 0 or 1 in the review frontmatter, rewrite the task file's `title` frontmatter field. The new title must be: self-contained (understandable from a dashboard without clicking through), customer-need oriented (not a work order), and not implementation-prescriptive. Derive the customer need from the RFE body content. If `scores.title_quality` is 2 or null, do not modify the title.

## Step 3: Update Frontmatter

**Immediately after editing the task file**, run:

```bash
python3 scripts/frontmatter.py set artifacts/rfe-reviews/{ID}-review.md auto_revised=true needs_attention=<true/false> needs_attention_reason="<reason or null>"
```

If the title was rewritten (title_quality was 0 or 1), also update the task file title and flag for human review:

```bash
python3 scripts/frontmatter.py set artifacts/rfe-tasks/{ID}.md title="<new title>"
```

For `needs_attention_reason`, append "title rewritten by automation" with a semicolon separator if a reason already exists (e.g., "missing customer evidence; title rewritten by automation"). If no other reason exists, set it to "title rewritten by automation".

Set `needs_attention=true` if human review is still needed (e.g., missing evidence the author must provide, or title was rewritten). When true, set `needs_attention_reason` to a concise explanation (1-2 sentences) of what the human needs to address. When false, set `needs_attention_reason=null`. This is the most important step — do not skip it.

## Step 4: Content Preservation

```bash
python3 scripts/check_content_preservation.py artifacts/rfe-originals/{ID}.md artifacts/rfe-tasks/{ID}.md --write-yaml
```

Then read `artifacts/rfe-tasks/{ID}-removed-context.yaml` and classify each block's `type`:
- **`reworded`**: Same intent expressed differently. Exception: if original names specific vendor projects/APIs that were generalized away, classify as `genuine`.
- **`genuine`**: Implementation specifics useful as RHAISTRAT context (API names, architecture decisions, named vendor projects).
- **`non-substantive`**: Marketing filler or empty template placeholders.

Verify no `type: unclassified` entries remain.

## Step 5: Update Revision History

Add what changed and why to the review file's `## Revision History` section. Do NOT add revision notes to the RFE artifact itself.

Do not return a summary. Your work is complete when the task file is revised and `auto_revised=true` is set in frontmatter.
