# Initiative Revise Agent Instructions

Auto-revise initiative {ID} to address review findings.

## Step 1: Read Context

1. Read the initiative: `artifacts/initiatives/{ID}.md`
2. Read the review: `artifacts/initiative-reviews/{ID}-review.md`
3. Read the original: `artifacts/initiative-originals/{ID}.md`

## Step 2: Revise the Task File

**Only edit sections that directly caused a rubric failure.** Do not rewrite sections that passed review.

- **WHAT**: Make the objective specific and concrete. Replace vague language with clear outcomes. A reader should understand exactly what will be delivered.
- **WHY**: Add evidence — metrics, incidents, cost data, competitive gaps. If you don't have specifics, flag the section with `[NEEDS: specific metrics/evidence]` for human follow-up.
- **Scope**: Clarify boundaries. If scope is fuzzy, add explicit inclusions and exclusions. Prose is fine — formal In/Out sections are optional.
- **Open to HOW**: When implementation details cross from describing deliverables into mandating architecture, reframe prescriptive content into suggestive context (see Implementation Detail Boundaries below).
- **Right-sized**: If the initiative bundles independent workstreams, flag for split recommendation rather than attempting to narrow scope in-place.

### Implementation Detail Boundaries

**Reframe, don't remove.** When implementation details cross from What into How, the problem is usually the framing, not the information. Reframe prescriptive architecture into suggestive context rather than deleting it. Only remove content as a last resort.

**What's acceptable:**
- Technologies dictated by integration context (e.g. "must support vLLM" when vLLM is the existing runtime)
- Technologies listed as suggestions or illustrations (e.g. "could use Redis or similar caching layer")
- Technical detail that describes the deliverable itself (observable behavior, APIs, interfaces)

**What crosses the line:**
- Prescribing technology choices as decisions when alternatives exist (e.g. "implement using Redis" when the choice is open)
- Mandating internal architecture, repo structure, or algorithmic design
- Linking design docs as "the solution"

When reframing, add suggestive language ("such as", "could leverage", "one approach would be") rather than removing the technology reference entirely.

**Do not invent missing evidence.** If Problem Statement is flagged for missing data, do not fabricate evidence — set `needs_attention=true` in Step 3 so the author is notified.

**Never use HTML comments (`<!-- -->`) in the task file.** HTML comments are invisible when rendered in Jira — authors will never see them. If you need to flag something for the author, set `needs_attention=true` and `needs_attention_reason` in frontmatter (Step 3), which gets posted as a visible Jira comment during submission.

## Step 3: Update Frontmatter

**Immediately after editing the task file**, run:

```bash
python3 scripts/frontmatter.py set artifacts/initiative-reviews/{ID}-review.md auto_revised=true needs_attention=<true/false> needs_attention_reason="<reason or null>"
```

Set `needs_attention=true` if human review is still needed (e.g., missing evidence the author must provide). When true, set `needs_attention_reason` to a concise explanation (1-2 sentences) of what the human needs to address. When false, set `needs_attention_reason=null`. This is the most important step — do not skip it, and do not defer it until after Step 4.

## Step 4: Content Preservation

```bash
python3 scripts/check_content_preservation.py artifacts/initiative-originals/{ID}.md artifacts/initiatives/{ID}.md --write-yaml
```

If the file `artifacts/initiatives/{ID}-removed-context.yaml` exists after this, read it and classify each block's `type`:
- **`reworded`**: Same intent expressed differently. Exception: if original names specific technologies that were generalized away, classify as `genuine`.
- **`genuine`**: Implementation specifics useful as strategy context (architecture decisions, technology choices, design rationale).
- **`non-substantive`**: Marketing filler or empty template placeholders.

Verify no `type: unclassified` entries remain.

## Step 5: Update Revision History

Add what changed and why to the review file's `## Revision History` section. Do NOT add revision notes to the initiative artifact itself.

Do not return a summary. Your work is complete when the task file is revised and `auto_revised=true` is set in frontmatter.
