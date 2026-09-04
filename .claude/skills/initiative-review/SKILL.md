---
name: initiative-review
description: Review and improve Initiatives. Accepts one or more Jira keys (e.g., /initiative-review RHOAIENG-12345) to fetch and review existing Initiatives, or reviews local artifacts from /initiative-create.
user-invocable: true
allowed-tools: Glob, Bash, Agent, AskUserQuestion
---

You are an Initiative review orchestrator. Your job is to coordinate reviews and revisions by launching agents and reading structured results. **Critical: never read file contents into your context — only read frontmatter via `scripts/frontmatter.py read` and check file existence via Glob.** All content-heavy work is delegated to agents.

## Review Step 0: Parse Arguments and Persist Flags

Parse `$ARGUMENTS` for flags and IDs:
- Strip `--headless` flag if present (suppresses end-of-run summary)
- Strip `--caller <name>` flag if present (identifies calling skill for headless return)
- Remaining arguments are one or more space-separated Initiative IDs (RHOAIENG-NNNN or INIT-NNN)

Persist parsed flags (survives context compression):

```bash
python3 scripts/state.py init tmp/initiative-review-config.yaml headless=<true/false> caller=<split|none>
```

Persist all IDs to disk (survives context compression):

```bash
python3 scripts/state.py write-ids tmp/initiative-review-all-ids.txt <all_IDs>
```

