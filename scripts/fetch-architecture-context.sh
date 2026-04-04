#!/bin/bash
# Fetches the latest RHOAI architecture context via sparse checkout.
# Safe to run multiple times — pulls updates if already cloned.

if [ -n "${RFE_SKIP_BOOTSTRAP:-}" ]; then
  echo "RFE_SKIP_BOOTSTRAP set - skipping dependency bootstrapping step"
  exit 0
fi

CONTEXT_DIR=".context/architecture-context"

LATEST=$(curl -sL https://api.github.com/repos/opendatahub-io/architecture-context/contents/architecture | python3 -c "import sys,json; entries=json.load(sys.stdin); names=sorted(e['name'] for e in entries if e['name'].startswith('rhoai-')); print(names[-1] if names else '')")

if [ -z "$LATEST" ] || [ "$LATEST" = "null" ]; then
  echo "Could not detect latest architecture version"
  exit 1
fi

if [ -d "$CONTEXT_DIR" ]; then
  git -C "$CONTEXT_DIR" sparse-checkout set "architecture/$LATEST"
  git -C "$CONTEXT_DIR" pull --quiet
else
  git clone --depth 1 --filter=blob:none --sparse https://github.com/opendatahub-io/architecture-context "$CONTEXT_DIR"
  git -C "$CONTEXT_DIR" sparse-checkout set "architecture/$LATEST"
fi

echo "Architecture context ready: $CONTEXT_DIR/architecture/$LATEST"
