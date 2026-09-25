#!/usr/bin/env bash
# pre-rfe-creator.sh — Validate inputs on the host before the sandbox starts.
# Warnings only: missing credentials must not fail dry-runs.
set -euo pipefail

echo "::notice::rfe-creator pre-script starting"

missing=()
[[ -z "${JIRA_SERVER:-}" ]] && missing+=("JIRA_SERVER")
[[ -z "${JIRA_USER:-}" ]] && missing+=("JIRA_USER")
[[ -z "${JIRA_TOKEN:-}" ]] && missing+=("JIRA_TOKEN")
[[ -z "${GH_TOKEN:-}" ]] && missing+=("GH_TOKEN")
[[ -z "${FULLSEND_TASK:-}" ]] && missing+=("FULLSEND_TASK")
[[ -z "${GOOGLE_APPLICATION_CREDENTIALS:-}" ]] && missing+=("GOOGLE_APPLICATION_CREDENTIALS")

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "WARNING: Missing variables: ${missing[*]}"
  echo "Dry-run and skipped-task runs can continue; live Jira/GitHub work will fail."
else
  echo "Credentials validated"
fi

echo "::notice::rfe-creator pre-script complete"
