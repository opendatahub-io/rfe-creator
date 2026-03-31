#!/usr/bin/env python3
"""Clean up orphan children from a failed split and un-archive the parent."""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from artifact_utils import (
    find_review_file,
    read_frontmatter,
    scan_task_files,
    update_frontmatter,
    find_artifact_file_including_archived,
)

ARTIFACTS_DIR = os.path.join(os.getcwd(), "artifacts")


def main():
    parser = argparse.ArgumentParser(
        description="Clean up orphan children from a failed split")
    parser.add_argument("parent_id", help="Parent RFE ID (e.g. RHAIRFE-100)")
    args = parser.parse_args()

    parent_id = args.parent_id
    tasks_dir = os.path.join(ARTIFACTS_DIR, "rfe-tasks")
    reviews_dir = os.path.join(ARTIFACTS_DIR, "rfe-reviews")

    # 1. Find and delete orphan children
    deleted = []
    for path, data in scan_task_files(ARTIFACTS_DIR):
        if data.get("parent_key") != parent_id:
            continue
        child_id = data["rfe_id"]
        basename = os.path.splitext(os.path.basename(path))[0]

        # Delete task file
        os.remove(path)

        # Delete companion files (comments, removed-context)
        for companion in glob.glob(
                os.path.join(tasks_dir, basename + "-*")):
            os.remove(companion)

        # Delete review file
        review = find_review_file(ARTIFACTS_DIR, child_id)
        if review:
            os.remove(review)

        deleted.append(os.path.basename(path))

    # 2. Delete split-status.yaml
    split_status = os.path.join(reviews_dir,
                                f"{parent_id}-split-status.yaml")
    if os.path.exists(split_status):
        os.remove(split_status)

    # 3. Un-archive the parent
    restored = ""
    parent_path = find_artifact_file_including_archived(
        ARTIFACTS_DIR, parent_id)
    if parent_path:
        fm, _ = read_frontmatter(parent_path)
        if fm.get("status") == "Archived":
            update_frontmatter(parent_path, {"status": "Ready"}, "rfe-task")
            restored = f"{parent_id} status=Ready"

    print(f"DELETED={','.join(deleted)}")
    print(f"RESTORED={restored}")


if __name__ == "__main__":
    main()
