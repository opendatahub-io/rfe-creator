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
import type_registry
from artifact_utils import update_frontmatter

_TYPES = type_registry.load()
_TYPE_CONFIG = {
    name: {"reviews_dir": _TYPES.get(name).dirs()["reviews"], "review_schema": f"{name}-review"}
    for name in _TYPES.names()
}


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=_TYPES.choices(), default="rfe")
    args = parser.parse_args()

    tc = _TYPE_CONFIG[args.type]

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
        status_path = f"{tc['reviews_dir']}/{pid}-split-status.yaml"
        if not os.path.exists(status_path):
            # No split status — treat as no-split
            _set_revise(pid, tc["reviews_dir"], tc["review_schema"])
            continue

        with open(status_path) as f:
            status = yaml.safe_load(f) or {}

        action = status.get("action", "no-split")
        if action == "no-split":
            _set_revise(pid, tc["reviews_dir"], tc["review_schema"])
        else:
            split_parents.append(pid)

    # Collect children for all split parents at once
    if split_parents:
        result = subprocess.run(
            ["python3", "scripts/collect_children.py", "--type", args.type] + split_parents,
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            if ":" not in line:
                continue
            pid, children_str = line.split(":", 1)
            pid = pid.strip()
            children = [c.strip() for c in children_str.split(",") if c.strip()]
            if children:
                all_children.extend(children)
            else:
                # Split attempted but no children found (R8a)
                _set_revise(pid, tc["reviews_dir"], tc["review_schema"])

    _write_ids("tmp/pipeline-split-children-ids.txt", all_children)
    print(f"CHILDREN={len(all_children)}")


def _set_revise(
    rfe_id,
    reviews_dir=_TYPE_CONFIG["rfe"]["reviews_dir"],
    review_schema=_TYPE_CONFIG["rfe"]["review_schema"],
):
    """Set recommendation=revise on the review file (defaults: the rfe type, as before)."""
    review_path = f"{reviews_dir}/{rfe_id}-review.md"
    if os.path.exists(review_path):
        update_frontmatter(review_path, {"recommendation": "revise"}, review_schema)


def _write_ids(path, ids):
    os.makedirs(os.path.dirname(path) or "tmp", exist_ok=True)
    with open(path, "w") as f:
        for id_ in ids:
            f.write(f"{id_}\n")


if __name__ == "__main__":
    main()
