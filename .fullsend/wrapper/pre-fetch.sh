#!/usr/bin/env bash
set -euo pipefail

# Ensure scripts/ is available in CWD by symlinking to the repo's scripts dir.
# The symlink at .fullsend/scripts -> ../scripts resolves through to the real dir.
if [[ ! -e scripts ]]; then
    REAL_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd -P)"
    ln -s "$REAL_SCRIPTS" scripts
fi

# Extract issue key from URL
# e.g., https://issues.redhat.com/browse/PROJ-1234 -> PROJ-1234
ISSUE_KEY="${FULLSEND_WORK_ITEM_URL##*/}"

# Fetch issue and write all artifact files (task, original, comments)
python3 scripts/fetch_issue.py "$ISSUE_KEY" --fetch-all artifacts
