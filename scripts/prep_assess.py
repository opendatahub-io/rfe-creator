#!/usr/bin/env python3
"""Prepare a single RFE for assessment by the assess-rfe plugin.

Combines prep_single (cleanup) + copy of the task file into the assessment
directory. This replaces the two-step process in rfe.review (prep_single + cp).

Usage:
    python3 scripts/prep_assess.py RHAIRFE-1234
    python3 scripts/prep_assess.py RFE-001

Outputs:
    FILE=tmp/rfe-assess/single/<ID>.md
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import type_registry

_TYPES = type_registry.load()

# Type-neutral staging dir (design §10: byte-stable for every type).
SINGLE_DIR = "tmp/rfe-assess/single"


def _task_dir_for(issue_id):
    """``dirs.tasks`` of the type that owns ``issue_id``; anything unrecognised is an rfe
    (the default branch of the prefix sniff this replaces)."""
    desc = _TYPES.detect(issue_id) or _TYPES.get("rfe")
    return desc.dirs()["tasks"]


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
    src = os.path.join(_task_dir_for(rfe_id), f"{rfe_id}.md")
    if not os.path.isfile(src):
        print(f"ERROR: Task file not found: {src}", file=sys.stderr)
        sys.exit(1)

    with open(src, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.strip() or "---" not in content:
        print(f"SKIP: {rfe_id} — task file empty or missing frontmatter: {src}", file=sys.stderr)
        sys.exit(0)

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
