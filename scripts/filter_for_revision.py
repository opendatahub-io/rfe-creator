#!/usr/bin/env python3
"""Filter IDs to those needing revision, rejecting score regressions.

Supports both RFE IDs (RHAIRFE-*, RFE-*) and Initiative IDs (RHOAIENG-*, INIT-*).

For each ID:
- If score < before_score: sets recommendation=autorevise_reject (revision made it worse)
- If pass=true: skip (already passing)
- If feasibility=infeasible: skip (can't be fixed by revision)
- If recommendation=reject or autorevise_reject: skip
- Otherwise: include in output for revision

Usage:
    python3 scripts/filter_for_revision.py ID1 [ID2 ...]

Output:
    Space-separated IDs that should receive a revise agent, or empty if none.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import type_registry
from artifact_utils import read_frontmatter_validated, update_frontmatter

_TYPES = type_registry.load()


def _review_path_and_schema(item_id):
    """Review path and schema of the type that owns ``item_id``; anything unrecognised is an
    rfe (the default branch of the prefix sniff this replaces)."""
    desc = _TYPES.detect(item_id) or _TYPES.get("rfe")
    return f"{desc.dirs()['reviews']}/{item_id}-review.md", f"{desc.name}-review"


def main():
    if len(sys.argv) < 2:
        print("Usage: filter_for_revision.py ID1 [ID2 ...]", file=sys.stderr)
        sys.exit(1)

    ids = sys.argv[1:]
    revise_ids = []

    for rfe_id in ids:
        review_path, schema = _review_path_and_schema(rfe_id)
        try:
            data, _ = read_frontmatter_validated(review_path, schema)
        except Exception as e:
            print(f"Warning: cannot read review for {rfe_id}: {e}", file=sys.stderr)
            continue

        score = data.get("score", 0)
        before_score = data.get("before_score")
        passed = data.get("pass", False)
        feasibility = data.get("feasibility", "feasible")
        recommendation = data.get("recommendation", "revise")

        # Check for score regression
        if before_score is not None and score < before_score:
            update_frontmatter(review_path, {"recommendation": "autorevise_reject"}, schema)
            print(
                f"{rfe_id}: score regressed ({before_score} -> {score}), setting autorevise_reject",
                file=sys.stderr,
            )
            continue

        if passed:
            continue

        if feasibility == "infeasible":
            continue

        if recommendation in ("reject", "autorevise_reject", "split"):
            continue

        revise_ids.append(rfe_id)

    print(" ".join(revise_ids))


if __name__ == "__main__":
    main()
