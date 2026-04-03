#!/usr/bin/env python3
"""Identify which RFE IDs need processing, accounting for resume and
change detection.

Supports two modes:

1. File-based (preferred): reads IDs and changed-IDs from files, writes
   the final process list to an output file. Changed IDs always bypass
   the resume check (their Jira content has changed, so local reviews
   are stale). New IDs are checked for existing passing reviews.

2. Positional args (legacy): accepts IDs as arguments, prints PROCESS=
   and SKIP= lines to stdout.

Usage:
    # File-based (used by auto-fix skill)
    python3 scripts/check_resume.py --ids-file tmp/autofix-all-ids.txt \\
        --changed-file tmp/autofix-changed-ids.txt \\
        --output-file tmp/autofix-process-ids.txt

    # Legacy positional args
    python3 scripts/check_resume.py RHAIRFE-1234 RHAIRFE-5678
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from artifact_utils import find_review_file, read_frontmatter


def read_ids_from_file(path):
    """Read IDs from a file, one per line. Returns empty list if missing."""
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def check_resume(ids, changed_ids, artifacts_dir):
    """Determine which IDs need processing.

    Changed IDs always need processing (bypass resume check).
    Other IDs are checked for existing passing reviews.

    Returns (process_ids, skip_ids).
    """
    changed_set = set(changed_ids)
    process_ids = []
    skip_ids = []

    for rfe_id in ids:
        # Changed IDs always bypass resume — local reviews are stale
        if rfe_id in changed_set:
            process_ids.append(rfe_id)
            continue

        # Check for existing passing review
        review_path = find_review_file(artifacts_dir, rfe_id)
        if review_path and os.path.exists(review_path):
            data, _ = read_frontmatter(review_path)
            if data.get("pass") is True and data.get("error") is None:
                skip_ids.append(rfe_id)
                continue
        process_ids.append(rfe_id)

    return process_ids, skip_ids


def main():
    parser = argparse.ArgumentParser(
        description="Check which RFE IDs need processing")
    parser.add_argument("ids", nargs="*", help="RFE IDs to check (legacy)")
    parser.add_argument("--ids-file",
                        help="File containing all IDs (one per line)")
    parser.add_argument("--changed-file",
                        help="File containing changed IDs (one per line)")
    parser.add_argument("--output-file",
                        help="File to write process IDs to (one per line)")
    parser.add_argument("--artifacts-dir", default="artifacts",
                        help="Path to artifacts directory (default: artifacts)")
    args = parser.parse_args()

    # File-based mode
    if args.ids_file:
        all_ids = read_ids_from_file(args.ids_file)
        changed_ids = read_ids_from_file(args.changed_file)
        process_ids, skip_ids = check_resume(
            all_ids, changed_ids, args.artifacts_dir)

        # Write output file
        if args.output_file:
            os.makedirs(os.path.dirname(args.output_file) or "tmp",
                        exist_ok=True)
            with open(args.output_file, "w", encoding="utf-8") as f:
                for id_ in process_ids:
                    f.write(f"{id_}\n")

        # Print counts for logging
        changed_count = len(set(changed_ids) & set(process_ids))
        print(f"PROCESS={len(process_ids)}")
        print(f"SKIP={len(skip_ids)}")
        print(f"CHANGED={changed_count}")
        return

    # Legacy positional args mode
    if not args.ids:
        parser.print_help()
        sys.exit(1)

    process_ids, skip_ids = check_resume(args.ids, [], args.artifacts_dir)
    print(f"PROCESS={','.join(process_ids)}")
    print(f"SKIP={','.join(skip_ids)}")


if __name__ == "__main__":
    main()
