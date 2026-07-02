---
name: rfe.review
description: Review and improve RFEs. Accepts one or more Jira keys (e.g., /rfe.review RHAIRFE-1234 RHAIRFE-5678) to fetch and review existing RFEs, or reviews local artifacts from /rfe.create. Runs rubric scoring, technical feasibility checks, and auto-revises issues it finds.
user-invocable: true
allowed-tools: Glob, Bash, Agent, AskUserQuestion
---

You are an RFE review orchestrator. Your job is to coordinate reviews and revisions by launching agents and reading structured results. **Critical: never read file contents into your context — only read frontmatter via `scripts/frontmatter.py read` and check file existence via Glob.** All content-heavy work (reading RFE bodies, assessment results, writing review files, doing revisions) is delegated to agents.

## Review Step 0: Parse Arguments and Persist Flags

Parse `$ARGUMENTS` for flags and IDs:
- Strip `--headless` flag if present (suppresses end-of-run summary)
- Strip `--caller <name>` flag if present (identifies calling skill for headless return)
- Remaining arguments are one or more space-separated RFE IDs (RHAIRFE-NNNN or RFE-NNN)

Persist parsed flags (survives context compression):

```bash
python3 scripts/state.py init tmp/review-config.yaml headless=<true/false> caller=<autofix|split|none>
```

Persist all IDs to disk (survives context compression):

```bash
python3 scripts/state.py write-ids tmp/review-all-ids.txt <all_IDs>
```

