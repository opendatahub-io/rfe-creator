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

## Step 0.5: Bootstrap Dependencies

Run bootstrap early so agent definitions (e.g. `rfe-scorer`) are installed
in `.claude/agents/` before they're needed in Phase 2. The CREATE phase gives
the background agent rescan time to register them.

```bash
bash scripts/bootstrap-assess-rfe.sh
```

If bootstrap fails, retry once. If the retry also fails, continue — auto-fix
will attempt bootstrap again in its own setup step.

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

Validate the batch file before spending any agent budget on it. Use `--strict` so unknown fields and duplicate prompts (typically typos or copy-paste mistakes) block the run too, not just hard errors:

```bash
python3 scripts/validate_batch_input.py <input_file> --strict
```

If this exits nonzero, stop and report the printed `ERROR:`/`WARNING:` lines to the user instead of proceeding — do not fan out agents against a batch that's already known to be malformed.

Count entries and pre-allocate all IDs upfront:

```bash
python3 scripts/next_rfe_id.py --from-batch <input_file>   # input_file = the --input path; prints one RFE ID per entry
```

Persist the pre-allocated IDs before launching any agents — the Phase 1 barrier below reads this file:

```bash
python3 scripts/state.py write-ids tmp/speedrun-all-ids.txt <all_IDs>
```

For each entry, launch an Agent to invoke `/rfe.create`. Pass the pre-assigned ID so each Agent knows which ID to use:

```
Agent(prompt: "/rfe.create --headless --rfe-id RFE-001 [--priority <priority>] <prompt>")
Agent(prompt: "/rfe.create --headless --rfe-id RFE-002 [--priority <priority>] <prompt>")
...
Agent(prompt: "/rfe.create --headless --rfe-id RFE-<N> [--priority <priority>] <prompt>")
```

Each entry is a single business need — `/rfe.create` must produce exactly one RFE per invocation. Launch all N Agents in a single message so they run concurrently. Your next Bash call after that message MUST be the Phase 1 barrier — a blocking check that reads the task files on disk:

```bash
python3 scripts/check_review_progress.py --wait --phase create --id-file tmp/speedrun-all-ids.txt
```

Call it immediately, before any agent has reported. Do not count agent-completion notifications and do not track how many agents have finished — the barrier is the only completion signal for Phase 1.

Exit 0 means all N task files exist, each with parseable frontmatter carrying its own `rfe_id` — Phase 1 is done. Exit 3 is a timeout, not a failure; it prints the still-pending IDs. A file that is half-written, unparseable, or holding the wrong `rfe_id` counts as pending, so it times out rather than releasing the barrier. Re-run the command as long as that pending list keeps shrinking. If the same IDs stay pending across 3 consecutive exit-3 results, those agents are dead and re-running will never clear them — launch a replacement Agent for each still-pending ID, then resume the barrier. You must have exactly N RFE IDs before moving on. **Never delete or re-create task files during Phase 1** — quality issues are addressed in Phase 2 (Auto-fix).

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

Invoke auto-fix using the **Skill** tool (NOT Agent — Agent runs in background and causes the session to terminate). Build the args from the config file:

```
Skill(skill: "rfe.auto-fix", args: "--headless --announce-complete --batch-size <batch_size> <all_IDs_from_file>")
```

Pass `--headless` and `--announce-complete` through if set in the config. **Always** pass `--batch-size <batch_size>` using the value from `tmp/speedrun-config.yaml` — never omit it, never let auto-fix's own default take over. The speedrun default (5) was already pinned in Step 0; relying on it here is what makes runs reproducible.

Auto-fix handles: assessment, feasibility checks, review, auto-revision, re-assessment, splitting oversized RFEs, retry queue, and report generation. The Skill call blocks until auto-fix completes — this is correct. **Do NOT stop, summarize, or skip remaining batches early** — the pipeline must process every ID through all phases. Never end a turn with a text-only response (no tool call) in order to wait for something — that hands control back, and you only run again if an agent-completion notification wakes you.

**Bash discipline:** Issue exactly one operation per Bash call. Never use command substitution `$(...)` or chain commands with `;`, `&&`, or `||` — they trigger an approval prompt and are denied in headless mode. Instead, pass a value between commands by writing it to a `tmp/` file with `scripts/state.py` and reading it back in a separate call.

After auto-fix returns, verify all RFEs were processed:

```bash
python3 scripts/check_autofix_complete.py
```

If incomplete (exit code 1), the output shows `MISSING_IDS=RFE-006,RFE-007,...`. Re-invoke auto-fix with the Skill tool using only the missing IDs:

```
Skill(skill: "rfe.auto-fix", args: "--headless --batch-size <batch_size> <missing_IDs>")
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

If IDs are ready, invoke submit using the **Skill** tool:

```
Skill(skill: "rfe.submit", args: "--dry-run --headless <passing_IDs>")
```

Pass `--dry-run` and `--headless` through if set in the config. If not headless, `/rfe.submit` will show a confirmation table before writing to Jira — this is the one mandatory interaction point.

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
