"""Check if an RFE task file was revised compared to its original.

Compares file content (excluding YAML frontmatter) and reports whether
the files differ. Used by the review orchestrator to fix the revised flag
when the revise agent runs out of budget before setting it.

Usage:
    python3 scripts/check_revised.py artifacts/rfe-originals/ID.md artifacts/rfe-tasks/ID.md
"""

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


def main():
    if len(sys.argv) != 3:
        print("Usage: check_revised.py <original_file> <task_file>", file=sys.stderr)
        sys.exit(2)

    original_path = sys.argv[1]
    task_path = sys.argv[2]

    try:
        with open(original_path) as f:
            original = strip_frontmatter(f.read())
        with open(task_path) as f:
            task = strip_frontmatter(f.read())
    except FileNotFoundError as e:
        print(f"FILE_MISSING={e.filename}")
        sys.exit(1)

    if original.strip() != task.strip():
        print("REVISED=true")
    else:
        print("REVISED=false")


if __name__ == "__main__":
    main()
