---
name: rfe.review
description: "Compatibility alias for /rfe-review, kept so existing /rfe.review invocations keep working. Review, improve and auto-revise RFEs: prefer /rfe-review (Initiatives: /rfe-review --type initiative)."
user-invocable: true
allowed-tools: Glob, Bash, Agent, AskUserQuestion
---

`/rfe.review` is the compatibility alias of `/rfe-review`. Read `${CLAUDE_SKILL_DIR}/../rfe-review/SKILL.md` (the sibling skill directory; Claude Code substitutes the variable, on a host that does not, use this skill file's directory) and follow it from Step 0 with the same arguments: wherever that file refers to its invocation arguments (its placeholder is spelled dollar-sign ARGUMENTS), use exactly the arguments below, which are this invocation's:

$ARGUMENTS
