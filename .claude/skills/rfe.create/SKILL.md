---
name: rfe.create
description: "Compatibility alias for /rfe-create, kept so existing /rfe.create invocations keep working. Write a new RFE: prefer /rfe-create (Initiatives: /rfe-create --type initiative)."
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion
---

`/rfe.create` is the compatibility alias of `/rfe-create`. Read `${CLAUDE_SKILL_DIR}/../rfe-create/SKILL.md` (the sibling skill directory; where that file writes `${CLAUDE_SKILL_DIR}`, use `${CLAUDE_SKILL_DIR}/../rfe-create`; on a host that substitutes nothing, use this skill file's directory) and follow it from Step 0 with the same arguments: wherever that file refers to its invocation arguments (its placeholder is spelled dollar-sign ARGUMENTS), use exactly the arguments below, which are this invocation's:

$ARGUMENTS
