# Snapshot-Based Incremental Fetch

## Problem

The original auto-fix pipeline processed RFEs by running a JQL query
against Jira and processing every result. Two issues:

1. **Blind spots**: Labels like `rfe-creator-autofix-rubric-pass` were
   excluded at the JQL level. If someone later edited the description of
   a passing RFE, the system never saw it again.

2. **Wasted work**: Every run re-fetched and re-evaluated issues whose
   descriptions hadn't changed. The only gate was `check_resume`, which
   skipped issues with passing local reviews — but couldn't distinguish
   "unchanged in Jira" from "changed but we happen to have a local
   review."

## Solution

Instead of filtering at the JQL level and relying on labels to decide
what to process, the snapshot system fetches **all** open issues (minus
permanent exclusions), computes a content hash of each description, and
diffs against the previous snapshot. Changed and new issues get priority
ordering; unchanged issues fill remaining capacity within the limit.

## Hard vs Soft Filters

| Filter | Before | After | Rationale |
|--------|--------|-------|-----------|
| `statusCategory != Done` | JQL | JQL | Closed issues never need processing |
| `rfe-creator-ignore` | JQL | JQL | Permanent human override — never touch |
| `rfe-creator-autofix-rubric-pass` | JQL | Hash diff | If someone edits a passing RFE, we should see it |
| `rfe-creator-needs-attention` | Not filtered | Hash diff | If a human resolves the flag and revises the description, we should re-evaluate |

Soft-filtered labels are no longer excluded from the query. Issues with
these labels are fetched and hashed like everything else. If their
description hasn't changed, the diff naturally skips them. If it has
changed, they surface for processing regardless of labels.

## Files

```
artifacts/auto-fix-runs/
  issue-snapshot-<YYYYMMDD-HHMMSS>.yaml   # Snapshot: {key: content_hash}
```

### Snapshot (`issue-snapshot-*.yaml`)

The snapshot is **cumulative**: each run merges selected issues into the
previous snapshot rather than replacing it. Issues not selected within
the limit retain their previous hashes, and issues never selected remain
absent (staying NEW until selected). Updated by `submit.py` after Jira
writes with post-submit hashes.

```yaml
query_timestamp: "2026-04-02T19:54:36Z"
timestamp: "2026-04-02T19:54:37Z"
bootstrapped_from: "20260402-195436"   # only present on bootstrap snapshots
issues:
  RHAIRFE-1234: "a1b2c3..."
  RHAIRFE-5678: "d4e5f6..."
```

## Content Hashing Pipeline

```
Jira ADF description
  -> adf_to_markdown()
  -> normalize_for_hash()     # collapse whitespace, normalize quotes
  -> SHA256
```

The normalization step ensures that trivial formatting differences
(indentation, blank lines, curly vs straight quotes) don't produce false
positives.

Both fetch and submit use the same pipeline. `submit.py` converts its
artifact markdown to ADF (via `markdown_to_adf`) for the Jira write,
then hashes that ADF through the same pipeline fetch uses
(ADF → markdown → normalize → SHA256). This roundtrip ensures the
post-submit hash matches what the next fetch will compute from Jira's
stored ADF, avoiding false positives from conversion differences.

## Command Sequence

A full CI run executes these steps in order:

```bash
# 1. Fetch and diff
#    Reads:   artifacts/auto-fix-runs/issue-snapshot-*.yaml  (previous snapshot)
#    Writes:  artifacts/auto-fix-runs/issue-snapshot-<ts>.yaml (new snapshot)
#             tmp/autofix-all-ids.txt                        (all IDs to process)
#             tmp/autofix-changed-ids.txt                    (changed-only IDs)
python3 scripts/snapshot_fetch.py fetch "<jql>" \
  --ids-file tmp/autofix-all-ids.txt \
  --changed-file tmp/autofix-changed-ids.txt \
  [--limit 100] [--data-dir "<path>"]

# 2. Review and process (auto-fix pipeline steps 1-5)
#    Reads:   tmp/autofix-all-ids.txt, tmp/autofix-changed-ids.txt
#    Writes:  artifacts/rfe-tasks/*, artifacts/rfe-reviews/*
#             artifacts/auto-fix-runs/<run-id>.yaml (run report)

# 3. Submit to Jira
#    Reads:   artifacts/rfe-tasks/*, artifacts/rfe-reviews/*
#    Updates: artifacts/auto-fix-runs/issue-snapshot-<ts>.yaml (post-submit hashes)
python3 scripts/submit.py

# 4. Push everything
git add artifacts/
git commit -m "run results"
git push
```

### State Files

