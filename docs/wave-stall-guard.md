# Wave stall guard

`python3 scripts/pipeline_state.py wait-for-wave` is the headless pipeline's
barrier: it runs `check_review_progress.py --wait --max-wait 90` over the
current wave and exits 3 while any (poll phase, id) slot is pending; the
orchestrating model re-runs it on exit 3. A subagent that dies silently — or
writes its output to the wrong path, or is never actually launched — leaves
its slot pending forever, and without a guard the barrier is re-run until the
CI job timeout (observed on several open-model eval runs; design D14 / R2).

The guard lives in `scripts/pipeline_state.py` (`cmd_wait_for_wave`,
`_handle_stall`, `_escalate_stuck`, `WAVE_STALL_POLICY`). It changes nothing
while a wave is making progress: the `check_review_progress` flags, the exit
codes (0 done / 3 re-run), the `Re-run: ...` line and the files on disk are
the same as before, apart from one tracker file under `tmp/`.

## Knobs

| Variable | Default | Meaning |
|---|---|---|
| `PIPELINE_WAVE_STALL_SECS` | `900` | No-progress window for a retry-eligible wave. Escalate-only waves use twice this (`1800`). `0` (or negative) disables the guard and restores the unbounded wait. |
| `PIPELINE_WAVE_RETRY_CAP` | `2` | Re-dispatch budget per (pipeline phase, id) before a stuck id is escalated. `0` escalates on the first stall. |

Both are read on every call, so they can be changed mid-run. Counts and the
tracker live in `tmp/pipeline-stall-retries.yaml` and
`tmp/pipeline-wave-progress.yaml`; `python3 scripts/state.py clean` wipes
them with the rest of `tmp/`.

## Wave freshness (AISDLC-33)

Two timestamps under `tmp/` decide whether an assess result or review file
counts as this phase's output:

- `tmp/pipeline-phase-entry.txt` — written when the pipeline *enters* an agent
  phase (`next-action`, `advance`, `set-phase`; never re-written by a repeat
  `next-action` for the same phase). `next-action`'s wave pre-filter and the
  `advance` guard treat an assess result or review last modified before it as
  *pending*, so the id stays in the wave and its agent is launched.
- `tmp/pipeline-wave-launch.txt` — written by every `launch_wave` / `set-wave`;
  `wait-for-wave` passes it to `check_review_progress.py` as `--since` (and to
  its own slot counts), so a file older than the wave's launch cannot release
  the barrier.

A third file, `tmp/pipeline-revise-baseline.json` (AISDLC-45), serves the
revise slot the same way: written with the revise id list, it holds each id's
review write time, and the slot stays pending until the revise agent has
written the review (its frontmatter step, its last action) — the `auto_revised`
flag alone cannot tell this wave's revision from the one `REASSESS_RESTORE`
re-raised, and a task edit alone would release the slot mid-revision.

`REASSESS_SAVE` deletes the review and result files before a reassess phase is
entered, so a file older than either reference can only be a late write by a
previous cycle's agent. Before the rule, such a file made the pre-filter skip
the launch altogether and the phase advanced on the stale verdict, or released
the barrier before the wave's own agent had finished, whose later write then
clobbered the review the orchestrator had just restored (`auto_revised` and
`before_score` lost, observed twice on 2026-09-17). Dimension files
(feasibility, alignment) are reused across cycles by design and are exempt;
the revise slot is exempt from the time rule too and is gated by the revise
baseline below instead. Without a recorded entry or launch the legacy rule
applies (interactive skills call `check_review_progress.py` directly).

The companion repair is the reconcile (`scripts/reconcile_reviews.py`):
`REASSESS_RESTORE` and `SPLIT_RESTORE` keep each item's `*-review-state.json`
(`restore --keep-state`), COLLECT (and `SPLIT_CORRECTION_CHECK` for split
children) re-applies it idempotently before routing and removes it, so a write
that lands after the restore is undone whatever released the barrier; the same
step flags every review still failing once no revision can follow. `BATCH_START`
sweeps leftover state files for the batch's ids.

## Reset-on-progress

Progress is the number of (poll phase, id) slots of the wave that are no
longer pending (completed or error). The count is recorded before the poll
and re-checked when the 90-second poll returns; the deadline resets whenever
the count grows, never on a poll that merely returned. The tracker is keyed
by a signature of the wave (phase + sorted ids), so a new wave starts a fresh
window, and it is deleted when the wave completes, when a stall is handled,
and whenever `next-action` or `set-wave` writes a new wave file. That last
rule matters when the orchestrator abandons a barrier before it observed exit
0 (the agent finished and it ran `next-action` instead of re-running
`wait-for-wave`): the tracker would otherwise survive, and a later wave over
the same phase and ids — the retry batch, a later reassess cycle — would
inherit its deadline and be declared stalled on its first poll. A
legitimately slow agent is therefore never cut off as long as *something* in
the wave keeps finishing; only a wave in which nothing reaches a terminal
state for a full window is treated as stalled.

