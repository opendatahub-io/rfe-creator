#!/bin/bash
# Ensures the assess-rfe skills are available locally.
# Safe to run multiple times — skips clone if already present.

CONTEXT_DIR=".context/assess-rfe"

if [ ! -d "$CONTEXT_DIR" ]; then
  git clone https://github.com/n1hility/assess-rfe "$CONTEXT_DIR" 2>&1
fi

# Copy all skills from the plugin
for skill_dir in "$CONTEXT_DIR"/skills/*/; do
  skill_name=$(basename "$skill_dir")
  target=".claude/skills/$skill_name"
  mkdir -p "$target"
  cp "$skill_dir/SKILL.md" "$target/SKILL.md"
done
