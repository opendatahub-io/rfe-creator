# Thin Dispatcher + Prompt Files for rfe.auto-fix

## Context

During the `20260406-075052` run, context compression in the `rfe.auto-fix` orchestrator caused 36 revise agents to receive degraded instructions — they set `auto_revised=True` without modifying files. Root cause: inline Skill calls (`/rfe.review`, `/rfe.split`) accumulate their full orchestration output in the parent's context. By batch 2, compression fires and degrades agent launch instructions.

The chosen approach makes the orchestrator a **thin generic dispatcher** that knows almost nothing about what the agents do. Agent instructions live in prompt files on disk. Phase transitions use a **hybrid model**: linear sequences are an ordered array, conditional branches use decision scripts. The orchestrator's SKILL.md shrinks to ~80 lines.

## Design Invariants

### Invariant 1: Background-only agents in batch orchestrators

Pipeline/batch workflows (like `rfe.auto-fix`) must **only** launch background agents (`run_in_background: true`). The orchestrator never blocks on an agent or reads its return value. All results flow through files on disk, polled via `check_review_progress.py`.

**Exception**: Interactive workflows (like `/rfe.review` on a single RFE) may use foreground agents since they operate on narrow data — one record and its children/grandchildren at a time.

### Invariant 2: Orchestrator context isolation

The orchestrator's context contains **only its own process state**: phase, IDs, counters, and control-flow directives. No subtask or subagent content — no RFE bodies, review text, scores, or agent outputs — is ever inlined into the orchestrator's context.

**Inter-phase communication** happens exclusively through:
- Files on disk (ID files, frontmatter, artifacts)
- Scripts that return **machine-readable directives** (ID lists, counts, status codes, boolean flags)

### Invariant 3: Resumability at every phase boundary

The pipeline is fully resumable at any phase boundary. If the orchestrator crashes, loses context, or is manually stopped, a new session can pick up exactly where the previous one left off:

1. `get-phase` returns the current phase from disk — no context memory needed
2. `get-phase-config` returns what to do — identical output regardless of conversation history
3. The dispatch loop writes a poll file with all phase IDs and calls `check_review_progress.py` once before dispatching — only PENDING IDs are dispatched, completed ones are skipped. No per-phase result mapping needed; `check_review_progress.py` already knows what "done" means for each phase.
4. `advance` only fires after the barrier clears (all agents complete), so phase boundaries are always consistent — there is never a "half-advanced" state

This makes the pipeline robust to both crashes and context compression. Even if compression completely destroys the orchestrator's memory of what it was doing, the SKILL.md's generic dispatch loop + disk state is sufficient to continue. The LLM doesn't need to "remember" anything — it just reads the loop instructions and the disk tells it where it is.

### Invariant 4: Single entry point

Script phase execution is routed through `pipeline_state.py run-phase`, which resolves the command internally. `get-phase-config` does not emit the `command` or `ids_file` fields for script phases. This prevents the LLM from learning and independently invoking scripts outside the dispatch loop, particularly after context compaction degrades loop instructions.

Agent phases intentionally expose `pre_script` and `post_verify` commands because the orchestrator must execute them per-ID and post-barrier respectively. These are narrow, phase-specific hooks — not general script invocation.

### Invariant 5: Dispatch-before-advance

`advance` refuses to proceed unless dispatch is complete:

- **Script phases**: `run-phase` writes a dispatch marker (`tmp/.dispatch-marker`); `advance` checks the marker exists and matches the current phase. This prevents the LLM from skipping dispatch and hammering `advance` repeatedly after context compaction.
- **Agent phases**: `advance` calls `check_review_progress.check_id()` for the phase's `poll_phase` (and any parallel `poll_phase` entries) against all IDs in `ids_file`. If any ID is still pending, `advance` refuses and prints the exact `check_review_progress.py --poll` command to run. This catches the case where the LLM skips polling entirely or exits before agents complete — `advance` redirects it back to polling.

Noop phases are exempt. `advance --dry-run` bypasses both checks.

**Manual recovery operations** enabled by the state machine:
- `pipeline_state.py set-phase <PHASE>` — skip a stuck phase or re-run a failed one
- `pipeline_state.py advance --dry-run` — show what transition would happen given current disk state without making it (deterministic replay)
- `pipeline_state.py diagnose` — check invariants (e.g., "in SPLIT_REVIEW but no child IDs on disk" = corruption, not just stuck)

## Architecture

### The Dispatch Loop

The orchestrator is a generic loop (~20 lines of SKILL.md):

```
loop:
  phase = pipeline_state.py get-phase
  if phase == DONE: break

  config = pipeline_state.py get-phase-config   # → type, prompt_file, ids_file, ...
  if config.type == "agent":
    ids = state.py read-ids <ids_file>
    write poll file with all ids
    check_review_progress.py once → get PENDING ids only   # resumability
    while ids remain:
      wave = take next max_concurrent from ids
      for each id in wave: launch background Agent(...)
      poll with check_review_progress.py --poll until exit 0
    if config.post_verify:
      run config.post_verify   # writes error frontmatter, removes failed IDs from active set
  elif config.type == "script":
    pipeline_state.py run-phase              # resolves command internally
  # else: type == "noop" — pure decision point, no dispatch

  pipeline_state.py advance                      # → decision logic picks next phase
```

Three phase types:
- **`agent`** — fan-out background agents, poll until barrier clears. If `post_verify` is set, run post-barrier verification to detect agent failures.
- **`script`** — run a command synchronously (SETUP, FIXUP, ERROR_COLLECT, etc.)
- **`noop`** — pure decision point, no dispatch. The loop just calls `advance()`, which runs decision scripts internally and sets the next phase. Used by REASSESS_CHECK, BATCH_DONE, COLLECT, SPLIT_CORRECTION_CHECK.

The orchestrator **never reads prompt files** — the agents do. The orchestrator **never decides what's next** — `advance` does. And the loop is **resumable at every iteration** — disk state is the only source of truth.

### Post-Barrier Verification

Agent phases support two additional config fields for failure detection:

- **`post_verify`** (command): Runs after the barrier clears. Checks for expected output files per phase. For each ID where output is missing:
  1. Writes error frontmatter to the review file (`error=<phase>_failed`)
  2. Removes the ID from the active IDs file
  3. Prints `FAILED=ID1,ID2` for barrier summary logging

This replaces the LLM-driven Glob checks in the current SKILL.md orchestrator. The current system detects agent failures via Glob + manual frontmatter writes after each phase barrier (E1-E5 error templates). Plan A's `verify_phase.py` makes this deterministic — a script, not an LLM judgment call.

### Phased Barrier Model

Each phase is a synchronization barrier. Within a phase, all agents run concurrently. The orchestrator polls (`check_review_progress.py`) until every agent completes before advancing. Only one phase is active at any time.

```
SPLIT_ASSESS:
  dispatch → [A1] [A2] [B1] [B2] [C1] [C2]    fan-out
  poll: 0/6 → sleep → 3/6 → sleep → 6/6       barrier wait
  advance → SPLIT_REVIEW                        next phase
```

Items with different lifecycle lengths share the dispatch loop but enter different phase sequences:

