#!/usr/bin/env python3
"""Resilient split-submission of child RFEs to Jira.

Submits child RFEs produced by /rfe.split to Jira with proper linking and
parent closure. Designed to be idempotent and resumable — uses Jira comments
as the durable store for content and progress tracking.

Reads all structured metadata from YAML frontmatter on task files.
Identifies parent (status: Archived, rfe_id matches parent key) and children
(parent_key matches parent's rfe_id) from frontmatter.

Usage:
    python scripts/split_submit.py RHAIRFE-XXXX [--dry-run] [--artifacts-dir DIR]

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
    get_issue,
    get_comments,
    add_comment,
    create_issue,
    add_labels,
    create_issue_link,
    get_transitions,
    do_transition,
    markdown_to_adf,
    text_to_adf_paragraph,
    archival_comment_adf,
    strip_metadata,
)

from artifact_utils import (
    read_frontmatter,
    read_frontmatter_validated,
    update_frontmatter,
    scan_task_files,
    find_review_file,
    rename_to_jira_key,
    rebuild_index,
    parse_child_artifact,
    ValidationError,
)


# ─── Recovery / State Detection ──────────────────────────────────────────────

def _extract_adf_text(node):
    """Recursively extract plain text from an ADF node."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_extract_adf_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    return _extract_adf_text(node.get("content", []))


class SubmissionState:
    """Tracks progress of the split submission."""

    def __init__(self):
        self.phase1_done = {}   # child_index -> comment ID
        self.phase2_done = {}   # child_index -> created Jira key
        self.parent_closed = False
        self.total_children = 0


def discover_state(server, user, token, parent_key, expected_children):
    """Scan parent's comments and links to determine submission progress."""
    state = SubmissionState()
    state.total_children = len(expected_children)

    # 1. Scan comments for [RFE Creator] markers
    comments = get_comments(server, user, token, parent_key)
    for comment in comments:
        body_text = _extract_adf_text(comment.get("body", {}))

        archival_match = re.search(
            r'\[RFE Creator\] Split child (\d+) of (\d+):', body_text
        )
        if archival_match:
            idx = int(archival_match.group(1))
            state.phase1_done[idx] = comment["id"]
            continue

        confirm_match = re.search(
            r'\[RFE Creator\] Created as (\S+),.*\(ref: child (\d+) of (\d+)\)',
            body_text
        )
        if confirm_match:
            created_key = confirm_match.group(1)
            idx = int(confirm_match.group(2))
            state.phase2_done[idx] = created_key
            continue

    # 2. Check issue links for "Issue split" outward links
    issue = get_issue(server, user, token, parent_key,
                      ["issuelinks", "status"])
    for link in issue.get("fields", {}).get("issuelinks", []):
        if link.get("type", {}).get("name") != "Issue split":
            continue
        outward = link.get("outwardIssue")
        if not outward:
            continue
        child_key = outward["key"]
        child_summary = outward.get("fields", {}).get("summary", "")
        for idx, (_, title, _, _) in enumerate(expected_children, 1):
            if title == child_summary and idx not in state.phase2_done:
                state.phase2_done[idx] = child_key

    # 3. Check parent status
    status_cat = (issue.get("fields", {}).get("status", {})
                  .get("statusCategory", {}).get("key", ""))
    state.parent_closed = (status_cat == "done")

    return state


# ─── Phases ───────────────────────────────────────────────────────────────────

def phase1_persist(server, user, token, parent_key, children, state, dry_run):
    """Post archival comments for each child not yet persisted."""
    total = len(children)
    for idx, (rfe_id, title, priority, artifact_path) in enumerate(children, 1):
        if idx in state.phase1_done:
            print(f"  Phase 1: Child {idx}/{total} already posted, skipping")
            continue

        _, _, full_markdown, _ = parse_child_artifact(artifact_path)
        header = f"[RFE Creator] Split child {idx} of {total}: {title}"

        if dry_run:
            print(f"  Phase 1: Would post archival comment for child "
                  f"{idx}/{total}: {title} ({len(full_markdown)} chars)")
            state.phase1_done[idx] = "dry-run"
            continue

        body_adf = archival_comment_adf(header, full_markdown)
        result = add_comment(server, user, token, parent_key, body_adf)
        state.phase1_done[idx] = result["id"]
        print(f"  Phase 1: Posted content for child {idx}/{total}: {title}")


