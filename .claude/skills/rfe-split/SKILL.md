---
name: rfe-split
description: Split oversized work items of any registered type — RFEs and Initiatives — into smaller, right-sized ones. Takes one or more IDs of items already in the workspace (fetched by /rfe-review or written by /rfe-create; e.g. /rfe-split RHAIRFE-1234 RHAIRFE-5678, /rfe-split --type initiative INIT-001) and skips an ID with no local task file. Runs non-interactively — decomposes, generates new items, reviews them, self-corrects, and checks coverage.
user-invocable: true
allowed-tools: Glob, Bash, Agent, Skill, AskUserQuestion
---

You are a work-item splitting orchestrator. Your job is to coordinate decomposition by launching agents and reading structured results. **Critical: never read file contents into your context — only read frontmatter via `scripts/frontmatter.py read` and check file existence via Glob.** All content-heavy work (reading item bodies, decomposition analysis, generating children) is delegated to agents.

## Split Step 0: Resolve the Type, Parse Arguments and Persist Flags

**Layout check.** If `scripts/bootstrap.sh` is not in the working directory, the plugin is installed elsewhere (a marketplace install running in a project): run once

```bash
bash "${CLAUDE_SKILL_DIR}/../../../scripts/bootstrap.sh" --layout
```

The plugin root is three directories above this skill's `SKILL.md`; Claude Code substitutes `${CLAUDE_SKILL_DIR}`, on a host that does not, use this skill file's directory. That call links the plugin's `scripts/` and `types/` into the working directory (and refuses if the directory already carries its own), after which every command below runs exactly as written. In a checkout nothing is linked and the check is a no-op.

Parse `$ARGUMENTS` for flags and IDs:
- Strip `--type <t>` if present (an explicit type — always wins)
- Strip `--headless` flag if present (suppresses end-of-run summary)
- Remaining arguments are one or more space-separated item IDs (`{ID_GRAMMAR}` once resolved)

Resolve the work-item type — forward only `--type <t>` (when given), `--headless` (when given) and the explicit IDs:

```bash
python3 scripts/type_registry.py resolve [--type <t>] [--headless] <IDs>
```

It prints `TYPE RESOLVED: <type> (<how>)`; a non-zero exit is an error — stop and report it. Then print the stage's launch block and keep it: every `{VAR}` in this skill is the value of that `VAR=` line, and every agent you launch gets the whole block prepended to its prompt:

```bash
python3 scripts/type_registry.py launch-vars <type> split
```

Persist parsed flags (survives context compression):

```bash
python3 scripts/state.py init tmp/{STATE_PREFIX}split-config.yaml type={TYPE} headless=<true/false>
```

If no arguments provided, stop with: "Usage: `/rfe-split [--type <t>] <ID> [ID2 ...]`. Provide one or more IDs."

Persist all IDs to disk (survives context compression):

```bash
python3 scripts/state.py write-ids tmp/{STATE_PREFIX}split-all-ids.txt <all_IDs>
```

For each ID, verify the task file exists via Glob (`{TASKS_DIR}/<ID>.md`). If missing, report and skip.

## Split Step 1: Launch Split Agents

The split agent prompt is the type's own file, `{SPLIT_RULES_PATH}` (from the launch block). For each ID, launch a **split agent** (model: opus, run_in_background: true):

```
<launch block>

Substitute: {ID}=<ID>, {TASK_FILE}={TASKS_DIR}/<ID>.md, {REVIEW_FILE}={REVIEWS_DIR}/<ID>-review.md

Read {SPLIT_RULES_PATH} and follow all instructions exactly.
```

Launch all split agents in parallel.

Write IDs to poll file once, then poll using `NEXT_POLL` interval:

```bash
python3 scripts/state.py write-ids {POLL_FILE_PREFIX}split.txt <all_IDs>
python3 scripts/check_review_progress.py --phase {POLL_PREFIX}split --id-file {POLL_FILE_PREFIX}split.txt
```

Sleep for the `NEXT_POLL` seconds reported by the script before polling again. Only output status when COMPLETED count changes. If any agent runs longer than 5 minutes, check its status.

After all agents complete, check split-status files for each ID. If the file is missing, write error to review frontmatter:

```bash
python3 scripts/frontmatter.py set {REVIEWS_DIR}/<ID>-review.md error="split_failed: agent did not write split-status file" type={TYPE}
```

## Split Step 2: Collect Children and Review

