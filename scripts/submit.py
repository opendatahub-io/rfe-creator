#!/usr/bin/env python3
"""Submit RFE artifacts to Jira — create new or update existing tickets.

Handles the standard (non-split) submission flow. For split submissions,
use split_submit.py instead.

Usage:
    python scripts/submit.py [--dry-run] [--artifacts-dir DIR]

Environment variables:
    JIRA_SERVER  Jira server URL (e.g. https://mysite.atlassian.net)
    JIRA_USER    Jira username/email
    JIRA_TOKEN   Jira API token
"""

import argparse
import os
import re
import sys

from jira_utils import (
    require_env,
    create_issue,
    update_issue,
    add_labels,
    add_comment,
    strip_metadata,
    markdown_to_adf,
    find_artifact_file,
    find_removed_context_file,
    check_needs_attention,
    has_revision_notes,
)


def parse_rfes_md(path):
    """Parse artifacts/rfes.md to find submittable RFEs.

    Returns: [(rfe_id, title, jira_key_or_none, priority, size, status), ...]
    Skips header/separator rows and archived/split entries.
    """
    with open(path, encoding="utf-8") as f:
        content = f.read()

    rfes = []
    for line in content.split("\n"):
        row_match = re.match(
            r'^\|\s*~*\s*(RFE-\d+)\s*~*\s*\|'
            r'\s*(.*?)\s*\|'
            r'\s*(.*?)\s*\|'
            r'\s*(.*?)\s*\|'
            r'\s*(.*?)\s*\|'
            r'\s*(.*?)\s*\|',
            line
        )
        if not row_match:
            continue

        rfe_id = row_match.group(1).strip().strip("~")
        title = row_match.group(2).strip().strip("~")
        jira_key = row_match.group(3).strip().strip("~")
        priority = row_match.group(4).strip().strip("~")
        size = row_match.group(5).strip().strip("~")
        status = row_match.group(6).strip()

        # Skip archived/split entries
        if "Split" in status or "Archived" in status or "split" in status:
            continue

        jira_key = jira_key if jira_key and jira_key != "—" else None
        rfes.append((rfe_id, title, jira_key, priority, size, status))

    return rfes


def parse_review_report(artifacts_dir):
    """Parse review report to find per-RFE recommendations.

    Returns: {rfe_id: recommendation} or None if no report exists.
    Recommendation is 'submit', 'revise', 'reject', or 'split'.
    """
    report_path = os.path.join(artifacts_dir, "rfe-review-report.md")
    if not os.path.exists(report_path):
        return None

    recommendations = {}
    current_rfe = None

    with open(report_path, encoding="utf-8") as f:
        for line in f:
            rfe_match = re.match(r'^###\s+(RFE-\d+):', line)
            if rfe_match:
                current_rfe = rfe_match.group(1)
                continue

            if current_rfe:
                rec_match = re.match(
                    r'^\*\*Recommendation\*\*:\s*\*\*(\w+)\*\*', line
                )
                if rec_match:
                    recommendations[current_rfe] = rec_match.group(1).lower()
                    current_rfe = None

    return recommendations