## Policy per phase

Re-dispatching a stuck id launches a second agent while the first may still be
alive, so it is only allowed where an agent writes a single output file and a
second run over the same inputs is a no-op. The table is keyed by *pipeline*
phase (not poll phase) and a test pins it against the phase table, so a new
agent phase must be classified.

| Phase | Policy | Why |
|---|---|---|
| `FETCH` | retry | fetch-agent writes `<tasks>/<id>.md` (+ companions) from Jira; a re-fetch overwrites |
| `ASSESS`, `REASSESS_ASSESS`, `SPLIT_ASSESS`, `SPLIT_REASSESS` | retry | scorer writes `tmp/rfe-assess/single/<id>.result.md`; each dimension (feasibility, alignment) writes one file |
| `REVIEW`, `REASSESS_REVIEW`, `SPLIT_REVIEW`, `SPLIT_RE_REVIEW` | retry | review-agent writes `<reviews>/<id>-review.md` from the assess and dimension files |
| `REVISE`, `REASSESS_REVISE`, `SPLIT_REVISE` | escalate-only | revise-agent edits the task file in place and writes the removed-context companion; two agents would double-edit |
| `SPLIT` (incl. the correction pass) | escalate-only | split-agent mints child task files and archives the parent; two agents would mint duplicate children |

**Retry.** The stuck ids are left pending, their `<PHASE>:<id>` counter is
bumped, and the command exits 0. The orchestrator runs `next-action` as
usual; its pre-filter re-derives a wave from the still-pending ids and
launches them again (running the phase's `pre_script` again, which is
idempotent). A re-dispatch relaunches *every* agent of that id's wave, not
only the stuck slot — the wave directive is per id, and `prep_assess.py`
re-stages the item, so a scorer that had already finished runs once more;
that is the cost of a retry and the reason the policy admits only idempotent
phases. Ids at the cap are escalated instead.