def phase2_create_link(server, user, token, parent_key, children, state,
                       artifacts_dir, dry_run):
    """Create tickets, link to parent, and post confirmation comments."""
    total = len(children)
    for idx, (rfe_id, title, priority, artifact_path) in enumerate(children, 1):
        if idx in state.phase2_done:
            print(f"  Phase 2: Child {idx}/{total} already created as "
                  f"{state.phase2_done[idx]}, skipping")
            continue

        if idx not in state.phase1_done:
            print(f"  ERROR: Child {idx}/{total} has no archival comment. "
                  f"Run Phase 1 first.", file=sys.stderr)
            sys.exit(1)

        _, _, _, cleaned_markdown = parse_child_artifact(artifact_path)
        description_adf = markdown_to_adf(cleaned_markdown)

        # Determine labels from review frontmatter
        labels = ["rfe-creator-auto-created", "rfe-creator-split-result"]

        review_path = find_review_file(artifacts_dir, rfe_id)
        review_rec = None
        if review_path:
            try:
                review_data, _ = read_frontmatter_validated(
                    review_path, "rfe-review")
                review_rec = review_data.get("recommendation")
                if review_data.get("auto_revised", False):
                    labels.append("rfe-creator-auto-revised")
                if review_data.get("needs_attention", False):
                    labels.append("rfe-creator-needs-attention")
            except (ValidationError, Exception):
                pass  # proceed without review data
        if review_rec == "submit":
            labels.append("rfe-creator-autofix-pass")

        if dry_run:
            print(f"  Phase 2: Would create RHAIRFE ticket for child "
                  f"{idx}/{total}: {title} (priority: {priority})")
            print(f"           Labels: {', '.join(labels)}")
            print(f"           Would link to {parent_key} via 'Issue split'")
            state.phase2_done[idx] = "RHAIRFE-DRY"
            continue

        # 1. Create ticket with labels
        child_key = create_issue(server, user, token, "RHAIRFE",
                                 "Feature Request", title, description_adf,
                                 priority, labels=labels)
        print(f"  Phase 2: Created {child_key} for child {idx}/{total}: "
              f"{title}")
        print(f"           Labels: {', '.join(labels)}")

        # 2. Link to parent
        create_issue_link(server, user, token, "Issue split",
                          parent_key, child_key)
        print(f"           Linked {child_key} to {parent_key}")

        # 3. Post confirmation comment
        confirm_text = (f"[RFE Creator] Created as {child_key}, linked to "
                        f"parent. (ref: child {idx} of {total})")
        add_comment(server, user, token, parent_key,
                    text_to_adf_paragraph(confirm_text))

        state.phase2_done[idx] = child_key


