"""Check if an RFE task file was revised compared to its original.

Modes:
  Single-pair:  check_revised.py <original> <task>
    Prints REVISED=true/false.  Used by orchestrator for one-off checks.

  Batch:  check_revised.py --batch [ID ...]
    Scans originals vs tasks for every ID (or all if none given),
    sets auto_revised in review frontmatter directly.  No LLM loop needed.

Usage:
    python3 scripts/check_revised.py artifacts/rfe-originals/ID.md artifacts/rfe-tasks/ID.md
    python3 scripts/check_revised.py --batch
    python3 scripts/check_revised.py --batch RHAIRFE-1504 RHAIRFE-1510
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from artifact_utils import find_review_file, read_frontmatter, read_ids_file, update_frontmatter


def strip_frontmatter(text):
    """Remove YAML frontmatter (--- delimited) from text."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return "\n".join(lines[i + 1 :])
    return text


def check_pair(original_path, task_path):
    """Return True if body content differs, False if same, None if file missing."""
    try:
        with open(original_path) as f:
            original = strip_frontmatter(f.read())
        with open(task_path) as f:
            task = strip_frontmatter(f.read())
    except FileNotFoundError:
        return None
    return original.strip() != task.strip()


def batch_mode(ids, artifacts_dir="artifacts"):
    """Compare originals to tasks and set auto_revised in review frontmatter."""
    originals_dir = os.path.join(artifacts_dir, "rfe-originals")
    tasks_dir = os.path.join(artifacts_dir, "rfe-tasks")

    # If no IDs given, discover from originals dir
    if not ids:
        ids = [os.path.splitext(f)[0] for f in os.listdir(originals_dir) if f.endswith(".md")]

    changed = 0
    for rfe_id in sorted(ids):
        original = os.path.join(originals_dir, f"{rfe_id}.md")
        task = os.path.join(tasks_dir, f"{rfe_id}.md")
        review = find_review_file(artifacts_dir, rfe_id)
        if not review:
            continue

        revised = check_pair(original, task)
        if revised is None:
            continue

        data, _ = read_frontmatter(review)
        current = data.get("auto_revised", False)
        if revised != current:
            update_frontmatter(review, {"auto_revised": revised}, "rfe-review")
            changed += 1
            print(f"{rfe_id}: auto_revised {current} -> {revised}")
        else:
            print(f"{rfe_id}: auto_revised={current} (correct)")

    print(f"UPDATED={changed}")


def _extract_ids_file(argv):
    """Pull an --ids-file <path> pair (or --ids-file=<path>) out of argv.

    Returns (remaining_argv, ids_from_file). Lets --batch read IDs from a
    file instead of forcing the skill to use $(...) command substitution.
    """
    remaining = []
    ids = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--ids-file":
            if i + 1 >= len(argv):
                print("Error: --ids-file requires a path argument", file=sys.stderr)
                sys.exit(2)
            ids.extend(read_ids_file(argv[i + 1]))
            i += 2
            continue
        if arg.startswith("--ids-file="):
            ids.extend(read_ids_file(arg.split("=", 1)[1]))
            i += 1
            continue
        remaining.append(arg)
        i += 1
    return remaining, ids


def main():
    if "--batch" in sys.argv:
        rest, file_ids = _extract_ids_file(sys.argv[1:])
        args = [a for a in rest if a != "--batch"]
        batch_mode(args + file_ids)
        return

    if len(sys.argv) != 3:
        print("Usage: check_revised.py <original> <task>", file=sys.stderr)
        print("       check_revised.py --batch [ID ...]", file=sys.stderr)
        sys.exit(2)

    revised = check_pair(sys.argv[1], sys.argv[2])
    if revised is None:
        missing = sys.argv[1] if not os.path.exists(sys.argv[1]) else sys.argv[2]
        print(f"FILE_MISSING={missing}")
        sys.exit(1)
    if revised:
        print("REVISED=true")
    else:
        print("REVISED=false")


if __name__ == "__main__":
    main()
