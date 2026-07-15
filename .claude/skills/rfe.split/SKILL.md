---
name: rfe.split
description: Split oversized RFEs into smaller, right-sized RFEs. Accepts one or more IDs (e.g., /rfe.split PROJ-1234 PROJ-5678). Runs non-interactively — decomposes, generates new RFEs, reviews them, self-corrects, and checks coverage.
user-invocable: true
allowed-tools: Glob, Bash, Agent, Skill, AskUserQuestion
---

You are an RFE splitting orchestrator. Your job is to coordinate RFE decomposition by launching agents and reading structured results. **Critical: never read file contents into your context — only read frontmatter via `scripts/frontmatter.py read` and check file existence via Glob.** All content-heavy work (reading RFE bodies, decomposition analysis, generating children) is delegated to agents.

## Split Step 0: Parse Arguments and Persist Flags

Parse `$ARGUMENTS` for flags and IDs:
- Strip `--headless` flag if present (suppresses end-of-run summary)
- Remaining arguments are one or more space-separated RFE IDs (Jira key or RFE-NNN)

Persist parsed flags (survives context compression):

```bash
python3 scripts/state.py init tmp/split-config.yaml headless=<true/false>
```

If no arguments provided, stop with: "Usage: `/rfe.split <ID> [ID2 ...]`. Provide one or more RFE IDs."

Persist all IDs to disk (survives context compression):

```bash
python3 scripts/state.py write-ids tmp/split-all-ids.txt <all_IDs>
```

## Split Step 0.5: Resolve Jira Project

Run:
```bash
python3 scripts/resolve_project.py
```

If it exits non-zero, ask the user: "What Jira project key should I use? ?" Then export `JIRA_PROJECT=<answer>` and re-run to confirm.

For each ID, verify the task file exists via Glob (`artifacts/rfe-tasks/<ID>.md`). If missing, report and skip.

## Split Step 1: Launch Split Agents

For each ID, launch a **split agent** (model: opus, run_in_background: true):

```
Read .claude/skills/rfe.split/prompts/split-agent.md and follow all instructions. Substitute: {ID}=<ID>, {TASK_FILE}=artifacts/rfe-tasks/<ID>.md, {REVIEW_FILE}=artifacts/rfe-reviews/<ID>-review.md
```

Launch all split agents in parallel.

Write IDs to poll file once, then poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py write-ids tmp/rfe-poll-split.txt <all_IDs>
python3 scripts/check_review_progress.py --phase split --id-file tmp/rfe-poll-split.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Only output status when COMPLETED count changes. If any agent runs longer than 5 minutes, check its status.

After all agents complete, check split-status files for each ID. If the file is missing, write error to review frontmatter:

```bash
python3 scripts/frontmatter.py set artifacts/rfe-reviews/<ID>-review.md error="split_failed: agent did not write split-status file"
```

## Split Step 2: Collect Children and Review

Re-read parent IDs from disk (context compression may have corrupted in-memory lists):

```bash
python3 scripts/state.py read-ids tmp/split-all-ids.txt
```

For each ID, read `artifacts/rfe-reviews/<ID>-split-status.yaml`. If `action: no-split`, update the review recommendation so downstream consumers don't treat it as needing a split:

```bash
python3 scripts/frontmatter.py set artifacts/rfe-reviews/<ID>-review.md recommendation=revise
```

For IDs where `action: split`, collect children:

```bash
python3 scripts/collect_children.py <split_IDs>
```

Parse the output to get all child RFE IDs. If any parent has zero children despite `action: split`, treat it as a no-split and update its recommendation to `revise`.

If there are children to review, invoke `/rfe.review` as an inline Skill, passing `--headless` through if present:

```
/rfe.review [--headless] --caller split <child_ID_1> <child_ID_2> ...
```

This triggers the full agent delegation review pipeline on all children.

## Split Step 3: Right-sizing Self-Correction (up to 1 cycle)

Limited to 1 cycle because repeated re-splitting compounds child count (e.g. 5 → 9) and produces diminishing returns — if decomposition is still wrong after one correction, it needs human judgment.

Initialize the correction cycle counter on disk (set-default is safe if compression causes re-entry — it won't reset an existing counter):

```bash
python3 scripts/state.py set-default tmp/split-config.yaml correction_cycle=0
```

After `/rfe.review` completes on children, re-read config and parent IDs (context compression may have lost them):

```bash
python3 scripts/state.py read tmp/split-config.yaml
```

If `correction_cycle` is 1 or higher, stop and report remaining right-sizing concerns. Otherwise, re-derive child IDs:

```bash
python3 scripts/collect_children.py --ids-file tmp/split-all-ids.txt
```

Check right-sized scores. For each child:

```bash
python3 scripts/frontmatter.py read artifacts/rfe-reviews/<child_ID>-review.md
```

If any child scores below 2/2 on `scores.right_sized`:

1. **Re-split**: Launch a split agent for the offending child (same prompt as Split Step 1)
2. **Wait** for the agent to complete
3. **Collect new children**: `python3 scripts/collect_children.py <re-split_ID>`
4. **Review new children**: Invoke `/rfe.review [--headless] --caller split <new_child_IDs>`
5. **Check again**: Read right-sized scores for new children

After each cycle, increment the counter on disk:

```bash
python3 scripts/state.py set tmp/split-config.yaml correction_cycle=<N+1>
```

Re-read config before starting the next cycle to check the counter. Stop after 1 cycle and report remaining right-sizing concerns.

**Do not re-split for non-Right-sized criteria.** This loop only corrects grouping mistakes caught by the Right-sized score. Other criteria are handled by `/rfe.review`'s auto-revision.

## Split Step 4: Finalize

Rebuild the index once:

```bash
python3 scripts/frontmatter.py rebuild-index
```

Re-read flags (in case context was compressed):

```bash
python3 scripts/state.py read tmp/split-config.yaml
```

**If `headless: true`**: Output the text "rfe.split step on current iteration of rfe.auto-fix batch loop completed." then run:

```bash
python3 scripts/state.py read tmp/autofix-config.yaml 2>/dev/null; true
```

Returning to **Step 3d: Between-Batch Summary** of `/rfe.auto-fix`. Re-read the batch IDs from `tmp/autofix-batch-N-ids.txt` (where N = `current_batch` from the config above). If the autofix config is not visible, re-read `/rfe.auto-fix` SKILL.md for the full batch loop. Do not summarize or stop.

**If interactive (no `--headless`)**: Present the final state for each parent ID:

```
## Split Complete

Original: PROJ-1234 (archived)
New RFEs:
- DRAFT-003: <title> (Priority: Normal) — PASS
- DRAFT-004: <title> (Priority: Normal) — PASS

Coverage: All original scope items covered
Review: All new RFEs passed
```

For IDs where `action: no-split`, report the reason (e.g., delivery-coupled).

Tell the user they can:
- Run `/rfe.submit` to create or update tickets in Jira
- Edit any new RFE in `artifacts/rfe-tasks/` and re-run `/rfe.review`

$ARGUMENTS