| File | Written by | Read by | Lifetime |
|------|-----------|---------|----------|
| `artifacts/auto-fix-runs/issue-snapshot-<ts>.yaml` | `snapshot_fetch.py fetch`, updated by `submit.py` | `snapshot_fetch.py fetch` (next run) | Permanent (accumulates) |
| `artifacts/auto-fix-runs/<YYYYMMDD-HHMMSS>.yaml` | auto-fix pipeline (run report) | `bootstrap_snapshot.py` | Permanent (one per run) |
| `tmp/autofix-all-ids.txt` | `snapshot_fetch.py fetch` | auto-fix pipeline, `check_resume.py` | Current run only |
| `tmp/autofix-changed-ids.txt` | `snapshot_fetch.py fetch` | `check_resume.py` | Current run only |

### Ordering Constraints

The command sequence is ordered to minimize data loss on failure:

1. **Submit updates the snapshot**: After Jira API calls succeed,
   `submit.py` updates the snapshot with post-submit hashes so the
   next fetch doesn't re-flag our own changes.

2. **Single push at the end**: Everything is pushed once after all
   work is done. If the push fails, submitted issues are re-processed
   on the next run (safe but redundant — the conflict check in
   `submit.py` catches identical content).

## Pipeline Data Flow (Steady State)

```
  from previous run

  snapshot ──► ┌─────────────────────┐
               │ 1. FETCH            │
               │ load prev snapshot  │
               │ fetch Jira state    │
               │ diff vs previous    │
               │ select within limit │
               │ write new snapshot  │
               └─────────┬───────────┘
                         │
                         ├── ids-file
                         ├── changed-file
                         │
               ┌─────────┴───────────┐
               │ 2. REVIEW / PROCESS │
               │ auto-fix pipeline   │
               │ (uses changed-file) │
               └─────────┬───────────┘
                         │
               ┌─────────┴───────────┐
               │ 3. SUBMIT           │
               │ Jira API calls      │
               │ update snapshot     │
               └─────────┬───────────┘
                         │
               ┌─────────┴───────────┐
               │ 4. GIT PUSH         │
               │ snapshot + results  │
               └─────────────────────┘
```

### Cumulative Merge

```
  previous snapshot ──► merge with selected ──► new snapshot
       (all prior        (only issues           (previous +
        entries)         within limit)           selected)
```

Unselected issues retain their previous hashes in the snapshot.
Issues never selected remain absent and stay NEW until selected.

## Diffing Logic

`diff_snapshots(current_issues, previous_data)` compares each issue's
current content hash against the previous snapshot:

- **UNCHANGED** (hash matches): issue's description hasn't changed since
  last selected. Included in output after changed/new issues. When a
  limit is set, unchanged issues fill remaining capacity.
- **CHANGED** (hash differs): issue was previously selected but its
  description has been modified since. Gets priority ordering and
  bypasses `check_resume`.
- **NEW** (not in previous snapshot): issue has never been selected.
  Gets priority ordering but goes through normal `check_resume`.
- **In previous but not current**: issue left scope (closed, filtered).
  Retained in the snapshot as an inert entry (no pruning) so that if
  reopened with an edited description, it surfaces as CHANGED.

### Selection and Limit

Changed and new issues get priority ordering. If they don't exhaust
the limit, unchanged issues fill the remaining capacity. Only selected
issues (within the limit) have their hashes merged into the new
snapshot. Unselected issues retain their previous hashes — enabling
stale-hash change detection on future runs.

## Post-Submit Snapshot Update

When `submit.py` updates or creates an issue in Jira, the description
in Jira changes. Without intervention, the next fetch would see a hash
mismatch and flag the issue as "changed" — re-processing our own work.

After all Jira writes succeed, `submit.py` updates the current
snapshot's issue hashes with the post-submit content hashes. This way
the snapshot reflects what Jira now has, and the next fetch correctly
treats these issues as unchanged.

This also handles newly created issues. Without the post-submit hash,
a new issue would appear as "new" on the next run. With it in the
snapshot, the issue is treated as known.

## Failure Modes

**Job dies during submit (before push):**
The snapshot was written during fetch but not yet updated with
post-submit hashes. On the next run, submitted issues show as
"changed" (hash mismatch) and get re-processed. This is safe but
redundant — the conflict check in `submit.py` catches identical
content, and the review pipeline is idempotent.

**Job dies after processing but before submit:**
Snapshot and reviews were written locally but no Jira writes happened.
In CI (ephemeral environment), local state is lost — equivalent to
"dies before fetch." Locally, the snapshot marks issues as seen; next
run treats them as UNCHANGED and `check_resume` skips them if passing
reviews exist. Safe but submit must be re-triggered manually.

