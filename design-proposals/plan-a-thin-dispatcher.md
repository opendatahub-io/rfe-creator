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
3. The dispatch loop diffs IDs against existing results on disk to identify incomplete work — agents that already produced their output file are skipped, only remaining IDs are dispatched
4. `advance` only fires after the barrier clears (all agents complete), so phase boundaries are always consistent — there is never a "half-advanced" state

This makes the pipeline robust to both crashes and context compression. Even if compression completely destroys the orchestrator's memory of what it was doing, the SKILL.md's generic dispatch loop + disk state is sufficient to continue. The LLM doesn't need to "remember" anything — it just reads the loop instructions and the disk tells it where it is.

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

  config = pipeline_state.py get-phase-config   # → prompt_file, ids_file, max_concurrent, ...
  if config.type == "agent":
    ids = state.py read-ids <ids_file>
    ids = filter out IDs that already have results on disk  # resumability
    while ids remain:
      wave = take next max_concurrent from ids
      for each id in wave: launch background Agent(...)
      poll with check_review_progress.py until wave done
  elif config.type == "script":
    run config.command

  pipeline_state.py advance                      # → decision logic picks next phase
```

The orchestrator **never reads prompt files** — the agents do. The orchestrator **never decides what's next** — `advance` does. And the loop is **resumable at every iteration** — disk state is the only source of truth.

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
INIT → BOOTSTRAP → RESUME_CHECK → BATCH_START

MAIN PIPELINE (per batch):
  BATCH_START → [FETCH, SETUP, ASSESS, REVIEW, REVISE, FIXUP]
  FIXUP → REASSESS_CHECK
  → decision: reassess?
       yes (cycle < 2, reassess IDs exist) → [REASSESS_SAVE, REASSESS_ASSESS, REASSESS_REVIEW, REASSESS_RESTORE, REASSESS_REVISE, REASSESS_FIXUP] → back to REASSESS_CHECK
       no → COLLECT

COLLECT:
  → decision: splits?
       yes → SPLIT → [SPLIT_COLLECT, SPLIT_PIPELINE_START, SPLIT_ASSESS, SPLIT_REVIEW, SPLIT_REVISE, SPLIT_FIXUP, SPLIT_CORRECTION_CHECK]
       no → BATCH_DONE

SPLIT_CORRECTION_CHECK:
  → undersized & cycle < 1 → cycle back to SPLIT (only undersized IDs)
  → otherwise → BATCH_DONE

All agent phases use max_concurrent waves (see below) to cap concurrency.

BATCH_DONE:
  → decision: more batches?
       yes → BATCH_START
       errors exist & retry_cycle < 1 → ERROR_COLLECT → BATCH_START (retry batch)
       no errors or retry_cycle >= 1 → REPORT → DONE
```

ERROR_COLLECT is a script phase (`scripts/error_collect.py`) that prepares error IDs for a clean retry. **This script must be idempotent** — a crash mid-ERROR_COLLECT must be recoverable by re-running the script. All steps use create-or-overwrite semantics; no step depends on the absence of a previous step's output.

