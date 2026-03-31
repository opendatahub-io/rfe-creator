"""Phase-aware progress checker for agent polling.

Reports completion status for a list of RFE IDs based on the current phase.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from artifact_utils import read_frontmatter


PHASE_CHECKS = {
    "fetch": lambda id: f"artifacts/rfe-tasks/{id}.md",
    "assess": lambda id: f"/tmp/rfe-assess/single/{id}.result.md",
    "feasibility": lambda id: f"artifacts/rfe-reviews/{id}-feasibility.md",
    "review": lambda id: f"artifacts/rfe-reviews/{id}-review.md",
    "split": lambda id: f"artifacts/rfe-reviews/{id}-split-status.yaml",
}


def check_id(phase, rfe_id):
    """Check one ID. Returns 'completed', 'pending', or 'error'."""
    path = PHASE_CHECKS[phase](rfe_id)
    if not os.path.exists(path):
        return "pending"
    if phase == "review":
        data, _ = read_frontmatter(path)
        if data.get("error"):
            return "error"
    return "completed"


def main():
    parser = argparse.ArgumentParser(
        description="Check review pipeline progress by phase")
    parser.add_argument("--phase", required=True,
                        choices=list(PHASE_CHECKS.keys()),
                        help="Pipeline phase to check")
    parser.add_argument("ids", nargs="+", metavar="ID",
                        help="RFE IDs to check")
    args = parser.parse_args()

    completed = 0
    errors = 0
    pending_ids = []

    for rfe_id in args.ids:
        result = check_id(args.phase, rfe_id)
        if result == "completed":
            completed += 1
        elif result == "error":
            errors += 1
        else:
            pending_ids.append(rfe_id)

    print(f"TOTAL={len(args.ids)}")
    print(f"COMPLETED={completed}")
    print(f"PENDING={','.join(pending_ids) if pending_ids else ''}")
    print(f"ERRORS={errors}")


if __name__ == "__main__":
    main()
