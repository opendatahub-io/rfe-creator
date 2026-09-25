---
name: rfe.auto-fix
description: "Compatibility alias for /rfe-auto-fix, kept so existing /rfe.auto-fix invocations keep working. Batch review, revision and split of RFEs: prefer /rfe-auto-fix (Initiatives: /rfe-auto-fix --type initiative)."
user-invocable: true
allowed-tools: Glob, Bash, Agent
---

`/rfe.auto-fix` is the compatibility alias of `/rfe-auto-fix`. Read `${CLAUDE_SKILL_DIR}/../rfe-auto-fix/SKILL.md` (the sibling skill directory; where that file writes `${CLAUDE_SKILL_DIR}`, use `${CLAUDE_SKILL_DIR}/../rfe-auto-fix`; on a host that substitutes nothing, use this skill file's directory) and follow it from Step 0 with the same arguments: wherever that file refers to its invocation arguments (its placeholder is spelled dollar-sign ARGUMENTS), use exactly the arguments below, which are this invocation's:

$ARGUMENTS
