#!/usr/bin/env bash
# Host-side bootstrap, run before the sandbox exists: vendors each registered
# type's assess assets and the architecture context into the checkout, so the
# sandbox needs no GitHub egress (RFE_SKIP_BOOTSTRAP=1 keeps it from repeating
# this step).
set -euo pipefail

: "${TARGET_REPO_DIR:?TARGET_REPO_DIR must be set}"
echo "rfe-creator pre-script: bootstrapping ${TARGET_REPO_DIR}"

cd -- "${TARGET_REPO_DIR}"
for t in $(python3 scripts/type_registry.py list); do
  bash scripts/bootstrap.sh --type "$t"
done
bash scripts/fetch-architecture-context.sh

echo "rfe-creator pre-script: complete"