1. **Collect error IDs** across all batches via `collect_recommendations.py --errors`, recording which phase each error occurred in (fetch_failed, assess_failed, review_failed, revise_failed, split_failed, etc.)
2. **Save error history** to `tmp/pipeline-retry-errors.yaml` — error type, failure phase, and original error message per ID. Preserved for post-mortem even if the retry succeeds or a second failure overwrites.
3. **Persist retry IDs** to `tmp/pipeline-retry-ids.txt` (read by `generate_run_report.py` to identify which IDs were retried and whether they recovered — see REPORT phase data flow below)
4. **Artifact cleanup** — deletes all intermediate results so the dispatch loop's resumability skip filter doesn't no-op the retry:

   | Artifact | Deleted for | Why |
   |----------|-------------|-----|
   | `artifacts/rfe-tasks/<ID>.md` | REVISE-phase errors only | Revise can leave a half-written/corrupted task file; must re-fetch |
   | `artifacts/rfe-reviews/<ID>-review.md` | All error IDs | Skip filter checks this for REVIEW |
   | `artifacts/rfe-reviews/<ID>-feasibility.md` | All error IDs | Skip filter checks this for FEASIBILITY |
   | `/tmp/rfe-assess/single/<ID>.md` | All error IDs | Assessment input |
   | `/tmp/rfe-assess/single/<ID>.result.md` | All error IDs | Skip filter checks this for ASSESS |
   | `artifacts/rfe-reviews/<ID>-split-status.yaml` | split_failed IDs | Skip filter checks this for SPLIT |
   | Child task/review/assess/feasibility files | split_failed IDs | Via `cleanup_partial_split.py` (extended to also clean `/tmp/rfe-assess/single/<child>.md` and `<child>.result.md`) |

   **Why this is necessary**: The dispatch loop's skip filter ("filter out IDs that already have results on disk") is designed for crash recovery. Without cleanup, an ID that failed at REVIEW would still have its task file and assessment result on disk — FETCH and ASSESS would skip it, and REVIEW would see the same stale inputs that caused the original failure.

   **What is NOT deleted**: `artifacts/rfe-tasks/<ID>.md` for non-REVISE errors (preserved — FETCH will skip it via the resume filter, which is correct since the task file itself is fine), `artifacts/rfe-originals/<ID>.md` (baseline copy, always preserved).

5. **Post-cleanup verification** — for each retry ID, confirm that no skip-triggering artifacts remain on disk. Specifically: no `<ID>.result.md` in `/tmp/rfe-assess/single/`, no `<ID>-review.md` in `artifacts/rfe-reviews/`, no `<ID>-feasibility.md` in `artifacts/rfe-reviews/`. For REVISE errors, also confirm no `<ID>.md` in `artifacts/rfe-tasks/`. If any remain (e.g., filesystem error during delete), log a warning and retry the delete. This is the safety net against silent no-op retries.
6. **Write retry batch file** (`tmp/pipeline-batch-{N+1}-ids.txt`)
7. **Update state**: increment `total_batches` and set `retry_cycle = 1` (`batch` is incremented by `advance(BATCH_START)`, not here)

The retry batch flows through the **same** FETCH → SETUP → ASSESS → REVIEW → REVISE → FIXUP → reassess → COLLECT → split pipeline as any other batch. No special retry states needed.

**Tradeoff**: The retry-as-batch approach trades state duplication (22 fewer states) for cleanup correctness — ERROR_COLLECT must delete the right artifacts or retries silently no-op. The artifact cleanup contract above is the critical specification. SETUP also re-runs idempotent bootstrap scripts (~15s overhead), which is acceptable.

**REPORT phase data flow**: REPORT is a script phase that calls `generate_run_report.py`. The script reads `tmp/pipeline-retry-ids.txt` (if it exists) to identify which IDs were retried, and `tmp/pipeline-retry-errors.yaml` for original error details. This is file-based — the domain-ignorant orchestrator doesn't need to know which batch was a retry.

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
vars:
  DATA_FILE: "/tmp/rfe-assess/single/{ID}.md"
  RUN_DIR: "/tmp/rfe-assess/single"
  PROMPT_PATH: ".context/assess-rfe/scripts/agent_prompt.md"

# Script phase example:
type: script
command: "python3 scripts/bootstrap-assess-rfe.sh && bash scripts/fetch-architecture-context.sh"
```

This config is **encoded in `pipeline_state.py`** (a Python dict/dataclass), not in the SKILL.md. The orchestrator never sees the contents of prompt files or the meaning of variables.

## What Changes

All changes are in the **rfe-creator** repo (`/Users/jason/devel/rfe-creator/`).

| File | Action |
|------|--------|
| `scripts/pipeline_state.py` | **New** (~200 lines) — phase tracking, config, transition logic |
| `scripts/error_collect.py` | **New** (~60 lines) — artifact cleanup + retry batch creation |
| `scripts/check_right_sized.py` | **New** (~30 lines) — returns undersized child IDs |
| `scripts/collect_recommendations.py` | **Modify** — add `--errors` flag |
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
INIT, BOOTSTRAP, RESUME_CHECK,
BATCH_START, FETCH, SETUP, ASSESS, REVIEW, REVISE, FIXUP,
REASSESS_CHECK, REASSESS_SAVE, REASSESS_ASSESS, REASSESS_REVIEW,
  REASSESS_RESTORE, REASSESS_REVISE, REASSESS_FIXUP,
COLLECT, SPLIT, SPLIT_COLLECT,
  SPLIT_PIPELINE_START, SPLIT_ASSESS, SPLIT_REVIEW, SPLIT_REVISE, SPLIT_FIXUP,
  SPLIT_CORRECTION_CHECK,
BATCH_DONE, ERROR_COLLECT,
REPORT, DONE
```

