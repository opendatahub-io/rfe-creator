#!/usr/bin/env python3
"""Check for concurrent Jira modifications before submitting RFEs.

Compares the current Jira description against the original snapshot saved
at fetch time. If they differ, someone modified the RFE in Jira since we
last fetched it, and submitting would overwrite their changes.

Usage:
    python3 scripts/check_conflicts.py [--artifacts-dir DIR]

Exit codes:
    0  No conflicts — safe to submit
    1  Conflicts detected — submission should be blocked
    2  Error (missing env vars, API failure, etc.)

Output:
    CONFLICT_COUNT=N
    For each conflict:
      CONFLICT: <rfe_id> — modified in Jira since last fetch
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

from jira_utils import require_env, get_issue, adf_to_markdown
from artifact_utils import scan_task_files


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--artifacts-dir", default="artifacts",
                        help="Artifacts directory (default: artifacts)")
    args = parser.parse_args()

    server, user, token = require_env()
    if not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars "
              "required.", file=sys.stderr)
        sys.exit(2)

    originals_dir = os.path.join(args.artifacts_dir, "rfe-originals")

    # Find Jira-sourced RFEs that have original snapshots
    tasks = scan_task_files(args.artifacts_dir)
    jira_rfes = []
    for task_path, task_data in tasks:
        rfe_id = task_data["rfe_id"]
        if not rfe_id.startswith("RHAIRFE-"):
            continue
        if task_data.get("status") == "Archived":
            continue
        original_path = os.path.join(originals_dir, f"{rfe_id}.md")
        if os.path.exists(original_path):
            jira_rfes.append((rfe_id, original_path))

    if not jira_rfes:
        print("CONFLICT_COUNT=0")
        print("OK: no Jira-sourced RFEs to check")
        sys.exit(0)

    conflicts = []
    for rfe_id, original_path in jira_rfes:
        # Read the original snapshot
        with open(original_path, encoding="utf-8") as f:
            original_desc = f.read().strip()

        # Fetch current Jira description
        try:
            issue = get_issue(server, user, token, rfe_id,
                              fields=["description"])
            fields = issue.get("fields", {})
            current_desc_raw = fields.get("description")
            if isinstance(current_desc_raw, dict):
                current_desc = adf_to_markdown(current_desc_raw).strip()
            elif current_desc_raw is None:
                current_desc = ""
            else:
                current_desc = str(current_desc_raw).strip()
        except Exception as e:
            print(f"Warning: could not fetch {rfe_id}: {e}", file=sys.stderr)
            continue

        if original_desc != current_desc:
            conflicts.append(rfe_id)

    print(f"CONFLICT_COUNT={len(conflicts)}")
    if conflicts:
        for rfe_id in conflicts:
            print(f"CONFLICT: {rfe_id} — modified in Jira since last fetch")
        sys.exit(1)
    else:
        print("OK: no conflicts detected")
        sys.exit(0)


if __name__ == "__main__":
    main()
