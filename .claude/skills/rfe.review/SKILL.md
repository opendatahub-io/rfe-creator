---
name: rfe.review
description: "Compatibility alias for /rfe-review, kept so existing /rfe.review invocations keep working. Review, improve and auto-revise RFEs: prefer /rfe-review (Initiatives: /rfe-review --type initiative)."
user-invocable: true
allowed-tools: Bash, Agent, AskUserQuestion
---

`/rfe.review` is the compatibility alias of `/rfe-review`. Unless the working directory is the plugin root (this skill directory's third parent, a checkout), first run once `bash "${CLAUDE_SKILL_DIR}/../../../scripts/bootstrap.sh" --layout` (on a host that leaves the variable unsubstituted, use this skill file's directory; on any nonzero exit stop and show its message). Read `${CLAUDE_SKILL_DIR}/../rfe-review/SKILL.md` (the sibling skill directory; its own layout check is then a no-op) and follow it from Step 0 with the same arguments: wherever that file refers to its invocation arguments (its placeholder is spelled dollar-sign ARGUMENTS), use exactly the arguments below, which are this invocation's:

$ARGUMENTS
