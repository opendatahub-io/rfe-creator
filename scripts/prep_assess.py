#!/usr/bin/env python3
"""Prepare a single RFE for assessment by the assess-rfe plugin.

Combines prep_single (cleanup) + copy of the task file into the assessment
directory. This replaces the two-step process in rfe.review (prep_single + cp).

Usage:
    python3 scripts/prep_assess.py RHAIRFE-1234
    python3 scripts/prep_assess.py RFE-001

Outputs:
    FILE=/tmp/rfe-assess/single/<ID>.md
"""

import os
import sys


SINGLE_DIR = "/tmp/rfe-assess/single"
TASK_DIR = os.path.join("artifacts", "rfe-tasks")


def main():
    if len(sys.argv) < 2:
        print("Usage: prep_assess.py ID", file=sys.stderr)
        sys.exit(1)

    rfe_id = sys.argv[1]
    os.makedirs(SINGLE_DIR, exist_ok=True)

    # Clean up stale files (same as prep_single.py)
    for suffix in (".md", ".result.md"):
        path = os.path.join(SINGLE_DIR, f"{rfe_id}{suffix}")
        if os.path.exists(path):
            os.remove(path)

    # Copy task file
    src = os.path.join(TASK_DIR, f"{rfe_id}.md")
    if not os.path.isfile(src):
        print(f"ERROR: Task file not found: {src}", file=sys.stderr)
        sys.exit(1)

    dst = os.path.join(SINGLE_DIR, f"{rfe_id}.md")
    with open(src, "r", encoding="utf-8") as f:
        content = f.read()
    with open(dst, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"FILE={dst}")


if __name__ == "__main__":
    main()