Observed live (fault-injected eval, 2026-09-15, window 60 s, one weak-draft
case at full effort): the scorer finished inside the first poll, the
feasibility agent did not, the second poll returned with no new completion,
the barrier printed the STALL line and exited 0, and the orchestrator
continued with `next-action` ("The stall guard re-dispatched the stuck
feasibility agent. The barrier exited 0, so continuing with next-action").
The re-dispatched wave completed and the run passed.

**Escalate.** Escalation goes through the existing post-barrier contract and
never fabricates agent output — no assess result, dimension file or fetch
output is written. The id is removed from the wave file *and* from the
phase's ids file, exactly as `verify_phase.py` drops an id whose output is
missing: the barrier releases, `next-action` does not re-dispatch it,
`post_verify` runs on the remaining ids, and `ERROR_COLLECT` at `BATCH_DONE`
picks the error up for the single retry batch. The marker depends on the
phase kind:

| Phase kind | Marker | Downstream |
|---|---|---|
| fetch / assess / review-class | The registry error-stub review (`verify_phase.write_error_stubs`, the same shape `validate_types.build_error_stub` checks), with `error: <phase base>_stalled` naming the agent that never finished: the stuck poll phase with the type's `pipeline.poll_prefix` stripped, so `assess_stalled` for `assess` and `initiative-assess` alike (`fetch_stalled`, `assess_stalled`, `feasibility_stalled`, `alignment_stalled`, `review_stalled`). When the stub cannot be merged into a half-written review (a `scores` member the schema does not know), the review's frontmatter is replaced with the stub and one `verify_phase: <id>: ...` stderr line says why. | Retryable: `error_collect.py` cleans the stub and queues the id. |
| revise | `error: revise_stalled`, `needs_attention: true` on the real review; score and recommendation kept, `auto_revised` untouched (no revision is claimed). `check_id`'s revise row holds the slot pending until the review has been written since `tmp/pipeline-revise-baseline.json` was taken, then keys on `auto_revised`; the marker write itself moves the write time, so in `REASSESS_REVISE` (flag already re-raised) the marker completes the slot while in `REVISE` / `SPLIT_REVISE` it stays pending — either way the id is retired by leaving the wave and ids files, which `_escalate_stuck` does right after the marker. | Retryable: `error_collect.py`'s revise path restores the task file from its original (undoing a half-finished edit) and deletes the removed-context companion before the retry. `collect_recommendations --reassess` does not reassess it. |
| split | `error: split_not_attempted: wave stalled ...` (the non-retryable class `submit.py` records for parents a split pass skipped) and `needs_attention: true` on the real review, plus `<reviews>/<id>-split-status.yaml` with `status: failed`, `action: no-split`, which makes the split slot terminal and routes the parent to `split_collect`'s R8 no-split branch if anything reads it. Nothing is cleaned up: the agent may still be alive. | Non-retryable: `error_collect.py` excludes it from the retry batch; `generate_run_report.py` counts it failed, not split. The parent is still `status: Ready` with no children, so `submit.py` does not see it as a Phase 1 split parent: it reaches Phase 2 as a regular item and is disposed there — needs-attention label and comment (that is what the flag buys), then `processed: true` in the snapshot — so it is **not** re-selected until its Jira content changes. It gets no feasibility label and is never auto-approved: a review carrying an `error` has no verdict (see [What submit.py does with the markers](#what-submitpy-does-with-the-markers)). This differs from `submit.py`'s own `split_not_attempted:` path, which exits before Phase 2 and leaves the parent unprocessed; teaching Phase 2 to leave `split_not_attempted:` / `*_stalled` reviews unprocessed is a follow-up in `submit.py`. If the agent was slow rather than dead and archives the parent and mints children later in the job, Phase 1 skips the split-submit (same section). |

The revise and split markers update the review in place. When that is
impossible — there is no review, or the schema rejects the one there is (a
hand-edited or half-written file: `feasibility: likely` still polls as a
completed review) — they fall back to the same registry error stub carrying
the same `error` value, so the escalation never raises. This matters because
escalation runs before the wave and ids files are rewritten: an exception
there would turn the bounded barrier into a crash loop in which every re-run
repeats the poll and the same traceback.

When neither writer can produce a marker (the review path is not a writable
file), the id is **not** retired. Retiring it with no marker on disk would make
it vanish from `collect_recommendations --errors`, `error_collect.py` and the
run report without a trace, so instead: the ids whose marker landed are
released as usual, the unrecorded id stays in the wave file and the phase's
ids file, the tracker is cleared, and the command exits 1 with a
`wait-for-wave: ESCALATION FAILED` line (see below). For a split parent the
`no-split` status file is written only once the review marker is on disk, so
an unrecorded parent's slot stays pending instead of releasing on the next
poll with nothing recorded. After the reviews directory is fixed, re-running
`wait-for-wave` starts a fresh window and escalates the id for real.

## What submit.py does with the markers

- **Phase 1 skips a parent the guard gave up on.** A split agent escalated as
  `split_not_attempted:` that was slow rather than dead can still archive the
  parent and mint its children after the marker was written and after the
  parent left `tmp/pipeline-split-ids.txt`, so `SPLIT_COLLECT` never picks the
  children up and no `SPLIT_ASSESS` / `SPLIT_REVIEW` wave sees them. Phase 1
  used to select such a parent by task `status: Archived` plus children
  carrying its `parent_key` and split-submit children nobody reviewed. It now
  reads the parent's review first: a review whose `error` starts with
  `split_not_attempted:` or ends with `_stalled` excludes the parent from the
  split-submit loop with one line —
  `  <key>: SKIP split-submit - review error <error>; children were not reviewed, left for an operator`
  — and the parent is in no plan, so it is neither split-submitted nor marked
  processed; its children stay local. An unreadable review counts as no error.
  `split_refused:` and `split_submit_failed:` parents behave as before.
- **A review carrying an `error` has no feasibility verdict.** Every
  `*_failed` / `*_stalled` stub inherits the stub shape (`feasibility:
  feasible`, `recommendation: revise`, `pass: false`, `score: 0`) although no
  feasibility review ran, and a stall marker on a real review no longer
  describes the item. Phase 2 therefore passes no verdict to the feasibility
  label logic for an error-bearing review — no feasibility label is added and
  none is removed — and never auto-approves it, whatever its `pass` and
  `feasibility` fields say. The needs-attention label and comment and the
  rubric logic are unchanged. (This also means today's `*_failed` stubs no
  longer receive `feasibility-pass`.)

## What the operator sees

Nothing until a stall. Then exactly one stderr line from the call that
returned (it exits 0, so the orchestrator goes straight back to
`next-action`), preceded by a fallback line only when a review had to be
replaced: `wait-for-wave: could not mark <id>'s review (<reason>); replacing
it with the <revise|split> error stub (error=<error>)` when a revise or split
review could not be updated, and `verify_phase: <id>: frontmatter.py set
failed (<reason>); replaced the review frontmatter with the <error> stub` when
the stub itself could not be merged into a half-written review:

```
wait-for-wave: STALL in <PHASE> (<poll phases joined by +>): no wave slot reached a terminal state for <idle>s (window <window>s, policy retry|escalate-only); re-dispatching <id> (attempt n/cap), ...; escalating <id>, ... (retry cap N reached) -> <marker>, removed from the wave and <ids file>
```

Examples:

```
wait-for-wave: STALL in ASSESS (assess+feasibility): no wave slot reached a terminal state for 900s (window 900s, policy retry); re-dispatching RHAIRFE-1002 (attempt 1/2)
wait-for-wave: STALL in ASSESS (assess+feasibility): no wave slot reached a terminal state for 900s (window 900s, policy retry); escalating RHAIRFE-1002 (retry cap 2 reached) -> assess_stalled error-stub review, removed from the wave and tmp/pipeline-active-ids.txt
wait-for-wave: STALL in SPLIT (split): no wave slot reached a terminal state for 1800s (window 1800s, policy escalate-only); escalating RHAIRFE-1001 -> split_not_attempted error + no-split status file, removed from the wave and tmp/pipeline-split-ids.txt
```

If an escalated id could not be marked at all (see above), the STALL line
lists it as `escalation FAILED for <id> (see next line)` instead of
`escalating ...`, one more line follows, and the command exits **1** rather
than 0 — the orchestrator's re-run loop stops there until the reviews directory
is fixed:

```
wait-for-wave: ESCALATION FAILED for <ids>: no error marker could be written (<id>: <reason from the stub writer, when it gave one>); left in the wave and <ids file> - fix the reviews directory and re-run
```

`tmp/pipeline-retry-errors.yaml` (written by `error_collect.py` at
`BATCH_DONE`) carries the `*_stalled` values, so a stall is distinguishable
there from an agent that finished without output (`*_failed`). The run report
(`generate_run_report.py`) does not surface them: a `*_stalled` stub is
counted `failed` like any other error stub, with no reason on its entry. The
only stall marker the report shows is the split one, as `failed_reason: Agent
failed: split_not_attempted: wave stalled ...`. After the run, the review's
`error` field and the retry-errors file are where a stall is told apart from a
`*_failed` stub.

## Limitations

- Retry counts are per run and per (phase, id): an id that used its retries
  in `ASSESS` is escalated at the first stall of `ASSESS` in the retry batch
  too (other phases have their own counters).
- An escalate-only agent that is merely slow and finishes after escalation
  leaves its artifacts behind (an edited task file, or children and an
  archived parent). The review carries the stall error, so the run does not
  treat the item as done; the retry path (revise) restores the original, and
  a stalled split is flagged for the operator in Jira (needs-attention label
  and comment) but not re-selected automatically until its Jira content
  changes, and children a late split agent minted are left local, never
  split-submitted (`submit.py` Phase 1 skips the parent). The double window
  exists to make this rare.
- A retried first attempt is not cancelled — the scripts have no handle on a
  subagent — and the wave uses fixed artifact paths, so a first attempt that
  was merely stuck and finishes *after* the retry's result was accepted
  overwrites it. The retry policy makes that safe by construction for the
  phases it admits: the stale write is the same agent over the same staged
  input (assess result, dimension file, fetched task), so the artifact it
  replaces is equivalent, and the phase that consumed the accepted artifact
  has already read it. The one ordering that could matter is a REVIEW retry
  whose stale first-pass review lands after the item was revised and
  re-reviewed: the file would then carry a pre-revision verdict. It needs an
  agent that is a full window (15 min) late and then finishes minutes later
  still; the run report's `before_score` / `auto_revised` provenance makes it
  visible after the fact. Attempt-scoped output paths, the real fence, change
  the agents' output contract (prompt edits) and are a follow-up, not part of
  this guard.
- The guard bounds the barrier; it does not diagnose why the agent died.
  Look at the subagent transcript for that.

Tests: `tests/test_pipeline_state.py::TestWaveStallPolicy`,
`::TestWaveStall` (bounded barrier and the post_verify leg after it,
reset-on-progress before and during a poll, split and revise paths, both
shipped types, disable, no-stall neutrality, the stub fallback on an
unmergeable review, tracker normalization, the ESCALATION FAILED path),
`tests/test_verify_phase.py::TestWriteErrorStubs` and, for `submit.py`,
`tests/test_submit.py::TestStallEscalatedSplitParentIsSkipped`,
`::TestFeasibilityLabelOnSubmit` and `::TestApprovedTransition` (the
error-bearing-review cases).
