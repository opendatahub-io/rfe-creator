# Fetch Agent Instructions

Fetch Jira issue {KEY} and write artifacts. Steps:

1. Run: python3 ${CLAUDE_SKILL_DIR}/scripts/fetch_issue.py {KEY} --fetch-all artifacts
   If this succeeds (exit 0), skip to step 3.
   If it exits with code 2 (missing JIRA creds), continue to step 2.
   If it exits with any other error, report the failure and stop.

2. MCP fallback (only if step 1 exited with code 2):
   a. Call mcp__atlassian__getJiraIssue with cloudId="https://redhat.atlassian.net", issueIdOrKey="{KEY}", fields=["summary","description","priority","labels","status","comment"], responseContentFormat="markdown"
   b. Write the Jira description to artifacts/rfe-tasks/{KEY}.md as-is — preserve the original markdown structure, headings, and content exactly as fetched. Do not add a title heading — the title lives in frontmatter only.
   c. Run: python3 ${CLAUDE_SKILL_DIR}/scripts/frontmatter.py schema rfe-task
      Then: python3 ${CLAUDE_SKILL_DIR}/scripts/frontmatter.py set artifacts/rfe-tasks/{KEY}.md rfe_id={KEY} title="<title>" priority=<priority> status=Ready original_labels="<comma-separated labels or null if none>"
   d. Save the same description content from step 2b to artifacts/rfe-originals/{KEY}.md (just the description body — no frontmatter, no title heading).
   e. Write comments to artifacts/rfe-tasks/{KEY}-comments.md formatted as:
      # Comments: {KEY}
      ## <Author> — <date>
      <body>
      If no comments, write "No comments found."
      This file provides stakeholder context. It is NOT part of the RFE content and must NOT be pushed back to Jira during submission.

3. Verify all output files exist:
   - artifacts/rfe-tasks/{KEY}.md (with frontmatter)
   - artifacts/rfe-originals/{KEY}.md
   - artifacts/rfe-tasks/{KEY}-comments.md

Do not return a summary. Your work is complete when the output files exist.
