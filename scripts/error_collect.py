#!/usr/bin/env python3
"""Collect error IDs, clean artifacts, and create a retry batch.

Must be idempotent — a crash at any point allows a safe re-run.

Usage:
    python3 scripts/error_collect.py [--type rfe|initiative]
"""

import os
import shutil
import subprocess
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import type_registry
from artifact_utils import read_frontmatter

_TYPES = type_registry.load()

STATE_FILE = "tmp/pipeline-state.yaml"
RETRY_ERRORS_FILE = "tmp/pipeline-retry-errors.yaml"
RETRY_IDS_FILE = "tmp/pipeline-retry-ids.txt"


def _load_state():
    with open(STATE_FILE) as f:
        return yaml.safe_load(f)


def _save_state(state):
    with open(STATE_FILE, "w") as f:
        yaml.dump(state, f, default_flow_style=False, sort_keys=False)


def _read_ids(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


def _write_ids(path, ids):
    os.makedirs(os.path.dirname(path) or "tmp", exist_ok=True)
    with open(path, "w") as f:
        for id_ in ids:
            f.write(f"{id_}\n")


_TYPE_CONFIG = {
    name: {
        "reviews_dir": _TYPES.get(name).dirs()["reviews"],
        "originals_dir": _TYPES.get(name).dirs()["originals"],
        "tasks_dir": _TYPES.get(name).dirs()["tasks"],
    }
    for name in _TYPES.names()
}


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=_TYPES.choices(), default="rfe")
    args = parser.parse_args()

    pipeline_type = args.type
    tc = _TYPE_CONFIG[pipeline_type]

    state = _load_state()

    # Step 1: Set retry_cycle = 1 FIRST (prevents infinite loops)
    state["retry_cycle"] = 1
    _save_state(state)

    # Step 2: Collect error IDs
    all_ids = _read_ids("tmp/pipeline-all-ids.txt")
    if not all_ids:
        print("ERROR_COLLECT: no IDs to check", file=sys.stderr)
        sys.exit(1)

    result = subprocess.run(
        ["python3", "scripts/collect_recommendations.py", "--type", pipeline_type, "--errors"]
        + all_ids,
        capture_output=True,
        text=True,
    )
    # A crashed or garbled collector must fail this phase loudly, not parse
    # to an empty list — empty means "nothing to retry", which sends the
    # pipeline to REPORT as if collection succeeded. Leave the retry file
    # untouched so a later re-run of this phase starts from honest state.
    if result.returncode != 0:
        print(
            f"ERROR_COLLECT: collect_recommendations failed "
            f"(exit {result.returncode}): {result.stderr.strip()}",
            file=sys.stderr,
        )
        sys.exit(1)

    error_ids = []
    saw_marker = False
    for line in result.stdout.splitlines():
        if line.startswith("ERRORS="):
            saw_marker = True
            val = line.split("=", 1)[1].strip()
            if val:
                error_ids = [x.strip() for x in val.split(",") if x.strip()]
    if not saw_marker:
        print(
            "ERROR_COLLECT: collect_recommendations output has no ERRORS= line "
            f"— refusing to treat that as zero errors. stdout: {result.stdout.strip()[:200]}",
            file=sys.stderr,
        )
        sys.exit(1)

    if not error_ids:
        # Clear the retry file rather than leaving whatever a previous cycle
        # wrote — the ERROR_COLLECT transition reads it to decide between a
        # retry batch and going straight to REPORT, and a stale non-empty
        # file would start a retry for IDs that are no longer erroring.
        _write_ids(RETRY_IDS_FILE, [])
        print("ERROR_COLLECT: no error IDs found")
        return

    # Step 3: Save error history
    error_details = {}
    for rfe_id in error_ids:
        review_path = f"{tc['reviews_dir']}/{rfe_id}-review.md"
        if os.path.exists(review_path):
            try:
                data, _ = read_frontmatter(review_path)
                error_details[rfe_id] = {
                    "error": data.get("error", "unknown"),
                }
            except Exception:
                error_details[rfe_id] = {"error": "unreadable_review"}
        else:
            error_details[rfe_id] = {"error": "no_review_file"}

    with open(RETRY_ERRORS_FILE, "w") as f:
        yaml.dump(error_details, f, default_flow_style=False, sort_keys=False)

    # Non-retryable classes stay OUT of the retry batch (RHAIFIRST-571):
    # split_refused: is routed to a human (RHAIFIRST-562), and
    # split_submit_failed: may be PARTIALLY APPLIED in Jira — its parent is
    # quarantined (RHAIFIRST-570) and an automated retry could mint
    # duplicate children. Both remain in the error history above.
    non_retryable = ("split_refused:", "split_submit_failed:", "split_not_attempted:")
    retryable_ids = [
        rfe_id
        for rfe_id in error_ids
        if not str(error_details.get(rfe_id, {}).get("error", "")).startswith(non_retryable)
    ]
    excluded = [rfe_id for rfe_id in error_ids if rfe_id not in retryable_ids]
    if excluded:
        print(f"ERROR_COLLECT: excluded from retry (non-retryable): {', '.join(excluded)}")
    if not retryable_ids:
        # Same contract as the zero-errors return above: a cleared file sends
        # the ERROR_COLLECT transition to REPORT instead of a retry batch.
        _write_ids(RETRY_IDS_FILE, [])
        print("ERROR_COLLECT: no retryable error IDs found")
        return

    # Step 4: Persist retry IDs
    _write_ids(RETRY_IDS_FILE, retryable_ids)

    # Step 5: Artifact cleanup
    for rfe_id in retryable_ids:
        err = error_details.get(rfe_id, {}).get("error", "")
        is_revise_error = "revise" in str(err).lower()
        # Prefix allow-list, not a substring test: split_refused: (nothing
        # created — cleanup pointless) and split_submit_failed: (Jira may be
        # PARTIALLY applied — cleanup would delete local child files whose
        # Jira twins already exist, orphaning them) must never trigger
        # cleanup_partial_split (RHAIFIRST-570). Only the agent-side
        # split_failed class, recorded before any Jira write, is safe to
        # clean and retry.
        is_split_error = str(err).startswith("split_failed")

        # Restore task file from original for revise errors
        if is_revise_error:
            orig = f"{tc['originals_dir']}/{rfe_id}.md"
            task = f"{tc['tasks_dir']}/{rfe_id}.md"
            if os.path.exists(orig) and os.path.exists(task):
                # Read current frontmatter
                try:
                    fm, _ = read_frontmatter(task)
                except Exception:
                    fm = {}
                # Atomic restore: copy original to temp, set frontmatter, rename
                tmp = task + ".tmp"
                shutil.copy2(orig, tmp)
                if fm:
                    subprocess.run(
                        ["python3", "scripts/frontmatter.py", "set", tmp]
                        + [f"{k}={v}" for k, v in fm.items() if k != "content"],
                        capture_output=True,
                    )
                os.rename(tmp, task)

        # Delete review and assessment artifacts
        for path in [
            f"{tc['reviews_dir']}/{rfe_id}-review.md",
            f"{tc['reviews_dir']}/{rfe_id}-feasibility.md",
            f"tmp/rfe-assess/single/{rfe_id}.md",
            f"tmp/rfe-assess/single/{rfe_id}.result.md",
        ]:
            if os.path.exists(path):
                os.remove(path)

        # Delete removed-context for revise errors
        if is_revise_error:
            rc = f"{tc['tasks_dir']}/{rfe_id}-removed-context.yaml"
            if os.path.exists(rc):
                os.remove(rc)

        # Clean up split artifacts
        if is_split_error:
            split_status = f"{tc['reviews_dir']}/{rfe_id}-split-status.yaml"
            if os.path.exists(split_status):
                os.remove(split_status)
            # Clean children via cleanup_partial_split.py. Without --type it
            # defaults to rfe and would scan rfe-tasks/ for an initiative's
            # children, finding none and leaving them orphaned.
            subprocess.run(
                [
                    "python3",
                    "scripts/cleanup_partial_split.py",
                    rfe_id,
                    "--type",
                    pipeline_type,
                ],
                capture_output=True,
            )

    # Step 6: Post-cleanup verification
    warnings = []
    for rfe_id in retryable_ids:
        for path in [
            f"tmp/rfe-assess/single/{rfe_id}.result.md",
            f"{tc['reviews_dir']}/{rfe_id}-review.md",
            f"{tc['reviews_dir']}/{rfe_id}-feasibility.md",
        ]:
            if os.path.exists(path):
                warnings.append(f"  stale: {path}")
                os.remove(path)  # retry delete
    if warnings:
        print("WARNING: stale artifacts found after cleanup:", file=sys.stderr)
        for w in warnings:
            print(w, file=sys.stderr)

    # Step 7: Write retry batch file (idempotent guard)
    # Keyed on the batch number recorded in state, not on the derived filename.
    # total_batches is bumped before the file is written, so re-deriving it on a
    # re-run points at the NEXT slot: the guard sees no file there, allocates a
    # second retry batch, and leaves the first one empty.
    retry_batch = state.get("retry_batch")
    if retry_batch is None:
        retry_batch = state.get("total_batches", 0) + 1
        state["retry_batch"] = retry_batch
        state["total_batches"] = retry_batch
        _save_state(state)
    _write_ids(f"tmp/pipeline-batch-{retry_batch}-ids.txt", retryable_ids)

    print(
        f"ERROR_COLLECT: retry batch with {len(retryable_ids)} error IDs "
        f"[{', '.join(retryable_ids)}]"
    )
    for rfe_id, details in error_details.items():
        print(f"  {rfe_id}: {details.get('error', 'unknown')}")


if __name__ == "__main__":
    main()