### Phase config map (Python dict in `pipeline_state.py`)

Each phase maps to:
```python
PHASE_CONFIG = {
    "FETCH": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/fetch-agent.md",
        "ids_file": "tmp/pipeline-active-ids.txt",
        "poll_phase": "fetch",
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
        "parallel": [  # also launch feasibility agents
            {"prompt": ".claude/skills/rfe-feasibility-review/SKILL.md", "poll_phase": "feasibility"}
        ],
        "pre_script": "python3 scripts/prep_assess.py {ID}",
        "vars": { ... }
    },
    # ... etc
}
```

### Transition logic (`advance` command)

```python
def advance(current_phase, state):
    # Preamble (one-time)
    PREAMBLE = ["INIT", "BOOTSTRAP", "RESUME_CHECK", "BATCH_START"]

    # BATCH_START increments the batch counter and resets per-batch counters
    # so each batch (including retry) gets fresh reassess and correction cycles
    if current_phase == "BATCH_START":
        state["batch"] += 1
        state["reassess_cycle"] = 0
        state["correction_cycle"] = 0
        return "FETCH"

    # Linear sequences — advance to next element, last element falls through to decision points
    MAIN_SEQUENCE = ["FETCH", "SETUP", "ASSESS", "REVIEW", "REVISE", "FIXUP"]
    REASSESS_SEQUENCE = ["REASSESS_SAVE", "REASSESS_ASSESS", "REASSESS_REVIEW",
                         "REASSESS_RESTORE", "REASSESS_REVISE", "REASSESS_FIXUP"]
    SPLIT_SEQUENCE = ["SPLIT_COLLECT", "SPLIT_PIPELINE_START", "SPLIT_ASSESS",
                      "SPLIT_REVIEW", "SPLIT_REVISE", "SPLIT_FIXUP", "SPLIT_CORRECTION_CHECK"]

    for seq in [PREAMBLE, MAIN_SEQUENCE, REASSESS_SEQUENCE, SPLIT_SEQUENCE]:
        if current_phase in seq[:-1]:
            return seq[seq.index(current_phase) + 1]

    # Decision points — reassess loop
    if current_phase == "FIXUP":
        return "REASSESS_CHECK"

    if current_phase == "REASSESS_CHECK":
        reassess_ids = run("collect_recommendations.py --reassess")
        cycle = state["reassess_cycle"]
        if reassess_ids and cycle < 2:
            state["reassess_cycle"] = cycle + 1
            return "REASSESS_SAVE"
        return "COLLECT"

    if current_phase == "REASSESS_FIXUP":
        return "REASSESS_CHECK"  # loops back; CHECK re-evaluates cycle

    # Decision points — collect and split
    if current_phase == "COLLECT":
        split_ids = run("collect_recommendations.py")  # parse SPLIT=
        if split_ids:
            return "SPLIT"
        return "BATCH_DONE"

    if current_phase == "SPLIT":
        return "SPLIT_COLLECT"

    if current_phase == "SPLIT_CORRECTION_CHECK":
        undersized = run("check_right_sized.py <child_ids>")
        if undersized and state["correction_cycle"] < 1:
            state["correction_cycle"] += 1
            return "SPLIT"
        return "BATCH_DONE"

    # Decision points — batch control and retry
    if current_phase == "BATCH_DONE":
        if state["batch"] < state["total_batches"]:
            return "BATCH_START"
        if state["retry_cycle"] < 1:
            error_ids = run("collect_recommendations.py --errors <all_ids>")
            if error_ids:
                return "ERROR_COLLECT"
        return "REPORT"

    if current_phase == "ERROR_COLLECT":
        # Script has already: cleaned artifacts, written new batch file,
        # incremented total_batches, set retry_cycle=1
        return "BATCH_START"

    # Terminal
    if current_phase == "REPORT":
        return "DONE"
```