- **Main pipeline** (all batch items): FETCH → ASSESS → REVIEW → REVISE → FIXUP → COLLECT
- **Split sub-pipeline** (split candidates): SPLIT → SPLIT_COLLECT → SPLIT_ASSESS → ... → SPLIT_CORRECTION_CHECK
- **Correction loop** (undersized children): cycles back through the split sub-pipeline (capped at 1)

ID files determine which items participate in each phase. Items that don't need splitting never enter SPLIT phases. Items whose children all pass never enter the correction loop.

**Tradeoff**: Fast items wait for slow siblings at each barrier. The throughput cost is minor — barriers add no re-work, and the bottleneck is agent execution time. The simplicity gain is significant: fan-in joins are trivial (the barrier IS the join), and the orchestrator needs no coordination logic.

### Phase Sequence (hybrid transitions)

Linear sequences are arrays. Conditional branches are in `pipeline_state.py advance`.

```
Setup (orchestrated by SKILL.md, not the state machine):
  init → snapshot fetch → bootstrap → resume check → set-phase BATCH_START

MAIN PIPELINE (per batch):
  BATCH_START → [FETCH, SETUP, ASSESS, REVIEW, REVISE, FIXUP]
  BATCH_START populates pipeline-active-ids.txt from the batch file
  REVIEW → REVISE transition runs filter_for_revision.py and writes pipeline-revise-ids.txt
    (REVISE dispatches only filtered IDs; empty list = no agents launched, advance immediately)
  FIXUP → REASSESS_CHECK
  → decision: reassess?
       yes (cycle < 2, reassess IDs exist) → [REASSESS_SAVE, REASSESS_ASSESS, REASSESS_REVIEW, REASSESS_RESTORE, REASSESS_REVISE, REASSESS_FIXUP] → back to REASSESS_CHECK
       no → COLLECT
  On the last cycle (cycle == max), REASSESS_RESTORE writes zero revise IDs so REASSESS_REVISE is a no-op.
  This ensures every revision is followed by a review — the last cycle only re-scores, it does not revise.

COLLECT:
  → decision: splits?
       yes → SPLIT → SPLIT_COLLECT
         → decision: children exist?
              yes → [SPLIT_PIPELINE_START, SPLIT_ASSESS, SPLIT_REVIEW, SPLIT_REVISE, SPLIT_FIXUP, SPLIT_SAVE, SPLIT_REASSESS, SPLIT_RE_REVIEW, SPLIT_RESTORE, SPLIT_CORRECTION_CHECK]
              no (all no-split or zero children from collector) → BATCH_DONE
       no → BATCH_DONE
  After SPLIT_REVISE + SPLIT_FIXUP, revised children (from pipeline-revise-ids.txt) are re-assessed and
  re-reviewed via SPLIT_SAVE/REASSESS/RE_REVIEW/RESTORE. Children that didn't need revision skip these
  phases (empty ID file = no agents launched). This ensures every revision is followed by a review.

All agent phases use max_concurrent waves (see below) to cap concurrency.

BATCH_DONE:
  Errors accumulate across batches; ERROR_COLLECT runs only after all batches complete.
  → decision: more batches?  (checked first — errors are deferred)
       yes → BATCH_START
       errors exist & retry_cycle < 1 → ERROR_COLLECT → BATCH_START (retry batch)
       no errors or retry_cycle >= 1 → REPORT → DONE
```

ERROR_COLLECT is a script phase (`scripts/error_collect.py`) that prepares error IDs for a clean retry. **This script must be idempotent** — a crash mid-ERROR_COLLECT must be recoverable by re-running the script. Step ordering is designed so a crash at any point either allows a safe re-run or at worst skips the retry (fail-safe), never infinite-loops.

