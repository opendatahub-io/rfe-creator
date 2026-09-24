# Fetch Agent Instructions

Fetch Jira issue {KEY} and write artifacts. Steps:

1. Run: python3 scripts/fetch_issue.py {KEY} --fetch-all artifacts --type initiative
   If this succeeds (exit 0), skip to step 3.
   If it exits with code 2 (missing JIRA creds), continue to step 2.
   If it exits with any other error, report the failure and stop.

2. MCP fallback (only if step 1 exited with code 2):
   a. Call mcp__atlassian__getJiraIssue with cloudId="https://redhat.atlassian.net", issueIdOrKey="{KEY}", fields=["summary","description","priority","labels","status","issuetype","project"], responseContentFormat="markdown"
      If the response's project.key or issuetype.name differs from the initiative binding (python3 scripts/type_registry.py binding initiative shows it), report the mismatch and stop — write no files.
   b. Write the Jira description to artifacts/initiatives/{KEY}.md as-is — preserve the original markdown structure, headings, and content exactly as fetched. Do not add a title heading — the title lives in frontmatter only.
   c. Run: python3 scripts/frontmatter.py schema initiative-task
      Then: python3 scripts/frontmatter.py set artifacts/initiatives/{KEY}.md initiative_id={KEY} title="<title>" priority=<priority> status=Ready original_labels="<comma-separated labels or null if none>" type=initiative tracker_ref={KEY}
   d. Save the same description content from step 2b to artifacts/initiative-originals/{KEY}.md (just the description body — no frontmatter, no title heading).

3. Verify all output files exist:
   - artifacts/initiatives/{KEY}.md (with frontmatter)
   - artifacts/initiative-originals/{KEY}.md

Do not return a summary. Your work is complete when the output files exist.