**Job dies before fetch completes:**
Nothing was written. Next run starts from the same snapshot as this
run. All work is re-done. Safe.

## Hard Filters

The JQL query is wrapped with constraints that should not change the
desired processing outcome:

```
(<user-jql>) AND statusCategory != Done
             AND labels not in (rfe-creator-ignore)
```

These filters are "hard" because an issue matching them should always be
processed if its content has changed. Contrast with soft filters (e.g.
`labels in (needs-tech-review)`) which control which issues are
candidates but shouldn't affect change detection.

## Bootstrap

`bootstrap_snapshot.py` creates the initial snapshot for a project that
has prior CI run history, before the first incremental fetch.

```
  results directory             Jira
  ─────────────────             ────

  run dirs ──► latest run  JQL ──► fetch all issues
               timestamp              │
               │                      ▼
               │    run report ──► filter to
               │                   processed IDs
               │                      │
               │    updated >= ──► updated keys
               │    run timestamp     │
               │            ┌──────┘
               │            │
               │            ▼  for each:
               │       ┌──────────────────┐
               │       │ changelog lookup │
               │       │ desc ──► hash    │
               │       │ status ──► excl. │
               │       └────────┬─────────┘
               │                │
               ▼                ▼
  run report ──► auto-   historical hashes
             revised IDs (pre-submit state)
                   │          │
                   ▼          ▼
              ┌─────────────────┐
              │ build snapshot  │
              │ unchanged ──► c.│
              │ updated ──► h.  │
              │ revised ──► c.  │
              │ Done ──► skip   │
              └────────┬────────┘
                       │
                       ▼
                issue-snapshot
```

The bootstrap snapshot accounts for:
- **Run-report filtering**: Only issues listed in the latest run
  report's `per_rfe` list are included in the snapshot. Issues that
  were open but not processed by the previous run remain absent,
  correctly surfacing as NEW on the first incremental fetch. If no
  run report is found, all fetched issues are included as a fallback.
- Historical descriptions at the run time (for externally modified issues)
- Current descriptions for auto-revised issues (our own submissions)
- Exclusion of issues that were out of scope (Done) at run time

This ensures the first incremental fetch after bootstrap only surfaces
issues that genuinely changed since the last CI run. The Done-status
check prevents reopened issues from being silently treated as "unchanged"
when they were never processed.

## Design Invariants

These invariants must hold and should guide future refactors:

1. **The snapshot only grows by selection.** An issue enters the
   snapshot when it is selected by `cmd_fetch` (within the limit) or
   added by `update_snapshot_hashes` (post-submit). No other path
   adds entries.

2. **The snapshot never shrinks.** Closed or filtered issues remain
   as inert entries. This ensures that reopened issues with edited
   descriptions are detected via hash mismatch (CHANGED), bypassing
   resume check.

3. **Hash staleness is correct, not a bug.** An unselected issue's
   hash may be many runs old. If the issue is later edited, the stale
   hash correctly differs from the current hash (CHANGED). If not
   edited, the stale hash still matches (UNCHANGED). Staleness is the
   mechanism that makes cumulative merge work.

4. **NEW issues do not bypass resume check.** Only CHANGED issues
   (hash mismatch) go in the `changed_file` and bypass `check_resume`.
   NEW issues (not in snapshot) go through normal resume check — if a
   passing review exists, they're skipped.

5. **Without a limit, behavior is identical to the old design.** When
   `limit = len(current)`, all issues are selected, all merged — the
   snapshot contains everything.

6. **`update_snapshot_hashes` is additive.** It uses `dict.update()`
   on the latest snapshot, adding or updating entries. This is
   compatible with cumulative snapshots.

## Known Gaps

- **Split children not added to snapshot.** `split_submit.py` creates
  child issues but does not update the snapshot with their hashes.
  They appear as NEW on the next run. Pre-existing gap, not introduced
  by cumulative merge.

- **NEW issues with stale passing reviews.** A NEW issue that happens
  to have a passing review from a prior run will be skipped by
  `check_resume`. Same behavior as UNCHANGED with a stale review —
  pre-existing, not specific to this design.

## Scripts

| Script | Role |
|--------|------|
| `scripts/snapshot_fetch.py` | Fetch, diff, write snapshot and ID files |
| `scripts/check_resume.py` | Filter IDs to process; changed IDs bypass resume check |
| `scripts/submit.py` | Jira writes, update snapshot with post-submit hashes |
| `scripts/split_submit.py` | Split submissions (does not update snapshot — see Known Gaps) |
| `scripts/bootstrap_snapshot.py` | Initial snapshot from prior run history |