def parse_artifact_title(path):
    """Extract the title from an RFE artifact, without the RFE-NNN prefix."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            title_match = re.match(r'^#\s+(?:RFE-\d+:\s+)?(.+)$', line)
            if title_match:
                return title_match.group(1).strip()
    return "Untitled"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print planned actions without making API calls")
    parser.add_argument("--artifacts-dir", default="artifacts",
                        help="Artifacts directory (default: artifacts)")
    args = parser.parse_args()

    server, user, token = require_env()

    if not args.dry_run and not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars "
              "required.", file=sys.stderr)
        print("Set these or use --dry-run for local-only validation.",
              file=sys.stderr)
        sys.exit(1)

    # Parse rfes.md
    rfes_path = os.path.join(args.artifacts_dir, "rfes.md")
    if not os.path.exists(rfes_path):
        print(f"Error: {rfes_path} not found.", file=sys.stderr)
        sys.exit(1)

    rfes = parse_rfes_md(rfes_path)
    if not rfes:
        print("Error: No submittable RFEs found in rfes.md.", file=sys.stderr)
        sys.exit(1)

    # Parse review report
    recommendations = parse_review_report(args.artifacts_dir)
    if recommendations is None:
        print("Warning: No review report found. Submitting without review "
              "validation.", file=sys.stderr)

    # Build submission plan
    plan = []
    for rfe_id, title, jira_key, priority, size, status in rfes:
        artifact_path = find_artifact_file(args.artifacts_dir, rfe_id)
        if not artifact_path:
            print(f"Warning: No artifact file for {rfe_id}, skipping.",
                  file=sys.stderr)
            continue

        # Use title from artifact (cleaner than rfes.md table)
        artifact_title = parse_artifact_title(artifact_path)

        # Check review recommendation
        rec = (recommendations or {}).get(rfe_id, "submit")
        if rec == "reject":
            plan.append({
                "rfe_id": rfe_id, "title": artifact_title,
                "jira_key": jira_key, "priority": priority, "size": size,
                "action": "SKIP", "labels": [], "skip_reason": "rejected",
                "artifact_path": artifact_path,
            })
            continue

        # Determine labels
        labels = []
        if not jira_key:
            labels.append("rfe-creator-auto-created")
        if has_revision_notes(artifact_path):
            labels.append("rfe-creator-auto-revised")
        if rec == "revise" or check_needs_attention(args.artifacts_dir,
                                                     rfe_id):
            labels.append("rfe-creator-needs-attention")

        action = f"Update {jira_key}" if jira_key else "Create"
        plan.append({
            "rfe_id": rfe_id, "title": artifact_title,
            "jira_key": jira_key, "priority": priority, "size": size,
            "action": action, "labels": labels, "skip_reason": None,
            "artifact_path": artifact_path,
        })

    # Print summary
    print(f"Submission plan: {len(plan)} RFEs")
    print(f"{'RFE':<10} {'Title':<50} {'Priority':<10} {'Action':<20}")
    print("-" * 90)
    for entry in plan:
        t = entry["title"]
        display_title = t[:47] + "..." if len(t) > 50 else t
        print(f"{entry['rfe_id']:<10} {display_title:<50} "
              f"{entry['priority']:<10} {entry['action']:<20}")
        if entry["labels"]:
            print(f"{'':>10} Labels: {', '.join(entry['labels'])}")
        if entry["skip_reason"]:
            print(f"{'':>10} Reason: {entry['skip_reason']}")
    print()

    # Execute
    results = {}  # rfe_id -> jira_key
    for entry in plan:
        rfe_id = entry["rfe_id"]
        if entry["skip_reason"]:
            print(f"  {rfe_id}: Skipping — {entry['skip_reason']}")
            continue

        # Read and clean artifact content
        with open(entry["artifact_path"], encoding="utf-8") as f:
            raw_content = f.read()
        cleaned = strip_metadata(raw_content)
        description_adf = markdown_to_adf(cleaned)

        jira_key = entry["jira_key"]
        title = entry["title"]
        labels = entry["labels"]

        if jira_key:
            # Update existing ticket
            if args.dry_run:
                print(f"  {rfe_id}: Would update {jira_key}")
            else:
                update_issue(server, user, token, jira_key, title,
                             description_adf)
                print(f"  {rfe_id}: Updated {jira_key}")
                if labels:
                    add_labels(server, user, token, jira_key, labels)
                    print(f"           Labels: {', '.join(labels)}")
            results[rfe_id] = jira_key
        else:
            # Create new ticket
            if args.dry_run:
                print(f"  {rfe_id}: Would create RHAIRFE ticket: {title}")
                results[rfe_id] = "RHAIRFE-DRY"
            else:
                new_key = create_issue(server, user, token, "RHAIRFE",
                                       "Feature Request", title,
                                       description_adf, entry["priority"],
                                       labels=labels)
                print(f"  {rfe_id}: Created {new_key}")
                if labels:
                    print(f"           Labels: {', '.join(labels)}")
                results[rfe_id] = new_key

        # Post removed-context comment if applicable
        removed_path = find_removed_context_file(args.artifacts_dir, rfe_id)
        if removed_path:
            with open(removed_path, encoding="utf-8") as f:
                removed_content = f.read()
            target_key = results.get(rfe_id)
            if args.dry_run:
                print(f"  {rfe_id}: Would post removed-context comment "
                      f"({len(removed_content)} chars)")
            elif target_key:
                comment_adf = markdown_to_adf(removed_content)
                add_comment(server, user, token, target_key, comment_adf)
                print(f"  {rfe_id}: Posted removed-context comment")

    print()

    # Write ticket mapping
    mapping_path = os.path.join(args.artifacts_dir, "jira-tickets.md")
    site = (server.rstrip("/") if server
            else "https://example.atlassian.net")
    with open(mapping_path, "w", encoding="utf-8") as f:
        f.write("# Jira Tickets\n\n")
        f.write("| RFE | Jira Key | Title | Priority | URL |\n")
        f.write("|-----|----------|-------|----------|-----|\n")
        for entry in plan:
            rfe_id = entry["rfe_id"]
            key = results.get(rfe_id, "—")
            if entry["skip_reason"]:
                key = f"SKIPPED ({entry['skip_reason']})"
                url = "—"
            elif key and key not in ("—", "RHAIRFE-DRY"):
                url = f"{site}/browse/{key}"
            else:
                url = "—"
            f.write(f"| {rfe_id} | {key} | {entry['title']} "
                    f"| {entry['priority']} | {url} |\n")

    print(f"Done. Ticket mapping written to {mapping_path}")


if __name__ == "__main__":
    main()
