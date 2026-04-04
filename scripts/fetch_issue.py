#!/usr/bin/env python3
"""Fetch a Jira issue and print its fields as JSON.

Lightweight read utility for skills that need to fetch issues when the
Atlassian MCP server is unavailable. Outputs JSON to stdout for the
calling skill to parse.

Usage:
    python3 scripts/fetch_issue.py RHAIRFE-1234 [--fields summary,description,comment,priority,labels,status] [--markdown]

    # Fetch everything and write all artifact files at once
    python3 scripts/fetch_issue.py RHAIRFE-1234 --fetch-all artifacts

Environment variables:
    JIRA_SERVER  Jira server URL (e.g. https://mysite.atlassian.net)
    JIRA_USER    Jira username/email
    JIRA_TOKEN   Jira API token

Exit codes:
    0  Success
    1  API/network/script error
    2  Missing JIRA credentials (caller should try MCP fallback)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

from jira_utils import require_env, get_issue, get_comments, adf_to_markdown


def _desc_to_markdown(desc_raw):
    """Convert a raw description field (ADF dict or string) to markdown."""
    if isinstance(desc_raw, dict):
        return adf_to_markdown(desc_raw).strip()
    elif desc_raw is not None:
        return str(desc_raw).strip()
    return ""


def _format_comment_date(iso_date):
    """Format an ISO timestamp to a human-readable date string."""
    # Jira dates look like "2025-01-15T10:30:00.000+0000"
    if not iso_date:
        return "Unknown date"
    return iso_date[:10]


def _fetch_all(issue_key, artifacts_dir, server, user, token):
    """Fetch issue and write all artifact files.

    Returns 0 on success, 1 on error.
    """
    tasks_dir = os.path.join(artifacts_dir, "rfe-tasks")
    originals_dir = os.path.join(artifacts_dir, "rfe-originals")
    os.makedirs(tasks_dir, exist_ok=True)
    os.makedirs(originals_dir, exist_ok=True)

    # Fetch issue fields
    try:
        issue = get_issue(server, user, token, issue_key,
                          fields=["summary", "description", "priority",
                                  "labels", "status"])
    except Exception as e:
        print(f"Error fetching issue {issue_key}: {e}", file=sys.stderr)
        return 1

    fields = issue.get("fields", {})
    desc_md = _desc_to_markdown(fields.get("description"))

    # Extract field values
    summary = fields.get("summary", "")
    priority_obj = fields.get("priority")
    priority = priority_obj.get("name", "Major") if isinstance(
        priority_obj, dict) else "Major"
    labels = fields.get("labels", [])
    labels_str = ",".join(labels) if labels else "null"

    # Write task file (description body)
    task_path = os.path.join(tasks_dir, f"{issue_key}.md")
    with open(task_path, "w", encoding="utf-8") as f:
        f.write(desc_md + "\n")

    # Set frontmatter via frontmatter.py
    fm_args = [
        sys.executable, "scripts/frontmatter.py", "set", task_path,
        f"rfe_id={issue_key}",
        f"title={summary}",
        f"priority={priority}",
        "status=Ready",
        f"original_labels={labels_str}",
    ]
    result = subprocess.run(fm_args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error setting frontmatter: {result.stderr.strip()}",
              file=sys.stderr)
        return 1

    # Write original description (deterministic baseline for conflict
    # detection)
    orig_path = os.path.join(originals_dir, f"{issue_key}.md")
    with open(orig_path, "w", encoding="utf-8") as f:
        f.write(desc_md + "\n")

    # Fetch and write comments
    try:
        comments = get_comments(server, user, token, issue_key)
    except Exception as e:
        print(f"Error fetching comments for {issue_key}: {e}",
              file=sys.stderr)
        return 1

    comments_path = os.path.join(tasks_dir, f"{issue_key}-comments.md")
    with open(comments_path, "w", encoding="utf-8") as f:
        f.write(f"# Comments: {issue_key}\n\n")
        if not comments:
            f.write("No comments found.\n")
        else:
            for c in comments:
                author = c.get("author", {}).get("displayName", "Unknown")
                date = _format_comment_date(c.get("created", ""))
                body = c.get("body", {})
                if isinstance(body, dict):
                    body = adf_to_markdown(body).strip()
                elif body is not None:
                    body = str(body).strip()
                else:
                    body = ""
                f.write(f"## {author} — {date}\n\n{body}\n\n")

    print(f"OK: wrote {task_path}, {orig_path}, {comments_path}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("issue_key",
                        help="Jira issue key (e.g. RHAIRFE-1234)")

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--fields", default=None,
                            help="Comma-separated list of fields to fetch "
                                 "(default: summary,description,priority,"
                                 "labels,status). "
                                 "Use 'comment' to also fetch comments.")
    mode_group.add_argument("--fetch-all", metavar="ARTIFACTS_DIR",
                            help="Fetch issue and write all artifact files "
                                 "(rfe-tasks, rfe-originals, comments) to "
                                 "the given directory.")

    parser.add_argument("--markdown", action="store_true",
                        help="Convert ADF fields (description, comments) "
                             "to markdown strings in the output")
    parser.add_argument("--write-original", metavar="DIR",
                        help="Write the description as markdown to "
                             "DIR/<issue_key>.md. If JIRA creds are "
                             "available, refetches via REST API and uses "
                             "adf_to_markdown for deterministic output. "
                             "If not, copies DIR/<issue_key>.input.md "
                             "as a fallback.")
    args = parser.parse_args()

    server, user, token = require_env()

    # --fetch-all mode: script does everything
    if args.fetch_all:
        if not all([server, user, token]):
            print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars "
                  "required for --fetch-all mode.", file=sys.stderr)
            sys.exit(2)
        rc = _fetch_all(args.issue_key, args.fetch_all, server, user, token)
        sys.exit(rc)

    # --write-original-only mode: no --fields means caller just wants
    # the original description snapshot written to disk.
    if args.write_original and not args.fields:
        os.makedirs(args.write_original, exist_ok=True)
        orig_path = os.path.join(args.write_original,
                                 f"{args.issue_key}.md")
        base, ext = os.path.splitext(orig_path)
        input_path = base + ".input" + ext
        if all([server, user, token]):
            issue = get_issue(server, user, token, args.issue_key,
                              fields=["description"])
            desc_md = _desc_to_markdown(
                issue.get("fields", {}).get("description"))
            with open(orig_path, "w", encoding="utf-8") as f:
                f.write(desc_md + "\n")
            if os.path.exists(input_path):
                os.remove(input_path)
        elif os.path.exists(input_path):
            shutil.copy2(input_path, orig_path)
            os.remove(input_path)
        else:
            print(f"Warning: no JIRA creds and no {input_path}, "
                  "skipping --write-original", file=sys.stderr)
        return

    # Default fields when not in write-original-only mode
    if not args.fields:
        args.fields = "summary,description,priority,labels,status"

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

    # Write original description snapshot for conflict detection
    if args.write_original:
        desc_md = _desc_to_markdown(fields.get("description"))
        os.makedirs(args.write_original, exist_ok=True)
        orig_path = os.path.join(args.write_original,
                                 f"{args.issue_key}.md")
        with open(orig_path, "w", encoding="utf-8") as f:
            f.write(desc_md + "\n")

    json.dump(output, sys.stdout, indent=2)
    print()  # trailing newline


if __name__ == "__main__":
    main()
