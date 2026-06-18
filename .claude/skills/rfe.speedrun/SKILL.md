---
name: rfe.speedrun
description: End-to-end RFE pipeline. Accepts a single idea, Jira key(s), or a YAML batch file. Creates, reviews, auto-fixes (with splits), and submits. Supports --headless, --announce-complete, and --dry-run for CI.
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion, Skill
---

You are running the full RFE pipeline in speedrun mode. Your goal is to go from problem statements to submitted Jira tickets with minimal interaction. You orchestrate by calling other skills — never duplicate their work.

## Step 0: Parse Arguments and Persist Flags

Parse `$ARGUMENTS` for:
- `--input <path>`: Path to a YAML file with batch entries
- `--headless`: Suppress questions and confirmations (for CI / eval)
- `--announce-complete`: Print completion marker when done (for CI / eval harnesses)
- `--dry-run`: Skip Jira writes in submit
- `--batch-size N`: Override batch size (default 5), passed to auto-fix
- Remaining arguments: either a single Jira key (RHAIRFE-NNNN) or a free-text idea

Clean temp state and persist parsed flags. `batch_size` MUST always be a concrete integer — if the user did not pass `--batch-size`, substitute the speedrun default of `5`. Do not write `<N>`, `null`, or omit the field.

```bash
python3 scripts/state.py clean
python3 scripts/prep_assess.py --clean-all
python3 scripts/state.py init tmp/speedrun-config.yaml headless=<true/false> announce_complete=<true/false> dry_run=<true/false> batch_size=<N or 5> input_file=<path or null>
```

Determine pipeline mode:
- **Mode A (Batch YAML)**: `--input` flag present → batch create + auto-fix + submit
- **Mode B (Existing RFE)**: argument is a Jira key (RHAIRFE-NNNN) → skip create, auto-fix + submit
- **Mode C (Single idea)**: free-text argument, no `--input` → single create + auto-fix + submit

If no arguments provided, stop with usage instructions.

## Defaults

When the user doesn't specify, use these defaults:
- **Priority**: Normal
- **Size**: S or M (unless the input clearly describes a large initiative)
- **RFE count**: Single RFE per entry, unless an entry describes multiple distinct business needs
- **Labels**: None unless specified

## Phase 1: Create

**Mode A (Batch YAML)**: Read the YAML input file. Format:

```yaml
- prompt: "Users need to verify model signatures at serving time"
  priority: Critical
  labels: [candidate-3.5]
- prompt: "TrustyAI operator crashes on large clusters"
  priority: Major
```

Count entries and pre-allocate all IDs upfront:

```bash
N=$(python3 -c "import yaml; print(len(yaml.safe_load(open('batch.yaml'))))")
python3 scripts/next_rfe_id.py $N   # prints RFE-001 through RFE-<N>
```

For each entry, launch an Agent to invoke `/rfe.create`. Pass the pre-assigned ID so each Agent knows which ID to use:

```
Agent for entry 1:  /rfe.create --headless --rfe-id RFE-001 [--priority <priority>] <prompt>
Agent for entry 2:  /rfe.create --headless --rfe-id RFE-002 [--priority <priority>] <prompt>
...
Agent for entry N:  /rfe.create --headless --rfe-id RFE-<N> [--priority <priority>] <prompt>
```

Each entry is a single business need — `/rfe.create` must produce exactly one RFE per invocation. Wait for all N agents to complete. You must have exactly N RFE IDs — if fewer were created, retry the missing entries. **Never delete or re-create task files during Phase 1** — quality issues are addressed in Phase 2 (Auto-fix).

**Mode B (Existing RFE)**: Skip Phase 1. The Jira key(s) from arguments become the processing list.

**Mode C (Single idea)**: Invoke `/rfe.create` with the user's input:

```
/rfe.create [--headless] <idea_text>
```

If not headless, `/rfe.create` will ask clarifying questions. Collect created RFE IDs.

After Phase 1 (all modes), persist the ID list to disk:

```bash
python3 scripts/state.py write-ids tmp/speedrun-all-ids.txt <all_IDs>
```

## Phase 2: Auto-fix

Re-read config and ID list from disk (in case context was compressed during Phase 1):

```bash
python3 scripts/state.py read tmp/speedrun-config.yaml
python3 scripts/state.py read-ids tmp/speedrun-all-ids.txt
```

Build the auto-fix command using flags from the config file:

```
/rfe.auto-fix [--headless] [--announce-complete] --batch-size <batch_size> <all_IDs_from_file>
```

Pass `--headless` and `--announce-complete` through if set in the config. **Always** pass `--batch-size <batch_size>` using the value from `tmp/speedrun-config.yaml` — never omit it, never let auto-fix's own default take over. The speedrun default (5) was already pinned in Step 0; relying on it here is what makes runs reproducible.

Auto-fix handles: assessment, feasibility checks, review, auto-revision, re-assessment, splitting oversized RFEs, retry queue, and report generation. Wait for it to complete. **Do NOT stop, summarize, or skip remaining batches early** — the pipeline must process every ID through all phases. Never emit a text-only response (no tool call) during pipeline execution — this terminates the CI process.

After auto-fix returns, verify all RFEs were processed:

```bash
python3 scripts/check_autofix_complete.py
```

If incomplete (exit code 1), the output shows `MISSING_IDS=RFE-006,RFE-007,...`. Re-invoke auto-fix with only the missing IDs:

```text
/rfe.auto-fix [--headless] [--batch-size N] <missing_IDs>
```

Repeat the verify+retry cycle until all RFEs have reviews or 3 retries have been exhausted.

## Phase 3: Submit

Re-read flags (in case context was compressed):

```bash
python3 scripts/state.py read tmp/speedrun-config.yaml
```

Re-read ID list from disk:

```bash
python3 scripts/state.py read-ids tmp/speedrun-all-ids.txt
```

Collect passing IDs:

```bash
python3 scripts/collect_recommendations.py <all_IDs_from_file>
```

Parse the `SUBMIT=` line for IDs ready to submit.

If no IDs are ready to submit, skip to Phase 4.

If IDs are ready:

```
/rfe.submit [--dry-run] [--headless] <passing_IDs>
```

If not headless: `/rfe.submit` will show a confirmation table before writing to Jira — this is the one mandatory interaction point.

If headless: pass `--headless` so submit skips confirmation.

## Phase 4: Summary

Re-read flags:

```bash
python3 scripts/state.py read tmp/speedrun-config.yaml
```

If headless, output a brief machine-readable summary. If interactive, output:

```
## Speedrun Complete

### Created
- RFE-NNN: <title> (Priority: Normal)

### Review Results
- Passed: N
- Failed: N
- Split: N (into M children)

### Submitted
- RHAIRFE-NNNN: <title> [created/updated/dry-run]

### Reports
- Run report: artifacts/auto-fix-runs/<timestamp>.yaml
- Review report: artifacts/auto-fix-runs/<timestamp>-report.html

### Remaining Issues
<Any RFEs that could not be auto-fixed, or "None">
```

$ARGUMENTS
