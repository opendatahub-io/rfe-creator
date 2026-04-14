#!/usr/bin/env python3
"""Post-barrier verification for agent phases.

Checks that expected output files exist for each ID after a phase
completes. Missing outputs are treated as agent failures — error
frontmatter is written and the ID is removed from the active set.

Usage:
    python3 scripts/verify_phase.py --phase assess --ids-file tmp/pipeline-active-ids.txt
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from artifact_utils import read_frontmatter

PHASE_OUTPUT = {
    "fetch": lambda id: f"artifacts/rfe-tasks/{id}.md",
    "assess": lambda id: f"/tmp/rfe-assess/single/{id}.result.md",
    "feasibility": lambda id: f"artifacts/rfe-reviews/{id}-feasibility.md",
    "review": lambda id: f"artifacts/rfe-reviews/{id}-review.md",
    "split": lambda id: f"artifacts/rfe-reviews/{id}-split-status.yaml",
}


def verify(phase, ids_file):
    ids = []
    if os.path.exists(ids_file):
        with open(ids_file) as f:
            ids = [line.strip() for line in f if line.strip()]

    if not ids:
        print("FAILED=")
        return

    output_fn = PHASE_OUTPUT.get(phase)
    if not output_fn:
        print(f"Unknown phase: {phase}", file=sys.stderr)
        sys.exit(1)

    failed = []
    for rfe_id in ids:
        path = output_fn(rfe_id)
        exists = os.path.exists(path)

        # For review phase, also check that score is set
        if exists and phase == "review":
            try:
                data, _ = read_frontmatter(path)
                if data.get("score") is None:
                    exists = False
            except Exception:
                exists = False

        if not exists:
            failed.append(rfe_id)
            # Write error frontmatter via frontmatter.py (creates file if needed)
            review_path = f"artifacts/rfe-reviews/{rfe_id}-review.md"
            error_msg = f"{phase}_failed"
            try:
                subprocess.run([
                    "python3", "scripts/frontmatter.py", "set", review_path,
                    f"rfe_id={rfe_id}",
                    f"error={error_msg}",
                    "score=0", "pass=false", "recommendation=revise",
                    "feasibility=feasible", "auto_revised=false",
                    "needs_attention=true",
                    f"needs_attention_reason=Agent failed: {error_msg}",
                    "scores.what=0", "scores.why=0",
                    "scores.open_to_how=0", "scores.not_a_task=0",
                    "scores.right_sized=0",
                ], check=True, capture_output=True)
            except Exception:
                pass

    # Remove failed IDs from active set
    if failed:
        failed_set = set(failed)
        remaining = [id_ for id_ in ids if id_ not in failed_set]
        with open(ids_file, "w") as f:
            for id_ in remaining:
                f.write(f"{id_}\n")

    print(f"FAILED={','.join(failed)}")


def main():
    parser = argparse.ArgumentParser(
        description="Post-barrier verification for agent phases")
    parser.add_argument("--phase", required=True,
                        choices=list(PHASE_OUTPUT.keys()),
                        help="Phase to verify")
    parser.add_argument("--ids-file", required=True,
                        help="File containing IDs to check")
    args = parser.parse_args()
    verify(args.phase, args.ids_file)


if __name__ == "__main__":
    main()
