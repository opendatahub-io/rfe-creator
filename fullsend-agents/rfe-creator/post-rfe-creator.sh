#!/usr/bin/env bash
# post-rfe-creator.sh — Report agent-result.json after the sandbox exits.
set -euo pipefail

RESULT_FILE="${FULLSEND_VALIDATED_ITERATION_DIR}/agent-result.json"

if [[ ! -f "${RESULT_FILE}" ]]; then
  echo "ERROR: agent-result.json not found at ${RESULT_FILE}, $(pwd)"
  exit 1
fi

ACTION=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["action"])' "${RESULT_FILE}")
PIPELINE=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("pipeline", ""))' "${RESULT_FILE}")
SUMMARY=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("summary", ""))' "${RESULT_FILE}")

echo "Result: action=${ACTION} pipeline=${PIPELINE}"
echo "Summary: ${SUMMARY}"

if [[ "${ACTION}" == "failed" ]]; then
  exit 1
fi

if [[ -z "${REPO_DIR:-}" ]]; then
  echo "ERROR: downloaded repository is unavailable"
  exit 1
fi

if [[ -d "${REPO_DIR}/tmp" ]]; then
  cp -r -- "${REPO_DIR}/tmp" "${TARGET_REPO_DIR}"
fi
if [[ -d -- "${REPO_DIR}/artifacts" ]]; then
  cp -r "${REPO_DIR}/artifacts" "${TARGET_REPO_DIR}"
fi
