#!/usr/bin/env python3
"""Find child RFEs by parent_key and print them grouped by parent."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from artifact_utils import scan_task_files


def main():
    parser = argparse.ArgumentParser(
        description="Find child RFEs by parent_key")
    parser.add_argument("parent_ids", nargs="+",
                        help="One or more parent RFE IDs (e.g. RHAIRFE-100)")
    args = parser.parse_args()

    artifacts_dir = os.path.join(os.getcwd(), "artifacts")
    tasks = scan_task_files(artifacts_dir)

    # Build parent -> children mapping
    children_by_parent = {pid: [] for pid in args.parent_ids}
    for _path, data in tasks:
        parent = data.get("parent_key")
        if parent and parent in children_by_parent:
            if data.get("status") != "Archived":
                children_by_parent[parent].append(data["rfe_id"])

    for pid in args.parent_ids:
        kids = ",".join(children_by_parent[pid])
        print(f"{pid}:{kids}")


if __name__ == "__main__":
    main()
