#!/bin/bash
# Fetches the latest RHOAI architecture context via sparse checkout.
# Safe to run multiple times — pulls updates if already cloned.

CONTEXT_DIR=".context/architecture-context"

LATEST=$(curl -sL https://api.github.com/repos/opendatahub-io/architecture-context/contents/architecture | jq -r '[.[] | select(.name | startswith("rhoai-")) | .name] | sort | last')

if [ -z "$LATEST" ] || [ "$LATEST" = "null" ]; then
  echo "Could not detect latest architecture version"
  exit 1
fi

if [ -d "$CONTEXT_DIR" ]; then
  cd "$CONTEXT_DIR"
  git sparse-checkout set "architecture/$LATEST"
  git pull --quiet
  cd -
else
  git clone --depth 1 --filter=blob:none --sparse https://github.com/opendatahub-io/architecture-context "$CONTEXT_DIR"
  cd "$CONTEXT_DIR"
  git sparse-checkout set "architecture/$LATEST"
  cd -
fi

echo "Architecture context ready: $CONTEXT_DIR/architecture/$LATEST"
