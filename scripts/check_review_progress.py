"""Phase-aware progress checker for agent polling.

Reports completion status for a list of RFE IDs based on the current phase.
"""

import argparse
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(__file__))
from artifact_utils import read_frontmatter


PHASE_CHECKS = {
    "fetch": lambda id: f"artifacts/rfe-tasks/{id}.md",
    "assess": lambda id: f"/tmp/rfe-assess/single/{id}.result.md",
    "feasibility": lambda id: f"artifacts/rfe-reviews/{id}-feasibility.md",
    "review": lambda id: f"artifacts/rfe-reviews/{id}-review.md",
    "revise": lambda id: f"artifacts/rfe-reviews/{id}-review.md",
    "split": lambda id: f"artifacts/rfe-reviews/{id}-split-status.yaml",
}


def check_id(phase, rfe_id):
    """Check one ID. Returns 'completed', 'pending', or 'error'."""
    path = PHASE_CHECKS[phase](rfe_id)
    if not os.path.exists(path):
        return "pending"
    if phase == "review":
        try:
            data, _ = read_frontmatter(path)
        except Exception:
            return "pending"
        if not data.get("score"):
            return "pending"
        if data.get("error"):
            return "error"
    if phase == "revise":
        try:
            data, _ = read_frontmatter(path)
        except Exception:
            return "pending"
        if data.get("auto_revised"):
            return "completed"
        return "pending"
    return "completed"


def main():
    parser = argparse.ArgumentParser(
        description="Check review pipeline progress by phase")
    parser.add_argument("--phase", required=True,
                        choices=list(PHASE_CHECKS.keys()),
                        help="Pipeline phase to check")
    parser.add_argument("--id-file",
                        help="File containing IDs (one per line or "
                             "space-separated)")
    parser.add_argument("--fast-poll", action="store_true",
                        help="Cap poll interval at 15s (interactive mode). "
                             "Auto-enabled when config files show headless=false.")
    parser.add_argument("ids", nargs="*", metavar="ID",
                        help="RFE IDs to check")
    args = parser.parse_args()

    ids = args.ids
    if args.id_file:
        with open(args.id_file) as f:
            ids = f.read().split()
    if not ids:
        print("No IDs provided", file=sys.stderr)
        sys.exit(2)

    completed = 0
    errors = 0
    pending_ids = []

    for rfe_id in ids:
        result = check_id(args.phase, rfe_id)
        if result == "completed":
            completed += 1
        elif result == "error":
            errors += 1
        else:
            pending_ids.append(rfe_id)

    total = len(ids)
    pending = len(pending_ids)
    parts = [f"COMPLETED={completed}/{total}"]
    if pending:
        parts.append(f"PENDING={pending}")
    if errors:
        parts.append(f"ERRORS={errors}")

    # Auto-detect interactive mode from config files
    fast = args.fast_poll
    if not fast:
        for cfg in ("tmp/review-config.yaml", "tmp/split-config.yaml",
                     "tmp/autofix-config.yaml", "tmp/speedrun-config.yaml"):
            if os.path.exists(cfg):
                try:
                    with open(cfg) as f:
                        data = yaml.safe_load(f)
                    if data and data.get("headless") is False:
                        fast = True
                        break
                except Exception:
                    pass

    # Suggest next poll interval based on completion ratio
    if pending == 0:
        next_poll = 0
    elif fast:
        next_poll = 15
    elif completed / total >= 0.75:
        next_poll = 15
    elif completed / total >= 0.5:
        next_poll = 30
    else:
        next_poll = 60
    parts.append(f"NEXT_POLL={next_poll}")

    print(", ".join(parts))


if __name__ == "__main__":
    main()
