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
import re
import sys
import unicodedata

from jira_utils import require_env, get_issue, adf_to_markdown
from artifact_utils import scan_task_files


def _normalize_for_compare(text):
    """Normalize text to ignore ADF-to-markdown conversion artifacts.

    Handles: curly quotes, non-breaking spaces, carriage returns,
    dash/arrow variants, trailing whitespace, emoji, table alignment,
    and other Unicode normalization differences.
    """
    # Unicode normalize (NFC)
    text = unicodedata.normalize("NFC", text)
    # Carriage returns
    text = text.replace("\r", "")
    # Curly quotes -> straight
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    # Dashes: em dash -> —, en dash -> -  (normalize to ASCII)
    text = text.replace("\u2014", "---").replace("\u2013", "--")
    # Arrows: → -> ->
    text = text.replace("\u2192", "->")
    # Non-breaking space -> regular space
    text = text.replace("\xa0", " ")
    # Collapse multiple spaces to one (table alignment differences)
    text = re.sub(r"  +", " ", text)
    # Strip emoji (Unicode emoji blocks)
    text = re.sub(
        r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
        r"\U0001F680-\U0001F6FF\U0001F900-\U0001F9FF"
        r"\U00002702-\U000027B0\U0000FE00-\U0000FE0F]", "", text)
    # Normalize table separator rows (varying dash counts)
    text = re.sub(r"-{2,}", "--", text)
    # Strip auto-linked URLs: [url](url) -> url
    text = re.sub(r"\[([^\]]+)\]\(\1/?\.?\)", r"\1", text)
    # Strip zero-width characters
    text = re.sub(r"[\u200b\u200c\u200d\u2060\ufeff]", "", text)
    # Strip trailing whitespace per line
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    # Collapse multiple blank lines to one
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
            original_desc = _normalize_for_compare(f.read())

        # Fetch current Jira description
        try:
            issue = get_issue(server, user, token, rfe_id,
                              fields=["description"])
            fields = issue.get("fields", {})
            current_desc_raw = fields.get("description")
            if isinstance(current_desc_raw, dict):
                current_desc = _normalize_for_compare(
                    adf_to_markdown(current_desc_raw))
            elif current_desc_raw is None:
                current_desc = ""
            else:
                current_desc = _normalize_for_compare(
                    str(current_desc_raw))
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
