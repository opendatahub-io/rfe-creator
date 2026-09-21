"""Check if an RFE task file was revised compared to its original.

Modes:
  Single-pair:  check_revised.py <original> <task>
    Prints REVISED=true/false.  Used by orchestrator for one-off checks.

  Batch:  check_revised.py --batch [ID ...]
    Scans originals vs tasks for every ID (or all if none given),
    sets auto_revised in review frontmatter directly.  No LLM loop needed.
    --lower-only: the last-reader guard (REPORT transition, submit.py
    start-up) — a set flag on a task whose body still equals its original
    is lowered; nothing is ever raised. Prints LOWERED=<ids>.
    --artifacts-dir DIR: resolve the bare type dirs under DIR, not ./artifacts.

Usage:
    python3 scripts/check_revised.py artifacts/rfe-originals/ID.md artifacts/rfe-tasks/ID.md
    python3 scripts/check_revised.py --batch
    python3 scripts/check_revised.py --batch RHAIRFE-1504 RHAIRFE-1510
    python3 scripts/check_revised.py --type initiative --batch --ids-file tmp/ids.txt
    python3 scripts/check_revised.py --batch --lower-only --artifacts-dir /path/artifacts

An unregistered --type exits 2 with the registered type list.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import type_registry
from artifact_utils import find_review_file, read_frontmatter, read_ids_file, update_frontmatter

_TYPES = type_registry.load()
# Usage text: "rfe|initiative" today, following the registry when a type is added.
_TYPE_CHOICES = "|".join(_TYPES.choices())


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


# Bare dir form: this script joins onto artifacts_dir itself (Q13).
_TYPE_CONFIG = {
    name: {
        "originals_dir": _TYPES.get(name).dirs(form="bare")["originals"],
        "tasks_dir": _TYPES.get(name).dirs(form="bare")["tasks"],
        "review_schema": f"{name}-review",
    }
    for name in _TYPES.names()
}


def batch_mode(ids, artifacts_dir="artifacts", pipeline_type="rfe", lower_only=False):
    """Compare originals to tasks and set auto_revised in review frontmatter.

    ``lower_only`` is the last-reader guard (the REPORT transition and submit.py start-up):
    a set flag on a task whose body still equals its original is lowered, and nothing is
    ever raised — a removed-context companion can make task != original without a
    revision, so raising stays FIXUP's job over the revise ids. A revise agent's write can
    land after FIXUP (2026-09-21 stage dry run: the wave released at 09:36:43, FIXUP
    lowered the flag at 09:36:46, the agent's last write restored it at 09:37:06), and
    the last reader must not trust a flag the content contradicts.
    """
    # --type is validated against the registry in main() (type_registry.parse_type_arg), so
    # the lookup cannot miss for a CLI caller; the dict's keys ARE the registry names.
    tc = _TYPE_CONFIG[pipeline_type]
    originals_dir = os.path.join(artifacts_dir, tc["originals_dir"])
    tasks_dir = os.path.join(artifacts_dir, tc["tasks_dir"])

    # If no IDs given, discover from originals dir
    if not ids:
        if os.path.isdir(originals_dir):
            ids = [os.path.splitext(f)[0] for f in os.listdir(originals_dir) if f.endswith(".md")]
        else:
            ids = []

    changed = 0
    lowered = []
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
        if lower_only:
            if current and not revised:
                update_frontmatter(review, {"auto_revised": False}, tc["review_schema"])
                changed += 1
                lowered.append(rfe_id)
                print(f"{rfe_id}: auto_revised True -> False (task body equals the original)")
            continue
        if revised != current:
            update_frontmatter(review, {"auto_revised": revised}, tc["review_schema"])
            changed += 1
            print(f"{rfe_id}: auto_revised {current} -> {revised}")
        else:
            print(f"{rfe_id}: auto_revised={current} (correct)")

    if lower_only:
        print(f"LOWERED={','.join(lowered)}")
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


def _extract_option(argv, name):
    """Pull a ``--name <value>`` pair (or ``--name=<value>``) out of argv.

    Returns (remaining_argv, value or None). A flag without a value exits 2.
    """
    remaining = []
    value = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == name:
            if i + 1 >= len(argv):
                print(f"Error: {name} requires a value", file=sys.stderr)
                sys.exit(2)
            value = argv[i + 1]
            i += 2
            continue
        if arg.startswith(f"{name}="):
            value = arg.split("=", 1)[1]
            i += 1
            continue
        remaining.append(arg)
        i += 1
    return remaining, value


def main():
    # --type is hand-parsed by the registry's shared helper (design §5 rung 1): an unregistered
    # name, or a trailing flag, exits 2 with the registered list; rfe when the flag is absent.
    try:
        pipeline_type, argv = type_registry.parse_type_arg(_TYPES, sys.argv[1:])
    except type_registry.ResolveError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(exc.exit_code)

    if "--batch" in argv:
        rest, file_ids = _extract_ids_file(argv)
        rest, artifacts_dir = _extract_option(rest, "--artifacts-dir")
        lower_only = "--lower-only" in rest
        args = [a for a in rest if a not in ("--batch", "--lower-only")]
        batch_mode(
            args + file_ids,
            artifacts_dir=artifacts_dir or "artifacts",
            pipeline_type=pipeline_type,
            lower_only=lower_only,
        )
        return

    if len(argv) != 2:
        print(
            f"Usage: check_revised.py [--type {_TYPE_CHOICES}] <original> <task>", file=sys.stderr
        )
        print(f"       check_revised.py [--type {_TYPE_CHOICES}] --batch [ID ...]", file=sys.stderr)
        sys.exit(2)

    revised = check_pair(argv[0], argv[1])
    if revised is None:
        missing = argv[0] if not os.path.exists(argv[0]) else argv[1]
        print(f"FILE_MISSING={missing}")
        sys.exit(1)
    if revised:
        print("REVISED=true")
    else:
        print("REVISED=false")


if __name__ == "__main__":
    main()
