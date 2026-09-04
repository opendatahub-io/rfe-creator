---
name: initiative-speedrun
description: End-to-end Initiative pipeline. Accepts a single idea, Jira key(s), or a YAML batch file. Creates, reviews, auto-fixes (with splits), and submits. Supports --headless, --announce-complete, and --dry-run for CI.
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion, Skill
---

You are running the full Initiative pipeline in speedrun mode. Your goal is to go from objectives to submitted Jira tickets with minimal interaction. You orchestrate by calling other skills — never duplicate their work.

## Step 0: Parse Arguments and Persist Flags

Parse `$ARGUMENTS` for:
- `--input <path>`: Path to a YAML file with batch entries
- `--headless`: Suppress questions and confirmations (for CI / eval)
- `--announce-complete`: Print completion marker when done (for CI / eval harnesses)
- `--dry-run`: Skip Jira writes in submit
- `--batch-size N`: Override batch size (default 5), passed to auto-fix
- Remaining arguments: either a single Jira key (RHOAIENG-NNNN) or a free-text objective

Clean temp state and persist parsed flags. `batch_size` MUST always be a concrete integer — if the user did not pass `--batch-size`, substitute the speedrun default of `5`. Do not write `<N>`, `null`, or omit the field.

```bash
python3 scripts/state.py clean
python3 scripts/prep_assess.py --clean-all
python3 scripts/state.py init tmp/initiative-speedrun-config.yaml headless=<true/false> announce_complete=<true/false> dry_run=<true/false> batch_size=<N or 5> input_file=<path or null>
```

Determine pipeline mode:
- **Mode A (Batch YAML)**: `--input` flag present → batch create + auto-fix + submit
- **Mode B (Existing Initiative)**: argument is a Jira key (RHOAIENG-NNNN) → skip create, auto-fix + submit
- **Mode C (Single idea)**: free-text argument, no `--input` → single create + auto-fix + submit

If no arguments provided, stop with usage instructions.

## Step 0.5: Bootstrap Dependencies

Run bootstrap early so agent definitions (e.g. `initiative-scorer`) are installed
in `.claude/agents/` before they're needed in Phase 2. The CREATE phase gives
the background agent rescan time to register them.

```bash
bash scripts/bootstrap-assess-rfe.sh --type initiative
```

If bootstrap fails, retry once. If the retry also fails, continue — auto-fix
will attempt bootstrap again in its own setup step.

## Defaults

When the user doesn't specify, use these defaults:
- **Priority**: Normal
- **Labels**: None unless specified

## Phase 1: Create

**Mode A (Batch YAML)**: Read the YAML input file. Format:

```yaml
- prompt: "We need to implement model signature verification at serving time"
  priority: Critical
  parent_key: RHAISTRAT-1234
  clarifying_context: |
    The security team has flagged model integrity as a gap...
- prompt: "Consolidate the inference backends under a unified API"
  priority: Major
```

Validate the batch file before spending any agent budget on it. Use `--strict` so unknown fields and duplicate prompts block the run:

```bash
python3 scripts/validate_batch_input.py <input_file> --type initiative --strict
```

If this exits nonzero, stop and report the printed `ERROR:`/`WARNING:` lines to the user instead of proceeding.

Count entries and pre-allocate all IDs upfront:

```bash
python3 scripts/next_rfe_id.py --prefix INIT --dir artifacts/initiatives --from-batch <input_file>
```

For each entry, launch an Agent to invoke `/initiative-create`. Pass the pre-assigned ID so each Agent knows which ID to use:

```
Agent for entry 1:  /initiative-create --headless --initiative-id INIT-001 [--priority <priority>] [--parent <parent_key>] <prompt>
Agent for entry 2:  /initiative-create --headless --initiative-id INIT-002 [--priority <priority>] [--parent <parent_key>] <prompt>
...
Agent for entry N:  /initiative-create --headless --initiative-id INIT-<N> [--priority <priority>] [--parent <parent_key>] <prompt>
```

Each entry is a single objective — `/initiative-create` must produce exactly one Initiative per invocation. Wait for all N agents to complete. You must have exactly N Initiative IDs — if fewer were created, retry the missing entries. **Never delete or re-create task files during Phase 1** — quality issues are addressed in Phase 2 (Auto-fix).

**Mode B (Existing Initiative)**: Skip Phase 1. The Jira key(s) from arguments become the processing list.

