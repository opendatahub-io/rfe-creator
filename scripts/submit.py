#!/usr/bin/env python3
"""Submit RFE artifacts to Jira — create new or update existing tickets.

Handles the standard (non-split) submission flow. For split submissions,
use split_submit.py instead.

Reads all structured metadata from YAML frontmatter on task and review files.
No regex parsing of markdown prose.

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

import yaml

from jira_utils import (
    require_env,
    create_issue,
    update_issue,
    add_labels,
    add_comment,
    strip_metadata,
    markdown_to_adf,
)

from artifact_utils import (
    read_frontmatter,
    read_frontmatter_validated,
    update_frontmatter,
    scan_task_files,
    find_artifact_file,
    find_removed_context_yaml,
    find_review_file,
    rename_to_jira_key,
    rebuild_index,
    ValidationError,
)


def _render_jira_comment(yaml_path):
    """Read removed-context YAML and render postable blocks as markdown.

    Posts blocks with type 'genuine' or 'unclassified' (safety fallback).
    Skips blocks with type 'reworded' or 'non-substantive'.
    Returns empty string if no blocks qualify.
    """
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data or "blocks" not in data:
        return ""

    postable_types = {"genuine", "unclassified"}
    sections = []
    for block in data["blocks"]:
        btype = block.get("type", "unclassified")
        if btype in postable_types:
            heading = block.get("heading", "")
            content = block.get("content", "")
            sections.append(f"## {heading}\n{content}")

    if not sections:
        return ""

    preamble = ("*[RFE Creator]* The following technical implementation "
                "details were removed from the RFE description during review. "
                "This content is better suited for a RHAISTRAT and is "
                "preserved here for reference:")
    return preamble + "\n\n" + "\n\n".join(sections)


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

    # Scan task files for submittable RFEs
    tasks = scan_task_files(args.artifacts_dir)
    if not tasks:
        print("Error: No RFE task files found.", file=sys.stderr)
        sys.exit(1)

    # Filter to non-archived RFEs
    submittable = [(path, data) for path, data in tasks
                   if data.get("status") != "Archived"]
    if not submittable:
        print("Error: No submittable RFEs found (all archived).",
              file=sys.stderr)
        sys.exit(1)

    # Build submission plan
    plan = []
    for task_path, task_data in submittable:
        rfe_id = task_data["rfe_id"]
        title = task_data["title"]
        is_existing = rfe_id.startswith("RHAIRFE-")
        priority = task_data["priority"]
        size = task_data.get("size", "M")

        # Read review frontmatter if available
        review_path = find_review_file(args.artifacts_dir, rfe_id)
        review_data = None
        if review_path:
            try:
                review_data, _ = read_frontmatter_validated(
                    review_path, "rfe-review")
            except (ValidationError, Exception) as e:
                print(f"Warning: cannot read review for {rfe_id}: {e}",
                      file=sys.stderr)

        # Get recommendation from review
        rec = "submit"
        if review_data:
            rec = review_data.get("recommendation", "submit")

        if rec in ("reject", "autorevise_reject"):
            plan.append({
                "rfe_id": rfe_id, "title": title,
                "is_existing": is_existing, "priority": priority, "size": size,
                "action": "SKIP", "labels": [], "skip_reason": "rejected",
                "task_path": task_path,
            })
            continue

        # For existing RFEs, check if content has changed
        if is_existing:
            original_path = os.path.join(
                args.artifacts_dir, "rfe-originals", f"{rfe_id}.md")
            if os.path.exists(original_path):
                with open(original_path, encoding="utf-8") as f:
                    original_body = strip_metadata(f.read())
                with open(task_path, encoding="utf-8") as f:
                    current_body = strip_metadata(f.read())
                if original_body.strip() == current_body.strip():
                    # No content changes — still apply labels (e.g. pass marker)
                    no_change_labels = []
                    if review_data and review_data.get("auto_revised", False):
                        no_change_labels.append("rfe-creator-auto-revised")
                    if review_data and review_data.get("needs_attention", False):
                        no_change_labels.append("rfe-creator-needs-attention")
                    if review_data and rec == "submit":
                        no_change_labels.append("rfe-creator-autofix-pass")
                    plan.append({
                        "rfe_id": rfe_id, "title": title,
                        "is_existing": is_existing, "priority": priority,
                        "size": size,
                        "action": "Label only" if no_change_labels else "SKIP",
                        "labels": no_change_labels,
                        "skip_reason": None if no_change_labels else "no changes",
                        "task_path": task_path,
                    })
                    continue

        # Determine labels
        labels = []
        if not is_existing:
            labels.append("rfe-creator-auto-created")
        if review_data and review_data.get("auto_revised", False):
            labels.append("rfe-creator-auto-revised")
        if review_data and review_data.get("needs_attention", False):
            labels.append("rfe-creator-needs-attention")
        if review_data and rec == "submit":
            labels.append("rfe-creator-autofix-pass")

        action = f"Update {rfe_id}" if is_existing else "Create"
        plan.append({
            "rfe_id": rfe_id, "title": title,
            "is_existing": is_existing, "priority": priority, "size": size,
            "action": action, "labels": labels, "skip_reason": None,
            "task_path": task_path,
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
    results = {}  # rfe_id -> assigned jira key
    for entry in plan:
        rfe_id = entry["rfe_id"]
        if entry["skip_reason"]:
            print(f"  {rfe_id}: Skipping — {entry['skip_reason']}")
            continue

        if entry["action"] == "Label only":
            labels = entry["labels"]
            if args.dry_run:
                print(f"  {rfe_id}: Would add labels: {', '.join(labels)}")
            else:
                add_labels(server, user, token, rfe_id, labels)
                print(f"  {rfe_id}: Labels: {', '.join(labels)}")
            results[rfe_id] = rfe_id
            continue

        # Read and clean artifact content
        with open(entry["task_path"], encoding="utf-8") as f:
            raw_content = f.read()
        cleaned = strip_metadata(raw_content)
        description_adf = markdown_to_adf(cleaned)

        title = entry["title"]
        labels = entry["labels"]

        if entry["is_existing"]:
            # Update existing ticket (rfe_id is the Jira key)
            if args.dry_run:
                print(f"  {rfe_id}: Would update")
            else:
                update_issue(server, user, token, rfe_id, title,
                             description_adf)
                print(f"  {rfe_id}: Updated")
                if labels:
                    add_labels(server, user, token, rfe_id, labels)
                    print(f"           Labels: {', '.join(labels)}")
            results[rfe_id] = rfe_id
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

        # Post removed-context Jira comment if applicable
        yaml_path = find_removed_context_yaml(args.artifacts_dir, rfe_id)
        if yaml_path:
            comment_md = _render_jira_comment(yaml_path)
            target_key = results.get(rfe_id)
            if not comment_md:
                pass  # No postable blocks
            elif args.dry_run:
                print(f"  {rfe_id}: Would post removed-context comment "
                      f"({len(comment_md)} chars)")
            elif target_key:
                comment_adf = markdown_to_adf(comment_md)
                add_comment(server, user, token, target_key, comment_adf)
                print(f"  {rfe_id}: Posted removed-context comment")

    print()

    # Post-submit: update frontmatter and rename files
    for entry in plan:
        rfe_id = entry["rfe_id"]
        if entry["skip_reason"]:
            continue

        assigned_key = results.get(rfe_id)
        if not assigned_key or assigned_key == "RHAIRFE-DRY":
            continue

        if not entry["is_existing"]:
            # New RFE: rename files from RFE-NNN to RHAIRFE-NNNN
            rename_to_jira_key(args.artifacts_dir, rfe_id, assigned_key)
            print(f"  {rfe_id}: Renamed artifacts to {assigned_key}")
        else:
            # Existing: just update status
            update_frontmatter(entry["task_path"],
                               {"status": "Submitted"},
                               "rfe-task")

    # Rebuild index
    rebuild_index(args.artifacts_dir)
    print(f"Done. Index rebuilt at {args.artifacts_dir}/rfes.md")


if __name__ == "__main__":
    main()
