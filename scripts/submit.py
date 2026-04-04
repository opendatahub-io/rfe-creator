#!/usr/bin/env python3
"""Submit RFE artifacts to Jira — create new or update existing tickets.

Handles both split and standard submissions in one pass. Split parents
(RHAIRFE with status: Archived) are submitted via split_submit.py first,
then regular RFEs are updated/created directly.

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
import subprocess
import sys

# Ensure progress output is visible immediately when stdout is redirected
# to a file or pipe (Python defaults to full buffering in that case).
sys.stdout.reconfigure(line_buffering=True)

import yaml

from jira_utils import (
    require_env,
    create_issue,
    update_issue,
    add_labels,
    remove_labels,
    add_comment,
    get_issue,
    adf_to_markdown,
    strip_metadata,
    markdown_to_adf,
    normalize_for_compare,
)

_normalize_for_compare = normalize_for_compare

from snapshot_fetch import compute_content_hash, update_snapshot_hashes

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


def _post_needs_attention_comment(server, user, token, entry, results,
                                  dry_run):
    """Post a Jira comment explaining why human attention is needed.

    Only posts if:
    - needs_attention_reason is set in the review
    - The issue did not already have the rfe-creator-needs-attention label
      when it was fetched from Jira (checked via original_labels)
    """
    reason = entry.get("attn_reason")
    if not reason:
        return

    original_labels = entry.get("original_labels") or []
    if "rfe-creator-needs-attention" in original_labels:
        return  # Already flagged in a prior run

    target_key = results.get(entry["rfe_id"])
    if dry_run:
        print(f"  {entry['rfe_id']}: Would post needs-attention comment")
        return

    if not target_key:
        return

    comment_md = (
        "*[RFE Creator]* This RFE has been flagged for human review:\n\n"
        f"{reason}"
    )
    comment_adf = markdown_to_adf(comment_md)
    add_comment(server, user, token, target_key, comment_adf)
    print(f"  {entry['rfe_id']}: Posted needs-attention comment")


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

    # Scan task files
    tasks = scan_task_files(args.artifacts_dir)
    if not tasks:
        print("Error: No RFE task files found.", file=sys.stderr)
        sys.exit(1)

    # --- Phase 1: Submit splits via split_submit.py ---
    # Find RHAIRFE parents that were split (Archived + have children)
    child_parent_keys = {data.get("parent_key") for _, data in tasks
                         if data.get("parent_key")}
    split_parent_data = {data["rfe_id"]: data for _, data in tasks
                         if data.get("status") == "Archived"
                         and data["rfe_id"].startswith("RHAIRFE-")
                         and data["rfe_id"] in child_parent_keys}
    split_parents = list(split_parent_data.keys())

    if split_parents:
        print(f"Phase 1: Submitting {len(split_parents)} split parent(s)\n")
        script_dir = os.path.dirname(os.path.abspath(__file__))
        split_script = os.path.join(script_dir, "split_submit.py")

        for parent_key in sorted(split_parents):
            cmd = [sys.executable, split_script, parent_key,
                   "--artifacts-dir", args.artifacts_dir]
            if args.dry_run:
                cmd.append("--dry-run")
            print(f"--- {parent_key} ---")
            result = subprocess.run(cmd)
            if result.returncode == 2:
                # Too many children — record refusal, flag for human review
                print(f"  {parent_key}: Split refused — too many children")
                review_path = os.path.join(args.artifacts_dir, "rfe-reviews",
                                           f"{parent_key}-review.md")
                attn_reason = (
                    "Automatic splitting produced too many child RFEs. "
                    "The decomposition needs human review to determine "
                    "the right granularity."
                )
                update_frontmatter(review_path, {
                    "error": "split_refused: too many leaf children",
                    "needs_attention": True,
                    "needs_attention_reason": attn_reason,
                }, "rfe-review")

                # Reuse the existing helper for the Jira comment
                parent_labels = (split_parent_data[parent_key]
                                 .get("original_labels") or [])
                refusal_entry = {
                    "rfe_id": parent_key,
                    "attn_reason": attn_reason,
                    "original_labels": parent_labels,
                }
                refusal_results = {parent_key: parent_key}
                _post_needs_attention_comment(
                    server, user, token, refusal_entry,
                    refusal_results, args.dry_run)

                # Add label (helper only posts comment)
                if not args.dry_run:
                    add_labels(server, user, token, parent_key,
                               ["rfe-creator-needs-attention"])
                continue
            elif result.returncode != 0:
                print(f"Error: split_submit.py failed for {parent_key} "
                      f"(exit code {result.returncode})", file=sys.stderr)
                sys.exit(result.returncode)
            print()

    # --- Phase 2: Submit regular (non-split) RFEs ---
    # Re-scan after splits may have renamed files
    tasks = scan_task_files(args.artifacts_dir)

    # Filter to non-archived RFEs without a parent (split children were
    # already handled by split_submit.py in Phase 1)
    submittable = [(path, data) for path, data in tasks
                   if data.get("status") != "Archived"
                   and not data.get("parent_key")]
    if not submittable:
        if split_parents:
            # All RFEs were splits — nothing left for Phase 2
            rebuild_index(args.artifacts_dir)
            print(f"Done. Index rebuilt at {args.artifacts_dir}/rfes.md")
            return
        print("Error: No submittable RFEs found.", file=sys.stderr)
        sys.exit(1)

    if split_parents:
        print(f"Phase 2: Submitting {len(submittable)} regular RFE(s)\n")

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

        # Collect needs-attention info for Jira comment
        original_labels = task_data.get("original_labels") or []
        attn_reason = None
        if review_data and review_data.get("needs_attention", False):
            attn_reason = review_data.get("needs_attention_reason")

        if rec in ("reject", "autorevise_reject"):
            # Check if rubric-pass label needs to be removed (RFE was
            # previously passing but no longer does after re-review)
            remove = []
            if (is_existing
                    and "rfe-creator-autofix-rubric-pass" in original_labels):
                remove.append("rfe-creator-autofix-rubric-pass")
            plan.append({
                "rfe_id": rfe_id, "title": title,
                "is_existing": is_existing, "priority": priority, "size": size,
                "action": "Remove labels" if remove else "SKIP",
                "labels": [], "remove_labels": remove,
                "skip_reason": None if remove else "rejected",
                "task_path": task_path,
                "attn_reason": None, "original_labels": original_labels,
            })
            continue

        # For existing RFEs, check for Jira conflicts
        if is_existing and not args.dry_run:
            original_path = os.path.join(
                args.artifacts_dir, "rfe-originals", f"{rfe_id}.md")
            if os.path.exists(original_path):
                try:
                    with open(original_path, encoding="utf-8") as f:
                        orig_snap = _normalize_for_compare(f.read())
                    issue = get_issue(server, user, token, rfe_id,
                                      fields=["description"])
                    desc_raw = issue.get("fields", {}).get("description")
                    if isinstance(desc_raw, dict):
                        jira_desc = _normalize_for_compare(
                            adf_to_markdown(desc_raw))
                    elif desc_raw is None:
                        jira_desc = ""
                    else:
                        jira_desc = _normalize_for_compare(str(desc_raw))
                    if orig_snap != jira_desc:
                        plan.append({
                            "rfe_id": rfe_id, "title": title,
                            "is_existing": is_existing, "priority": priority,
                            "size": size,
                            "action": "SKIP", "labels": [],
                            "remove_labels": [],
                            "skip_reason": "Jira conflict — description "
                                           "modified since fetch",
                            "task_path": task_path,
                            "attn_reason": None,
                            "original_labels": original_labels,
                        })
                        continue
                except Exception as e:
                    print(f"Warning: conflict check failed for {rfe_id}: "
                          f"{e}", file=sys.stderr)

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
                        no_change_labels.append("rfe-creator-autofix-rubric-pass")
                    plan.append({
                        "rfe_id": rfe_id, "title": title,
                        "is_existing": is_existing, "priority": priority,
                        "size": size,
                        "action": "Label only" if no_change_labels else "SKIP",
                        "labels": no_change_labels, "remove_labels": [],
                        "skip_reason": None if no_change_labels else "no changes",
                        "task_path": task_path,
                        "attn_reason": attn_reason,
                        "original_labels": original_labels,
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
            labels.append("rfe-creator-autofix-rubric-pass")

        action = f"Update {rfe_id}" if is_existing else "Create"
        plan.append({
            "rfe_id": rfe_id, "title": title,
            "is_existing": is_existing, "priority": priority, "size": size,
            "action": action, "labels": labels, "remove_labels": [],
            "skip_reason": None, "task_path": task_path,
            "attn_reason": attn_reason, "original_labels": original_labels,
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
        if entry.get("remove_labels"):
            print(f"{'':>10} Remove: {', '.join(entry['remove_labels'])}")
        if entry["skip_reason"]:
            print(f"{'':>10} Reason: {entry['skip_reason']}")
    print()

    # Execute
    results = {}  # rfe_id -> assigned jira key
    submitted_hashes = {}  # assigned_key -> content_hash
    for entry in plan:
        rfe_id = entry["rfe_id"]
        if entry["skip_reason"]:
            print(f"  {rfe_id}: Skipping — {entry['skip_reason']}")
            continue

        if entry["action"] == "Remove labels":
            remove = entry["remove_labels"]
            if args.dry_run:
                print(f"  {rfe_id}: Would remove labels: "
                      f"{', '.join(remove)}")
            else:
                remove_labels(server, user, token, rfe_id, remove)
                print(f"  {rfe_id}: Removed labels: {', '.join(remove)}")
            continue

        if entry["action"] == "Label only":
            labels = entry["labels"]
            if args.dry_run:
                print(f"  {rfe_id}: Would add labels: {', '.join(labels)}")
            else:
                add_labels(server, user, token, rfe_id, labels)
                print(f"  {rfe_id}: Labels: {', '.join(labels)}")
            results[rfe_id] = rfe_id
            _post_needs_attention_comment(
                server, user, token, entry, results, args.dry_run)
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
                submitted_hashes[rfe_id] = compute_content_hash(description_adf)
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
                submitted_hashes[new_key] = compute_content_hash(description_adf)

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

        # Post needs-attention comment if newly flagged
        _post_needs_attention_comment(
            server, user, token, entry, results, args.dry_run)

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

    # Update snapshot with post-submit hashes so the next fetch
    # doesn't re-flag our own changes
    if submitted_hashes and not args.dry_run:
        snap_dir = os.path.join(args.artifacts_dir, "auto-fix-runs")
        updated = update_snapshot_hashes(submitted_hashes, snap_dir)
        if updated:
            print(f"  Updated snapshot with {len(submitted_hashes)} "
                  f"post-submit hashes: {updated}")
        else:
            print("  Warning: no snapshot found to update",
                  file=sys.stderr)

    # Rebuild index
    rebuild_index(args.artifacts_dir)
    print(f"Done. Index rebuilt at {args.artifacts_dir}/rfes.md")


if __name__ == "__main__":
    main()
