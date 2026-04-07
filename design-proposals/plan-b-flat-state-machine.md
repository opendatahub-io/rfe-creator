# Flat State Machine Dispatcher for rfe.auto-fix

## Context

During the `20260406-075052` run, context compression in the `rfe.auto-fix` orchestrator caused 36 revise agents to receive degraded instructions — they set `auto_revised=True` without modifying files. Root cause: inline Skill calls (`/rfe.review`, `/rfe.split`) accumulate their full orchestration output in the parent's context. By batch 2, compression fires and degrades agent launch instructions.

Claude Code doesn't support nested background agents. Since `/rfe.review` launches background agents, it cannot itself run as a background agent. The chosen approach is a **flat state machine**: the top-level dispatcher directly launches all leaf agents. No `Skill` calls. No nesting.

## Design Invariants

### Invariant 1: Background-only agents in batch orchestrators

Pipeline/batch workflows (like `rfe.auto-fix`) must **only** launch background agents (`run_in_background: true`). The orchestrator never blocks on an agent or reads its return value. All results flow through files on disk, polled via `check_review_progress.py`.

**Exception**: Interactive workflows (like `/rfe.review` on a single RFE) may use foreground agents since they operate on narrow data — one record and its children/grandchildren at a time.

**What this eliminates**: All `Skill` tool calls in the orchestrator (which block and nest the entire sub-skill's context), and any foreground `Agent` calls.

### Observability

The conversation log is the primary debugging artifact. The LLM makes explicit `set-phase NEXT` calls with visible reasoning at each transition — the log shows which phase section was executed, what script outputs informed the decision, and why a particular branch was taken. Decision-point transitions (reassess, split, correction) include the LLM's interpretation of script outputs inline.

Each phase section in the SKILL.md instructs the LLM to call summary scripts and print aggregate counts at barriers:

```
ASSESS complete: scored=10 avg_score=3.2 below_threshold=3
set-phase REVIEW

COLLECT complete: submit=7 split=2 errors=1
set-phase SPLIT (reason: split-recommended RFEs found)
```

At batch boundaries, `batch_summary.py --counts-only` provides totals. The CI monitor (rfe-autofixer) can parse `set-phase` calls and summary lines from the conversation stream.

**Manual recovery**: `pipeline_state.py set-phase <PHASE>` for skipping or re-running phases. `pipeline_state.py status` shows the full state file.

### Resumability

The phase is persisted to disk. A new session calls `get-phase`, reads the corresponding SKILL.md section, and continues. In a resume scenario the LLM reads a fresh SKILL.md (no compression — clean context), and each phase section instructs it to diff IDs against existing results on disk to skip completed agents. `set-phase` only happens after all agents in a phase complete (barrier model), so phase boundaries are always consistent.

### Invariant 2: Orchestrator context isolation

The orchestrator's context contains **only its own process state**: phase, IDs, counters, and control-flow directives. No subtask or subagent content — no RFE bodies, review text, scores, or agent outputs — is ever inlined into the orchestrator's context.

**Inter-phase communication** happens exclusively through:
- Files on disk (ID files, frontmatter, artifacts)
- Scripts that return **machine-readable directives** (ID lists, counts, status codes, boolean flags)

**What this eliminates**:
- `batch_summary.py` per-RFE detail lines in orchestrator context → redirect to file, orchestrator only sees the summary counts line
- `frontmatter.py read` of full review metadata for right-sizing checks → new script `check_right_sized.py` that returns just undersized IDs
- Any `Read` of RFE task files, review files, or agent outputs by the orchestrator itself

## What Changes

All changes are in the **rfe-creator** repo (`/Users/jason/devel/rfe-creator/`).

| File | Action |
|------|--------|
| `scripts/pipeline_state.py` | **New** (~80 lines) — phase-tracking state manager |
| `scripts/check_right_sized.py` | **New** (~30 lines) — returns undersized child IDs (invariant 2) |
| `.claude/skills/rfe.auto-fix/SKILL.md` | **Rewrite** (~300 lines) — flat dispatcher |
| `.claude/settings.json` | **Add** permissions for new scripts |

Unchanged: all agent prompt templates, all existing scripts (state.py, check_review_progress.py, etc.), `rfe.review/SKILL.md` and `rfe.split/SKILL.md` (kept for interactive single-RFE use).

## 1. `scripts/pipeline_state.py`

Thin wrapper around `state.py` that adds `phase` as a first-class concept. Uses `tmp/pipeline-state.yaml` as single state file (replaces `autofix-config.yaml`, `review-config.yaml`, `split-config.yaml`).

**Commands:**
```bash
# Initialize
python3 scripts/pipeline_state.py init --batch-size 5 --headless --announce-complete

# Phase management
python3 scripts/pipeline_state.py set-phase FETCH     # validates against phase enum
python3 scripts/pipeline_state.py get-phase            # prints current phase

# Key-value (delegates to state.py)
python3 scripts/pipeline_state.py set key=value ...
python3 scripts/pipeline_state.py get <key>
python3 scripts/pipeline_state.py set-default key=value ...

# Read full state
python3 scripts/pipeline_state.py status               # prints all of tmp/pipeline-state.yaml
```

**Phase enum** (enforced by `set-phase`):
```
INIT, BOOTSTRAP, RESUME_CHECK,
BATCH_START, FETCH, SETUP, ASSESS, REVIEW, REVISE, REVISE_FIXUP,
REASSESS_CHECK, REASSESS_SAVE, REASSESS_ASSESS, REASSESS_REVIEW,
  REASSESS_RESTORE, REASSESS_REVISE, REASSESS_REVISE_FIXUP,
COLLECT, SPLIT, SPLIT_COLLECT,
  SPLIT_REVIEW_ASSESS, SPLIT_REVIEW_REVIEW, SPLIT_REVIEW_REVISE, SPLIT_REVIEW_REVISE_FIXUP,
  SPLIT_CORRECTION_CHECK,
BATCH_DONE,
RETRY_SETUP, RETRY_FETCH, RETRY_ASSESS, RETRY_REVIEW, RETRY_REVISE, RETRY_REVISE_FIXUP, RETRY_COLLECT,
REPORT, DONE
```

ID files continue using `state.py write-ids` / `read-ids` with `tmp/pipeline-*.txt` naming.

**State file format** (`tmp/pipeline-state.yaml`):
```yaml
phase: ASSESS
batch: 1
total_batches: 3
headless: true
announce_complete: true
batch_size: 5
start_time: 2026-04-06T12:00:00Z
reassess_cycle: 0
correction_cycle: 0
```

## 2. `scripts/check_right_sized.py`

Reads review frontmatter for a list of child IDs and prints only the undersized ones. Keeps review content out of orchestrator context (invariant 2).

```bash
python3 scripts/check_right_sized.py ID1 ID2 ID3
# stdout: ID1 ID3     (only undersized IDs, space-separated; empty if all OK)
```

Internally: reads `artifacts/rfe-reviews/<ID>-review.md` frontmatter, checks `scores.right_sized < 2`. ~30 lines.

## 3. `batch_summary.py --counts-only`

Add `--counts-only` flag to existing `batch_summary.py`. When set, only prints the `TOTAL=X PASSED=Y FAILED=Z SPLIT=S ERRORS=E` counts line, suppressing per-RFE detail lines. Default behavior (without flag) is unchanged for interactive use.

## 4. Rewrite `rfe.auto-fix/SKILL.md`

### Frontmatter change
```yaml
allowed-tools: Glob, Bash, Agent    # was: Glob, Bash, Skill
```

### Structure: common patterns + phase dispatch

Define reusable patterns once at the top of the SKILL.md to avoid repeating the same 10-line agent launch + poll sequence 12 times. Each phase section becomes 3-5 lines referencing the pattern. Target: ~300 lines total (down from 813 across 3 files).

**Common patterns** (defined once, referenced by phases):

1. **Agent Launch Pattern**: Read IDs from file. Launch in **waves** of `max_concurrent` (e.g., 10): take next batch of IDs, for each launch a background Agent (`run_in_background: true` — invariant 1) with a prompt that reads a template and substitutes variables, write poll file, poll with `check_review_progress.py` until wave done, repeat for remaining IDs. **Never read the agent's return value** — all results are on disk (invariant 2). Wave dispatch caps peak concurrent API calls across all phases (main pipeline and split sub-pipeline alike).

2. **Phase Recovery Pattern**: Read `pipeline_state.py status` and active IDs from disk. Never trust in-memory state. Only script outputs that are machine-readable directives (ID lists, counts, boolean flags) may be captured (invariant 2).

3. **Phase Advance Pattern**: `pipeline_state.py set-phase <NEXT>`, then continue the dispatcher loop.

### Phase sequence per batch

```
BATCH_START → FETCH → SETUP → ASSESS → REVIEW → REVISE → REVISE_FIXUP
  → REASSESS_CHECK ─┬─ (none/maxed) → COLLECT
                     └─ REASSESS_SAVE → REASSESS_ASSESS → REASSESS_REVIEW
                        → REASSESS_RESTORE → REASSESS_REVISE → REASSESS_REVISE_FIXUP
                        → REASSESS_CHECK (loop, max 2 cycles)
  → COLLECT ─┬─ (no splits) → BATCH_DONE
             └─ SPLIT → SPLIT_COLLECT → SPLIT_REVIEW_ASSESS → SPLIT_REVIEW_REVIEW
                → SPLIT_REVIEW_REVISE → SPLIT_REVIEW_REVISE_FIXUP
                → SPLIT_CORRECTION_CHECK ─┬─ (undersized, cycle < 1) → SPLIT (correction)
                                           └─ (ok/maxed) → BATCH_DONE
  All agent phases use max_concurrent wave dispatch (see Agent Launch Pattern).
  → BATCH_DONE ─┬─ (more batches) → BATCH_START
                └─ (last batch) → RETRY_SETUP
```

### Phase details

**INIT**: Parse `$ARGUMENTS`, call `pipeline_state.py init`, run `snapshot_fetch.py` or write explicit IDs. Set phase BOOTSTRAP.

**BOOTSTRAP**: Run `bootstrap-assess-rfe.sh` (retry once). Set phase RESUME_CHECK.

**RESUME_CHECK**: Run `check_resume.py`, split IDs into batch files (`tmp/pipeline-batch-N-ids.txt`), set `total_batches` and `start_time`. Set phase BATCH_START.

**BATCH_START**: Increment batch counter, read batch IDs from `tmp/pipeline-batch-N-ids.txt`, write to `tmp/pipeline-active-ids.txt`. Set phase FETCH.

**FETCH**: For each ID in `tmp/pipeline-active-ids.txt` where task file is missing: launch fetch agent (template: `rfe.review/prompts/fetch-agent.md`, substitute `{KEY}`). Poll `--phase fetch`. Verify, write errors for missing files, update active IDs. Set phase SETUP.

**SETUP**: Run `bootstrap-assess-rfe.sh` and `fetch-architecture-context.sh` in parallel. Set phase ASSESS.

**ASSESS**: For each active ID: `prep_assess.py`, launch assess agent (template: `rfe.review/prompts/assess-agent.md`, subagent_type: `rfe-scorer`) and feasibility agent (template: `rfe-feasibility-review/SKILL.md`). Poll `--phase assess` and `--phase feasibility`. Verify, write errors. Set phase REVIEW.

**REVIEW**: Launch review agents (template: `rfe.review/prompts/review-agent.md`, `{FIRST_PASS}=true`). Poll `--phase review`. Set phase REVISE.

**REVISE**: Run `filter_for_revision.py`. If empty, skip to REVISE_FIXUP. Else launch revise agents (template: `rfe.review/prompts/revise-agent.md`). Poll `--phase revise`. Set phase REVISE_FIXUP.

**REVISE_FIXUP**: For each revised ID, run `check_revised.py`, fix `auto_revised` flag. Set phase REASSESS_CHECK.

**REASSESS_CHECK**: Run `collect_recommendations.py --reassess`. Read `reassess_cycle`. If no reassess IDs or cycle >= 2: set phase COLLECT. Else: write reassess IDs, increment cycle, set phase REASSESS_SAVE.

**REASSESS_SAVE → REASSESS_ASSESS → REASSESS_REVIEW → REASSESS_RESTORE → REASSESS_REVISE → REASSESS_REVISE_FIXUP**: Same as ASSESS/REVIEW/REVISE but on reassess subset IDs. Uses `preserve_review_state.py save/restore` to track cumulative state. REASSESS_REVISE_FIXUP loops back to REASSESS_CHECK.

**COLLECT**: Run `collect_recommendations.py` on full batch IDs. Parse `SPLIT=`. If none: set phase BATCH_DONE. Else: write split IDs, reset `correction_cycle=0`, set phase SPLIT.

**SPLIT**: Launch split agents (template: `rfe.split/prompts/split-agent.md`). Poll `--phase split`. Set phase SPLIT_COLLECT.

**SPLIT_COLLECT**: Run `collect_children.py`. Handle no-split cases. Write child IDs to `tmp/pipeline-split-children-ids.txt`. If no children: set phase BATCH_DONE. Set phase SPLIT_REVIEW_ASSESS.

**SPLIT_REVIEW_ASSESS → SPLIT_REVIEW_REVIEW → SPLIT_REVIEW_REVISE → SPLIT_REVIEW_REVISE_FIXUP**: Same as ASSESS/REVIEW/REVISE but on child IDs. No reassess cycle for children.

**SPLIT_CORRECTION_CHECK**: Run `check_right_sized.py` on child IDs — returns undersized IDs only (invariant 2: no review content in orchestrator context). If undersized exist and `correction_cycle < 1`: overwrite split IDs with undersized children, increment `correction_cycle`, set phase SPLIT. If none or cycle maxed: set phase BATCH_DONE.

**BATCH_DONE**: Run `batch_summary.py > tmp/pipeline-batch-N-summary.txt` (invariant 2: detail lines go to file, not orchestrator context). Reset `reassess_cycle=0`, `correction_cycle=0`. If more batches: set phase BATCH_START. Else: set phase RETRY_SETUP.

**RETRY_SETUP**: Run `collect_recommendations.py` on all IDs. If no errors: set phase REPORT. Else: `cleanup_partial_split.py`, clear errors, write retry IDs, set phase RETRY_FETCH.

**RETRY_FETCH → RETRY_ASSESS → RETRY_REVIEW → RETRY_REVISE → RETRY_REVISE_FIXUP → RETRY_COLLECT**: Same as main pipeline but for retry IDs only, no reassess/split cycles. RETRY_COLLECT reports permanent failures, sets phase REPORT.

**REPORT**: Run `generate_run_report.py` and `generate_review_pdf.py`. Run `frontmatter.py rebuild-index`. Set phase DONE.

**DONE**: Run `batch_summary.py --counts-only` on all IDs — outputs only the `TOTAL=X PASSED=Y ...` counts line (invariant 2: no per-RFE details in orchestrator context; full detail is already in per-batch summary files). If `announce_complete`: run `finish.py`.

## 5. Settings change

Add to `.claude/settings.json` allow list:
```json
"Bash(python3 scripts/pipeline_state.py *)",
"Bash(python3 scripts/check_right_sized.py *)"
```

## ID file naming

| File | Contents |
|------|----------|
| `tmp/pipeline-all-ids.txt` | All IDs to process |
| `tmp/pipeline-changed-ids.txt` | Changed IDs (bypass resume) |
| `tmp/pipeline-process-ids.txt` | After resume check |
| `tmp/pipeline-batch-N-ids.txt` | Per-batch IDs |
| `tmp/pipeline-active-ids.txt` | Current batch working set |
| `tmp/pipeline-revise-ids.txt` | IDs needing revision |
| `tmp/pipeline-reassess-ids.txt` | IDs needing reassessment |
| `tmp/pipeline-split-ids.txt` | Parent IDs being split |
| `tmp/pipeline-split-children-ids.txt` | Child IDs after split |
| `tmp/pipeline-retry-ids.txt` | Error IDs for retry |
| `tmp/rfe-poll-*.txt` | Polling files (unchanged) |

## Implementation order

1. Write `scripts/pipeline_state.py` (~80 lines)
2. Write `scripts/check_right_sized.py` (~30 lines)
3. Add `--counts-only` flag to `scripts/batch_summary.py`
4. Rewrite `.claude/skills/rfe.auto-fix/SKILL.md` (~300 lines)
5. Update `.claude/settings.json`

## Verification

1. **Single ID**: Run with 1 explicit ID, verify full phase sequence executes
2. **Small batch**: Run with `--batch-size 5` on 10 IDs (2 batches), verify batch 2 agents get proper instructions
3. **Split flow**: Include an oversized RFE, verify SPLIT → SPLIT_REVIEW phases work
4. **Error retry**: Introduce a fetch failure, verify RETRY phases work
5. **Key metric**: No "Let me continue from where we left off" messages in batch 2+
6. **CI run**: Full `--limit 100 --batch-size 50` run, verify all 100 issues have results
