#!/usr/bin/env bash
set -euo pipefail

REGISTRY_URL="${JTBD_REGISTRY_URL:-https://gitlab.cee.redhat.com/yingzhou/jtbd-knowledge-registry.git}"
TARGET_DIR=".context/jtbd-registry"
REQUIRED_FILES=("index.yaml" "governance.yaml")

if [ -d "$TARGET_DIR/.git" ]; then
  echo "Updating JTBD registry..."
  git -C "$TARGET_DIR" pull --ff-only 2>/dev/null || {
    echo "Warning: pull failed, using existing checkout"
  }
else
  echo "Cloning JTBD registry..."
  mkdir -p "$(dirname "$TARGET_DIR")"
  git clone "$REGISTRY_URL" "$TARGET_DIR" 2>/dev/null || {
    echo "Error: failed to clone JTBD registry (URL redacted)"
    echo "JTBD enrichment will be unavailable for this run."
    exit 0
  }
fi

for f in "${REQUIRED_FILES[@]}"; do
  if [ ! -f "$TARGET_DIR/$f" ]; then
    echo "Error: required file $TARGET_DIR/$f not found after clone"
    echo "JTBD enrichment will be unavailable for this run."
    exit 0
  fi
done

echo "JTBD registry ready at $TARGET_DIR"
echo "  index.yaml: $(wc -l < "$TARGET_DIR/index.yaml") lines"
echo "  governance.yaml: $(wc -l < "$TARGET_DIR/governance.yaml") lines"
echo "  jobs/: $(ls "$TARGET_DIR/jobs/" 2>/dev/null | wc -l) files"
echo "  personas/: $(ls "$TARGET_DIR/personas/" 2>/dev/null | wc -l) files"
