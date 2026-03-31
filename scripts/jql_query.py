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
    """Run a JQL search with pagination, yielding issue keys."""
    start_at = 0
    page_size = 100
    total = None
    count = 0

    while True:
        path = (f"/search?jql={urllib.parse.quote(jql, safe='')}"
                f"&startAt={start_at}&maxResults={page_size}&fields=key")
        data = api_call_with_retry(server, path, user, token)

        if total is None:
            total = data.get("total", 0)
            print(f"TOTAL={total}")

        issues = data.get("issues", [])
        if not issues:
            break

        for issue in issues:
            print(issue["key"])
            count += 1
            if limit and count >= limit:
                return

        start_at += len(issues)
        if start_at >= total:
            break


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

    jql = f"({args.jql}) AND statusCategory != Done"
    search_issues(server, user, token, jql, args.limit)


if __name__ == "__main__":
    main()
