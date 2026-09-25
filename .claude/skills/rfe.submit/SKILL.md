---
name: rfe.submit
description: "Compatibility alias for /rfe-submit, kept so existing /rfe.submit invocations keep working. Submit or update RFEs in Jira: prefer /rfe-submit (Initiatives: /rfe-submit --type initiative)."
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
---

`/rfe.submit` is the compatibility alias of `/rfe-submit`. Read `${CLAUDE_SKILL_DIR}/../rfe-submit/SKILL.md` (the sibling skill directory; where that file writes `${CLAUDE_SKILL_DIR}`, use `${CLAUDE_SKILL_DIR}/../rfe-submit`; on a host that substitutes nothing, use this skill file's directory) and follow it from Step 0 with the same arguments: wherever that file refers to its invocation arguments (its placeholder is spelled dollar-sign ARGUMENTS), use exactly the arguments below, which are this invocation's:

$ARGUMENTS
