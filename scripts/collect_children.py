#!/usr/bin/env python3
"""Find child RFEs or Initiatives by parent_key and print them grouped by parent."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import type_registry
from artifact_utils import resolve_ids, scan_initiative_task_files, scan_task_files

_TYPES = type_registry.load()


def main():
    parser = argparse.ArgumentParser(description="Find child RFEs by parent_key")
    parser.add_argument("parent_ids", nargs="*", help="One or more parent IDs (e.g. RHAIRFE-100)")
    parser.add_argument(
        "--ids-file",
        help="Read parent IDs from a file (one per line) instead of positional args",
    )
    parser.add_argument(
        "--type",
        choices=_TYPES.choices(),
        default="rfe",
        help="Artifact type to scan (default: rfe)",
    )
    args = parser.parse_args()

    parent_ids = resolve_ids(args.parent_ids, args.ids_file)
    if not parent_ids:
        parser.error("no parent IDs provided (pass positionally or via --ids-file)")

    artifacts_dir = os.path.join(os.getcwd(), "artifacts")

    # The scan pair is still forked in artifact_utils (it collapses into one generic keyed on
    # dirs.tasks/schema.task in a later PR); select it by type name, read the id field from
    # the registry.
    if args.type == "initiative":
        tasks = scan_initiative_task_files(artifacts_dir)
    else:
        tasks = scan_task_files(artifacts_dir)
    id_field = _TYPES.get(args.type).id_field

    # Build parent -> children mapping
    children_by_parent = {pid: [] for pid in parent_ids}
    for _path, data in tasks:
        parent = data.get("parent_key")
        if parent and parent in children_by_parent:
            if data.get("status") != "Archived":
                children_by_parent[parent].append(data[id_field])

    for pid in parent_ids:
        kids = ",".join(children_by_parent[pid])
        print(f"{pid}:{kids}")


if __name__ == "__main__":
    main()
