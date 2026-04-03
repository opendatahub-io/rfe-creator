#!/usr/bin/env python3
"""Snapshot-based JQL fetch with change detection.

Fetches all issues matching a JQL query, computes content hashes, and
diffs against the previous snapshot to identify genuinely changed issues.
Only changed issues are output for processing, avoiding redundant work.

Usage:
    python3 scripts/snapshot_fetch.py fetch "<jql>" --ids-file tmp/autofix-all-ids.txt --changed-file tmp/autofix-changed-ids.txt [--limit 100] [--data-dir <path>]

Output (stdout):
    TOTAL=<count>
    CHANGED=<count>
    NEW=<count>
"""

import argparse
import glob
import hashlib
import os
import sys
import urllib.parse
from datetime import datetime, timezone

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jira_utils import (
    require_env,
    api_call_with_retry,
    adf_to_markdown,
    normalize_for_compare,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_DIR = os.path.join(SCRIPT_DIR, "..", "artifacts", "auto-fix-runs")


def normalize_for_hash(text):
    """Aggressively normalize text for content hashing.

    Stricter than normalize_for_compare — collapses all whitespace
    differences so that ADF conversion jitter and trivial formatting
    edits (indentation, blank lines, tabs) don't produce false changes.
    """
    text = normalize_for_compare(text)
    # Collapse all leading/trailing whitespace on each line
    lines = [line.strip() for line in text.splitlines()]
    # Drop empty lines entirely
    lines = [line for line in lines if line]
    return "\n".join(lines)


def compute_content_hash(adf_description):
    """Compute SHA256 of normalized description content.

    Pipeline: ADF -> markdown -> normalize (aggressive) -> SHA256
    """
    if not adf_description:
        return hashlib.sha256(b"").hexdigest()
    markdown = adf_to_markdown(adf_description)
    normalized = normalize_for_hash(markdown)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _fetch_paginated(server, user, token, jql, fields):
    """Paginate through a JQL search, yielding issue dicts."""
    page_size = 100
    next_page_token = None

    while True:
        path = (f"/search/jql?jql={urllib.parse.quote(jql, safe='')}"
                f"&maxResults={page_size}&fields={fields}")
        if next_page_token:
            path += f"&nextPageToken="
            path += urllib.parse.quote(next_page_token, safe='')
        data = api_call_with_retry(server, path, user, token)

        for issue in data.get("issues", []):
            yield issue

        if data.get("isLast", True):
            break
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break


def fetch_all_issues(server, user, token, jql):
    """Fetch all issues matching JQL with description and labels.

    Returns an ordered dict of {key: {"content_hash": str, "labels": list}}.
    Order matches Jira's default (created desc).
    """
    issues = {}
    for issue in _fetch_paginated(
            server, user, token, jql, "key,description,labels"):
        key = issue["key"]
        fields = issue.get("fields", {})
        description = fields.get("description")
        labels = fields.get("labels", [])
        content_hash = compute_content_hash(description)
        issues[key] = {
            "content_hash": content_hash,
            "labels": labels,
        }
    return issues


def find_previous_snapshot():
    """Find the most recent valid promoted snapshot.

    Walks backwards through issue-snapshot-*.yaml files sorted by name
    (which sorts by timestamp since names use YYYYMMDD-HHMMSS format).
    """
    pattern = os.path.join(SNAPSHOT_DIR, "issue-snapshot-*.yaml")
    files = sorted(glob.glob(pattern), reverse=True)

    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            if data and isinstance(data.get("issues"), dict):
                return f, data
        except Exception:
            continue
    return None, None


def load_snapshot_from_dir(data_dir):
    """Find the previous snapshot in a local data directory.

    Follows the 'latest' symlink to find the most recent run, then
    walks backwards through run directories looking for a snapshot
    in auto-fix-runs/.

    Returns snapshot_data dict or None.
    """
    if not os.path.isdir(data_dir):
        print(f"Data repo path not found: {data_dir}", file=sys.stderr)
        return None

    # Follow 'latest' symlink to find the most recent run
    latest_link = os.path.join(data_dir, "latest")
    if os.path.islink(latest_link):
        latest_target = os.readlink(latest_link)
        print(f"Data repo latest: {latest_target}", file=sys.stderr)
    else:
        print("Data repo: no 'latest' symlink, scanning directories",
              file=sys.stderr)
        latest_target = None

    # Collect run directories sorted newest-first
    run_dirs = []
    for name in sorted(os.listdir(data_dir), reverse=True):
        if name.startswith(".") or name == "latest":
            continue
        path = os.path.join(data_dir, name)
        if os.path.isdir(path):
            run_dirs.append(name)

    if latest_target and latest_target in [os.path.basename(d)
                                           for d in run_dirs]:
        # Put the symlink target first, then the rest newest-first
        run_dirs.remove(latest_target)
        run_dirs.insert(0, latest_target)

    # Walk backwards through runs looking for a snapshot
    for run_name in run_dirs:
        run_path = os.path.join(data_dir, run_name)
        snap_dir = os.path.join(run_path, "auto-fix-runs")
        if not os.path.isdir(snap_dir):
            continue
        pattern = os.path.join(snap_dir, "issue-snapshot-*.yaml")
        snap_files = sorted(glob.glob(pattern), reverse=True)
        for sf in snap_files:
            try:
                with open(sf, encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
                if data and isinstance(data.get("issues"), dict):
                    print(f"Data repo snapshot: {run_name}/"
                          f"{os.path.basename(sf)} "
                          f"({len(data['issues'])} issues)",
                          file=sys.stderr)
                    return data
            except Exception:
                continue

    print("Data repo: no valid snapshot found", file=sys.stderr)
    return None


def diff_snapshots(current_issues, previous_data):
    """Compare current fetch against previous snapshot.

    Returns (changed_keys, new_keys) preserving Jira's fetch order.
    - changed: hash differs from previous snapshot
    - new: not in previous snapshot at all
    """
    if previous_data is None:
        # First run — all issues are "new"
        return [], list(current_issues.keys())

    prev_issues = previous_data.get("issues", {})
    changed = []
    new = []

    for key, data in current_issues.items():
        current_hash = data["content_hash"]

        if key not in prev_issues:
            new.append(key)
            continue

        if current_hash != prev_issues[key]:
            changed.append(key)

    return changed, new


def write_id_file(path, ids):
    """Write IDs to a file, one per line."""
    os.makedirs(os.path.dirname(path) or "tmp", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for id_ in ids:
            f.write(f"{id_}\n")


def update_snapshot_hashes(hashes, snapshot_dir=None):
    """Update the latest snapshot with post-submit content hashes.

    Called by submit.py after Jira writes so the next fetch sees
    the post-submit state and doesn't re-flag our own changes.
    """
    snap_dir = snapshot_dir or SNAPSHOT_DIR
    pattern = os.path.join(snap_dir, "issue-snapshot-*.yaml")
    files = sorted(glob.glob(pattern), reverse=True)

    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            if data and isinstance(data.get("issues"), dict):
                data["issues"].update(hashes)
                with open(f, "w", encoding="utf-8") as fh:
                    yaml.dump(data, fh, default_flow_style=False,
                              sort_keys=False)
                return f
        except Exception:
            continue
    return None


def cmd_fetch(args):
    """Fetch all issues, diff against previous snapshot, write ID files."""
    server, user, token = require_env()
    if not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN required",
              file=sys.stderr)
        sys.exit(1)

    # Load previous snapshot
    prev_path, prev_data = find_previous_snapshot()

    # If no local snapshot, try the data directory
    if prev_data is None and args.data_dir:
        prev_data = load_snapshot_from_dir(args.data_dir)

    if prev_path:
        prev_count = len(prev_data.get("issues", {}))
        print(f"Previous snapshot: {prev_path} ({prev_count} issues)",
              file=sys.stderr)
    elif prev_data:
        prev_count = len(prev_data.get("issues", {}))
        print(f"Previous snapshot: from data dir ({prev_count} issues)",
              file=sys.stderr)
    else:
        print("Previous snapshot: none (first run)", file=sys.stderr)

    # Hard filters only
    jql = (f"({args.jql}) AND statusCategory != Done "
           f"AND labels not in (rfe-creator-ignore)")
    print(f"JQL={jql}", file=sys.stderr)

    query_timestamp = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")

    # Fetch all matching issues
    current = fetch_all_issues(server, user, token, jql)
    print(f"Fetched {len(current)} issues", file=sys.stderr)

    # Write snapshot directly to artifacts
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    snapshot = {
        "query_timestamp": query_timestamp,
        "timestamp": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "issues": {k: v["content_hash"] for k, v in current.items()},
    }
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(SNAPSHOT_DIR, f"issue-snapshot-{ts}.yaml")
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(snapshot, f, default_flow_style=False, sort_keys=False)

    # Diff
    changed, new = diff_snapshots(current, prev_data)

    # Maintain Jira's original order across both sets
    changed_set = set(changed)
    new_set = set(new)
    priority_ids = [k for k in current.keys()
                    if k in changed_set or k in new_set]
    unchanged_ids = [k for k in current.keys()
                     if k not in changed_set and k not in new_set]

    # Changed/new get priority ordering; total capped at limit
    limit = args.limit or len(current)
    all_ids = (priority_ids + unchanged_ids)[:limit]

    # Split for downstream
    out_changed = [k for k in all_ids if k in changed_set]
    out_new = [k for k in all_ids if k in new_set]

    # Write ID files for downstream scripts
    write_id_file(args.ids_file, all_ids)
    write_id_file(args.changed_file, out_changed)

    # Output counts only — IDs are in files
    print(f"TOTAL={len(all_ids)}")
    print(f"CHANGED={len(out_changed)}")
    print(f"NEW={len(out_new)}")
    print(f"UNCHANGED={len(all_ids) - len(out_changed) - len(out_new)}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    fetch_p = sub.add_parser(
        "fetch", help="Fetch issues and diff against previous snapshot")
    fetch_p.add_argument("jql", help="JQL query string")
    fetch_p.add_argument("--limit", type=int, default=None,
                         help="Max number of changed keys to output")
    fetch_p.add_argument("--ids-file", required=True,
                         help="Output file for all IDs to process")
    fetch_p.add_argument("--changed-file", required=True,
                         help="Output file for changed-only IDs")
    fetch_p.add_argument("--data-dir",
                         help="Local directory with previous run results")

    args = parser.parse_args()
    if args.command == "fetch":
        cmd_fetch(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
