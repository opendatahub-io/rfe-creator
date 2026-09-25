---
name: rfe.split
description: "Compatibility alias for /rfe-split, kept so existing /rfe.split invocations keep working. Split oversized RFEs: prefer /rfe-split (Initiatives: /rfe-split --type initiative)."
user-invocable: true
allowed-tools: Glob, Bash, Agent, Skill, AskUserQuestion
---

`/rfe.split` is the compatibility alias of `/rfe-split`. Read `${CLAUDE_SKILL_DIR}/../rfe-split/SKILL.md` (the sibling skill directory; where that file writes `${CLAUDE_SKILL_DIR}`, use `${CLAUDE_SKILL_DIR}/../rfe-split`; on a host that substitutes nothing, use this skill file's directory) and follow it from Step 0 with the same arguments: wherever that file refers to its invocation arguments (its placeholder is spelled dollar-sign ARGUMENTS), use exactly the arguments below, which are this invocation's:

$ARGUMENTS
