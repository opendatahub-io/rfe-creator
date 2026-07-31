#!/usr/bin/env bash
set -euo pipefail

# Fullsend runs the post-script from the run directory. Resolve the
# repo root and cd into tmp/ where the agent wrote artifacts/.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$REPO_ROOT/tmp"

# Get recommendations — outputs lines like SUBMIT=PROJ-1234,DRAFT-001
eval "$(python3 scripts/collect_recommendations.py --from-reviews)"

if [[ -z "${SUBMIT:-}" ]]; then
    echo "No RFEs recommended for submission. Done."
    exit 0
fi

# Submit all passing RFEs
python3 scripts/submit.py --ids "$SUBMIT" --artifacts-dir artifacts
