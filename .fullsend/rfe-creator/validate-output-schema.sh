#!/usr/bin/env bash
# validate-output-schema.sh — Validate agent output against a JSON Schema.
#
# Generic script used by the harness validation_loop (ADR 0022).
# Works for any agent — the schema path is configured in the harness.
#
# Required env vars:
#   FULLSEND_OUTPUT_SCHEMA — path to the JSON Schema file
#
# Optional env vars:
#   FULLSEND_OUTPUT_FILE  — filename to validate (default: agent-result.json)
#
# The script looks for the output file in the iteration output directory.
# The working directory is the iteration dir (set by run.go).

set -euo pipefail

: "${FULLSEND_OUTPUT_SCHEMA:?FULLSEND_OUTPUT_SCHEMA must be set}"

# Find the output JSON file in this iteration's output directory.
OUTPUT_DIR="output"
if [[ ! -d "${OUTPUT_DIR}" ]]; then
  echo "FAIL: output directory not found"
  exit 1
fi

_output_file="${FULLSEND_OUTPUT_FILE:-agent-result.json}"
_output_file="$(basename "${_output_file}")"
RESULT_FILE="${OUTPUT_DIR}/${_output_file}"
if [[ ! -f "${RESULT_FILE}" ]]; then
  echo "FAIL: ${RESULT_FILE} not found"
  exit 1
fi
echo "Validating: ${RESULT_FILE} against ${FULLSEND_OUTPUT_SCHEMA}"

# Validate JSON is parseable.
if ! python3 -m json.tool "${RESULT_FILE}" > /dev/null 2>&1; then
  echo "FAIL: ${RESULT_FILE} is not valid JSON"
  exit 1
fi

# Validate against schema using Python's jsonschema.
# jsonschema is required — fail hard if not installed.
if ! python3 -c "import jsonschema" 2>/dev/null; then
  echo "FAIL: provision the jsonschema dependency on the runner"
  exit 1
fi

if ! python3 -c "
import json, sys
from jsonschema import validate, ValidationError

with open(sys.argv[1]) as f:
    instance = json.load(f)
with open(sys.argv[2]) as f:
    schema = json.load(f)
try:
    validate(instance=instance, schema=schema)
    print('PASS: output validated against schema')
except ValidationError as e:
    print(f'FAIL: schema validation error: {e.message}')
    if e.path:
        print(f'  at: {\".\".join(str(p) for p in e.path)}')
    if 'properties' in e.schema:
        allowed = ', '.join(sorted(e.schema['properties'].keys()))
        print(f'  allowed properties: {allowed}')
    sys.exit(1)
" "${RESULT_FILE}" "${FULLSEND_OUTPUT_SCHEMA}"; then
  exit 1
fi

# rfe-creator: gate pipeline tasks on the state machine. Everything below only
# READS files from the downloaded repo. Never cd into it or execute anything
# from it: it is agent-writable, and this process runs on the host with the
# runner env (JIRA_TOKEN) in scope.
STATE=""
[[ -n "${TARGET_REPO_DIR:-}" ]] && STATE="${TARGET_REPO_DIR}/tmp/pipeline-state.yaml"

is_pipeline=0
[[ -n "${STATE}" && -f "${STATE}" ]] && is_pipeline=1
if [[ "${is_pipeline}" == 0 ]]; then
  exit 0
fi

if [[ -z "${STATE}" || ! -f "${STATE}" ]]; then
  echo "FAIL: pipeline task but no pipeline state to verify"
  exit 1
fi

# On any failure below, hand the next iteration what the previous one reported.
# Both fields are agent-written free text that fullsend fences into the retry
# prompt as data; keep each to one bounded line so they cannot crowd out the
# resume instruction.
SUMMARY=$(python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print(" ".join(str(r.get("summary", "")).split())[:300])' "${RESULT_FILE}")
ERRORS=$(python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); e=r.get("errors", []); e=e if isinstance(e, list) else [e]; print(" ".join("; ".join(map(str, e)).split())[:300])' "${RESULT_FILE}")

PHASE=$(awk -F': *' '/^phase:/{print $2; exit}' "${STATE}")
if [[ "${PHASE}" != "DONE" ]]; then
  echo "FAIL: pipeline phase is ${PHASE:-unknown}, not DONE. Resume it with: python3 scripts/pipeline_state.py next-action"
  echo "FAIL: previous run summary: ${SUMMARY}"
  echo "FAIL: previous run errors: ${ERRORS}"
  exit 1
fi

RUNS_DIR="${TARGET_REPO_DIR}/artifacts/auto-fix-runs"
if [[ -z "$(ls -A "${RUNS_DIR}" 2>/dev/null)" ]]; then
  echo "FAIL: phase DONE but ${RUNS_DIR} is empty"
  echo "FAIL: previous run summary: ${SUMMARY}"
  echo "FAIL: previous run errors: ${ERRORS}"
  exit 1
fi

echo "PASS: pipeline phase DONE, run report present"
