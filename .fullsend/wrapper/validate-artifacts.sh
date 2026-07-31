#!/usr/bin/env bash
set -euo pipefail

# Fullsend runs validation from the iteration directory. The extracted
# repo is at TARGET_REPO_DIR, and the agent works from the tmp/
# subdirectory where artifacts/ and scripts/ live.
cd "${TARGET_REPO_DIR:-.}/tmp"

errors=0

# Validate all task files
for f in artifacts/rfe-tasks/*.md; do
    [[ "$(basename "$f")" == *-comments.md ]] && continue
    python3 scripts/frontmatter.py read "$f" > /dev/null || errors=$((errors + 1))
done

# Validate all review files
for f in artifacts/rfe-reviews/*-review.md; do
    python3 scripts/frontmatter.py read "$f" > /dev/null || errors=$((errors + 1))
done

# Verify all rfe_ids belong to expected project or are DRAFT-NNN (split children)
for f in artifacts/rfe-tasks/*.md; do
    [[ "$(basename "$f")" == *-comments.md ]] && continue
    rfe_id=$(python3 scripts/frontmatter.py read "$f" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['rfe_id'])")
    # shellcheck disable=SC2254
    case "$rfe_id" in
        DRAFT-*) ;;
        "${JIRA_PROJECT}"-*) ;;
        *) echo "ERROR: unexpected project in rfe_id: $rfe_id"
           errors=$((errors + 1)) ;;
    esac
done

# Collect recommendations (exits non-zero if review files missing/broken)
python3 scripts/collect_recommendations.py --from-reviews || errors=$((errors + 1))

exit "$errors"
