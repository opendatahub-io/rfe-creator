---
name: initiative-split
description: Split oversized Initiatives into smaller, focused Initiatives. Accepts one or more IDs (e.g., /initiative-split RHOAIENG-1234 INIT-001). Runs non-interactively — decomposes, generates new Initiatives, reviews them, self-corrects, and checks coverage.
user-invocable: true
allowed-tools: Glob, Bash, Agent, Skill, AskUserQuestion
---

You are an Initiative splitting orchestrator. Your job is to coordinate Initiative decomposition by launching agents and reading structured results. **Critical: never read file contents into your context — only read frontmatter via `scripts/frontmatter.py read` and check file existence via Glob.** All content-heavy work (reading Initiative bodies, decomposition analysis, generating children) is delegated to agents.

## Split Step 0: Parse Arguments and Persist Flags

Parse `$ARGUMENTS` for flags and IDs:
- Strip `--headless` flag if present (suppresses end-of-run summary)
- Remaining arguments are one or more space-separated Initiative IDs (RHOAIENG-NNNN or INIT-NNN)

Persist parsed flags (survives context compression):

```bash
python3 scripts/state.py init tmp/initiative-split-config.yaml headless=<true/false>
```

If no arguments provided, stop with: "Usage: `/initiative-split <ID> [ID2 ...]`. Provide one or more Initiative IDs."

Persist all IDs to disk (survives context compression):

```bash
python3 scripts/state.py write-ids tmp/initiative-split-all-ids.txt <all_IDs>
```

For each ID, verify the task file exists via Glob (`artifacts/initiatives/<ID>.md`). If missing, report and skip.

## Split Step 1: Launch Split Agents

For each ID, launch a **split agent** (model: opus, run_in_background: true):

```
Read .claude/skills/initiative-split/prompts/split-agent.md and follow all instructions. Substitute: {ID}=<ID>, {TASK_FILE}=artifacts/initiatives/<ID>.md, {REVIEW_FILE}=artifacts/initiative-reviews/<ID>-review.md
```

Launch all split agents in parallel.

Write IDs to poll file once, then poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py write-ids tmp/initiative-poll-split.txt <all_IDs>
python3 scripts/check_review_progress.py --phase initiative-split --id-file tmp/initiative-poll-split.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Only output status when COMPLETED count changes. If any agent runs longer than 5 minutes, check its status.

After all agents complete, check split-status files for each ID. If the file is missing, write error to review frontmatter:

```bash
python3 scripts/frontmatter.py set artifacts/initiative-reviews/<ID>-review.md error="split_failed: agent did not write split-status file" type=initiative
```

## Split Step 2: Collect Children and Review

Re-read parent IDs from disk (context compression may have corrupted in-memory lists):

```bash
python3 scripts/state.py read-ids tmp/initiative-split-all-ids.txt
```

For each ID, read `artifacts/initiative-reviews/<ID>-split-status.yaml`. If `action: no-split`, update the review recommendation so downstream consumers don't treat it as needing a split:

```bash
python3 scripts/frontmatter.py set artifacts/initiative-reviews/<ID>-review.md recommendation=revise
```

For IDs where `action: split`, collect children:

```bash
python3 scripts/collect_children.py --type initiative <split_IDs>
```

Parse the output to get all child Initiative IDs. If any parent has zero children despite `action: split`, treat it as a no-split and update its recommendation to `revise`.

If there are children to review, invoke `/initiative-review` as an inline Skill, passing `--headless` through if present:

```
/initiative-review [--headless] --caller split <child_ID_1> <child_ID_2> ...
```

This triggers the full agent delegation review pipeline on all children.

## Split Step 3: Scope Self-Correction (up to 1 cycle)

Limited to 1 cycle because repeated re-splitting compounds child count and produces diminishing returns — if decomposition is still wrong after one correction, it needs human judgment.

Initialize the correction cycle counter on disk (set-default is safe if compression causes re-entry — it won't reset an existing counter):

```bash
python3 scripts/state.py set-default tmp/initiative-split-config.yaml correction_cycle=0
```

After `/initiative-review` completes on children, re-read config and parent IDs (context compression may have lost them):

```bash
python3 scripts/state.py read tmp/initiative-split-config.yaml
```

If `correction_cycle` is 1 or higher, stop and report remaining scope concerns. Otherwise, re-derive child IDs:

```bash
python3 scripts/collect_children.py --type initiative --ids-file tmp/initiative-split-all-ids.txt
```

Check review results. For each child:

```bash
python3 scripts/frontmatter.py read artifacts/initiative-reviews/<child_ID>-review.md
```

If any child has `recommendation=split` (indicating its scope is still too broad):

1. **Re-split**: Launch a split agent for the offending child (same prompt as Split Step 1)
2. **Wait** for the agent to complete
3. **Collect new children**: `python3 scripts/collect_children.py --type initiative <re-split_ID>`
4. **Review new children**: Invoke `/initiative-review [--headless] --caller split <new_child_IDs>`
5. **Check again**: Read review results for new children

After each cycle, increment the counter on disk:

```bash
python3 scripts/state.py set tmp/initiative-split-config.yaml correction_cycle=<N+1>
```

Re-read config before starting the next cycle to check the counter. Stop after 1 cycle and report remaining scope concerns.

**Do not re-split for non-Scope criteria.** This loop only corrects scope issues caught by the review agent's `split` recommendation. Other criteria are handled by `/initiative-review`'s auto-revision.

## Split Step 4: Finalize

Re-read flags (in case context was compressed):

```bash
python3 scripts/state.py read tmp/initiative-split-config.yaml
```

**If `headless: true`**: Output the text "initiative-split step completed." and stop.

**If interactive (no `--headless`)**: Present the final state for each parent ID:

```
## Split Complete

Original: RHOAIENG-1234 (archived)
New Initiatives:
- INIT-003: <title> (Priority: Normal) — PASS
- INIT-004: <title> (Priority: Normal) — PASS

Coverage: All original scope items covered
Review: All new Initiatives passed
```

For IDs where `action: no-split`, report the reason (e.g., tightly-coupled-workstreams).

Tell the user they can:
- Run `/initiative-submit` to create or update tickets in Jira
- Edit any new Initiative in `artifacts/initiatives/` and re-run `/initiative-review`

$ARGUMENTS
