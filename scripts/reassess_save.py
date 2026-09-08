#!/usr/bin/env python3
"""Save review state and delete stale files before reassessment.

Two operations that must happen together:
1. preserve_review_state.py save — saves before_score/before_scores
2. Delete review and assess result files (load-bearing for progress detection)

Feasibility files are intentionally NOT deleted — reused across cycles.

Usage:
    python3 scripts/reassess_save.py
    # Reads IDs from tmp/pipeline-reassess-ids.txt
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import type_registry

_TYPES = type_registry.load()
_TYPE_CONFIG = {
    name: {"reviews_dir": _TYPES.get(name).dirs()["reviews"]} for name in _TYPES.names()
}


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=_TYPES.choices(), default="rfe")
    args = parser.parse_args()

    reviews_dir = _TYPE_CONFIG[args.type]["reviews_dir"]

    ids_file = "tmp/pipeline-reassess-ids.txt"
    if not os.path.exists(ids_file):
        print("No reassess IDs file found", file=sys.stderr)
        sys.exit(1)

    with open(ids_file) as f:
        ids = [line.strip() for line in f if line.strip()]

    if not ids:
        print("REASSESS_SAVE: no IDs")
        return

    # Step 1: Save review state
    subprocess.run(["python3", "scripts/preserve_review_state.py", "save"] + ids, check=True)

    # Step 2: Delete stale files
    deleted = 0
    for rfe_id in ids:
        for path in [
            f"{reviews_dir}/{rfe_id}-review.md",
            f"tmp/rfe-assess/single/{rfe_id}.result.md",
        ]:
            if os.path.exists(path):
                os.remove(path)
                deleted += 1

    print(f"REASSESS_SAVE: saved {len(ids)} IDs, deleted {deleted} files")


if __name__ == "__main__":
    main()
