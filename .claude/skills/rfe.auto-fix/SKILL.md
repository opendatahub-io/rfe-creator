---
name: rfe.auto-fix
description: "Compatibility alias for /rfe-auto-fix, kept so existing /rfe.auto-fix invocations keep working. Batch review, revision and split of RFEs: prefer /rfe-auto-fix (Initiatives: /rfe-auto-fix --type initiative)."
user-invocable: true
allowed-tools: Glob, Bash, Agent
---

`/rfe.auto-fix` is the compatibility alias of `/rfe-auto-fix`. If `scripts/bootstrap.sh` is not in the working directory, first run once `bash "${CLAUDE_SKILL_DIR}/../../../scripts/bootstrap.sh" --layout` (the plugin root is this skill directory's third parent; on a host that leaves the variable unsubstituted, use this skill file's directory). Read `${CLAUDE_SKILL_DIR}/../rfe-auto-fix/SKILL.md` (the sibling skill directory; its own layout check is then a no-op) and follow it from Step 0 with the same arguments: wherever that file refers to its invocation arguments (its placeholder is spelled dollar-sign ARGUMENTS), use exactly the arguments below, which are this invocation's:

$ARGUMENTS
