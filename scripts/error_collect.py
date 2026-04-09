#!/usr/bin/env python3
"""Collect error IDs, clean artifacts, and create a retry batch.

Must be idempotent — a crash at any point allows a safe re-run.

Usage:
    python3 scripts/error_collect.py
"""

import os
import shutil
import subprocess
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from artifact_utils import read_frontmatter

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


def main():
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
        ["python3", "scripts/collect_recommendations.py", "--errors"]
        + all_ids,
        capture_output=True, text=True)
    error_ids = []
    for line in result.stdout.splitlines():
        if line.startswith("ERRORS="):
            val = line.split("=", 1)[1].strip()
            if val:
                error_ids = [x.strip() for x in val.split(",") if x.strip()]
    if not error_ids:
        print("ERROR_COLLECT: no error IDs found")
        return

    # Step 3: Save error history
    error_details = {}
    for rfe_id in error_ids:
        review_path = f"artifacts/rfe-reviews/{rfe_id}-review.md"
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
        yaml.dump(error_details, f, default_flow_style=False,
                  sort_keys=False)

    # Step 4: Persist retry IDs
    _write_ids(RETRY_IDS_FILE, error_ids)

    # Step 5: Artifact cleanup
    for rfe_id in error_ids:
        err = error_details.get(rfe_id, {}).get("error", "")
        is_revise_error = "revise" in str(err).lower()
        is_split_error = "split" in str(err).lower()

        # Restore task file from original for revise errors
        if is_revise_error:
            orig = f"artifacts/rfe-originals/{rfe_id}.md"
            task = f"artifacts/rfe-tasks/{rfe_id}.md"
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
                        + [f"{k}={v}" for k, v in fm.items()
                           if k != "content"],
                        capture_output=True)
                os.rename(tmp, task)

        # Delete review and assessment artifacts
        for path in [
            f"artifacts/rfe-reviews/{rfe_id}-review.md",
            f"artifacts/rfe-reviews/{rfe_id}-feasibility.md",
            f"/tmp/rfe-assess/single/{rfe_id}.md",
            f"/tmp/rfe-assess/single/{rfe_id}.result.md",
        ]:
            if os.path.exists(path):
                os.remove(path)

        # Delete removed-context for revise errors
        if is_revise_error:
            rc = f"artifacts/rfe-tasks/{rfe_id}-removed-context.yaml"
            if os.path.exists(rc):
                os.remove(rc)

        # Clean up split artifacts
        if is_split_error:
            split_status = (f"artifacts/rfe-reviews/"
                            f"{rfe_id}-split-status.yaml")
            if os.path.exists(split_status):
                os.remove(split_status)
            # Clean children via cleanup_partial_split.py
            subprocess.run(
                ["python3", "scripts/cleanup_partial_split.py", rfe_id],
                capture_output=True)

    # Step 6: Post-cleanup verification
    warnings = []
    for rfe_id in error_ids:
        for path in [
            f"/tmp/rfe-assess/single/{rfe_id}.result.md",
            f"artifacts/rfe-reviews/{rfe_id}-review.md",
            f"artifacts/rfe-reviews/{rfe_id}-feasibility.md",
        ]:
            if os.path.exists(path):
                warnings.append(f"  stale: {path}")
                os.remove(path)  # retry delete
    if warnings:
        print("WARNING: stale artifacts found after cleanup:",
              file=sys.stderr)
        for w in warnings:
            print(w, file=sys.stderr)

    # Step 7: Write retry batch file (idempotent guard)
    total = state.get("total_batches", 0)
    retry_batch_file = f"tmp/pipeline-batch-{total + 1}-ids.txt"
    if not os.path.exists(retry_batch_file):
        state["total_batches"] = total + 1
        _save_state(state)
        _write_ids(retry_batch_file, error_ids)

    print(f"ERROR_COLLECT: retry batch with {len(error_ids)} error IDs"
          f" [{', '.join(error_ids)}]")
    for rfe_id, details in error_details.items():
        print(f"  {rfe_id}: {details.get('error', 'unknown')}")


if __name__ == "__main__":
    main()
