#!/usr/bin/env python3
"""Check if split children are right-sized.

Reads review frontmatter for each ID and returns undersized IDs
(scores.right_sized < 2).

Usage:
    python3 scripts/check_right_sized.py ID1 ID2 ID3
    # stdout: RESPLIT=ID1 ID3
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from artifact_utils import read_frontmatter


def main():
    if len(sys.argv) < 2:
        print("Usage: check_right_sized.py ID1 [ID2 ...]", file=sys.stderr)
        sys.exit(1)

    ids = sys.argv[1:]
    undersized = []

    for rfe_id in ids:
        review_path = f"artifacts/rfe-reviews/{rfe_id}-review.md"
        if not os.path.exists(review_path):
            continue
        try:
            data, _ = read_frontmatter(review_path)
        except Exception:
            continue

        scores = data.get("scores", {})
        if isinstance(scores, dict):
            right_sized = scores.get("right_sized")
            if right_sized is not None and right_sized < 2:
                undersized.append(rfe_id)

    print(f"RESPLIT={' '.join(undersized)}")


if __name__ == "__main__":
    main()
