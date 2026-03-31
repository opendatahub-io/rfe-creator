#!/usr/bin/env python3
"""Identify already-processed RFE IDs for resume support.

Checks which IDs have passing review files (pass=true, no error field).
Outputs PROCESS= and SKIP= lines for shell consumption.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from artifact_utils import find_review_file, read_frontmatter


def main():
    parser = argparse.ArgumentParser(
        description="Check which RFE IDs already have passing reviews")
    parser.add_argument("ids", nargs="+", help="RFE IDs to check")
    parser.add_argument("--artifacts-dir", default="artifacts",
                        help="Path to artifacts directory (default: artifacts)")
    args = parser.parse_args()

    process_ids = []
    skip_ids = []

    for rfe_id in args.ids:
        review_path = find_review_file(args.artifacts_dir, rfe_id)
        if review_path and os.path.exists(review_path):
            data, _ = read_frontmatter(review_path)
            if data.get("pass") is True and data.get("error") is None:
                skip_ids.append(rfe_id)
                continue
        process_ids.append(rfe_id)

    print(f"PROCESS={','.join(process_ids)}")
    print(f"SKIP={','.join(skip_ids)}")


if __name__ == "__main__":
    main()
