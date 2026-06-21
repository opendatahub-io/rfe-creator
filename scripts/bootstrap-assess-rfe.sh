#!/bin/bash
# Ensures the assess-rfe plugin is available locally.
# Safe to run multiple times — clones on first run, pulls updates after.

if [ -n "${RFE_SKIP_BOOTSTRAP:-}" ]; then
  echo "RFE_SKIP_BOOTSTRAP set - skipping dependency bootstrapping step"
  exit 0
fi

CONTEXT_DIR=".context/assess-rfe"
# Scripts now live under each skill dir (assess-rfe moved them out of the repo
# root in assess-rfe#5 "move-scripts-to-skill-dirs").
RUBRIC_FILE="$CONTEXT_DIR/skills/assess-rfe/scripts/agent_prompt.md"

if [ ! -d "$CONTEXT_DIR" ]; then
  git clone https://github.com/n1hility/assess-rfe "$CONTEXT_DIR" 2>&1
else
  git -C "$CONTEXT_DIR" pull --ff-only 2>&1 || echo "WARN: assess-rfe pull failed, using cached version" >&2
fi

# Validate that the rubric file exists after cloning
if [ ! -f "$RUBRIC_FILE" ]; then
  echo "ERROR: Rubric file not found at $RUBRIC_FILE after bootstrap" >&2
  exit 1
fi

# Copy all skills from the plugin, including their bundled scripts/ so the
# copied SKILL.md's ${CLAUDE_SKILL_DIR}/scripts/... references resolve at
# runtime (scripts are co-located with each SKILL.md as of assess-rfe#5).
for skill_dir in "$CONTEXT_DIR"/skills/*/; do
  skill_name=$(basename "$skill_dir")
  target=".claude/skills/$skill_name"
  mkdir -p "$target"
  cp -r "$skill_dir". "$target/"
done

# Install agent definitions
if [ -d "$CONTEXT_DIR/agents" ]; then
  mkdir -p .claude/agents
  cp "$CONTEXT_DIR"/agents/*.md .claude/agents/
fi

# Export rubric to artifacts
python3 "$CONTEXT_DIR/skills/export-rubric/scripts/export_rubric.py" 2>/dev/null || true
