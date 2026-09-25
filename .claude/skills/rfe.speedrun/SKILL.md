---
name: rfe.speedrun
description: "Compatibility alias for /rfe-speedrun, kept so existing /rfe.speedrun invocations keep working. End-to-end RFE pipeline: prefer /rfe-speedrun (Initiatives: /rfe-speedrun --type initiative)."
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion, Skill
---

`/rfe.speedrun` is the compatibility alias of `/rfe-speedrun`. Read `${CLAUDE_SKILL_DIR}/../rfe-speedrun/SKILL.md` (the sibling skill directory; Claude Code substitutes the variable, on a host that does not, use this skill file's directory) and follow it from Step 0 with the same arguments: wherever that file refers to its invocation arguments (its placeholder is spelled dollar-sign ARGUMENTS), use exactly the arguments below, which are this invocation's:

$ARGUMENTS
