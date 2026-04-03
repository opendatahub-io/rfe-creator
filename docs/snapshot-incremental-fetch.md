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
compares against the previous run's hashes. Only issues whose description
actually changed get processed.

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

Written by `snapshot_fetch.py fetch` at the start of each run. Updated
by `submit.py` after Jira writes with post-submit hashes. Contains the
content hash of every issue's description.

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
markdown to ADF (via `markdown_to_adf`) before hashing with
`compute_content_hash`, ensuring the hash matches what fetch will compute
from Jira's stored ADF. This avoids false positives from markdown-to-ADF
conversion differences (tables, blockquotes, nested lists).

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
| `tmp/autofix-all-ids.txt` | `snapshot_fetch.py fetch` | auto-fix pipeline | Current run only |
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
  ─────────────────

  issue-snapshot ──► ┌──────────────────────────────────┐
                     │ 1. FETCH                          │
                     │    load previous snapshot          │
                     │    fetch current Jira state        │
                     │    diff current vs previous        │
                     │    write new issue-snapshot        │
                     └───────────────┬──────────────────┘
                                     │
                            changed + new IDs
                                     │
                     ┌───────────────┴──────────────────┐
                     │ 2. REVIEW / PROCESS               │
                     │    (auto-fix pipeline)             │
                     └───────────────┬──────────────────┘
                                     │
                     ┌───────────────┴──────────────────┐
                     │ 3. SUBMIT                         │
                     │    Jira API calls                  │
                     │    update issue-snapshot in place   │
                     └───────────────┬──────────────────┘
                                     │
                     ┌───────────────┴──────────────────┐
                     │ 4. GIT PUSH                       │
                     │    snapshot + results              │
                     └──────────────────────────────────┘
```

## Diffing Logic

`diff_snapshots(current_issues, previous_data)` compares each issue's
current content hash against the previous snapshot:

- **Hash matches**: issue is unchanged, excluded from output
- **Hash differs**: issue is "changed", included in output
- **Not in previous**: issue is "new", included in output
- **In previous but not current**: issue left scope (closed, filtered),
  silently dropped

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
  results directory                Jira
  ─────────────────                ────

  run dirs ──► find latest    hard-filter JQL ──► fetch all current
               run timestamp                      issues + hashes
               │                                       │
               ▼                                       │
          run timestamp                                │
               │                                       ▼
               │                  updated >= run ──► updated issue keys
               │                  timestamp               │
               │                                          │
               │         ┌────────────────────────────────┘
               │         │
               │         ▼  for each updated issue:
               │    ┌─────────────────────────────────┐
               │    │ changelog lookup                 │
               │    │   description at run time → hash │
               │    │   status at run time → exclude   │
               │    │     if Done                      │
               │    └──────────────┬──────────────────┘
               │                   │
               ▼                   ▼
  run report ──► find auto-   historical hashes
                 revised IDs  (pre-submit state)
                      │            │
                      ▼            ▼
                 ┌──────────────────────────┐
                 │ build snapshot            │
                 │   unchanged → current hash│
                 │   updated → historical    │
                 │   auto-revised → current  │
                 │   Done at run time → skip │
                 └─────────────┬────────────┘
                               │
                               ▼
                        issue-snapshot
                     (initial snapshot for
                      first incremental fetch)
```

The bootstrap snapshot accounts for:
- Historical descriptions at the run time (for externally modified issues)
- Current descriptions for auto-revised issues (our own submissions)
- Exclusion of issues that were out of scope (Done) at run time

This ensures the first incremental fetch after bootstrap only surfaces
issues that genuinely changed since the last CI run. The Done-status
check prevents reopened issues from being silently treated as "unchanged"
when they were never processed.

## Scripts

| Script | Role |
|--------|------|
| `scripts/snapshot_fetch.py` | Fetch, diff, write snapshot |
| `scripts/submit.py` | Jira writes, update snapshot with post-submit hashes |
| `scripts/bootstrap_snapshot.py` | Initial snapshot from Jira history |