Re-read parent IDs from disk (context compression may have corrupted in-memory lists):

```bash
python3 scripts/state.py read-ids tmp/{STATE_PREFIX}split-all-ids.txt
```

For each ID, read `{REVIEWS_DIR}/<ID>-split-status.yaml`. If `action: no-split`, update the review recommendation so downstream consumers don't treat it as needing a split:

```bash
python3 scripts/frontmatter.py set {REVIEWS_DIR}/<ID>-review.md recommendation=revise
```

For IDs where `action: split`, collect children:

```bash
python3 scripts/collect_children.py {TYPE_FLAG} <split_IDs>
```

Parse the output to get all child IDs. If any parent has zero children despite `action: split`, treat it as a no-split and update its recommendation to `revise`.

If there are children to review, invoke `/rfe-review` as an inline Skill, forwarding the resolved type and passing `--headless` through if present:

```
/rfe-review [--headless] --caller split {TYPE_FLAG} <child_ID_1> <child_ID_2> ...
```

This triggers the full agent delegation review pipeline on all children.

## Split Step 3: Right-sizing Self-Correction (up to 1 cycle)

Limited to 1 cycle because repeated re-splitting compounds child count (e.g. 5 → 9) and produces diminishing returns — if decomposition is still wrong after one correction, it needs human judgment.

Initialize the correction cycle counter on disk (set-default is safe if compression causes re-entry — it won't reset an existing counter):

```bash
python3 scripts/state.py set-default tmp/{STATE_PREFIX}split-config.yaml correction_cycle=0
```

After `/rfe-review` completes on children, re-read config and parent IDs (context compression may have lost them):

```bash
python3 scripts/state.py read tmp/{STATE_PREFIX}split-config.yaml
```

If `correction_cycle` is 1 or higher, stop and report remaining right-sizing concerns. Otherwise, re-derive child IDs:

```bash
python3 scripts/collect_children.py {TYPE_FLAG} --ids-file tmp/{STATE_PREFIX}split-all-ids.txt
```

Check right-sized scores. For each child:

```bash
python3 scripts/frontmatter.py read {REVIEWS_DIR}/<child_ID>-review.md
```

If any child scores below {RESPLIT_BELOW}/2 on `scores.{RESPLIT_FIELD}` (the descriptor's `pipeline.resplit` threshold — the same rule the headless pipeline's `check_right_sized.py` applies):

1. **Re-split**: Launch a split agent for the offending child (same prompt as Split Step 1)
2. **Wait** for the agent to complete
3. **Collect new children**: `python3 scripts/collect_children.py {TYPE_FLAG} <re-split_ID>`
4. **Review new children**: Invoke `/rfe-review [--headless] --caller split {TYPE_FLAG} <new_child_IDs>`
5. **Check again**: Read right-sized scores for new children

After each cycle, increment the counter on disk:

```bash
python3 scripts/state.py set tmp/{STATE_PREFIX}split-config.yaml correction_cycle=<N+1>
```

Re-read config before starting the next cycle to check the counter. Stop after 1 cycle and report remaining right-sizing concerns.

**Do not re-split for non-Right-sized criteria.** This loop only corrects grouping mistakes caught by the Right-sized score. Other criteria are handled by `/rfe-review`'s auto-revision.

## Split Step 4: Finalize

If `INDEX_ENABLED={INDEX_ENABLED}` is true, rebuild the index once:

```bash
python3 scripts/frontmatter.py rebuild-index
```

Re-read flags (in case context was compressed):

```bash
python3 scripts/state.py read tmp/{STATE_PREFIX}split-config.yaml
```

**If `headless: true`**: Output the text "rfe-split step completed." and stop.

**If interactive (no `--headless`)**: Present the final state for each parent ID:

```
## Split Complete

Original: {KEY_EXAMPLE} (archived)
New {ENTITY_PLURAL}:
- {LOCAL_PREFIX}003: <title> (Priority: Normal) — PASS
- {LOCAL_PREFIX}004: <title> (Priority: Normal) — PASS

Coverage: All original scope items covered
Review: All new {ENTITY_PLURAL} passed
```

For IDs where `action: no-split`, report the reason recorded in the split-status file (e.g., delivery-coupled).

Tell the user they can:
- Run `/rfe-submit --type {TYPE}` to create or update tickets in Jira
- Edit any new {ENTITY} in `{TASKS_DIR}/` and re-run `/rfe-review --type {TYPE}`

$ARGUMENTS
