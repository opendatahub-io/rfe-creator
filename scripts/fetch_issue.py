#!/usr/bin/env python3
"""Fetch a Jira issue and print its fields as JSON.

Lightweight read utility for skills that need to fetch issues when the
Atlassian MCP server is unavailable. Outputs JSON to stdout for the
calling skill to parse.

Usage:
    python3 scripts/fetch_issue.py RHAIRFE-1234 [--fields summary,description,comment,priority,labels,status] [--markdown]

Environment variables:
    JIRA_SERVER  Jira server URL (e.g. https://mysite.atlassian.net)
    JIRA_USER    Jira username/email
    JIRA_TOKEN   Jira API token
"""

import argparse
import json
import sys

from jira_utils import require_env, get_issue, get_comments, adf_to_markdown


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("issue_key",
                        help="Jira issue key (e.g. RHAIRFE-1234)")
    parser.add_argument("--fields",
                        default="summary,description,priority,labels,status",
                        help="Comma-separated list of fields to fetch "
                             "(default: summary,description,priority,"
                             "labels,status). "
                             "Use 'comment' to also fetch comments.")
    parser.add_argument("--markdown", action="store_true",
                        help="Convert ADF fields (description, comments) "
                             "to markdown strings in the output")
    args = parser.parse_args()

    server, user, token = require_env()
    if not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars "
              "required.", file=sys.stderr)
        sys.exit(1)

    requested = [f.strip() for f in args.fields.split(",")]
    fetch_comments = "comment" in requested
    api_fields = [f for f in requested if f != "comment"]

    # Fetch the issue
    issue = get_issue(server, user, token, args.issue_key,
                      fields=api_fields if api_fields else None)

    # Build output
    fields = issue.get("fields", {})
    output = {
        "key": issue.get("key"),
        "fields": {},
    }

    for field_name in api_fields:
        value = fields.get(field_name)
        # Convert ADF description to markdown if requested
        if args.markdown and field_name == "description" and \
                isinstance(value, dict):
            value = adf_to_markdown(value).strip()
        output["fields"][field_name] = value

    # Fetch comments separately if requested
    if fetch_comments:
        comments = get_comments(server, user, token, args.issue_key)
        output["comments"] = []
        for c in comments:
            body = c.get("body", {})
            if args.markdown and isinstance(body, dict):
                body = adf_to_markdown(body).strip()
            output["comments"].append({
                "author": c.get("author", {}).get("displayName", "Unknown"),
                "created": c.get("created", ""),
                "body": body,
            })

    json.dump(output, sys.stdout, indent=2)
    print()  # trailing newline


if __name__ == "__main__":
    main()
