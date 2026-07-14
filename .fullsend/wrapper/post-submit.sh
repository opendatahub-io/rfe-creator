#!/usr/bin/env bash
set -euo pipefail

# Ensure scripts/ is available in CWD (see pre-fetch.sh for explanation)
if [[ ! -e scripts ]]; then
    REAL_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd -P)"
    ln -s "$REAL_SCRIPTS" scripts
fi

# Get recommendations — outputs lines like SUBMIT=PROJ-1234,DRAFT-001
eval "$(python3 scripts/collect_recommendations.py --from-reviews)"

if [[ -z "${SUBMIT:-}" ]]; then
    echo "No RFEs recommended for submission. Done."
    exit 0
fi

# Submit all passing RFEs
python3 scripts/submit.py --ids "$SUBMIT" --artifacts-dir artifacts
