#!/usr/bin/env bash
set -euo pipefail

# Extract issue key from URL
# e.g., https://issues.redhat.com/browse/RHAIRFE-1234 -> RHAIRFE-1234
ISSUE_KEY="${FULLSEND_WORK_ITEM_URL##*/}"

# Fetch issue and write all artifact files (task, original, comments)
python3 scripts/fetch_issue.py "$ISSUE_KEY" --fetch-all artifacts
