#!/bin/bash
# Ensures the assess-rfe skill is available locally.
# Safe to run multiple times — skips clone if already present.

CONTEXT_DIR=".context/assess-rfe"
SKILL_DIR=".claude/skills/assess-rfe"

if [ ! -d "$CONTEXT_DIR" ]; then
  git clone https://github.com/n1hility/assess-rfe "$CONTEXT_DIR" 2>&1
fi

mkdir -p "$SKILL_DIR"
cp "$CONTEXT_DIR/skills/assess-rfe/SKILL.md" "$SKILL_DIR/SKILL.md"
