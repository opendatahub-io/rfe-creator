---
name: rfe.submit
description: "Compatibility alias for /rfe-submit, kept so existing /rfe.submit invocations keep working. Submit or update RFEs in Jira: prefer /rfe-submit (Initiatives: /rfe-submit --type initiative)."
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
---

`/rfe.submit` is the compatibility alias of `/rfe-submit`. If `scripts/bootstrap.sh` is not in the working directory, first run once `bash "${CLAUDE_SKILL_DIR}/../../../scripts/bootstrap.sh" --layout` (the plugin root is this skill directory's third parent; on a host that leaves the variable unsubstituted, use this skill file's directory). Read `${CLAUDE_SKILL_DIR}/../rfe-submit/SKILL.md` (the sibling skill directory; its own layout check is then a no-op) and follow it from Step 0 with the same arguments: wherever that file refers to its invocation arguments (its placeholder is spelled dollar-sign ARGUMENTS), use exactly the arguments below, which are this invocation's:

$ARGUMENTS
