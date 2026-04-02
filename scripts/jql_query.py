#!/usr/bin/env python3
"""Execute a JQL query against Jira and return paginated key list.

Usage:
    python3 scripts/jql_query.py "project = RHAIRFE AND status = New" [--limit N]

Output:
    TOTAL=<total_matching>
    RHAIRFE-100
    RHAIRFE-101
    ...
"""

import argparse
import sys
import os
import urllib.parse

# Add parent directory so we can import jira_utils
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jira_utils import require_env, api_call_with_retry


def search_issues(server, user, token, jql, limit=None):
    """Run a JQL search with cursor-based pagination, yielding issue keys."""
    page_size = 100
    keys = []
    next_page_token = None

    while True:
        path = (f"/search/jql?jql={urllib.parse.quote(jql, safe='')}"
                f"&maxResults={page_size}&fields=key")
        if next_page_token:
            path += f"&nextPageToken={urllib.parse.quote(next_page_token, safe='')}"
        data = api_call_with_retry(server, path, user, token)

        issues = data.get("issues", [])
        if not issues:
            break

        for issue in issues:
            keys.append(issue["key"])
            if limit and len(keys) >= limit:
                break

        if limit and len(keys) >= limit:
            break

        if data.get("isLast", True):
            break

        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

    print(f"TOTAL={len(keys)}")
    for key in keys:
        print(key)


def main():
    parser = argparse.ArgumentParser(
        description="Execute a JQL query and return issue keys.")
    parser.add_argument("jql", help="JQL query string")
    parser.add_argument("--limit", type=int, default=None,
                        help="Maximum number of keys to return")
    args = parser.parse_args()

    server, user, token = require_env()
    if not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN must be set",
              file=sys.stderr)
        sys.exit(1)

    jql = f"({args.jql}) AND statusCategory != Done AND labels not in (rfe-creator-ignore, rfe-creator-autofix-pass)"
    search_issues(server, user, token, jql, args.limit)


if __name__ == "__main__":
    main()