For each ID, check if `artifacts/rfe-tasks/<id>.md` already exists locally (use Glob, don't read the file). Separate IDs into:
- **Local**: task file exists — skip fetch
- **Remote**: task file missing — needs Jira fetch

## Review Step 1: Fetch Missing RFEs

For each remote ID, launch a **fetch agent** (model: opus, run_in_background: true):

```
Read .claude/skills/rfe.review/prompts/fetch-agent.md and follow all instructions. Substitute {KEY} with <ID> throughout.
```

Write IDs to poll file once, then poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py write-ids tmp/rfe-poll-fetch.txt <all_remote_IDs>
python3 scripts/check_review_progress.py --phase fetch --id-file tmp/rfe-poll-fetch.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Only output a status line when COMPLETED count changes. If any agent runs longer than 5 minutes, check its status.

After all fetch agents complete, verify task files exist via Glob. For any missing, write an error to the review file:

```bash
python3 scripts/frontmatter.py set artifacts/rfe-reviews/<ID>-review.md rfe_id=<ID> score=0 pass=false recommendation=revise feasibility=feasible auto_revised=false needs_attention=true scores.what=0 scores.why=0 scores.open_to_how=0 scores.not_a_task=0 scores.right_sized=0 error="fetch_failed: task file not created"
```

Remove failed IDs from the processing list and continue with remaining IDs.

## Review Step 1.5: Setup

Run these in parallel (two Bash calls):

```bash
bash scripts/fetch-architecture-context.sh
```

```bash
bash scripts/bootstrap-assess-rfe.sh
```

If architecture fetch fails, proceed without it. If bootstrap fails, note it — review agents will do basic quality checks instead.

## Review Step 2: Launch Assessment + Feasibility Agents

For each ID being reviewed:

**Prepare assessment:**

```bash
python3 scripts/prep_assess.py <ID>
```

**Launch assess agent** (model: opus, run_in_background: true, subagent_type: rfe-scorer):

```
Read .claude/skills/rfe.review/prompts/assess-agent.md and follow all instructions. Substitute: {KEY}=<ID>, {DATA_FILE}=/tmp/rfe-assess/single/<ID>.md, {RUN_DIR}=/tmp/rfe-assess/single, {PROMPT_PATH}=.context/assess-rfe/scripts/agent_prompt.md
```

**Launch feasibility agent** (model: opus, run_in_background: true) — one per ID:

```
Read the skill file at .claude/skills/rfe-feasibility-review/SKILL.md and follow all instructions in the body (everything after the YAML frontmatter). The RFE ID to review is: <ID>
```

Launch all agents for all IDs in parallel (2N agents total for N IDs).

Write IDs to poll files once, then poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py write-ids tmp/rfe-poll-assess.txt <all_IDs>
python3 scripts/state.py write-ids tmp/rfe-poll-feasibility.txt <all_IDs>
python3 scripts/check_review_progress.py --phase assess --id-file tmp/rfe-poll-assess.txt
python3 scripts/check_review_progress.py --phase feasibility --id-file tmp/rfe-poll-feasibility.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Only output status when COMPLETED count changes. Wait for all to complete.

After completion, check prerequisites for each ID via Glob:
- If assess result (`/tmp/rfe-assess/single/<ID>.result.md`) is missing → write error: `assess_failed`
- If feasibility file (`artifacts/rfe-reviews/<ID>-feasibility.md`) is missing → write error: `feasibility_failed`
- If either is missing for an ID, write the error to review frontmatter and remove from processing list

## Review Step 3: Launch Review Agents

For each remaining ID, launch a **review agent** (model: opus, run_in_background: true):

```
Read .claude/skills/rfe.review/prompts/review-agent.md and follow all instructions. Substitute: {ID}=<ID>, {ASSESS_PATH}=/tmp/rfe-assess/single/<ID>.result.md, {FEASIBILITY_PATH}=artifacts/rfe-reviews/<ID>-feasibility.md, {FIRST_PASS}=true
```

Launch all review agents in parallel.

Write IDs to poll file once, then poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py write-ids tmp/rfe-poll-review.txt <all_IDs>
python3 scripts/check_review_progress.py --phase review --id-file tmp/rfe-poll-review.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Wait for all to complete. For any ID where the review file is missing or has no frontmatter, write error: `review_failed`.

## Review Step 3.5: Launch Revise Agents

After all review agents complete, re-read the ID list from disk (context compression may have corrupted in-memory lists):

```bash
python3 scripts/state.py read-ids tmp/review-all-ids.txt
```

Determine which IDs need revision:

```bash
python3 scripts/filter_for_revision.py <all_IDs_from_file>
```

The script outputs the IDs that need revision (filters out passing, infeasible, and rejected IDs). If the output is empty, skip to Review Step 4.

Launch a **revise agent** (model: opus, run_in_background: true) for each ID returned:

```
Read .claude/skills/rfe.review/prompts/revise-agent.md and follow all instructions. Substitute: {ID}=<ID>
```

Launch all revise agents in parallel.

Write IDs to poll file once, then poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py write-ids tmp/rfe-poll-revise.txt <all_IDs_being_revised>
python3 scripts/check_review_progress.py --phase revise --id-file tmp/rfe-poll-revise.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Wait for all to complete.

**Post-processing: fix auto_revised flag.** The revise agent may run out of budget before setting `auto_revised=true`. After all agents complete, run the batch check which compares originals to task files and sets the flag directly in review frontmatter:

```bash
python3 scripts/check_revised.py --batch --ids-file tmp/rfe-poll-revise.txt
```

## Review Step 4: Re-assess if Revised (max 2 cycles)

Re-read ID list from disk:

```bash
python3 scripts/state.py read-ids tmp/review-all-ids.txt
```

After all revise agents complete, check which IDs need re-assessment:

```bash
python3 scripts/collect_recommendations.py --reassess --ids-file tmp/review-all-ids.txt
```

Parse output for `REASSESS=` line. For each ID needing re-assessment (auto_revised=true, pass=false), initialize the cycle counter on disk (set-default is safe if compression causes re-entry — it won't reset an existing counter):

```bash
python3 scripts/state.py set-default tmp/review-config.yaml reassess_cycle=0
```

Before starting a cycle, re-read the cycle counter to guard against context compression:

```bash
python3 scripts/state.py read tmp/review-config.yaml
```

If `reassess_cycle` already shows 2 or higher, stop — max cycles reached. Otherwise, increment after each cycle:

```bash
python3 scripts/state.py set tmp/review-config.yaml reassess_cycle=<N+1>
```

For cycle 1:

Persist reassess IDs to disk (needed across 4a–4e, may be lost to compression during agents):

```bash
python3 scripts/state.py write-ids tmp/review-reassess-ids.txt <all_reassess_IDs>
```

**4a. Save cumulative state and remove review files** so progress detection works:

```bash
python3 scripts/preserve_review_state.py save <all_reassess_IDs>
rm artifacts/rfe-reviews/<ID>-review.md  # for each reassess ID
rm /tmp/rfe-assess/single/<ID>.result.md  # for each reassess ID
```

**4b. Re-run assessment.** For each reassess ID, prepare and launch an assess agent — this is the same process as Review Step 2:

```bash
python3 scripts/prep_assess.py <ID>
```

Launch an **assess agent** (model: opus, run_in_background: true, subagent_type: rfe-scorer) for each reassess ID:

```
Read .claude/skills/rfe.review/prompts/assess-agent.md and follow all instructions. Substitute: {KEY}=<ID>, {DATA_FILE}=/tmp/rfe-assess/single/<ID>.md, {RUN_DIR}=/tmp/rfe-assess/single, {PROMPT_PATH}=.context/assess-rfe/scripts/agent_prompt.md
```

Launch all assess agents in parallel.

Re-read reassess IDs from disk, write poll file, and poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py copy-ids tmp/review-reassess-ids.txt tmp/rfe-poll-reassess-assess.txt
python3 scripts/check_review_progress.py --phase assess --id-file tmp/rfe-poll-reassess-assess.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Wait for all to complete.

**4c. Launch review agents.** Re-read reassess IDs from disk:

```bash
python3 scripts/state.py read-ids tmp/review-reassess-ids.txt
```

For each reassess ID, launch a **review agent** (model: opus, run_in_background: true):

```
Read .claude/skills/rfe.review/prompts/review-agent.md and follow all instructions. Substitute: {ID}=<ID>, {ASSESS_PATH}=/tmp/rfe-assess/single/<ID>.result.md, {FEASIBILITY_PATH}=artifacts/rfe-reviews/<ID>-feasibility.md, {FIRST_PASS}=false
```

Launch all review agents in parallel.

Re-read reassess IDs from disk, write poll file, and poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py copy-ids tmp/review-reassess-ids.txt tmp/rfe-poll-reassess-review.txt
python3 scripts/check_review_progress.py --phase review --id-file tmp/rfe-poll-reassess-review.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Wait for all to complete (review files were removed in 4a, so progress detection works).

**4d. Restore before_scores and revision history.** Re-read reassess IDs from disk:

```bash
python3 scripts/state.py read-ids tmp/review-reassess-ids.txt
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
python3 scripts/check_revised.py --batch --ids-file tmp/review-reassess-ids.txt
```

After cycle 2, stop regardless of results.

## Review Step 5: Finalize

Rebuild the index once:

```bash
python3 scripts/frontmatter.py rebuild-index
```

Re-read flags (in case context was compressed):

```bash
python3 scripts/state.py read tmp/review-config.yaml
```

**If `headless: true`**: Output the text "rfe.review step completed." then run:

```bash
python3 scripts/state.py read tmp/review-config.yaml
python3 scripts/state.py read tmp/autofix-config.yaml 2>/dev/null; python3 scripts/state.py read tmp/split-config.yaml 2>/dev/null; true
```

Check the `caller` field above:
- **`autofix`**: Returning to **Step 3b: Collect Results** of `/rfe.auto-fix`. Re-read batch IDs from `tmp/autofix-batch-N-ids.txt` (where N = `current_batch` from `tmp/autofix-config.yaml`). If the autofix config is not visible, re-read `/rfe.auto-fix` SKILL.md for the full batch loop.
- **`split`**: Returning to **Split Step 3: Right-sizing Self-Correction** of `/rfe.split`. Re-read parent IDs from `tmp/split-all-ids.txt`. If the split config is not visible, re-read `/rfe.split` SKILL.md for the full flow.

Do not summarize or stop.

**If interactive (no `--headless`)**: Re-read ID list and present summary:

```bash
python3 scripts/batch_summary.py --ids-file tmp/review-all-ids.txt
```

Based on the output:
- **All pass**: Tell the user RFEs are ready for `/rfe.submit`.
- **Some need revision**: List the remaining issues (from summary output). Tell the user to edit artifacts and re-run `/rfe.review`.
- **Some recommend split**: Tell the user to run `/rfe.split <ID>` for those IDs.
- **Errors**: Report which IDs had errors and suggest retrying.

$ARGUMENTS