**Mode C (Single idea)**: Invoke `/initiative-create` with the user's input:

```
/initiative-create [--headless] <idea_text>
```

If not headless, `/initiative-create` will ask clarifying questions. Collect created Initiative IDs.

After Phase 1 (all modes), persist the ID list to disk:

```bash
python3 scripts/state.py write-ids tmp/initiative-speedrun-all-ids.txt <all_IDs>
```

## Phase 2: Auto-fix

Re-read config and ID list from disk (in case context was compressed during Phase 1):

```bash
python3 scripts/state.py read tmp/initiative-speedrun-config.yaml
python3 scripts/state.py read-ids tmp/initiative-speedrun-all-ids.txt
```

Invoke auto-fix using the **Skill** tool (NOT Agent — Agent runs in background and causes the session to terminate). Build the args from the config file:

```
Skill(skill: "initiative-auto-fix", args: "--headless --announce-complete --batch-size <batch_size> <all_IDs_from_file>")
```

Pass `--headless` and `--announce-complete` through if set in the config. **Always** pass `--batch-size <batch_size>` using the value from `tmp/initiative-speedrun-config.yaml` — never omit it, never let auto-fix's own default take over. The speedrun default (5) was already pinned in Step 0; relying on it here is what makes runs reproducible.

Auto-fix handles: assessment, feasibility checks, alignment review, auto-revision, re-assessment, splitting oversized Initiatives, retry queue, and report generation. The Skill call blocks until auto-fix completes — this is correct. **Do NOT stop, summarize, or skip early** — the pipeline must process every ID through all phases. Never emit a text-only response (no tool call) during pipeline execution — this terminates the CI process.

**Bash discipline:** Issue exactly one operation per Bash call. Never use command substitution `$(...)` or chain commands with `;`, `&&`, or `||` — they trigger an approval prompt and are denied in headless mode. Instead, pass a value between commands by writing it to a `tmp/` file with `scripts/state.py` and reading it back in a separate call.

After auto-fix returns, verify all Initiatives were processed:

```bash
python3 scripts/check_autofix_complete.py --type initiative
```

If incomplete (exit code 1), the output shows `MISSING_IDS=RHOAIENG-1234,INIT-002,...`. Re-invoke auto-fix with the Skill tool using only the missing IDs:

```
Skill(skill: "initiative-auto-fix", args: "--headless --batch-size <batch_size> <missing_IDs>")
```

Repeat the verify+retry cycle until all Initiatives have reviews or 3 retries have been exhausted.

## Phase 3: Submit

Re-read flags (in case context was compressed):

```bash
python3 scripts/state.py read tmp/initiative-speedrun-config.yaml
```

Re-read ID list from disk:

```bash
python3 scripts/state.py read-ids tmp/initiative-speedrun-all-ids.txt
```

Collect passing IDs:

```bash
python3 scripts/collect_recommendations.py --type initiative <all_IDs_from_file>
```

Parse the `SUBMIT=` line for IDs ready to submit.

If no IDs are ready to submit, skip to Phase 4.

If IDs are ready, invoke submit using the **Skill** tool:

```
Skill(skill: "initiative-submit", args: "--dry-run --headless <passing_IDs>")
```

Pass `--dry-run` and `--headless` through if set in the config. If not headless, `/initiative-submit` will show a confirmation table before writing to Jira — this is the one mandatory interaction point.

## Phase 4: Summary

Re-read flags:

```bash
python3 scripts/state.py read tmp/initiative-speedrun-config.yaml
```

Re-read ID list:

```bash
python3 scripts/state.py read-ids tmp/initiative-speedrun-all-ids.txt
```

Generate machine-readable summary:

```bash
python3 scripts/batch_summary.py --type initiative --ids-file tmp/initiative-speedrun-all-ids.txt
```

If headless, output the counts line and stop. If interactive, output:

```
## Speedrun Complete

### Created
- INIT-NNN: <title> (Priority: Normal)

### Review Results
- Passed: N
- Failed: N
- Split: N (into M children)

### Submitted
- RHOAIENG-NNNN: <title> [created/updated/dry-run]

### Reports
- Run report: artifacts/auto-fix-runs/initiative-run-<timestamp>.yaml

### Remaining Issues
<Any Initiatives that could not be auto-fixed, or "None">
```

$ARGUMENTS
