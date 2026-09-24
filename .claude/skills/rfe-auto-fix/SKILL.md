---
name: rfe-auto-fix
description: Review and fix batches of work items automatically — RFEs by default, any registered type with --type (e.g. --type initiative). Accepts explicit IDs or a JQL query. Reviews, auto-revises, and splits oversized items. Non-interactive.
user-invocable: true
allowed-tools: Glob, Bash, Agent
---

You are a non-interactive work-item auto-fix pipeline. Do not ask questions or wait for confirmation. Make all decisions autonomously.

## Setup

Parse `$ARGUMENTS` for:
- `--type <t>` (an explicit type — always wins)
- `--jql "<query>"`, `--limit N`, `--batch-size N` (default 50), `--data-dir "<path>"`
- `--headless`, `--announce-complete`, `--reprocess`, `--random N`
- Remaining arguments: explicit item IDs

### 0. Resolve the type

Forward to `resolve` only `--type <t>` (when given), `--headless` (when given) and the explicit IDs — never `--jql`, `--data-dir`, `--limit`, `--batch-size`, `--announce-complete`, `--reprocess` or `--random`:

```bash
python3 scripts/type_registry.py resolve [--type <t>] [--headless] <IDs>
```

It prints one line, `TYPE RESOLVED: <type> (<how>)` — with no `--type` and no ids (JQL mode) a headless run resolves to the legacy default (`rfe`); the JQL's project is cross-checked against that type's binding by `snapshot_fetch.py` below. A non-zero exit is an error: stop. Then print the stage's launch block; every `{VAR}` in this skill is the value of that `VAR=` line:

```bash
python3 scripts/type_registry.py launch-vars <type> auto-fix
```

### 1. Init

```bash
python3 scripts/pipeline_state.py init {TYPE_FLAG} [--batch-size N] [--headless] [--announce-complete]
```

Pass `--headless` only when the caller passed it.

### 2. IDs

**JQL mode** (`--jql`):

```bash
python3 scripts/snapshot_fetch.py fetch "<query>" {TYPE_FLAG} --ids-file tmp/pipeline-all-ids.txt --changed-file tmp/pipeline-changed-ids.txt [--limit N] [--data-dir "<path>"] [--reprocess] [--random N]
```

Print `[AUTOFIX] JQL: <jql>` from stderr output. Pass `--reprocess` if set.

**Reprocess-only mode** (`--reprocess` without `--jql`):

```bash
python3 scripts/snapshot_fetch.py fetch --reprocess {TYPE_FLAG} --ids-file tmp/pipeline-all-ids.txt --changed-file tmp/pipeline-changed-ids.txt
```

**Explicit mode**:

```bash
python3 scripts/state.py write-ids tmp/pipeline-all-ids.txt <IDs>
python3 scripts/state.py write-ids tmp/pipeline-changed-ids.txt
```

If no IDs and no JQL and not `--reprocess`, stop with usage instructions.

### 3. Bootstrap

```bash
{BOOTSTRAP}
```

Retry once on failure. If retry fails, stop: "bootstrap failed."

### 4. Resume check + batch

```bash
python3 scripts/check_resume.py --ids-file tmp/pipeline-all-ids.txt --changed-file tmp/pipeline-changed-ids.txt --output-file tmp/pipeline-process-ids.txt
```

Read process IDs: `python3 scripts/state.py read-ids tmp/pipeline-process-ids.txt`

Split into batches of `batch_size`. Write each:

```bash
python3 scripts/state.py write-ids tmp/pipeline-batch-1-ids.txt <batch_1_IDs>
python3 scripts/state.py write-ids tmp/pipeline-batch-2-ids.txt <batch_2_IDs>
```

Start the pipeline:

```bash
python3 scripts/pipeline_state.py set total_batches=<M>
python3 scripts/pipeline_state.py set-phase BATCH_START
```

## Dispatch Loop

**CRITICAL: You MUST run the loop to completion.** Never stop early, skip batches, or produce a summary before all batches are processed. Context compression is handled automatically — old messages are compacted to free space. Budget and timeout are managed externally — do not estimate remaining resources or make preemptive exit decisions. If you are thinking "running low on context" or "given budget constraints" — ignore that impulse and continue the loop. The pipeline MUST reach phase `DONE` naturally.

**Bash discipline:** Issue exactly one operation per Bash call. Never use command substitution `$(...)` or chain commands with `;`, `&&`, or `||` — they trigger an approval prompt and are denied in headless mode, which can stall the pipeline. Instead, pass a value between commands by writing it to a `tmp/` file with `scripts/state.py` and reading it back in a separate call.

Repeat until phase is `DONE`:

### Step 1: Get next action

```bash
python3 scripts/pipeline_state.py next-action
```

Parse the YAML output for: `action`, `phase`, `message`, `agents`.

### Step 2: Execute

**done**: Exit loop. Run teardown.

**run_script**: Run `python3 scripts/pipeline_state.py run-phase`. Go to step 1.

**launch_wave**: For each agent in the `agents` list:
- Build prompt: `"<vars>\n\nRead <prompt_file> and follow all instructions exactly."`
- `vars` are pre-rendered KEY=VALUE lines — the type's launch block plus the per-agent values, with `{ID}` already substituted.
- Launch as background Agent (with `subagent_type` if present).

Then wait for completion:

```bash
python3 scripts/pipeline_state.py wait-for-wave
```

On exit 0 (complete): go to step 1.
On exit 3 (still pending): re-run `python3 scripts/pipeline_state.py wait-for-wave`.
Any other exit code is an error.

### Example `launch_wave` output

The values are the resolved type's (here `rfe`); the launch block lines are elided to `…`. A typed prompt file is the descriptor's relative path while the working directory carries it (the checkout, or a run directory linking it in) and an absolute path only when it does not (a marketplace install, a drop-in outside the checkout):

```yaml
action: launch_wave
phase: ASSESS
message: "ASSESS: wave 1/2 (5 IDs)"
agents:
  - subagent_type: rfe-scorer
    prompt_file: .claude/skills/rfe-review/prompts/assess-agent.md
    vars: |
      …
      KEY=RHAIRFE-1234
      DATA_FILE=tmp/rfe-assess/single/RHAIRFE-1234.md
      RUN_DIR=tmp/rfe-assess/single
      PROMPT_PATH=.context/assess-rfe/skills/assess-rfe/scripts/agent_prompt.md
  - prompt_file: types/rfe/dimensions/feasibility.md
    vars: |
      …
      ID=RHAIRFE-1234
```

## Teardown

After phase reaches `DONE`:

```bash
python3 scripts/batch_summary.py {TYPE_FLAG} --counts-only --ids-file tmp/pipeline-all-ids.txt
```

$ARGUMENTS
