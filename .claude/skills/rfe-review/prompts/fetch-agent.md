# Fetch Agent Instructions

Fetch Jira issue {KEY} and write artifacts. Steps:

1. Run: python3 scripts/fetch_issue.py {KEY} --fetch-all artifacts {TYPE_FLAG}
   If this succeeds (exit 0), skip to step 3.
   If it exits with code 2 (missing JIRA creds), continue to step 2.
   If it exits with any other error, report the failure and stop.

2. MCP fallback (only if step 1 exited with code 2):
   a. Call mcp__atlassian__getJiraIssue with cloudId="https://redhat.atlassian.net", issueIdOrKey="{KEY}", fields=["summary","description","priority","labels","status","issuetype","project"{COMMENTS_FIELD}], responseContentFormat="markdown"
      If the response's project.key or issuetype.name differs from the {TYPE} binding (python3 scripts/type_registry.py binding {TYPE} shows it), report the mismatch and stop — write no files.
   b. Write the Jira description to {TASKS_DIR}/{KEY}.md as-is — preserve the original markdown structure, headings, and content exactly as fetched. Do not add a title heading — the title lives in frontmatter only.
   c. Run: python3 scripts/frontmatter.py schema {TASK_SCHEMA}
      Then: python3 scripts/frontmatter.py set {TASKS_DIR}/{KEY}.md {ID_FIELD}={KEY} title="<title>" priority=<priority> status=Ready original_labels="<comma-separated labels or null if none>" type={TYPE} tracker_ref={KEY}
   d. Save the same description content from step 2b to {ORIGINALS_DIR}/{KEY}.md (just the description body — no frontmatter, no title heading).
   e. Only when this type keeps a comments companion (COMMENTS_COMPANION={COMMENTS_COMPANION}): write comments to {TASKS_DIR}/{KEY}-comments.md formatted as:
      # Comments: {KEY}
      ## <Author> — <date>
      <body>
      If no comments, write "No comments found."
      This file provides stakeholder context. It is NOT part of the {ENTITY} content and must NOT be pushed back to Jira during submission.

3. Verify all output files exist:
   - {TASKS_DIR}/{KEY}.md (with frontmatter)
   - {ORIGINALS_DIR}/{KEY}.md
   - {TASKS_DIR}/{KEY}-comments.md (only when COMMENTS_COMPANION={COMMENTS_COMPANION} is true)

Do not return a summary. Your work is complete when the output files exist.
