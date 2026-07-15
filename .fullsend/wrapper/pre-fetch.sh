#!/usr/bin/env bash
set -euo pipefail

# Copy scripts/ into CWD so they're available both here (host) and inside the
# sandbox (where a symlink to a host path would be broken).
if [[ ! -e scripts ]]; then
    REAL_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd -P)"
    cp -r "$REAL_SCRIPTS" scripts
fi

# Vendor PyYAML into scripts/ so it's on sys.path when the sandbox runs
# python3 scripts/*.py (Python adds the script's directory to sys.path[0]).
# Exclude the C extension (.so) — the sandbox lacks libyaml; PyYAML's
# pure-Python fallback works fine.
if [[ ! -d scripts/yaml ]]; then
    YAML_PKG="$(python3 -c "import yaml, os; print(os.path.dirname(yaml.__file__))")"
    mkdir -p scripts/yaml
    find "$YAML_PKG" -maxdepth 1 -name '*.py' -exec cp {} scripts/yaml/ \;
fi

# Bootstrap assess-rfe plugin (clones from GitHub on the host where
# network is available; the sandbox has no outbound access).
bash scripts/bootstrap-assess-rfe.sh

# Extract issue key from URL
# e.g., https://issues.redhat.com/browse/PROJ-1234 -> PROJ-1234
ISSUE_KEY="${FULLSEND_WORK_ITEM_URL##*/}"

# Fetch issue and write all artifact files (task, original, comments)
python3 scripts/fetch_issue.py "$ISSUE_KEY" --fetch-all artifacts
