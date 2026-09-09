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
import os
import sys
import urllib.parse

# Add parent directory so we can import jira_utils
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import type_registry
from jira_utils import api_call_with_retry, require_env

_TYPES = type_registry.load()


def default_exclusions(project):
    """Return the default exclusion clause appended to every query for ``project``.

    Excludes done items and the ``ignore`` / ``rubric_pass`` labels of the type's descriptor
    (``conventions.labels``), never ``split_quarantine`` (design §3.6 invariant 4).
    """
    labels = _TYPES.get(project).labels
    return (
        " AND statusCategory != Done"
        f" AND (labels not in ({labels['ignore']},"
        f" {labels['rubric_pass']}) OR labels is EMPTY)"
    )


def wrap_jql(jql, project):
    """Wrap a caller JQL in the default exclusions for ``project``."""
    return f"({jql}){default_exclusions(project)}"


def search_issues(server, user, token, jql, limit=None):
    """Run a JQL search with cursor-based pagination, yielding issue keys."""
    page_size = 100
    keys = []
    next_page_token = None

    while True:
        path = (
            f"/search/jql?jql={urllib.parse.quote(jql, safe='')}&maxResults={page_size}&fields=key"
        )
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
    parser = argparse.ArgumentParser(description="Execute a JQL query and return issue keys.")
    parser.add_argument("jql", help="JQL query string")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of keys to return")
    parser.add_argument(
        "--project",
        choices=_TYPES.choices(),
        default="rfe",
        help="Project type for label filtering (default: rfe)",
    )
    args = parser.parse_args()

    server, user, token = require_env()
    if not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN must be set", file=sys.stderr)
        sys.exit(1)

    jql = wrap_jql(args.jql, args.project)
    print(f"JQL={jql}", file=sys.stderr)
    search_issues(server, user, token, jql, args.limit)


if __name__ == "__main__":
    main()
