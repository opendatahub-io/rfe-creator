#!/usr/bin/env python3
"""Post-SPLIT-agent collection: route parents and gather child IDs.

For each parent:
- action=no-split → set recommendation=revise (R8)
- action=split with children → collect child IDs
- action=split but zero children → set recommendation=revise (R8a)

Writes child IDs to tmp/pipeline-split-children-ids.txt.

Usage:
    python3 scripts/split_collect.py
    # Reads parent IDs from tmp/pipeline-split-ids.txt
"""

import os
import subprocess
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from artifact_utils import update_frontmatter


def main():
    ids_file = "tmp/pipeline-split-ids.txt"
    if not os.path.exists(ids_file):
        print("No split IDs file found", file=sys.stderr)
        sys.exit(1)

    with open(ids_file) as f:
        parent_ids = [line.strip() for line in f if line.strip()]

    if not parent_ids:
        print("CHILDREN=0")
        _write_ids("tmp/pipeline-split-children-ids.txt", [])
        return

    all_children = []
    split_parents = []

    for pid in parent_ids:
        status_path = f"artifacts/rfe-reviews/{pid}-split-status.yaml"
        if not os.path.exists(status_path):
            # No split status — treat as no-split
            _set_revise(pid)
            continue

        with open(status_path) as f:
            status = yaml.safe_load(f) or {}

        action = status.get("action", "no-split")
        if action == "no-split":
            _set_revise(pid)
        else:
            split_parents.append(pid)

    # Collect children for all split parents at once
    if split_parents:
        result = subprocess.run(
            ["python3", "scripts/collect_children.py"] + split_parents,
            capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if ":" not in line:
                continue
            pid, children_str = line.split(":", 1)
            pid = pid.strip()
            children = [c.strip() for c in children_str.split(",")
                        if c.strip()]
            if children:
                all_children.extend(children)
            else:
                # Split attempted but no children found (R8a)
                _set_revise(pid)

    _write_ids("tmp/pipeline-split-children-ids.txt", all_children)
    print(f"CHILDREN={len(all_children)}")


def _set_revise(rfe_id):
    """Set recommendation=revise on the review file."""
    review_path = f"artifacts/rfe-reviews/{rfe_id}-review.md"
    if os.path.exists(review_path):
        update_frontmatter(review_path,
                           {"recommendation": "revise"}, "rfe-review")


def _write_ids(path, ids):
    os.makedirs(os.path.dirname(path) or "tmp", exist_ok=True)
    with open(path, "w") as f:
        for id_ in ids:
            f.write(f"{id_}\n")


if __name__ == "__main__":
    main()
