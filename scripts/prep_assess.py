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
        print("Usage: prep_assess.py ID | --clean-all", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "--clean-all":
        _clean_all()
        return

    rfe_id = sys.argv[1]
    os.makedirs(SINGLE_DIR, exist_ok=True)

    # Clean up stale files (same as prep_single.py)
    for suffix in (".md", ".result.md"):
        path = os.path.join(SINGLE_DIR, f"{rfe_id}{suffix}")
        if os.path.exists(path):
            os.remove(path)

    # Copy task file — validate it has substantive content to avoid
    # scoring empty/incomplete files during creation race conditions
    src = os.path.join(TASK_DIR, f"{rfe_id}.md")
    if not os.path.isfile(src):
        print(f"ERROR: Task file not found: {src}", file=sys.stderr)
        sys.exit(1)

    with open(src, "r", encoding="utf-8") as f:
        content = f.read()

    if len(content.strip()) < 50:
        print(f"ERROR: Task file too small ({len(content.strip())} chars),"
              f" may be incomplete: {src}", file=sys.stderr)
        sys.exit(1)

    dst = os.path.join(SINGLE_DIR, f"{rfe_id}.md")
    with open(dst, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"FILE={dst}")


def _clean_all():
    """Remove all files from the single-assessment directory.

    Called at the start of a pipeline run so that stale .result.md files
    from previous runs don't trip the Write tool's read-before-write guard.
    """
    if not os.path.isdir(SINGLE_DIR):
        return
    removed = 0
    for name in os.listdir(SINGLE_DIR):
        path = os.path.join(SINGLE_DIR, name)
        if os.path.isfile(path):
            os.remove(path)
            removed += 1
    print(f"CLEANED={removed} files from {SINGLE_DIR}")


if __name__ == "__main__":
    main()
