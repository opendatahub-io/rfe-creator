#!/usr/bin/env python3
"""Resolve Jira project configuration from environment variables.

Prints JIRA_PROJECT and JIRA_ISSUE_TYPE as KEY=VALUE lines.
Exits non-zero if JIRA_PROJECT is not set.

Usage:
    python3 scripts/resolve_project.py
"""

import os
import sys


def main():
    project = os.environ.get("JIRA_PROJECT")
    issue_type = os.environ.get("JIRA_ISSUE_TYPE", "Feature Request")

    if not project:
        print(
            "ERROR: JIRA_PROJECT is not set.\n"
            "Set the Jira project key for this RFE workflow:\n"
            "  export JIRA_PROJECT=YOURPROJECT\n"
            "Optionally set the issue type (default: Feature Request):\n"
            "  export JIRA_ISSUE_TYPE='Feature Request'",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"JIRA_PROJECT={project}")
    print(f"JIRA_ISSUE_TYPE={issue_type}")


if __name__ == "__main__":
    main()