1. **Set `retry_cycle = 1`** — done first to prevent infinite loops. If we crash after this but before writing the batch file, BATCH_DONE will see `retry_cycle >= 1` and go to REPORT (retry skipped, fail-safe). If we crash before this, re-running starts from scratch (safe).
2. **Collect error IDs** across all batches via `collect_recommendations.py --errors`. The failure phase is explicit in the review frontmatter `error` field (e.g., `error="fetch_failed: ..."`, `error="revise_failed: ..."`), set by the agent or script that detected the failure — not inferred from which artifacts exist.
3. **Save error history** to `tmp/pipeline-retry-errors.yaml` — error type, failure phase, and original error message per ID. Preserved for post-mortem even if the retry succeeds or a second failure overwrites.
4. **Persist retry IDs** to `tmp/pipeline-retry-ids.txt` (read by `generate_run_report.py` to identify which IDs were retried and whether they recovered — see REPORT phase data flow below)
5. **Artifact cleanup** — deletes or restores intermediate results so the dispatch loop's resumability skip filter doesn't no-op the retry:

   | Artifact | Action | Applies to | Why |
   |----------|--------|------------|-----|
   | `artifacts/rfe-tasks/<ID>.md` | Atomic restore from `rfe-originals/<ID>.md` with frontmatter | REVISE errors | Revise can leave a half-written/corrupted task file. Originals contain only the raw description (no frontmatter — see `fetch_issue.py`). Restore is atomic: (1) read current frontmatter via `frontmatter.py read`, (2) copy originals to a temp file, (3) set frontmatter on the temp file via `frontmatter.py set`, (4) `os.rename(tmp, task_path)`. Atomic rename avoids a crash window where the file has no frontmatter. Avoids re-fetching from Jira (wasted API call, divergence risk if issue edited since fetch). |
   | `artifacts/rfe-reviews/<ID>-review.md` | Delete | All error IDs | Skip filter checks this for REVIEW |
   | `artifacts/rfe-reviews/<ID>-feasibility.md` | Delete | All error IDs | Skip filter checks this for FEASIBILITY |
   | `/tmp/rfe-assess/single/<ID>.md` | Delete | All error IDs | Assessment input |
   | `/tmp/rfe-assess/single/<ID>.result.md` | Delete | All error IDs | Skip filter checks this for ASSESS |
   | `artifacts/rfe-reviews/<ID>-split-status.yaml` | Delete | split_failed IDs | Skip filter checks this for SPLIT |
   | `<ID>-removed-context.yaml` | Delete | REVISE errors | Stale data from failed revision (not a skip-filter trigger, but avoids confusing the retry) |
   | Child task/review/assess/feasibility files | Delete | split_failed IDs | Via `cleanup_partial_split.py` |

   **Never deleted**: `artifacts/rfe-tasks/<ID>.md` for non-REVISE errors (preserved — FETCH's skip filter sees the task file and skips, which is correct since the file is clean), `artifacts/rfe-originals/<ID>.md` (baseline copy, source of truth for restores and conflict detection), `artifacts/rfe-tasks/<ID>-comments.md` (inert Jira comment history, never modified locally). For REVISE errors, the task file is restored (not deleted) — FETCH also skips it, which is correct since the restored file has valid content.

   **Why this is necessary**: The dispatch loop's skip filter ("filter out IDs that already have results on disk") is designed for crash recovery. Without cleanup, an ID that failed at REVIEW would still have its assessment result on disk — ASSESS would skip it, and REVIEW would see the same stale inputs that caused the original failure.

6. **Post-cleanup verification** — for each retry ID, confirm that no skip-triggering artifacts remain on disk. Specifically: no `<ID>.result.md` in `/tmp/rfe-assess/single/`, no `<ID>-review.md` in `artifacts/rfe-reviews/`, no `<ID>-feasibility.md` in `artifacts/rfe-reviews/`. For REVISE errors, verify that the task file body matches the originals file (frontmatter may differ, body must match). If any check fails, log a warning and retry the delete/restore. This is the safety net against silent no-op retries.
7. **Write retry batch file** — uses guard to ensure idempotent total_batches increment:
   ```python
   retry_batch_file = f"tmp/pipeline-batch-{state['total_batches'] + 1}-ids.txt"
   if not os.path.exists(retry_batch_file):
       state["total_batches"] += 1
       write_ids(retry_batch_file, error_ids)
   ```
   (`batch` is incremented by `advance(BATCH_START)`, not here)

The retry batch flows through the **same** FETCH → SETUP → ASSESS → REVIEW → REVISE → FIXUP → reassess → COLLECT → split pipeline as any other batch. No special retry states needed.

**Tradeoff**: The retry-as-batch approach trades state duplication (22 fewer states) for cleanup correctness — ERROR_COLLECT must delete the right artifacts or retries silently no-op. The artifact cleanup contract above is the critical specification. SETUP also re-runs idempotent bootstrap scripts (~15s overhead), which is acceptable.

**REPORT phase data flow**: REPORT is a script phase that calls `generate_run_report.py --retry-errors-file tmp/pipeline-retry-errors.yaml`. The script reads `tmp/pipeline-retry-ids.txt` (if it exists) to identify which IDs were retried. For each retried ID, it reads the original error details (type, phase, message) from the retry-errors file and embeds them as an `original_error` field in the per-RFE entry in the run report YAML. This preserves what went wrong on the first attempt even when the retry succeeds and overwrites the review frontmatter — enabling debugging of recurring failure patterns across runs. The `tmp/pipeline-retry-errors.yaml` file is ephemeral; once embedded in the run report (`artifacts/auto-fix-runs/`), it can be cleaned up normally.

After generating the report, `advance(REPORT)` checks `state["announce_complete"]` and runs `finish.py` if set. This was previously only documented in the SKILL.md teardown section — now it's part of the `advance()` transition logic so it fires regardless of context state.

### Max Concurrent & Wave Dispatch

Each background agent is a separate Claude API call consuming rate limits and compute. A batch of 50 parents that all split into 3 children = 150 children. Launching 150 concurrent agents would overwhelm infrastructure.

**Solution**: All agent phases use `max_concurrent` (configurable, e.g., 10). When a phase has more IDs than `max_concurrent`, the dispatch loop launches them in **waves** — mini-barriers within a single phase:

```
SPLIT_ASSESS phase, 9 children, max_concurrent=3:
  Wave 1: launch [A1, A2, A3] → poll until 3/3 done
  Wave 2: launch [B1, B2, B3] → poll until 3/3 done
  Wave 3: launch [C1, C2, C3] → poll until 3/3 done
  advance → SPLIT_REVIEW
```

This applies to ALL agent phases (main pipeline and split sub-pipeline alike). The dispatch loop becomes: "while IDs remain: take next `max_concurrent`, launch, poll until done, repeat. Then advance."

**Phase config** includes `max_concurrent` per phase:
```yaml
type: agent
prompt: assess-agent.md
ids_file: tmp/pipeline-active-ids.txt
max_concurrent: 10
```

**Infrastructure impact**: Without wave dispatch, a split phase could spike to 150+ concurrent API requests — exceeding rate limits and degrading response times. Wave dispatch caps peak concurrency to a configurable limit across all phases. The fan-in join at SPLIT_CORRECTION_CHECK still works — all waves within the phase complete before advancing, so all children's results are on disk when the correction script runs.

### Phase Config

`pipeline_state.py get-phase-config` returns a structured response for the current phase:

```yaml
# Agent phase example:
type: agent
prompt: .claude/skills/rfe.review/prompts/assess-agent.md
ids_file: tmp/pipeline-active-ids.txt
subagent_type: rfe-scorer
poll_phase: assess
post_verify: "python3 scripts/verify_phase.py --phase assess --ids-file tmp/pipeline-active-ids.txt"
vars:
  DATA_FILE: "/tmp/rfe-assess/single/{ID}.md"
  RUN_DIR: "/tmp/rfe-assess/single"
  PROMPT_PATH: ".context/assess-rfe/scripts/agent_prompt.md"

# Script phase example (command and ids_file are internal-only, not emitted):
type: script
```

The `command` field is **internal-only** — it is stored in `PHASE_CONFIG` but not emitted by `get-phase-config`. The orchestrator calls `pipeline_state.py run-phase`, which resolves the command, applies `format_map(state)` for variable substitution (e.g., `{start_time}` in REPORT), appends IDs from `ids_file`, and executes the script. This prevents the LLM from learning script names and invoking them directly after context compaction (see Invariant 4).

This config is **encoded in `pipeline_state.py`** (a Python dict/dataclass), not in the SKILL.md. The orchestrator never sees the contents of prompt files, the meaning of variables, or the underlying script commands.

Agent `vars` are NOT substituted server-side — the orchestrator replaces `{ID}` per-agent at dispatch time.

## What Changes

All changes are in the **rfe-creator** repo (`/Users/jason/devel/rfe-creator/`).

| File | Action |
|------|--------|
| `scripts/pipeline_state.py` | **New** (~820 lines) — phase tracking, config, transition logic, dispatch-context, diagnostics |
| `scripts/error_collect.py` | **New** (~60 lines) — artifact cleanup + retry batch creation |
| `scripts/verify_phase.py` | **New** (~50 lines) — post-barrier error detection + frontmatter writes |
| `scripts/reassess_save.py` | **New** (~30 lines) — save review state + delete stale files for reassess |
| `scripts/split_collect.py` | **New** (~40 lines) — R8/R8a routing + collect_children.py |
| `scripts/check_right_sized.py` | **New** (~30 lines) — returns undersized child IDs |
| `scripts/collect_recommendations.py` | **Modify** — add `--errors` flag |
| `scripts/check_review_progress.py` | **Modify** — fix score=0 falsiness bug, add split-recommended revise completion |
| `scripts/cleanup_partial_split.py` | **Modify** — extend to also delete child feasibility files (`<child>-feasibility.md`) and child assessment files (`/tmp/rfe-assess/single/<child>.md`, `<child>.result.md`) |
| `scripts/generate_run_report.py` | **Modify** — add `--retry-errors-file` flag, embed `original_error` in per-RFE report entries |
| `scripts/batch_summary.py` | **Modify** — add `--counts-only` flag |
| `.claude/skills/rfe.auto-fix/SKILL.md` | **Rewrite** (~80 lines) — thin generic dispatcher |
| `.claude/settings.json` | **Add** permissions |

Unchanged: all existing prompt templates (`rfe.review/prompts/*.md`, `rfe.split/prompts/*.md`, `rfe-feasibility-review/SKILL.md`), all existing scripts, `rfe.review/SKILL.md` and `rfe.split/SKILL.md` (kept for interactive use).

## 1. `scripts/pipeline_state.py`

Combines three responsibilities:
- **Phase tracking**: `get-phase`, `set-phase` (validates against enum)
- **Phase config**: `get-phase-config` → returns type, prompt file, ids file, vars, poll phase
- **Transition logic**: `advance` → reads current phase + results, sets next phase

### Phase enum

```
BATCH_START, FETCH, SETUP, ASSESS, REVIEW, REVISE, FIXUP,
REASSESS_CHECK, REASSESS_SAVE, REASSESS_ASSESS, REASSESS_REVIEW,
  REASSESS_RESTORE, REASSESS_REVISE, REASSESS_FIXUP,
COLLECT, SPLIT, SPLIT_COLLECT,
  SPLIT_PIPELINE_START, SPLIT_ASSESS, SPLIT_REVIEW, SPLIT_REVISE, SPLIT_FIXUP,
  SPLIT_SAVE, SPLIT_REASSESS, SPLIT_RE_REVIEW, SPLIT_RESTORE,
  SPLIT_CORRECTION_CHECK,
BATCH_DONE, ERROR_COLLECT,
REPORT, DONE
```

### Phase config map (Python dict in `pipeline_state.py`)

Complete config for all phases. Phases not listed below use `{"type": "noop"}` (decision points handled by `advance()`).

```python
PHASE_CONFIG = {
    "BATCH_START": {"type": "noop"},  # advance() resets counters + populates active IDs
    "FETCH": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/fetch-agent.md",
        "ids_file": "tmp/pipeline-active-ids.txt",
        "poll_phase": "fetch",
        "post_verify": "python3 scripts/verify_phase.py --phase fetch --ids-file tmp/pipeline-active-ids.txt",
        "vars": {"KEY": "{ID}"}
    },
    "SETUP": {
        "type": "script",
        "command": "bash scripts/bootstrap-assess-rfe.sh & bash scripts/fetch-architecture-context.sh & wait"
    },
    "ASSESS": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/assess-agent.md",
        "ids_file": "tmp/pipeline-active-ids.txt",
        "subagent_type": "rfe-scorer",
        "poll_phase": "assess",
        "parallel": [
            {"prompt": ".claude/skills/rfe-feasibility-review/SKILL.md", "poll_phase": "feasibility"}
        ],
        "pre_script": "python3 scripts/prep_assess.py {ID}",
        "post_verify": "python3 scripts/verify_phase.py --phase assess --ids-file tmp/pipeline-active-ids.txt",
        "vars": { ... }
    },
    "REVIEW": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/review-agent.md",
        "ids_file": "tmp/pipeline-active-ids.txt",
        "poll_phase": "review",
        "post_verify": "python3 scripts/verify_phase.py --phase review --ids-file tmp/pipeline-active-ids.txt",
        "vars": {"FIRST_PASS": "true", ...}
    },
    "REVISE": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/revise-agent.md",
        "ids_file": "tmp/pipeline-revise-ids.txt",
        "poll_phase": "revise",
        "vars": {"ID": "{ID}"}
    },
    "FIXUP": {
        "type": "script",
        "command": "python3 scripts/check_revised.py --batch",
        "ids_file": "tmp/pipeline-revise-ids.txt"
    },

    # --- Reassess loop ---
    "REASSESS_CHECK": {"type": "noop"},  # advance() runs collect_recommendations.py --reassess
    "REASSESS_SAVE": {
        "type": "script",
        "command": "python3 scripts/reassess_save.py",
        "ids_file": "tmp/pipeline-reassess-ids.txt"
    },
    "REASSESS_ASSESS": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/assess-agent.md",
        "ids_file": "tmp/pipeline-reassess-ids.txt",
        "subagent_type": "rfe-scorer",
        "poll_phase": "assess",
        "pre_script": "python3 scripts/prep_assess.py {ID}",
        # NO "parallel" — feasibility intentionally NOT re-checked (invariant 4.2/5.4)
        "post_verify": "python3 scripts/verify_phase.py --phase assess --ids-file tmp/pipeline-reassess-ids.txt",
        "vars": { ... }
    },
    "REASSESS_REVIEW": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/review-agent.md",
        "ids_file": "tmp/pipeline-reassess-ids.txt",
        "poll_phase": "review",
        "post_verify": "python3 scripts/verify_phase.py --phase review --ids-file tmp/pipeline-reassess-ids.txt",
        "vars": {
            "FIRST_PASS": "false",  # Critical: prevents before_score/before_scores overwrite
            ...
        }
    },
    "REASSESS_RESTORE": {
        "type": "script",
        "command": "python3 scripts/preserve_review_state.py restore",
        "ids_file": "tmp/pipeline-reassess-ids.txt"
    },
    "REASSESS_REVISE": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/revise-agent.md",
        "ids_file": "tmp/pipeline-revise-ids.txt",  # filtered by advance(REASSESS_RESTORE)
        "poll_phase": "revise",
        "vars": {"ID": "{ID}"}
    },
    "REASSESS_FIXUP": {
        "type": "script",
        "command": "python3 scripts/check_revised.py --batch",
        "ids_file": "tmp/pipeline-revise-ids.txt"
    },

    # --- Collect + Split ---
    "COLLECT": {"type": "noop"},  # advance() runs collect_recommendations.py
    "SPLIT": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.split/prompts/split-agent.md",
        "ids_file": "tmp/pipeline-split-ids.txt",
        "poll_phase": "split",
        "vars": {"ID": "{ID}", ...}
    },
    "SPLIT_COLLECT": {
        "type": "script",
        "command": "python3 scripts/split_collect.py",
        "ids_file": "tmp/pipeline-split-ids.txt"
    },
    "SPLIT_PIPELINE_START": {"type": "noop"},  # sync marker between SPLIT_COLLECT decision and SPLIT_ASSESS
    "SPLIT_ASSESS": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/assess-agent.md",
        "ids_file": "tmp/pipeline-split-children-ids.txt",
        "subagent_type": "rfe-scorer",
        "poll_phase": "assess",
        "pre_script": "python3 scripts/prep_assess.py {ID}",
        "parallel": [
            {"prompt": ".claude/skills/rfe-feasibility-review/SKILL.md", "poll_phase": "feasibility"}
        ],
        "post_verify": "python3 scripts/verify_phase.py --phase assess --ids-file tmp/pipeline-split-children-ids.txt",
        "vars": { ... }
    },
    "SPLIT_REVIEW": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/review-agent.md",
        "ids_file": "tmp/pipeline-split-children-ids.txt",
        "poll_phase": "review",
        "post_verify": "python3 scripts/verify_phase.py --phase review --ids-file tmp/pipeline-split-children-ids.txt",
        "vars": {"FIRST_PASS": "true", ...}
    },
    "SPLIT_REVISE": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/revise-agent.md",
        "ids_file": "tmp/pipeline-revise-ids.txt",  # filtered by advance(SPLIT_REVIEW)
        "poll_phase": "revise",
        "vars": {"ID": "{ID}"}
    },
    "SPLIT_FIXUP": {
        "type": "script",
        "command": "python3 scripts/check_revised.py --batch",
        "ids_file": "tmp/pipeline-revise-ids.txt"
    },
    "SPLIT_SAVE": {
        "type": "script",
        "command": "python3 scripts/preserve_review_state.py save",
        "ids_file": "tmp/pipeline-revise-ids.txt"
    },
    "SPLIT_REASSESS": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/assess-agent.md",
        "ids_file": "tmp/pipeline-revise-ids.txt",
        "subagent_type": "rfe-scorer",
        "poll_phase": "assess",
        "pre_script": "python3 scripts/prep_assess.py {ID}",
        # NO "parallel" — feasibility NOT re-checked for revised children
        "post_verify": "python3 scripts/verify_phase.py --phase assess --ids-file tmp/pipeline-revise-ids.txt",
        "vars": { ... }
    },
    "SPLIT_RE_REVIEW": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/review-agent.md",
        "ids_file": "tmp/pipeline-revise-ids.txt",
        "poll_phase": "review",
        "post_verify": "python3 scripts/verify_phase.py --phase review --ids-file tmp/pipeline-revise-ids.txt",
        "vars": {"FIRST_PASS": "false", ...}
    },
    "SPLIT_RESTORE": {
        "type": "script",
        "command": "python3 scripts/preserve_review_state.py restore",
        "ids_file": "tmp/pipeline-revise-ids.txt"
    },
    "SPLIT_CORRECTION_CHECK": {"type": "noop"},  # advance() runs check_right_sized.py

    # --- Batch control + retry ---
    "BATCH_DONE": {"type": "noop"},
    "ERROR_COLLECT": {
        "type": "script",
        "command": "python3 scripts/error_collect.py"
    },

    # --- Terminal ---
    "REPORT": {
        "type": "script",
        "command": "python3 scripts/generate_run_report.py --start-time {start_time} --batch-size {batch_size}"
    },
}
```

### Transition logic (`advance` command)

```python
def advance(current_phase, state):
    # BATCH_START increments the batch counter, resets per-batch counters,
    # and populates pipeline-active-ids.txt from the batch file
    if current_phase == "BATCH_START":
        state["batch"] += 1
        state["reassess_cycle"] = 0
        state["correction_cycle"] = 0
        batch_file = f"tmp/pipeline-batch-{state['batch']}-ids.txt"
        copy_ids(batch_file, "tmp/pipeline-active-ids.txt")
        return "FETCH"

    # Filter before REVISE phases — run filter_for_revision.py to determine
    # which IDs actually need revision, write the filtered list to the revise
    # ID file. Handled before the linear sequence loop so they intercept the
    # transition that the loop would otherwise make automatically.
    if current_phase == "REVIEW":
        active_ids = read_ids("tmp/pipeline-active-ids.txt")
        revise_ids = run(f"filter_for_revision.py {' '.join(active_ids)}")
        write_ids("tmp/pipeline-revise-ids.txt", revise_ids)
        return "REVISE"

    if current_phase == "REASSESS_RESTORE":
        if state["reassess_cycle"] >= 2:
            # Last cycle: skip revise to avoid unreviewed changes
            write_ids("tmp/pipeline-revise-ids.txt", [])
        else:
            reassess_ids = read_ids("tmp/pipeline-reassess-ids.txt")
            revise_ids = run(f"filter_for_revision.py {' '.join(reassess_ids)}")
            write_ids("tmp/pipeline-revise-ids.txt", revise_ids)
        return "REASSESS_REVISE"

    if current_phase == "SPLIT_REVIEW":
        child_ids = read_ids("tmp/pipeline-split-children-ids.txt")
        revise_ids = run(f"filter_for_revision.py {' '.join(child_ids)}")
        write_ids("tmp/pipeline-revise-ids.txt", revise_ids)
        return "SPLIT_REVISE"

    # Linear sequences — advance to next element. Last element of each sequence
    # (FIXUP, REASSESS_FIXUP, SPLIT_CORRECTION_CHECK) intentionally falls through
    # seq[:-1] to reach explicit decision handlers below. REVIEW, REASSESS_RESTORE,
    # and SPLIT_REVIEW are also in these sequences but are intercepted above.
    MAIN_SEQUENCE = ["FETCH", "SETUP", "ASSESS", "REVIEW", "REVISE", "FIXUP"]
    REASSESS_SEQUENCE = ["REASSESS_SAVE", "REASSESS_ASSESS", "REASSESS_REVIEW",
                         "REASSESS_RESTORE", "REASSESS_REVISE", "REASSESS_FIXUP"]
    SPLIT_SEQUENCE = ["SPLIT_PIPELINE_START", "SPLIT_ASSESS",
                      "SPLIT_REVIEW", "SPLIT_REVISE", "SPLIT_FIXUP",
                      "SPLIT_SAVE", "SPLIT_REASSESS", "SPLIT_RE_REVIEW",
                      "SPLIT_RESTORE", "SPLIT_CORRECTION_CHECK"]

    for seq in [MAIN_SEQUENCE, REASSESS_SEQUENCE, SPLIT_SEQUENCE]:
        if current_phase in seq[:-1]:
            return seq[seq.index(current_phase) + 1]

    # Decision points — reassess loop
    if current_phase == "FIXUP":
        return "REASSESS_CHECK"

    if current_phase == "REASSESS_CHECK":
        active_ids = read_ids("tmp/pipeline-active-ids.txt")
        reassess_ids = run(f"collect_recommendations.py --reassess {' '.join(active_ids)}")
        cycle = state["reassess_cycle"]
        if reassess_ids and cycle < 2:
            state["reassess_cycle"] = cycle + 1
            write_ids("tmp/pipeline-reassess-ids.txt", reassess_ids)
            return "REASSESS_SAVE"
        return "COLLECT"

    if current_phase == "REASSESS_FIXUP":
        return "REASSESS_CHECK"  # loops back; CHECK re-evaluates cycle

    # Decision points — collect and split
    if current_phase == "COLLECT":
        active_ids = read_ids("tmp/pipeline-active-ids.txt")
        result = run(f"collect_recommendations.py {' '.join(active_ids)}")
        split_ids = parse_split_ids(result)
        if split_ids:
            write_ids("tmp/pipeline-split-ids.txt", split_ids)
            return "SPLIT"
        return "BATCH_DONE"

    if current_phase == "SPLIT":
        return "SPLIT_COLLECT"

    if current_phase == "SPLIT_COLLECT":
        child_ids = read_ids("tmp/pipeline-split-children-ids.txt")
        if not child_ids:
            return "BATCH_DONE"  # all no-split or zero children from collector
        return "SPLIT_PIPELINE_START"

    if current_phase == "SPLIT_CORRECTION_CHECK":
        child_ids = read_ids("tmp/pipeline-split-children-ids.txt")
        undersized = run(f"check_right_sized.py {' '.join(child_ids)}")
        if undersized and state["correction_cycle"] < 1:
            state["correction_cycle"] += 1
            write_ids("tmp/pipeline-split-ids.txt", undersized)  # narrow to undersized only
            return "SPLIT"
        return "BATCH_DONE"

    # Decision points — batch control and retry
    if current_phase == "BATCH_DONE":
        if state["batch"] < state["total_batches"]:
            return "BATCH_START"
        if state["retry_cycle"] < 1:
            all_ids = read_ids("tmp/pipeline-all-ids.txt")
            error_ids = run(f"collect_recommendations.py --errors {' '.join(all_ids)}")
            if error_ids:
                return "ERROR_COLLECT"
        return "REPORT"

    if current_phase == "ERROR_COLLECT":
        # Script has already: cleaned artifacts, written new batch file,
        # incremented total_batches, set retry_cycle=1
        return "BATCH_START"

    # Terminal — REPORT runs generate_run_report.py (dispatched by loop),
    # then advance handles announce_complete
    if current_phase == "REPORT":
        if state.get("announce_complete"):
            run("python3 scripts/finish.py")
        return "DONE"
```

### CLI

```bash
python3 scripts/pipeline_state.py init --batch-size 5 --headless --announce-complete
python3 scripts/pipeline_state.py get-phase              # → "ASSESS"
python3 scripts/pipeline_state.py set-phase REVIEW
python3 scripts/pipeline_state.py get-phase-config        # → YAML with type, prompt, ids_file, vars
python3 scripts/pipeline_state.py advance                 # → runs decision logic, sets next phase, prints it
python3 scripts/pipeline_state.py advance --dry-run       # → show transition without applying
python3 scripts/pipeline_state.py set key=value ...
python3 scripts/pipeline_state.py get <key>
python3 scripts/pipeline_state.py status                  # → full state YAML
python3 scripts/pipeline_state.py diagnose                # → cross-reference state with disk artifacts
python3 scripts/pipeline_state.py dispatch-context        # → print phase + dispatch instructions (for manual recovery)
python3 scripts/pipeline_state.py post-compact-hook       # → dispatch-context gated behind env var (for SessionStart hook)
```

### Observability

The orchestrator is domain-ignorant, but the scripts it calls are domain-aware. Three observability layers, all zero or near-zero marginal context cost:

**1. Agent spawn & poll output** (already in context — zero marginal cost)

Agent tool results and `check_review_progress.py` poll output already enter the context as part of normal dispatch. Per-wave agent counts, completion progress, and error counts are visible. No changes needed.

**2. Barrier summaries from `advance`** (~2-3 lines per phase transition)

`advance` runs a phase-appropriate summary before computing the next phase. Prints aggregate stats for the completed phase plus the transition decision:

```
ASSESS complete: scored=10 avg_score=3.2 below_threshold=3
ASSESS → REVIEW

REVIEW complete: revision_needed=6 split_recommended=2 passed=2
REVIEW → REVISE

REVISE complete: revised=5 unchanged=1
REVISE → FIXUP

FIXUP → REASSESS_CHECK: reassess=3 cycle=1/2 reason="IDs need reassessment, cycle limit not reached"

COLLECT complete: submit=7 split=2 errors=1
COLLECT → SPLIT: splits=2 reason="split-recommended RFEs found"

SPLIT complete: parents=2 children_created=6
SPLIT_REVIEW complete: children_passed=4 undersized=2
SPLIT_CORRECTION_CHECK → SPLIT: undersized=2 correction=0/1 reason="undersized children need re-split"

Batch 2/3 complete: submit=38 revise=9 split=3 errors=0
BATCH_DONE → BATCH_START: batch=3/3 reason="more batches remain"

Batch 3/3 complete: submit=45 revise=2 split=0 errors=3
BATCH_DONE → ERROR_COLLECT: errors=3 retry_cycle=0/1 reason="error IDs found, retry not yet attempted"

ERROR_COLLECT: retry batch 4 with 3 error IDs [RHAIRFE-1501, RHAIRFE-1522, RHAIRFE-1540]
  RHAIRFE-1501: review_failed (original error preserved)
  RHAIRFE-1522: revise_failed → task file restored from original
  RHAIRFE-1540: split_failed → partial children cleaned
  cleanup verified: 0 stale artifacts remain
ERROR_COLLECT → BATCH_START: batch=4/4

Retry batch 4/4 complete: submit=2 revise=0 split=0 errors=1
BATCH_DONE → REPORT: retry_cycle=1 reason="retry already attempted, reporting final state"
```

Stats come from existing scripts (`batch_summary.py`, `collect_recommendations.py`, `check_right_sized.py`) — `advance` just calls them and prints the counts before deciding the transition. The CI monitor (rfe-autofixer) can be adapted to parse this output for its TUI. The orchestrator doesn't interpret the stats — they're opaque script output that passes through the context for human and CI monitor consumption.

**Retry batch labeling**: `advance` checks `state["retry_cycle"] > 0` when formatting BATCH_DONE summaries. ERROR_COLLECT sets `retry_cycle = 1` before advancing to BATCH_START, so all barrier summaries within the retry batch use the "Retry batch N/N" prefix instead of "Batch N/N". This is a formatting convention in `advance` output — the dispatch loop itself is unaware.

**3. Post-mortem `diagnose` command** (zero context cost — never called during execution)

```bash
python3 scripts/pipeline_state.py diagnose
```

Reads `tmp/pipeline-state.yaml` and all ID files, cross-references with artifact state on disk (which files exist, which have errors in frontmatter). If `tmp/pipeline-retry-errors.yaml` exists, includes original error details for retried IDs and whether they recovered. Also detects silent no-op retries: if retry IDs still have stale artifacts on disk, flags them as "retry would be skipped by skip filter." Prints current phase, pending IDs, missing artifacts, error states. For debugging stuck or failed pipelines after the fact. ~50 lines in `pipeline_state.py`.

### State file (`tmp/pipeline-state.yaml`)

```yaml
phase: ASSESS
batch: 1            # incremented by advance(BATCH_START), starts at 0
total_batches: 3
headless: true
announce_complete: true
batch_size: 5
start_time: 2026-04-06T12:00:00Z
reassess_cycle: 0
correction_cycle: 0
retry_cycle: 0
```

## 2. `scripts/verify_phase.py`

```bash
python3 scripts/verify_phase.py --phase assess --ids-file tmp/pipeline-active-ids.txt
# stdout: FAILED=RHAIRFE-1501,RHAIRFE-1522
# (empty FAILED= if all passed)
```

Per-phase verification that checks for expected output files after the polling barrier clears (or times out). Uses the same path mapping as `check_review_progress.py`:

| Phase | Expected output |
|-------|-----------------|
| `fetch` | `artifacts/rfe-tasks/{ID}.md` |
| `assess` | `/tmp/rfe-assess/single/{ID}.result.md` |
| `feasibility` | `artifacts/rfe-reviews/{ID}-feasibility.md` |
| `review` | `artifacts/rfe-reviews/{ID}-review.md` (with `score` set) |
| `split` | `artifacts/rfe-reviews/{ID}-split-status.yaml` |

For each ID where the expected output is missing:
1. Writes error frontmatter via `frontmatter.py set` — for fetch failures, writes the full E1 error template; for other phases, sets `error=<phase>_failed`
2. Removes the ID from the active IDs file (so downstream phases skip it)
3. Collects the ID for the `FAILED=` summary line

~50 lines. This replaces the LLM-driven Glob checks in the current SKILL.md.

## 3. `scripts/reassess_save.py`

```bash
python3 scripts/reassess_save.py
# Reads IDs from tmp/pipeline-reassess-ids.txt
```

Wraps two operations that must happen together before reassessment:
1. `preserve_review_state.py save <ids>` — saves `before_score`/`before_scores` for delta tracking
2. Deletes stale files for each reassess ID:
   - `artifacts/rfe-reviews/{ID}-review.md`
   - `/tmp/rfe-assess/single/{ID}.result.md`

**File deletion is load-bearing**: Without it, the dispatch loop's pre-dispatch check sees existing results, reports all IDs as COMPLETED, launches zero agents, and `advance` fires immediately — a silent no-op reassess cycle.

**Not deleted**: `artifacts/rfe-reviews/{ID}-feasibility.md` — feasibility is intentionally NOT re-checked during reassess (invariant 4.2/5.4). The existing feasibility result is reused across reassess cycles.

~30 lines.

## 4. `scripts/split_collect.py`

```bash
python3 scripts/split_collect.py
# Reads IDs from tmp/pipeline-split-ids.txt
# stdout: CHILDREN=6
```

Post-SPLIT-agent collection that routes parent IDs and gathers child IDs:

1. Reads `artifacts/rfe-reviews/{ID}-split-status.yaml` for each parent ID
2. For `action=no-split`: sets `recommendation=revise` via `frontmatter.py` (transition R8 in state machine)
3. For `action=split` with children: runs `collect_children.py`, collects child IDs
4. For `action=split` but zero children from collector: sets `recommendation=revise` (transition R8a — collector found no valid children)
5. Writes all child IDs to `tmp/pipeline-split-children-ids.txt` (empty file if zero children)
6. Prints `CHILDREN=<count>` for barrier summary

The `advance(SPLIT_COLLECT)` handler then reads the child IDs file to decide: children exist → SPLIT_PIPELINE_START, no children → BATCH_DONE.

~40 lines.

## 5. `scripts/check_right_sized.py`

```bash
python3 scripts/check_right_sized.py ID1 ID2 ID3
# stdout: RESPLIT=ID1 ID3
# (undersized IDs only; empty RESPLIT= if all pass)
```

Per-parent-aware correction check. For each ID, reads `artifacts/rfe-reviews/<ID>-review.md` frontmatter, groups children by `parent_key`, and checks `scores.right_sized < 2`. Returns undersized IDs as a machine-readable directive — the orchestrator never reads assessment content, only the ID list.

This script is the fan-in join point for the split correction loop: all children's assessments complete (barrier), then this script aggregates results across parents and returns just the IDs needing re-split. ~40 lines.

## 6. `collect_recommendations.py --errors`

Add `--errors` flag. Takes IDs as positional args (scopes which IDs to check). Reads review frontmatter for each ID and returns those with a non-null `error` field. Output:

```bash
python3 scripts/collect_recommendations.py --errors RHAIRFE-1501 RHAIRFE-1522 RHAIRFE-1540
# stdout: ERRORS=RHAIRFE-1501,RHAIRFE-1522
# (empty ERRORS= if none have errors)
```

The default mode (no flag) already groups IDs by recommendation and includes an `ERRORS=` line. The `--errors` flag makes it explicit: only return error IDs, accept an ID list to scope the check, and skip the recommendation grouping. Used by `advance(BATCH_DONE)` to decide whether to enter ERROR_COLLECT, and by `error_collect.py` to collect the full set.

## 7. `cleanup_partial_split.py` extension

Currently deletes: child task files, companion files (`-comments.md`, `-removed-context.md`), child review files, and parent split-status.yaml. Also restores the parent from Archived to Ready.

Extend to also delete:

| Additional file | Path |
|-----------------|------|
| Child feasibility reviews | `artifacts/rfe-reviews/<child>-feasibility.md` |
| Child assessment input | `/tmp/rfe-assess/single/<child>.md` |
| Child assessment result | `/tmp/rfe-assess/single/<child>.result.md` |

Without this, ERROR_COLLECT's artifact cleanup for `split_failed` IDs would leave stale child assessment/feasibility files on disk, and a retry would skip those children's assessment phase (the resumability skip filter sees existing results).

## 8. `batch_summary.py --counts-only`

Add `--counts-only` flag. When set, only prints the `TOTAL=X PASSED=Y ...` counts line. Default unchanged.

## 9. Rewrite `rfe.auto-fix/SKILL.md` (~80 lines)

### Frontmatter
```yaml
allowed-tools: Glob, Bash, Agent    # was: Glob, Bash, Skill
```

### Structure

```markdown
## Setup (one-time, not dispatched)
1. Parse $ARGUMENTS
2. pipeline_state.py init
3. Run snapshot_fetch.py or write explicit IDs
4. bootstrap-assess-rfe.sh (retry once)
5. check_resume.py, split into batches
6. pipeline_state.py set-phase BATCH_START

## Dispatch Loop
Repeat until phase == DONE:

1. Run: pipeline_state.py get-phase-config
2. If type == "script": run the command
3. If type == "agent":
   - Read IDs from ids_file
   - While IDs remain, take next max_concurrent:
     - For each ID in wave: launch background Agent with:
       "Read <prompt_file> and follow all instructions.
        Substitute: {ID}=<id>, {VAR1}=<val1>, ..."
     - Write poll file, poll with check_review_progress.py until wave done
   - If post_verify: run post_verify script
4. Run: pipeline_state.py advance
5. Loop

## Teardown (after DONE)
1. batch_summary.py --counts-only on all IDs
```

That's it. ~80 lines. The orchestrator doesn't know what ASSESS means, what REVISE does, or how splits work. It just dispatches and advances. Teardown is minimal — `advance(REPORT)` handles `generate_run_report.py` (dispatched as a script phase) and `finish.py` (called conditionally from `advance`).

## 10. Settings change

### Permissions

```json
"Bash(python3 scripts/pipeline_state.py *)",
"Bash(python3 scripts/error_collect.py *)",
"Bash(python3 scripts/verify_phase.py *)",
"Bash(python3 scripts/reassess_save.py *)",
"Bash(python3 scripts/split_collect.py *)",
"Bash(python3 scripts/check_right_sized.py *)"
```

### Post-compaction context recovery hook

A SessionStart hook fires after context compaction to inject the current pipeline state back into the conversation. This is gated behind the `RFE_CREATOR_ENABLE_CONTEXT_HOOK` environment variable so it's a no-op outside pipeline runs.

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "compact",
        "hooks": [
          {
            "type": "command",
            "command": "python3 scripts/pipeline_state.py post-compact-hook"
          }
        ]
      }
    ]
  }
}
```

`post-compact-hook` checks `RFE_CREATOR_ENABLE_CONTEXT_HOOK` and, if set, calls `dispatch-context` which prints the current phase, batch progress, and a dispatch protocol summary — enough for the LLM to resume the dispatch loop without any conversation history. The `DISPATCH_PROTOCOL` dict in `pipeline_state.py` encodes the three dispatch patterns (noop, script, agent) as concise text instructions.

## Polling Bug Fixes

Two bugs in `check_review_progress.py` that cause polling hangs:

### score=0 falsiness (line 36)

Current: `if not data.get("score")` returns `"pending"` when `score=0` (Python falsiness). The error check on line 38 (`if data.get("error")`) is unreachable for score=0.

Fix: Change to `if data.get("score") is None`. Practical risk is near-zero (score=0 means the RFE is completely non-viable and would never pass review thresholds), but correctness matters for the error-routing path.

### Revise hang on split-recommended IDs

When `recommendation=split` and right-sizing is the only failing criterion, the revise agent makes no changes (it can't fix scope problems), never sets `auto_revised=true`, and polling returns `"pending"` forever.

**Root cause**: `filter_for_revision.py` lines 63-64 only skip `reject` and `autorevise_reject` — `recommendation=split` falls through to inclusion in the revise set. The revise agent launches, finds nothing to revise, and exits without setting the completion signal.

**Fix (Option B, recommended)**: Modify the revise-phase check in `check_review_progress.py` to also return `"completed"` when `recommendation=split`:

```python
if phase == "revise":
    ...
    if data.get("auto_revised"):
        return "completed"
    if data.get("recommendation") == "split":
        return "completed"  # revise agent can't fix right-sizing; completion expected without changes
    return "pending"
