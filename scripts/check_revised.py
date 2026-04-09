"""Check if an RFE task file was revised compared to its original.

Compares file content (excluding YAML frontmatter) and reports whether
the files differ. Used by the review orchestrator to fix the revised flag
when the revise agent runs out of budget before setting it.

Usage:
    python3 scripts/check_revised.py artifacts/rfe-originals/ID.md artifacts/rfe-tasks/ID.md
    python3 scripts/check_revised.py --batch ID1 ID2 ...
"""

import os
import subprocess
import sys


def strip_frontmatter(text):
    """Remove YAML frontmatter (--- delimited) from text."""
    lines = text.split('\n')
    if not lines or lines[0].strip() != '---':
        return text
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == '---':
            return '\n'.join(lines[i + 1:])
    return text


def check_pair(original_path, task_path):
    """Compare original and task file. Returns True if revised."""
    try:
        with open(original_path) as f:
            original = strip_frontmatter(f.read())
        with open(task_path) as f:
            task = strip_frontmatter(f.read())
    except FileNotFoundError:
        return None
    return original.strip() != task.strip()


def main_single():
    if len(sys.argv) != 3:
        print("Usage: check_revised.py <original_file> <task_file>",
              file=sys.stderr)
        sys.exit(2)

    result = check_pair(sys.argv[1], sys.argv[2])
    if result is None:
        print(f"FILE_MISSING")
        sys.exit(1)
    print(f"REVISED={'true' if result else 'false'}")


def main_batch():
    """Check multiple IDs and fix auto_revised flag where needed."""
    ids = sys.argv[2:]
    if not ids:
        print("FIXUP: 0 checked")
        return

    fixed = 0
    for rfe_id in ids:
        original = f"artifacts/rfe-originals/{rfe_id}.md"
        task = f"artifacts/rfe-tasks/{rfe_id}.md"
        review = f"artifacts/rfe-reviews/{rfe_id}-review.md"

        if not os.path.exists(review):
            continue

        revised = check_pair(original, task)
        if revised is None:
            continue

        if revised:
            # Content differs — ensure auto_revised is set
            subprocess.run(
                ["python3", "scripts/frontmatter.py", "set", review,
                 "auto_revised=true"],
                capture_output=True)
            fixed += 1
        else:
            # Content identical — clear false positive
            subprocess.run(
                ["python3", "scripts/frontmatter.py", "set", review,
                 "auto_revised=false"],
                capture_output=True)

    print(f"FIXUP: {len(ids)} checked, {fixed} revised")


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--batch":
        main_batch()
    else:
        main_single()


if __name__ == "__main__":
    main()
