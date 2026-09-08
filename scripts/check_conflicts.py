#!/usr/bin/env python3
"""Check for concurrent Jira modifications before submitting.

Compares the current Jira description against the original snapshot saved
at fetch time. If they differ, someone modified the issue in Jira since we
last fetched it, and submitting would overwrite their changes.

Usage:
    python3 scripts/check_conflicts.py [--type rfe|initiative] [--artifacts-dir DIR]

Exit codes:
    0  No conflicts — safe to submit
    1  Conflicts detected — submission should be blocked
    2  Error (missing env vars, API failure, etc.)

Output:
    CONFLICT_COUNT=N
    For each conflict:
      CONFLICT: <id> — modified in Jira since last fetch
    If no conflicts:
      OK: no conflicts detected

Environment variables:
    JIRA_SERVER  Jira server URL
    JIRA_USER    Jira username/email
    JIRA_TOKEN   Jira API token
"""

import argparse
import os
import sys

import type_registry
from artifact_utils import scan_initiative_task_files, scan_task_files
from jira_utils import check_description_conflict, require_env

_TYPES = type_registry.load()

# The task scanners are still a forked pair in artifact_utils (they collapse into one generic
# later in the PR-2 series), so they are the one entry selected by type name rather than read
# from the descriptor. A registered type without a scanner gets None and is refused in main().
_SCAN_FNS = {"rfe": scan_task_files, "initiative": scan_initiative_task_files}

# Derived from the type registry (design work-item-types-unified.md §10 item 2): the bare dir
# form (Q13), identity.id_field and the descriptor write prefix identity.<tracker>.key_prefixes[0].
_TYPE_CONFIG = {
    name: {
        "originals_dir": _TYPES.get(name).dirs(form="bare")["originals"],
        "scan_fn": _SCAN_FNS.get(name),
        "id_field": _TYPES.get(name).id_field,
        "jira_prefix": _TYPES.get(name).key_prefixes[0],
    }
    for name in _TYPES.names()
}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--artifacts-dir", default="artifacts", help="Artifacts directory (default: artifacts)"
    )
    parser.add_argument("--type", choices=_TYPES.choices(), default="rfe")
    args = parser.parse_args()

    tc = _TYPE_CONFIG[args.type]
    if tc["scan_fn"] is None:
        print(f"Error: no task scanner is registered for type {args.type!r}.", file=sys.stderr)
        sys.exit(2)

    server, user, token = require_env()
    if not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars required.", file=sys.stderr)
        sys.exit(2)

    originals_dir = os.path.join(args.artifacts_dir, tc["originals_dir"])

    tasks = tc["scan_fn"](args.artifacts_dir)
    jira_items = []
    for task_path, task_data in tasks:
        item_id = task_data[tc["id_field"]]
        if not item_id.startswith(tc["jira_prefix"]):
            continue
        if task_data.get("status") == "Archived":
            continue
        original_path = os.path.join(originals_dir, f"{item_id}.md")
        if os.path.exists(original_path):
            jira_items.append((item_id, original_path))

    if not jira_items:
        print("CONFLICT_COUNT=0")
        print("OK: no Jira-sourced items to check")
        sys.exit(0)

    conflicts = []
    for item_id, original_path in jira_items:
        try:
            has_conflict, _ = check_description_conflict(
                server, user, token, item_id, original_path
            )
            if has_conflict:
                conflicts.append(item_id)
        except Exception as e:
            print(f"Warning: could not fetch {item_id}: {e}", file=sys.stderr)

    print(f"CONFLICT_COUNT={len(conflicts)}")
    if conflicts:
        for item_id in conflicts:
            print(f"CONFLICT: {item_id} — modified in Jira since last fetch")
        sys.exit(1)
    else:
        print("OK: no conflicts detected")
        sys.exit(0)


if __name__ == "__main__":
    main()