For each ID, check if `artifacts/initiatives/<id>.md` already exists locally (use Glob, don't read the file). Separate IDs into:
- **Local**: task file exists — skip fetch
- **Remote**: task file missing — needs Jira fetch

## Review Step 1: Fetch Missing Initiatives

For each remote ID, launch a **fetch agent** (model: opus, run_in_background: true):

```
Read .claude/skills/initiative-review/prompts/fetch-agent.md and follow all instructions. Substitute {KEY} with <ID> throughout.
```

Write IDs to poll file once, then poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py write-ids tmp/initiative-poll-fetch.txt <all_remote_IDs>
python3 scripts/check_review_progress.py --phase initiative-fetch --id-file tmp/initiative-poll-fetch.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Only output a status line when COMPLETED count changes. If any agent runs longer than 5 minutes, check its status.

After all fetch agents complete, verify task files exist via Glob. For any missing, write an error to the review file:

```bash
python3 scripts/frontmatter.py set artifacts/initiative-reviews/<ID>-review.md initiative_id=<ID> score=0 pass=false recommendation=revise feasibility=feasible auto_revised=false needs_attention=true scores.what=0 scores.why=0 scores.scope=0 scores.open_to_how=0 scores.right_sized=0 error="fetch_failed: task file not created"
```

Remove failed IDs from the processing list and continue with remaining IDs.

## Review Step 1.5: Setup

Run these in parallel (two Bash calls):

```bash
bash scripts/fetch-architecture-context.sh
```

```bash
bash scripts/bootstrap-assess-rfe.sh --type initiative
```

If architecture fetch fails, proceed without it. If bootstrap fails, note it — review agents will do basic quality checks instead.

## Review Step 2: Launch Assessment + Feasibility Agents

For each ID being reviewed:

**Prepare assessment:**

```bash
python3 scripts/prep_assess.py <ID>
```

**Launch assess agent** (model: opus, run_in_background: true, subagent_type: initiative-scorer):

```
Read .claude/skills/initiative-review/prompts/assess-agent.md and follow all instructions. Substitute: {KEY}=<ID>, {DATA_FILE}=tmp/rfe-assess/single/<ID>.md, {RUN_DIR}=tmp/rfe-assess/single, {PROMPT_PATH}=.context/assess-rfe/skills/assess-initiative/scripts/agent_prompt.md
```

**Launch feasibility agent** (model: opus, run_in_background: true) — one per ID:

```
Read the skill file at .claude/skills/initiative-feasibility-review/SKILL.md and follow all instructions in the body (everything after the YAML frontmatter). The Initiative ID to review is: <ID>
```

**Launch alignment agent** (model: opus, run_in_background: true) — one per ID that has a RHAISTRAT parent:

Read the parent_key from the Initiative's frontmatter output above. If the parent_key matches `RHAISTRAT-*`, launch:

```
Read the skill file at .claude/skills/strategic-alignment-review/SKILL.md and follow all instructions in the body (everything after the YAML frontmatter). The Initiative ID to review is: <ID>
```

If no RHAISTRAT parent_key, skip the alignment agent for this ID.

Launch all agents for all IDs in parallel (up to 3N agents total for N IDs: assess + feasibility + alignment).

Write IDs to poll files once, then poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py write-ids tmp/initiative-poll-assess.txt <all_IDs>
python3 scripts/state.py write-ids tmp/initiative-poll-feasibility.txt <all_IDs>
python3 scripts/check_review_progress.py --phase initiative-assess --id-file tmp/initiative-poll-assess.txt
python3 scripts/check_review_progress.py --phase initiative-feasibility --id-file tmp/initiative-poll-feasibility.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Only output status when COMPLETED count changes. Wait for all to complete.

If at least one ID had a RHAISTRAT parent, poll the alignment agents the same way — skip this block entirely when none did, since the checker exits 2 on an empty ID list:

```bash
python3 scripts/state.py write-ids tmp/initiative-poll-alignment.txt <all_IDs_with_RHAISTRAT_parent>
python3 scripts/check_review_progress.py --phase initiative-alignment --id-file tmp/initiative-poll-alignment.txt
```

Alignment is informational, not blocking: if it is still PENDING after 5 minutes, stop polling and continue. The prerequisite check below records the missing file without failing the ID.

After completion, check prerequisites for each ID via Glob:
- If assess result (`tmp/rfe-assess/single/<ID>.result.md`) is missing → write error: `assess_failed`
- If feasibility file (`artifacts/initiative-reviews/<ID>-feasibility.md`) is missing → write error: `feasibility_failed`
- If alignment file (`artifacts/initiative-reviews/<ID>-alignment.md`) is missing AND a RHAISTRAT parent_key exists → note but do not treat as a blocking error (alignment is informational)

For any missing prerequisite:

```bash
python3 scripts/frontmatter.py set artifacts/initiative-reviews/<ID>-review.md initiative_id=<ID> score=0 pass=false recommendation=revise feasibility=feasible auto_revised=false needs_attention=true scores.what=0 scores.why=0 scores.scope=0 scores.open_to_how=0 scores.right_sized=0 error="<assess_failed or feasibility_failed>: file not created"
```

Remove failed IDs from the processing list and continue with remaining IDs.

## Review Step 3: Launch Review Agents

For each remaining ID, launch a **review agent** (model: opus, run_in_background: true):

```
Read .claude/skills/initiative-review/prompts/review-agent.md and follow all instructions. Substitute: {ID}=<ID>, {ASSESS_PATH}=tmp/rfe-assess/single/<ID>.result.md, {FEASIBILITY_PATH}=artifacts/initiative-reviews/<ID>-feasibility.md, {ALIGNMENT_PATH}=artifacts/initiative-reviews/<ID>-alignment.md, {FIRST_PASS}=true
```

Launch all review agents in parallel.

Write IDs to poll file once, then poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py write-ids tmp/initiative-poll-review.txt <all_IDs>
python3 scripts/check_review_progress.py --phase initiative-review --id-file tmp/initiative-poll-review.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Wait for all to complete. For any ID where the review file is missing or has no frontmatter, write error: `review_failed`.

## Review Step 3.5: Launch Revise Agents

After all review agents complete, re-read the ID list from disk (context compression may have corrupted in-memory lists):

```bash
python3 scripts/state.py read-ids tmp/initiative-review-all-ids.txt
```

Determine which IDs need revision:

```bash
python3 scripts/filter_for_revision.py <all_IDs_from_file>
```

The script outputs the IDs that need revision (filters out passing, infeasible, and rejected IDs). If the output is empty, skip to Review Step 4.

Launch a **revise agent** (model: opus, run_in_background: true) for each ID returned:

```
Read .claude/skills/initiative-review/prompts/revise-agent.md and follow all instructions. Substitute: {ID}=<ID>
```

Launch all revise agents in parallel.

Write IDs to poll file once, then poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py write-ids tmp/initiative-poll-revise.txt <all_IDs_being_revised>
python3 scripts/check_review_progress.py --phase initiative-revise --id-file tmp/initiative-poll-revise.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Wait for all to complete.

**Post-processing: fix auto_revised flag.** The revise agent may run out of budget before setting `auto_revised=true`. After all agents complete, run the batch check which compares originals to task files and sets the flag directly in review frontmatter:

```bash
python3 scripts/check_revised.py --type initiative --batch --ids-file tmp/initiative-poll-revise.txt
```

## Review Step 4: Re-assess if Revised (max 2 cycles)

Re-read ID list from disk:

```bash
python3 scripts/state.py read-ids tmp/initiative-review-all-ids.txt
```

After all revise agents complete, check which IDs need re-assessment:

```bash
python3 scripts/collect_recommendations.py --reassess --type initiative --ids-file tmp/initiative-review-all-ids.txt
```

Parse output for `REASSESS=` line. For each ID needing re-assessment (auto_revised=true, pass=false), initialize the cycle counter on disk (set-default is safe if compression causes re-entry — it won't reset an existing counter):

```bash
python3 scripts/state.py set-default tmp/initiative-review-config.yaml reassess_cycle=0
```

Before starting a cycle, re-read the cycle counter to guard against context compression:

```bash
python3 scripts/state.py read tmp/initiative-review-config.yaml
```

If `reassess_cycle` already shows 2 or higher, stop — max cycles reached. Otherwise, increment after each cycle:

```bash
python3 scripts/state.py set tmp/initiative-review-config.yaml reassess_cycle=<N+1>
```

For cycle 1:

Persist reassess IDs to disk (needed across 4a–4e, may be lost to compression during agents):

```bash
python3 scripts/state.py write-ids tmp/initiative-review-reassess-ids.txt <all_reassess_IDs>
```

**4a. Save cumulative state and remove review files** so progress detection works:

```bash
python3 scripts/preserve_review_state.py save <all_reassess_IDs>
rm artifacts/initiative-reviews/<ID>-review.md  # for each reassess ID
rm tmp/rfe-assess/single/<ID>.result.md  # for each reassess ID
```

**4b. Re-run assessment.** For each reassess ID, prepare and launch an assess agent — this is the same process as Review Step 2:

```bash
python3 scripts/prep_assess.py <ID>
```

Launch an **assess agent** (model: opus, run_in_background: true, subagent_type: initiative-scorer) for each reassess ID:

```
Read .claude/skills/initiative-review/prompts/assess-agent.md and follow all instructions. Substitute: {KEY}=<ID>, {DATA_FILE}=tmp/rfe-assess/single/<ID>.md, {RUN_DIR}=tmp/rfe-assess/single, {PROMPT_PATH}=.context/assess-rfe/skills/assess-initiative/scripts/agent_prompt.md
```

Launch all assess agents in parallel.

Re-read reassess IDs from disk, write poll file, and poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py copy-ids tmp/initiative-review-reassess-ids.txt tmp/initiative-poll-reassess-assess.txt
python3 scripts/check_review_progress.py --phase initiative-assess --id-file tmp/initiative-poll-reassess-assess.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Wait for all to complete.

**4c. Launch review agents.** Re-read reassess IDs from disk:

```bash
python3 scripts/state.py read-ids tmp/initiative-review-reassess-ids.txt
```

For each reassess ID, launch a **review agent** (model: opus, run_in_background: true):

```
Read .claude/skills/initiative-review/prompts/review-agent.md and follow all instructions. Substitute: {ID}=<ID>, {ASSESS_PATH}=tmp/rfe-assess/single/<ID>.result.md, {FEASIBILITY_PATH}=artifacts/initiative-reviews/<ID>-feasibility.md, {ALIGNMENT_PATH}=artifacts/initiative-reviews/<ID>-alignment.md, {FIRST_PASS}=false
```

Launch all review agents in parallel.

Re-read reassess IDs from disk, write poll file, and poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py copy-ids tmp/initiative-review-reassess-ids.txt tmp/initiative-poll-reassess-review.txt
python3 scripts/check_review_progress.py --phase initiative-review --id-file tmp/initiative-poll-reassess-review.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Wait for all to complete (review files were removed in 4a, so progress detection works).

**4d. Restore before_scores and revision history.** Re-read reassess IDs from disk:

```bash
python3 scripts/state.py read-ids tmp/initiative-review-reassess-ids.txt
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
python3 scripts/check_revised.py --type initiative --batch --ids-file tmp/initiative-review-reassess-ids.txt
```

After cycle 2, stop regardless of results.

## Review Step 5: Finalize

Re-read flags (in case context was compressed):

```bash
python3 scripts/state.py read tmp/initiative-review-config.yaml
```

**If `headless: true`**: Output the text "initiative-review step completed." then run each of these as its own Bash call — never chain them with `;` (chained commands are denied in headless mode):

```bash
python3 scripts/state.py read tmp/initiative-review-config.yaml
```

```bash
python3 scripts/state.py read tmp/initiative-split-config.yaml
```

A "State file not found" error just means that caller's config does not exist — continue with what was found.

Check the `caller` field above:
- **`split`**: Returning to **Split Step 3: Right-sizing Self-Correction** of `/initiative-split`. Re-read parent IDs from `tmp/initiative-split-all-ids.txt`. If the split config is not visible, re-read `/initiative-split` SKILL.md for the full flow.

Do not summarize or stop.

**If interactive (no `--headless`)**: Re-read ID list and present summary:

```bash
python3 scripts/batch_summary.py --type initiative --ids-file tmp/initiative-review-all-ids.txt
```

Based on the output:
- **All pass**: Tell the user Initiatives are ready for `/initiative-submit`.
- **Some need revision**: List the remaining issues (from summary output). Tell the user to edit artifacts and re-run `/initiative-review`.
- **Some recommend split**: Tell the user to run `/initiative-split <ID>` for those IDs.
- **Errors**: Report which IDs had errors and suggest retrying.

$ARGUMENTS
