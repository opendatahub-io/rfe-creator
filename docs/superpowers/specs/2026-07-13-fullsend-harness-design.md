# Fullsend Harness for RFE Creator

Date: 2026-07-13

## Problem

The rfe.speedrun skill works well interactively but has no fullsend integration.
We want `fullsend run rfe-creator` to process a Jira issue end-to-end: fetch,
review, revise, and submit. The skill itself must remain usable outside fullsend
with no code changes.

## Design Principles

1. **Skill stays clean.** No fullsend awareness in skill code. The agent
   definition is the only adapter layer.
2. **No Jira from the sandbox.** The LLM never contacts Jira. Pre-script
   fetches, post-script submits. Jira credentials exist only on the runner.
3. **Artifacts are the contract.** No intermediate JSON schema between agent
   and post-script. The artifact files (task frontmatter, review frontmatter)
   are the structured representation. Existing scripts (`collect_recommendations.py`,
   `submit.py`) already read them.
4. **Deterministic Jira writes.** `submit.py` and `split_submit.py` handle all
   Jira API calls, consistent with the existing project convention that write
   operations are not LLM-dependent.

## Invocation

```bash
# Manual PoC
FULLSEND_WORK_ITEM_URL=https://issues.redhat.com/browse/PROJ-1234 \
  fullsend run rfe-creator
```

`FULLSEND_WORK_ITEM_URL` is the canonical input per ADR 63 (polling-based work
discovery). The polling dispatch model and Jira webhook trigger will provide
this variable automatically once they land. For now, manual invocation suffices.

## File Layout

```
.fullsend/
  agents/
    rfe-creator.md              # Agent definition
  harnesses/
    rfe-creator.yaml            # Harness config
  scripts/
    pre-fetch.sh                # Fetch issue from Jira into artifacts/
    post-submit.sh              # Read recommendations, call submit.py
    validate-artifacts.sh       # Check frontmatter, project IDs, recommendations
```

No local policy file. The harness references the base policy from
`fullsend-ai/agents`.

## Harness Config

File: `.fullsend/harnesses/rfe-creator.yaml`

```yaml
agent: agents/rfe-creator.md
model: opus
policy: fullsend-ai/agents:policies/base.yaml
timeout_minutes: 30

skills:
  - .claude/skills/rfe.speedrun
  - .claude/skills/rfe.create
  - .claude/skills/rfe.review
  - .claude/skills/rfe.auto-fix
  - .claude/skills/rfe.split
  - .claude/skills/rfe.submit

pre_script: .fullsend/scripts/pre-fetch.sh
post_script: .fullsend/scripts/post-submit.sh

validation_loop:
  script: .fullsend/scripts/validate-artifacts.sh
  max_iterations: 1

env:
  runner:
    FULLSEND_WORK_ITEM_URL: "${FULLSEND_WORK_ITEM_URL}"
    JIRA_SERVER: "${JIRA_SERVER}"
    JIRA_USER: "${JIRA_USER}"
    JIRA_TOKEN: "${JIRA_TOKEN}"
    JIRA_PROJECT: "${JIRA_PROJECT}"
  sandbox:
    FULLSEND_WORK_ITEM_URL: "${FULLSEND_WORK_ITEM_URL}"
```

Key decisions:

- **Jira credentials in `runner` only.** The sandbox has no way to contact Jira.
- **`FULLSEND_WORK_ITEM_URL` in both.** The sandbox gets it for context (the
  agent knows what issue it is working on) but does not use it for network
  access.
- **`JIRA_PROJECT` in `runner`.** Used by the validation script to verify
  artifact IDs belong to the expected project.
- **`max_iterations: 1` on validation.** Malformed artifacts are a hard stop,
  not a retry. Retrying the agent on bad output is unlikely to help.

## Agent Definition

File: `.fullsend/agents/rfe-creator.md`

```markdown
# RFE Creator Agent

You are an RFE review and improvement agent. Your workspace contains
a pre-fetched RFE in `artifacts/rfe-tasks/`. Your job is to review,
score, and improve it.

## Instructions

1. Identify the RFE file in `artifacts/rfe-tasks/`
2. Run `/rfe.speedrun --headless --dry-run <ISSUE_KEY>` where ISSUE_KEY
   is the `rfe_id` from the file's frontmatter
3. Do not contact Jira. All artifacts are local. Jira submission is
   handled externally after you complete.
4. When the skill completes, your work is done. The artifacts you
   produced will be validated and submitted by external tooling.
```

The `--dry-run` flag ensures the skill skips Phase 3 (submit) even though there
are no Jira credentials in the sandbox. Belt and suspenders.

## Pre-Script

File: `.fullsend/scripts/pre-fetch.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

# Extract issue key from URL
# e.g., https://issues.redhat.com/browse/PROJ-1234 -> PROJ-1234
ISSUE_KEY="${FULLSEND_WORK_ITEM_URL##*/}"

# Fetch issue and write all artifact files (task, original, comments)
python3 scripts/fetch_issue.py "$ISSUE_KEY" --fetch-all artifacts
```

`fetch_issue.py --fetch-all` already creates:

- `artifacts/rfe-tasks/PROJ-1234.md` with YAML frontmatter
- `artifacts/rfe-originals/PROJ-1234.md` as baseline for diff
- `artifacts/rfe-tasks/PROJ-1234-comments.md` with stakeholder history

The agent wakes up to a workspace that looks like someone fetched the issue
manually. No special fullsend awareness needed in the skill.

## Validation Script

File: `.fullsend/scripts/validate-artifacts.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

errors=0

# Validate all task files
for f in artifacts/rfe-tasks/*.md; do
    [[ "$(basename "$f")" == *-comments.md ]] && continue
    python3 scripts/frontmatter.py read "$f" > /dev/null || errors=$((errors + 1))
done

# Validate all review files
for f in artifacts/rfe-reviews/*-review.md; do
    python3 scripts/frontmatter.py read "$f" > /dev/null || errors=$((errors + 1))
done

# Verify all rfe_ids belong to expected project or are DRAFT-NNN (split children)
for f in artifacts/rfe-tasks/*.md; do
    [[ "$(basename "$f")" == *-comments.md ]] && continue
    rfe_id=$(python3 scripts/frontmatter.py read "$f" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['rfe_id'])")
    case "$rfe_id" in
        DRAFT-*) ;;                    # Split children, pre-submission
        ${JIRA_PROJECT}-*) ;;          # Original issue or existing keys
        *) echo "ERROR: unexpected project in rfe_id: $rfe_id"
           errors=$((errors + 1)) ;;
    esac
done

# Collect recommendations (exits non-zero if review files missing/broken)
python3 scripts/collect_recommendations.py --from-reviews || errors=$((errors + 1))

exit $errors
```

Validates:

- All task files have valid frontmatter (required fields, value ranges)
- All review files have valid frontmatter (scores, recommendations)
- Every `rfe_id` is either `DRAFT-NNN` (split children) or `${JIRA_PROJECT}-*`
  (the original issue). Anything else fails.
- Recommendations are parseable

## Post-Script

File: `.fullsend/scripts/post-submit.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

# Get recommendations — outputs lines like SUBMIT=PROJ-1234,DRAFT-001
eval "$(python3 scripts/collect_recommendations.py --from-reviews)"

if [[ -z "${SUBMIT:-}" ]]; then
    echo "No RFEs recommended for submission. Done."
    exit 0
fi

# Submit all passing RFEs
python3 scripts/submit.py --ids "$SUBMIT" --artifacts-dir artifacts
```

`submit.py` handles the full complexity: updates existing issues, creates
new tickets for split children, handles the split workflow (archive parent,
create children, link, close parent) via `split_submit.py`. The post-script
just passes IDs through.

A `REJECT` recommendation for everything is a valid outcome (the agent
reviewed the RFE and decided it is fine as-is or infeasible). The post-script
exits cleanly with no Jira writes.

## Data Flow

```
pre-fetch.sh                sandbox (no Jira network)         post-submit.sh
────────────                ─────────────────────────         ──────────────
                            Agent reads artifacts/
fetch_issue.py    ────►     /rfe.speedrun --headless
  writes:                     --dry-run PROJ-1234
  rfe-tasks/                                                  validate-artifacts.sh
  rfe-originals/            Skill writes:                       checks frontmatter
  comments                    rfe-tasks/ (revised)              checks project IDs
                              rfe-reviews/                      checks recommendations
                              rfe-originals/              ─►
                              auto-fix-runs/              ─►  collect_recommendations.py
                                                              submit.py
                                                                creates/updates in Jira
```

## Splits

The skill can split an oversized RFE into multiple smaller ones during Phase 2
(auto-fix). When this happens:

- The original `PROJ-1234.md` gets `status: Archived` in frontmatter
- New `DRAFT-001.md`, `DRAFT-002.md`, etc. are created as children
- Corresponding review files are created for each child

The validation script handles this by validating all files, not just the
original issue key. The post-script handles it because `submit.py` already
understands splits: it detects archived parents, calls `split_submit.py` for
the transactional workflow, and creates/links children in Jira.

## What Does Not Change

- No modifications to any skill (`rfe.speedrun`, `rfe.create`, `rfe.review`,
  `rfe.auto-fix`, `rfe.split`, `rfe.submit`)
- No modifications to existing scripts except the one noted below
- No new JSON schemas or intermediate formats
- The skill remains fully usable outside fullsend via interactive or
  headless invocation

## One Existing Script Change

`collect_recommendations.py` currently takes explicit IDs as arguments. The
validation and post scripts need it to discover IDs from whatever review files
exist. This requires adding a `--from-reviews` flag that globs
`artifacts/rfe-reviews/*-review.md` and extracts IDs from filenames.

## Future: Eval Integration

Not in scope for the PoC. When ready, a second harness
(`rfe-creator-eval.yaml`) can use `base: harnesses/rfe-creator.yaml` and
override the pre/post scripts to assemble `batch.yaml` from the eval dataset
and run the judge suite against the artifacts. The agent definition handles
this naturally — if `batch.yaml` exists, the skill runs in batch mode.