### CLI

```bash
python3 scripts/pipeline_state.py init --batch-size 5 --headless
python3 scripts/pipeline_state.py get-phase              # → "ASSESS"
python3 scripts/pipeline_state.py set-phase REVIEW
python3 scripts/pipeline_state.py get-phase-config        # → YAML with type, prompt, ids_file, vars
python3 scripts/pipeline_state.py advance                 # → runs decision logic, sets next phase, prints it
python3 scripts/pipeline_state.py set key=value ...
python3 scripts/pipeline_state.py get <key>
python3 scripts/pipeline_state.py status                  # → full state YAML
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
  RHAIRFE-1522: revise_failed → task file deleted for re-fetch
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

## 2. `scripts/check_right_sized.py`

```bash
python3 scripts/check_right_sized.py ID1 ID2 ID3
# stdout: RESPLIT=ID1 ID3
# (undersized IDs only; empty RESPLIT= if all pass)
```

Per-parent-aware correction check. For each ID, reads `artifacts/rfe-reviews/<ID>-review.md` frontmatter, groups children by `parent_key`, and checks `scores.right_sized < 2`. Returns undersized IDs as a machine-readable directive — the orchestrator never reads assessment content, only the ID list.

This script is the fan-in join point for the split correction loop: all children's assessments complete (barrier), then this script aggregates results across parents and returns just the IDs needing re-split. ~40 lines.

## 3. `batch_summary.py --counts-only`

Add `--counts-only` flag. When set, only prints the `TOTAL=X PASSED=Y ...` counts line. Default unchanged.

## 4. Rewrite `rfe.auto-fix/SKILL.md` (~80 lines)

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
4. Run: pipeline_state.py advance
5. Loop

## Teardown (after DONE)
1. batch_summary.py --counts-only on all IDs
2. If announce_complete: finish.py
```

That's it. ~80 lines. The orchestrator doesn't know what ASSESS means, what REVISE does, or how splits work. It just dispatches and advances.

## 5. Settings change

```json
"Bash(python3 scripts/pipeline_state.py *)",
"Bash(python3 scripts/error_collect.py *)",
"Bash(python3 scripts/check_right_sized.py *)"
```

## ID file naming

| File | Contents |
|------|----------|
| `tmp/pipeline-all-ids.txt` | All IDs to process |
| `tmp/pipeline-process-ids.txt` | After resume check |
| `tmp/pipeline-batch-N-ids.txt` | Per-batch IDs |
| `tmp/pipeline-active-ids.txt` | Current batch working set |
| `tmp/pipeline-revise-ids.txt` | IDs needing revision |
| `tmp/pipeline-reassess-ids.txt` | IDs needing reassessment |
| `tmp/pipeline-split-ids.txt` | Parent IDs being split |
| `tmp/pipeline-split-children-ids.txt` | Child IDs after split |
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

1. Write `scripts/pipeline_state.py` (~200 lines)
2. Write `scripts/error_collect.py` (~60 lines)
3. Write `scripts/check_right_sized.py` (~30 lines)
4. Add `--errors` flag to `scripts/collect_recommendations.py`
5. Add `--counts-only` flag to `scripts/batch_summary.py`
6. Rewrite `.claude/skills/rfe.auto-fix/SKILL.md` (~80 lines)
7. Update `.claude/settings.json`

## Verification

1. **Unit test pipeline_state.py**: Test `advance` transitions for all decision points
2. **Single ID**: Run with 1 explicit ID, verify full phase sequence
3. **Small batch**: Run with `--batch-size 5` on 10 IDs, verify batch 2 agents get proper instructions
4. **Split flow**: Include an oversized RFE, verify split → child review → correction check
5. **Error retry**: Introduce a fetch failure, verify ERROR_COLLECT creates retry batch and it flows through main pipeline
6. **Key metric**: No context compression degradation in batch 2+
7. **CI run**: Full `--limit 100 --batch-size 50` run
