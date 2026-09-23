---
name: rfe-review
description: Review and improve work items of any registered type — RFEs (RHAIRFE) and Initiatives (RHOAIENG). Accepts one or more Jira keys (e.g., /rfe-review RHAIRFE-1234, /rfe-review --type initiative RHOAIENG-12345) to fetch and review existing items, or reviews local artifacts from /rfe-create. Runs rubric scoring, the type's review dimensions (technical feasibility, strategic alignment) and auto-revises issues it finds.
user-invocable: true
allowed-tools: Glob, Bash, Agent, AskUserQuestion
---

You are a work-item review orchestrator. Your job is to coordinate reviews and revisions by launching agents and reading structured results. **Critical: never read file contents into your context — only read frontmatter via `scripts/frontmatter.py read` and check file existence via Glob.** All content-heavy work (reading item bodies, assessment results, writing review files, doing revisions) is delegated to agents.

## Review Step 0: Resolve the Type, Parse Arguments and Persist Flags

Parse `$ARGUMENTS` for flags and IDs:
- Strip `--type <t>` if present (an explicit type — always wins)
- Strip `--headless` flag if present (suppresses end-of-run summary)
- Strip `--caller <name>` flag if present (identifies calling skill for headless return)
- Remaining arguments are one or more space-separated item IDs (the type's Jira key form or local id form — `{ID_GRAMMAR}` once resolved)

Resolve the work-item type. Forward only `--type <t>` (when given), `--headless` (when given) and the explicit IDs — never any other flag:

```bash
python3 scripts/type_registry.py resolve [--type <t>] [--headless] <IDs>
```

It prints one line, `TYPE RESOLVED: <type> (<how>)`; a non-zero exit (unknown type, ambiguous ids) is an error — stop and report it. Then print the stage's launch block and keep it: every `{VAR}` in this skill is the value of that `VAR=` line, and every agent you launch gets the whole block prepended to its prompt:

```bash
python3 scripts/type_registry.py launch-vars <type> review
```

Persist parsed flags (survives context compression):

```bash
python3 scripts/state.py init tmp/{STATE_PREFIX}review-config.yaml type={TYPE} headless=<true/false> caller=<split|none>
```

Persist all IDs to disk (survives context compression):

```bash
python3 scripts/state.py write-ids tmp/{STATE_PREFIX}review-all-ids.txt <all_IDs>
```

For each ID, check if `{TASKS_DIR}/<id>.md` already exists locally (use Glob, don't read the file). Separate IDs into:
- **Local**: task file exists — skip fetch
- **Remote**: task file missing — needs Jira fetch

**Agent prompts.** Every agent prompt below has the shape the headless dispatcher uses: the launch block first, then the per-agent substitutions, then `Read <prompt_file> and follow all instructions exactly.` — `<launch block>\n\nSubstitute: {KEY}=<ID> …\n\nRead .claude/skills/rfe-review/prompts/<prompt>.md and follow all instructions exactly.`

## Review Step 1: Fetch Missing Items

For each remote ID, launch a **fetch agent** (model: opus, run_in_background: true):

```
<launch block>

Substitute {KEY} with <ID> throughout.

Read .claude/skills/rfe-review/prompts/fetch-agent.md and follow all instructions exactly.
```

Write IDs to poll file once, then poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py write-ids {POLL_FILE_PREFIX}fetch.txt <all_remote_IDs>
python3 scripts/check_review_progress.py --phase {POLL_PREFIX}fetch --id-file {POLL_FILE_PREFIX}fetch.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Only output a status line when COMPLETED count changes. If any agent runs longer than 5 minutes, check its status.

After all fetch agents complete, verify task files exist via Glob. For any missing, write an error to the review file:

```bash
python3 scripts/frontmatter.py set {REVIEWS_DIR}/<ID>-review.md {ID_FIELD}=<ID> score=0 pass=false recommendation=revise feasibility=feasible auto_revised=false needs_attention=true {SCORE_ZERO_SET} error="fetch_failed: task file not created" type={TYPE}
```

Remove failed IDs from the processing list and continue with remaining IDs.

## Review Step 1.5: Setup

Run these in parallel (two Bash calls):

```bash
bash scripts/fetch-architecture-context.sh
```

```bash
{BOOTSTRAP}
```

If architecture fetch fails, proceed without it. If bootstrap fails, note it — review agents will do basic quality checks instead.

## Review Step 2: Launch Assessment + Dimension Agents

For each ID being reviewed:

**Prepare assessment:**

```bash
python3 scripts/prep_assess.py <ID>
```

**Launch assess agent** (model: opus, run_in_background: true, subagent_type: {SCORER_AGENT}):

```
<launch block>

Substitute: {KEY}=<ID>, {DATA_FILE}={ASSESS_STAGING}/<ID>.md, {RUN_DIR}={ASSESS_STAGING}

Read .claude/skills/rfe-review/prompts/assess-agent.md and follow all instructions exactly.
```

(`{PROMPT_PATH}`, the rubric, comes from the launch block — the descriptor's context-relative `pipeline.rubric.path` under `{CONTEXT_DIR}`.)

**Launch one agent per declared dimension** (model: opus, run_in_background: true) — one per ID and dimension. The launch block lists the dimensions: `DIMENSIONS={DIMENSIONS}`, and for each `<NAME>` a `DIMENSION_<NAME>_PROMPT`, `_FILE`, `_BLOCKING` and `_CONDITION` line. Every dimension agent gets the same prompt, with that dimension's prompt file:

```
Read the file at <DIMENSION_<NAME>_PROMPT> and follow all instructions in it. The {ENTITY} ID to review is: <ID>
```

`DIMENSION_<NAME>_CONDITION` says when to launch it: `always` — for every ID; `<field> startswith <prefix>` — read the task frontmatter (`python3 scripts/frontmatter.py read {TASKS_DIR}/<ID>.md`) and launch only when that field's value starts with the prefix; `context_exists <path>` — launch only when that path exists in the working directory. When the condition does not hold, skip that dimension for this ID — the review rules say what to record for a dimension that was not assessed.

Launch all agents for all IDs in parallel (up to (1 + number of dimensions) × N agents for N IDs).

Write IDs to poll files once, then poll using `NEXT_POLL` interval — the assess poll, plus one poll file and one `--phase {POLL_PREFIX}<name>` per blocking dimension (`DIMENSION_<NAME>_BLOCKING=true`), exactly as for assess:

```bash
python3 scripts/state.py write-ids {POLL_FILE_PREFIX}assess.txt <all_IDs>
python3 scripts/state.py write-ids {POLL_FILE_PREFIX}<name>.txt <all_IDs>
python3 scripts/check_review_progress.py --phase {POLL_PREFIX}assess --id-file {POLL_FILE_PREFIX}assess.txt
python3 scripts/check_review_progress.py --phase {POLL_PREFIX}<name> --id-file {POLL_FILE_PREFIX}<name>.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Only output status when COMPLETED count changes. Wait for all to complete.

A non-blocking dimension (`DIMENSION_<NAME>_BLOCKING=false`) is polled the same way (`{POLL_FILE_PREFIX}<name>.txt`, `--phase {POLL_PREFIX}<name>`) but only for the IDs it was launched for — skip its poll block entirely when it was launched for none, since the checker exits 2 on an empty ID list. It is informational, not blocking: if it is still PENDING after 5 minutes, stop polling and continue. The prerequisite check below records the missing file without failing the ID.

After completion, check prerequisites for each ID via Glob:
- If assess result (`{ASSESS_STAGING}/<ID>.result.md`) is missing → write error: `assess_failed`
- If a blocking dimension's file (its `DIMENSION_<NAME>_FILE` line in the launch block) is missing → write error: `<name>_failed`
- If a non-blocking dimension's file is missing AND its agent was launched → note but do not treat as a blocking error

For any missing prerequisite:

```bash
python3 scripts/frontmatter.py set {REVIEWS_DIR}/<ID>-review.md {ID_FIELD}=<ID> score=0 pass=false recommendation=revise feasibility=feasible auto_revised=false needs_attention=true {SCORE_ZERO_SET} error="<assess_failed or feasibility_failed>: file not created" type={TYPE}
```

Remove failed IDs from the processing list and continue with remaining IDs.

## Review Step 3: Launch Review Agents

For each remaining ID, launch a **review agent** (model: opus, run_in_background: true):

```
<launch block>

Substitute: {ID}=<ID>, {ASSESS_PATH}={ASSESS_STAGING}/<ID>.result.md, {FIRST_PASS}=true, plus one {<NAME>_PATH}=<DIMENSION_<NAME>_FILE> line for every dimension in `DIMENSIONS={DIMENSIONS}`

Read .claude/skills/rfe-review/prompts/review-agent.md and follow all instructions exactly.
```

Launch all review agents in parallel.

Write IDs to poll file once, then poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py write-ids {POLL_FILE_PREFIX}review.txt <all_IDs>
python3 scripts/check_review_progress.py --phase {POLL_PREFIX}review --id-file {POLL_FILE_PREFIX}review.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Wait for all to complete. For any ID where the review file is missing or has no frontmatter, write error: `review_failed` (same stub command as above).

## Review Step 3.5: Launch Revise Agents

After all review agents complete, re-read the ID list from disk (context compression may have corrupted in-memory lists):

```bash
python3 scripts/state.py read-ids tmp/{STATE_PREFIX}review-all-ids.txt
```

Determine which IDs need revision:

```bash
python3 scripts/filter_for_revision.py <all_IDs_from_file>
```

The script outputs the IDs that need revision (filters out passing, infeasible, and rejected IDs). If the output is empty, skip to Review Step 4.

Launch a **revise agent** (model: opus, run_in_background: true) for each ID returned:

```
<launch block>

Substitute: {ID}=<ID>

Read .claude/skills/rfe-review/prompts/revise-agent.md and follow all instructions exactly.
```

Launch all revise agents in parallel.

Write IDs to poll file once, then poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py write-ids {POLL_FILE_PREFIX}revise.txt <all_IDs_being_revised>
python3 scripts/check_review_progress.py --phase {POLL_PREFIX}revise --id-file {POLL_FILE_PREFIX}revise.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Wait for all to complete.

**Post-processing: fix auto_revised flag.** The revise agent may run out of budget before setting `auto_revised=true`, or set it after changing nothing (the flag is its completion marker). After all agents complete, run the batch check which compares originals to task files and sets the flag directly in review frontmatter:

```bash
python3 scripts/check_revised.py --batch {TYPE_FLAG} --ids-file {POLL_FILE_PREFIX}revise.txt
```

## Review Step 4: Re-assess if Revised (max 2 cycles)

Re-read ID list from disk:

```bash
python3 scripts/state.py read-ids tmp/{STATE_PREFIX}review-all-ids.txt
```

After all revise agents complete, check which IDs need re-assessment:

```bash
python3 scripts/collect_recommendations.py --reassess {TYPE_FLAG} --ids-file tmp/{STATE_PREFIX}review-all-ids.txt
```

Parse output for `REASSESS=` line. For each ID needing re-assessment (auto_revised=true, pass=false), initialize the cycle counter on disk (set-default is safe if compression causes re-entry — it won't reset an existing counter):

```bash
python3 scripts/state.py set-default tmp/{STATE_PREFIX}review-config.yaml reassess_cycle=0
```

Before starting a cycle, re-read the cycle counter to guard against context compression:

```bash
python3 scripts/state.py read tmp/{STATE_PREFIX}review-config.yaml
```

If `reassess_cycle` already shows 2 or higher, stop — max cycles reached. Otherwise, increment after each cycle:

```bash
python3 scripts/state.py set tmp/{STATE_PREFIX}review-config.yaml reassess_cycle=<N+1>
```

For cycle 1:

Persist reassess IDs to disk (needed across 4a–4e, may be lost to compression during agents):

```bash
python3 scripts/state.py write-ids tmp/{STATE_PREFIX}review-reassess-ids.txt <all_reassess_IDs>
```

**4a. Save cumulative state and remove review files** so progress detection works:

```bash
python3 scripts/preserve_review_state.py save <all_reassess_IDs>
rm {REVIEWS_DIR}/<ID>-review.md  # for each reassess ID
rm {ASSESS_STAGING}/<ID>.result.md  # for each reassess ID
```

**4b. Re-run assessment.** For each reassess ID, prepare and launch an assess agent — this is the same process as Review Step 2:

```bash
python3 scripts/prep_assess.py <ID>
```

Launch an **assess agent** (model: opus, run_in_background: true, subagent_type: {SCORER_AGENT}) for each reassess ID:

```
<launch block>

Substitute: {KEY}=<ID>, {DATA_FILE}={ASSESS_STAGING}/<ID>.md, {RUN_DIR}={ASSESS_STAGING}

Read .claude/skills/rfe-review/prompts/assess-agent.md and follow all instructions exactly.
```

Launch all assess agents in parallel.

Re-read reassess IDs from disk, write poll file, and poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py copy-ids tmp/{STATE_PREFIX}review-reassess-ids.txt {POLL_FILE_PREFIX}reassess-assess.txt
python3 scripts/check_review_progress.py --phase {POLL_PREFIX}assess --id-file {POLL_FILE_PREFIX}reassess-assess.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Wait for all to complete.

**4c. Launch review agents.** Re-read reassess IDs from disk:

```bash
python3 scripts/state.py read-ids tmp/{STATE_PREFIX}review-reassess-ids.txt
```

For each reassess ID, launch a **review agent** (model: opus, run_in_background: true):

```
<launch block>

Substitute: {ID}=<ID>, {ASSESS_PATH}={ASSESS_STAGING}/<ID>.result.md, {FIRST_PASS}=false, plus one {<NAME>_PATH}=<DIMENSION_<NAME>_FILE> line for every dimension in `DIMENSIONS={DIMENSIONS}`

Read .claude/skills/rfe-review/prompts/review-agent.md and follow all instructions exactly.
```

Launch all review agents in parallel.

Re-read reassess IDs from disk, write poll file, and poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py copy-ids tmp/{STATE_PREFIX}review-reassess-ids.txt {POLL_FILE_PREFIX}reassess-review.txt
python3 scripts/check_review_progress.py --phase {POLL_PREFIX}review --id-file {POLL_FILE_PREFIX}reassess-review.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Wait for all to complete (review files were removed in 4a, so progress detection works).

**4d. Restore before_scores and revision history.** Re-read reassess IDs from disk:

```bash
python3 scripts/state.py read-ids tmp/{STATE_PREFIX}review-reassess-ids.txt
```

```bash
python3 scripts/preserve_review_state.py restore <all_reassess_IDs_from_file>
```

**4e. Filter for revision** (also catches score regressions and sets autorevise_reject):

```bash
python3 scripts/filter_for_revision.py <all_reassess_IDs_from_file>
```

Launch revise agents for the IDs returned (if any). Wait for all to complete, then run the batch auto_revised flag fix:

```bash
python3 scripts/check_revised.py --batch {TYPE_FLAG} --ids-file tmp/{STATE_PREFIX}review-reassess-ids.txt
```

After cycle 2, stop regardless of results.

## Review Step 5: Finalize

If `INDEX_ENABLED={INDEX_ENABLED}` is true, rebuild the index once:

```bash
python3 scripts/frontmatter.py rebuild-index
```

Re-read flags (in case context was compressed):

```bash
python3 scripts/state.py read tmp/{STATE_PREFIX}review-config.yaml
```

**If `headless: true`**: Output the text "rfe-review step completed." then run each of these as its own Bash call — never chain them with `;` (chained commands are denied in headless mode):

```bash
python3 scripts/state.py read tmp/{STATE_PREFIX}review-config.yaml
```

```bash
python3 scripts/state.py read tmp/{STATE_PREFIX}split-config.yaml
```

A "State file not found" error just means that caller's config does not exist — continue with what was found.

Check the `caller` field above:
- **`split`**: Returning to **Split Step 3: Right-sizing Self-Correction** of `/rfe-split`. Re-read parent IDs from `tmp/{STATE_PREFIX}split-all-ids.txt`. If the split config is not visible, re-read `/rfe-split` SKILL.md for the full flow. Do not summarize or stop.
- **`none`** (a direct `/rfe-review --headless {TYPE_FLAG} <IDs>` with no calling skill): nothing to return to — stop here; the completion text above was the announcement.

**If interactive (no `--headless`)**: Re-read ID list and present summary:

```bash
python3 scripts/batch_summary.py {TYPE_FLAG} --ids-file tmp/{STATE_PREFIX}review-all-ids.txt
```

Based on the output:
- **All pass**: Tell the user {ENTITY_PLURAL} are ready for `/rfe-submit --type {TYPE}`.
- **Some need revision**: List the remaining issues (from summary output). Tell the user to edit artifacts and re-run `/rfe-review --type {TYPE}`.
- **Some recommend split**: Tell the user to run `/rfe-split --type {TYPE} <ID>` for those IDs.
- **Errors**: Report which IDs had errors and suggest retrying.

$ARGUMENTS
