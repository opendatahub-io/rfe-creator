#!/usr/bin/env python3
"""Check whether auto-fix processed all RFEs.

Reads the pipeline state and verifies every RFE has a review file.
Exits 0 if all complete, exits 1 with the list of missing IDs if not.

Usage:
    python3 scripts/check_autofix_complete.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    # Read all IDs from the speedrun state
    ids_file = "tmp/speedrun-all-ids.txt"
    if not os.path.exists(ids_file):
        print("ERROR: no speedrun ID list found", file=sys.stderr)
        sys.exit(1)

    with open(ids_file) as f:
        all_ids = [line.strip() for line in f if line.strip()]

    if not all_ids:
        print("ERROR: empty ID list", file=sys.stderr)
        sys.exit(1)

    # Check which IDs have review files
    reviews_dir = os.path.join("artifacts", "rfe-reviews")
    missing = []
    for rfe_id in all_ids:
        review_path = os.path.join(reviews_dir, f"{rfe_id}-review.md")
        if not os.path.exists(review_path):
            missing.append(rfe_id)

    if missing:
        print(f"INCOMPLETE: {len(missing)}/{len(all_ids)} RFEs missing reviews")
        print(f"MISSING_IDS={','.join(missing)}")
        sys.exit(1)
    else:
        print(f"COMPLETE: {len(all_ids)}/{len(all_ids)} RFEs reviewed")
        sys.exit(0)


if __name__ == "__main__":
    main()