```

The `post_verify` mechanism serves as a safety net for any other unforeseen polling hangs, but this fix handles the known case directly.

## Behavioral Changes vs Current System

### 1. Reassess set re-evaluated each cycle

Plan A's `REASSESS_CHECK` re-evaluates the reassess set each cycle by calling `collect_recommendations.py --reassess` again. The current system fixes the reassess set once after the first FIXUP. Effect: IDs that pass after cycle 1 are excluded from cycle 2 (avoids wasted assess+review calls). Benign — `filter_for_revision.py` (F2 filter) would skip them anyway.

### 2. Wave dispatch reduces recoverable work on polling hang

With `max_concurrent=10` and 30 IDs, a hung ID in wave 1 blocks waves 2 and 3. The current system launches all 30 concurrently and gets 29 results. Both systems are equally stuck at the phase barrier (neither can advance until 30/30 complete), but recovery after fixing the hung ID requires reprocessing up to 21 IDs in Plan A (the unstarted waves) vs 1 in the current system. Mitigated by fixing the polling bugs above and `post_verify` for detecting agent failures.

### 3. `auto_revised` flag is set exclusively by `check_revised.py --batch`

The review agent prompt (`review-agent.md`) no longer includes `auto_revised` in its `frontmatter.py set` call. Previously, the re-review agent in REASSESS_REVIEW would set `auto_revised=false`, clobbering the value set by the FIXUP phase. Now `check_revised.py --batch` (run during FIXUP and REASSESS_FIXUP) is the sole authority — it compares `rfe-originals/` against `rfe-tasks/` content and sets the flag based on whether the body actually differs. The batch script uses `artifact_utils` directly instead of subprocess calls to `frontmatter.py`.

### 4. Split children get feasibility reviews and post-revise re-review

Resolved: split children now get re-assessed and re-reviewed after revision via SPLIT_SAVE/REASSESS/RE_REVIEW/RESTORE phases (operating on revised IDs only). Children also get feasibility reviews: `SPLIT_ASSESS` includes `parallel: [feasibility]`.

### 5. collect_recommendations.py crash risk at decision points

`collect_recommendations.py` uses `read_frontmatter` without try/except. A corrupt YAML review file (e.g., from a partially-written agent output) crashes the entire script, losing routing results for ALL IDs in the batch. This affects three critical decision points: `REASSESS_CHECK`, `COLLECT`, and `BATCH_DONE`. Known risk — consider adding a `--resilient` flag that logs and skips corrupt files rather than crashing.

## ID file naming

| File | Contents |
|------|----------|
| `tmp/pipeline-all-ids.txt` | All IDs to process |
| `tmp/pipeline-process-ids.txt` | After resume check |
| `tmp/pipeline-batch-N-ids.txt` | Per-batch IDs |
| `tmp/pipeline-active-ids.txt` | Current batch working set (populated by `advance(BATCH_START)`) |
| `tmp/pipeline-revise-ids.txt` | IDs needing revision (populated by `advance(REVIEW)`, `advance(REASSESS_RESTORE)`, `advance(SPLIT_REVIEW)`) |
| `tmp/pipeline-reassess-ids.txt` | IDs needing reassessment (populated by `advance(REASSESS_CHECK)`) |
| `tmp/pipeline-split-ids.txt` | Parent IDs being split (populated by `advance(COLLECT)`) |
| `tmp/pipeline-split-children-ids.txt` | Child IDs after split (populated by `split_collect.py`) |
| `tmp/pipeline-retry-ids.txt` | Error IDs sent to retry batch (written by ERROR_COLLECT) |
| `tmp/pipeline-retry-errors.yaml` | Original error details per ID (preserved for reporting) |
| `tmp/rfe-poll-*.txt` | Polling files (unchanged) |

## Existing prompt files (no changes needed)

| File | Phase | Variables |
|------|-------|-----------|
| `rfe.review/prompts/fetch-agent.md` | FETCH | `{KEY}` |
| `rfe.review/prompts/assess-agent.md` | ASSESS | `{KEY}`, `{DATA_FILE}`, `{RUN_DIR}`, `{PROMPT_PATH}` |
| `rfe.review/prompts/review-agent.md` | REVIEW | `{ID}`, `{ASSESS_PATH}`, `{FEASIBILITY_PATH}`, `{FIRST_PASS}` |
| `rfe.review/prompts/revise-agent.md` | REVISE | `{ID}` |
| `rfe.split/prompts/split-agent.md` | SPLIT | `{ID}`, `{TASK_FILE}`, `{REVIEW_FILE}` |
| `rfe-feasibility-review/SKILL.md` | FEASIBILITY | `{ID}` (passed as text) |

Reassess phases reuse the same prompts with different variable values (e.g., `{FIRST_PASS}=false`).

## Implementation Order

1. Write `scripts/pipeline_state.py` (~250 lines)
2. Write `scripts/error_collect.py` (~60 lines)
3. Write `scripts/verify_phase.py` (~50 lines)
4. Write `scripts/reassess_save.py` (~30 lines)
5. Write `scripts/split_collect.py` (~40 lines)
6. Write `scripts/check_right_sized.py` (~30 lines)
7. Add `--errors` flag to `scripts/collect_recommendations.py`
8. Fix `scripts/check_review_progress.py` (score=0 bug, split-recommended revise completion)
9. Extend `scripts/cleanup_partial_split.py` (child feasibility + assessment files)
10. Add `--counts-only` flag to `scripts/batch_summary.py`
11. Rewrite `.claude/skills/rfe.auto-fix/SKILL.md` (~80 lines)
12. Update `.claude/settings.json`

## Verification

1. **Unit test pipeline_state.py**: Test `advance` transitions for all decision points
2. **Trace advance() end-to-end** for: (a) normal batch, (b) 2 reassess cycles, (c) split with all-no-split, (d) split with correction, (e) retry batch, (f) fetch agent crash
3. **Verify script calls**: Every `run(...)` in `advance()` passes required positional args (especially `collect_recommendations.py` and `check_right_sized.py`)
4. **Verify agent phase configs**: Every agent phase has `ids_file`, `poll_phase`, `post_verify`, and `pre_script` (where needed)
5. **Check PHASE_CONFIG coverage**: Every phase in the enum has a config entry
6. **Trace pipeline-active-ids.txt lifecycle**: Populated at BATCH_START, read by FETCH/SETUP/ASSESS/REVIEW, filtered subsets written to revise/reassess/split ID files
7. **Single ID**: Run with 1 explicit ID, verify full phase sequence
8. **Small batch**: Run with `--batch-size 5` on 10 IDs, verify batch 2 agents get proper instructions
9. **Split flow**: Include an oversized RFE, verify split → SPLIT_COLLECT decision → child review → correction check
10. **Error retry**: Introduce a fetch failure, verify ERROR_COLLECT creates retry batch and it flows through main pipeline
11. **Key metric**: No context compression degradation in batch 2+
12. **CI run**: Full `--limit 100 --batch-size 50` run
