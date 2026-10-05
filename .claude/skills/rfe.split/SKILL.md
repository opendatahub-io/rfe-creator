---
name: rfe.split
description: "Compatibility alias for /rfe-split, kept so existing /rfe.split invocations keep working. Split oversized RFEs: prefer /rfe-split (Initiatives: /rfe-split --type initiative)."
user-invocable: true
allowed-tools: Bash, Agent, Skill, AskUserQuestion
---

`/rfe.split` is the compatibility alias of `/rfe-split`. Unless the working directory is the plugin root (this skill directory's third parent, a checkout), first run once `bash "${CLAUDE_SKILL_DIR}/../../../scripts/bootstrap.sh" --layout` (on a host that leaves the variable unsubstituted, use this skill file's directory; on any nonzero exit stop and show its message). Read `${CLAUDE_SKILL_DIR}/../rfe-split/SKILL.md` (the sibling skill directory; its own layout check is then a no-op) and follow it from Step 0 with the same arguments: wherever that file refers to its invocation arguments (its placeholder is spelled dollar-sign ARGUMENTS), use exactly the arguments below, which are this invocation's:

$ARGUMENTS
