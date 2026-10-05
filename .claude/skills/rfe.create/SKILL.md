---
name: rfe.create
description: "Compatibility alias for /rfe-create, kept so existing /rfe.create invocations keep working. Write a new RFE: prefer /rfe-create (Initiatives: /rfe-create --type initiative)."
user-invocable: true
allowed-tools: Read, Write, Edit, Bash, AskUserQuestion
---

`/rfe.create` is the compatibility alias of `/rfe-create`. Unless the working directory is the plugin root (this skill directory's third parent, a checkout), first run once `bash "${CLAUDE_SKILL_DIR}/../../../scripts/bootstrap.sh" --layout` (on a host that leaves the variable unsubstituted, use this skill file's directory; on any nonzero exit stop and show its message). Read `${CLAUDE_SKILL_DIR}/../rfe-create/SKILL.md` (the sibling skill directory; its own layout check is then a no-op) and follow it from Step 0 with the same arguments: wherever that file refers to its invocation arguments (its placeholder is spelled dollar-sign ARGUMENTS), use exactly the arguments below, which are this invocation's:

$ARGUMENTS