def phase3_close(server, user, token, parent_key, children, state, dry_run):
    """Close the parent ticket with resolution Obsolete."""
    if state.parent_closed:
        print("  Phase 3: Parent already closed, skipping")
        return

    total = len(children)
    if len(state.phase2_done) < total:
        missing = [i for i in range(1, total + 1)
                   if i not in state.phase2_done]
        print(f"  ERROR: Cannot close parent — children {missing} not yet "
              f"created.", file=sys.stderr)
        sys.exit(1)

    if dry_run:
        print(f"  Phase 3: Would label {parent_key} with "
              f"rfe-creator-split-original")
        print(f"  Phase 3: Would transition {parent_key} to Closed "
              f"(resolution: Obsolete)")
        print(f"           Would post summary comment")
        return

    # Label the parent
    add_labels(server, user, token, parent_key,
               ["rfe-creator-split-original"])
    print(f"  Phase 3: Labeled {parent_key} with rfe-creator-split-original")

    # Find the "Closed" transition
    transitions = get_transitions(server, user, token, parent_key)
    closed_transition = None
    for t in transitions:
        if t["to"].get("name", "").lower() == "closed":
            closed_transition = t
            break

    if not closed_transition:
        available = [t["name"] for t in transitions]
        print(f"  WARNING: No 'Closed' transition found. Available: "
              f"{available}", file=sys.stderr)
        print(f"  Skipping parent closure.", file=sys.stderr)
        return

    # Transition with resolution
    do_transition(server, user, token, parent_key,
                  closed_transition["id"],
                  fields={"resolution": {"name": "Obsolete"}})
    print(f"  Phase 3: Transitioned {parent_key} to Closed (Obsolete)")

    # Post summary comment
    child_lines = []
    for idx, (_, title, _, _) in enumerate(children, 1):
        child_key = state.phase2_done[idx]
        child_lines.append(f"- {child_key}: {title}")
    summary = (
        f"[RFE Creator] This RFE has been split into {total} child RFEs:\n"
        + "\n".join(child_lines)
        + "\n\nOriginal content preserved in comments above."
    )
    add_comment(server, user, token, parent_key,
                text_to_adf_paragraph(summary))
    print(f"  Phase 3: Posted summary comment")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("parent_key", help="Parent Jira issue key to split")
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

    # Scan task files to find parent and children via frontmatter
    tasks = scan_task_files(args.artifacts_dir)
    if not tasks:
        print("Error: No task files found. Run /rfe.split first.",
              file=sys.stderr)
        sys.exit(1)

    # Find parent: status=Archived with matching rfe_id
    parent_task = None
    for path, data in tasks:
        if data.get("status") == "Archived" and \
                data.get("rfe_id") == args.parent_key:
            parent_task = (path, data)
            break

    if not parent_task:
        print(f"Error: No archived parent with rfe_id={args.parent_key} "
              f"found in task files.", file=sys.stderr)
        sys.exit(1)

    # Find children: parent_key matches the parent's rfe_id
    child_tasks = []
    for path, data in tasks:
        if data.get("parent_key") == args.parent_key and \
                data.get("status") != "Archived":
            child_tasks.append((path, data))

    if not child_tasks:
        print("Error: No child RFEs found with parent_key="
              f"{args.parent_key}.", file=sys.stderr)
        sys.exit(1)

    # Build children list: (rfe_id, title, priority, artifact_path)
    children = []
    for path, data in child_tasks:
        children.append((
            data["rfe_id"],
            data["title"],
            data["priority"],
            path,
        ))

    print(f"Split submission: {args.parent_key} -> {len(children)} children")
    for i, (rfe_id, title, priority, _) in enumerate(children, 1):
        print(f"  {i}. {rfe_id}: {title} (Priority: {priority})")
    print()

    # Discover state (skip for dry-run without credentials)
    if args.dry_run and not all([server, user, token]):
        print("Dry run (no Jira credentials — skipping recovery check)")
        print()
        state = SubmissionState()
        state.total_children = len(children)
    else:
        print("Checking submission state...")
        state = discover_state(server, user, token, args.parent_key,
                               children)
        if state.phase1_done:
            print(f"  Phase 1: {len(state.phase1_done)}/{len(children)} "
                  f"archival comments found")
        if state.phase2_done:
            print(f"  Phase 2: {len(state.phase2_done)}/{len(children)} "
                  f"tickets created")
        if state.parent_closed:
            print(f"  Phase 3: Parent already closed")
        if not state.phase1_done and not state.phase2_done:
            print(f"  Fresh start — no prior progress found")
        print()

    # Run phases
    print("Phase 1: Persisting child RFE content to parent comments...")
    phase1_persist(server, user, token, args.parent_key, children, state,
                   args.dry_run)
    print()

    print("Phase 2: Creating tickets and linking...")
    phase2_create_link(server, user, token, args.parent_key, children, state,
                       args.artifacts_dir, args.dry_run)
    print()

    print("Phase 3: Closing parent...")
    phase3_close(server, user, token, args.parent_key, children, state,
                 args.dry_run)
    print()

    # Post-submit: update frontmatter and rename files
    for idx, (rfe_id, title, priority, artifact_path) in \
            enumerate(children, 1):
        assigned_key = state.phase2_done.get(idx)
        if not assigned_key or assigned_key == "RHAIRFE-DRY":
            continue

        rename_to_jira_key(args.artifacts_dir, rfe_id, assigned_key)
        print(f"  {rfe_id}: Renamed artifacts to {assigned_key}")

    # Rebuild index
    rebuild_index(args.artifacts_dir)
    print(f"Done. Index rebuilt at {args.artifacts_dir}/rfes.md")


if __name__ == "__main__":
    main()
