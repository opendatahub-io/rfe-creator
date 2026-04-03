#!/usr/bin/env python3
"""Bootstrap the snapshot system from a previous CI run.

Reconstructs the Jira description state at the time of the last run
by examining issue changelogs, creating an accurate baseline snapshot
for incremental change detection.

The run timestamp comes from the results directory name (YYYYMMDD-HHMMSS).
Issues not updated since that time keep their current hash (unchanged).
Issues updated since then get a changelog lookup to find the description
that was current at the run time.

Usage:
    python3 scripts/bootstrap_snapshot.py --results-dir <path> "<jql>"
    python3 scripts/bootstrap_snapshot.py --dry-run --results-dir <path> "<jql>"

Environment variables:
    JIRA_SERVER  Jira server URL
    JIRA_USER    Jira username/email
    JIRA_TOKEN   Jira API token
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jira_utils import require_env, api_call_with_retry, make_request
from snapshot_fetch import (
    fetch_all_issues,
    compute_content_hash,
    _fetch_paginated,
    SNAPSHOT_DIR,
)


def find_latest_run_timestamp(results_dir):
    """Find the timestamp of the latest run from directory names.

    Follows 'latest' symlink if present, otherwise uses newest dir.
    Run directories are named YYYYMMDD-HHMMSS.
    Returns (name, datetime_utc) or (None, None).
    """
    latest = os.path.join(results_dir, "latest")
    if os.path.islink(latest):
        name = os.path.basename(os.readlink(latest))
        try:
            dt = datetime.strptime(name, "%Y%m%d-%H%M%S")
            return name, dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    for name in sorted(os.listdir(results_dir), reverse=True):
        if name.startswith(".") or name == "latest":
            continue
        if not os.path.isdir(os.path.join(results_dir, name)):
            continue
        try:
            dt = datetime.strptime(name, "%Y%m%d-%H%M%S")
            return name, dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None, None


def _fetch_changelog(server, user, token, key):
    """Fetch the full changelog for an issue.

    Returns list of entries, each with 'created' (datetime) and
    'items' (list of change items).
    """
    entries = []
    start_at = 0

    while True:
        path = (f"/issue/{urllib.parse.quote(key, safe='')}/changelog"
                f"?startAt={start_at}&maxResults=100")
        data = api_call_with_retry(server, path, user, token)

        for history in data.get("values", []):
            created_str = history.get("created", "")
            try:
                created = datetime.fromisoformat(
                    re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2',
                           created_str))
            except (ValueError, TypeError):
                continue
            entries.append({
                "created": created,
                "items": history.get("items", []),
            })

        total = data.get("total", 0)
        values = data.get("values", [])
        start_at += len(values)
        if start_at >= total or not values:
            break

    return entries


def _description_at_time(changelog, target_dt):
    """Extract the description at target_dt from changelog entries.

    Returns ADF dict or raw text string, or None if no description
    changes exist.  On Jira Cloud the structured content lives in
    from/to (ADF JSON).  On Jira Server/DC those fields are None and
    the content is in fromString/toString (wiki markup).
    """
    desc_changes = []
    for entry in changelog:
        for item in entry["items"]:
            if item.get("field") == "description":
                desc_changes.append({
                    "created": entry["created"],
                    "from": item.get("from") if item.get("from") is not None else item.get("fromString"),
                    "to": item.get("to") if item.get("to") is not None else item.get("toString"),
                })

    if not desc_changes:
        return None

    desc_changes.sort(key=lambda x: x["created"])

    # If the earliest change is after target, use the 'from' value.
    if desc_changes[0]["created"] > target_dt:
        return _parse_adf(desc_changes[0]["from"])

    # Otherwise, take the 'to' of the last change at or before target.
    result = None
    for change in desc_changes:
        if change["created"] <= target_dt:
            result = _parse_adf(change["to"])
        else:
            break

    return result


_DONE_STATUS_PATTERNS = (
    "done", "closed", "resolved", "completed",
    "won't do", "won't fix", "rejected",
    "cancelled", "canceled", "archived",
)


def _is_done_status(status_name):
    """Heuristic check for Done-category status names."""
    if not status_name:
        return False
    lower = status_name.lower().strip()
    return any(p in lower for p in _DONE_STATUS_PATTERNS)


def _was_done_at_time(changelog, target_dt):
    """Check if the issue was in a Done-like status at target_dt.

    Uses status change history from the changelog. If no status
    changes exist, assumes the issue's current status (which passed
    the statusCategory != Done filter) was always its status.
    """
    status_changes = []
    for entry in changelog:
        for item in entry["items"]:
            if item.get("field") == "status":
                status_changes.append({
                    "created": entry["created"],
                    "fromString": item.get("fromString", ""),
                    "toString": item.get("toString", ""),
                })

    if not status_changes:
        return False

    status_changes.sort(key=lambda x: x["created"])

    if status_changes[0]["created"] > target_dt:
        return _is_done_status(status_changes[0]["fromString"])

    status_at_time = None
    for change in status_changes:
        if change["created"] <= target_dt:
            status_at_time = change["toString"]
        else:
            break

    return _is_done_status(status_at_time) if status_at_time else False


def get_description_at_time(server, user, token, key, target_dt):
    """Get the description ADF that was current at target_dt.

    Fetches the changelog and finds the description at the target time.
    Returns ADF dict, or None if description has never changed.
    """
    changelog = _fetch_changelog(server, user, token, key)
    return _description_at_time(changelog, target_dt)


def _fetch_wiki_description(server, user, token, key):
    """Fetch current description as wiki markup via v2 API.

    Used for apples-to-apples comparison with changelog toString
    values (which are also wiki markup on Jira Server/DC).
    """
    url = f"{server.rstrip('/')}/rest/api/2/issue/{urllib.parse.quote(key, safe='')}?fields=description"
    data = make_request(url, user, token)
    return (data.get("fields") or {}).get("description") or ""


def _parse_adf(value):
    """Parse a changelog description value.

    On Jira Cloud, from/to contain ADF as a JSON string → returns dict.
    On Jira Server/DC, fromString/toString contain wiki markup → returns
    the raw string (compute_content_hash handles strings via
    adf_to_markdown pass-through).
    Returns None only when value is None (empty description).
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        # Wiki markup or other non-JSON text — return as-is
        return value
    return None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("jql", help="JQL query (same as auto-fix uses)")
    parser.add_argument("--results-dir", required=True,
                        help="Path to results repo with run directories")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be done without writing")
    parser.add_argument("--artifacts-dir", default=None,
                        help="Output directory (default: repo artifacts/)")
    args = parser.parse_args()

    server, user, token = require_env()
    if not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN required",
              file=sys.stderr)
        sys.exit(1)

    # Step 1: Find the last run timestamp
    run_name, run_dt = find_latest_run_timestamp(args.results_dir)
    if not run_dt:
        print("Error: no valid run directories found", file=sys.stderr)
        sys.exit(1)
    print(f"Last run: {run_name} ({run_dt.isoformat()})", file=sys.stderr)

    # Step 2: Fetch all current issues with hard filters
    jql = (f"({args.jql}) AND statusCategory != Done "
           f"AND labels not in (rfe-creator-ignore)")
    print(f"JQL: {jql}", file=sys.stderr)

    current = fetch_all_issues(server, user, token, jql)
    print(f"Fetched {len(current)} issues from Jira", file=sys.stderr)

    # Step 3: Find which issues were updated since the run
    run_jql_ts = run_dt.strftime("%Y-%m-%d %H:%M")
    updated_jql = (f"{jql} AND updated >= \"{run_jql_ts}\"")
    updated_keys = set()
    for issue in _fetch_paginated(
            server, user, token, updated_jql, "key"):
        updated_keys.add(issue["key"])
    print(f"Issues updated since run: {len(updated_keys)}",
          file=sys.stderr)

    # Step 4: Build snapshot — use historical descriptions for
    # issues updated since the run, current hash for the rest
    snapshot_issues = {}
    lookups = 0
    hist_changed = 0
    done_excluded = 0

    for key, data in current.items():
        if key not in updated_keys:
            snapshot_issues[key] = data["content_hash"]
            continue

        changelog = _fetch_changelog(server, user, token, key)
        lookups += 1

        # Skip issues that were in Done status at run time — they
        # were out of scope and will surface as "new" on first fetch
        if _was_done_at_time(changelog, run_dt):
            done_excluded += 1
            continue

        hist_desc = _description_at_time(changelog, run_dt)
        if hist_desc is None:
            # No description changes — current is the original
            snapshot_issues[key] = data["content_hash"]
        elif isinstance(hist_desc, dict):
            # ADF (Jira Cloud) — hash directly comparable
            hist_hash = compute_content_hash(hist_desc)
            snapshot_issues[key] = hist_hash
            if hist_hash != data["content_hash"]:
                hist_changed += 1
        else:
            # Wiki markup (Jira Server/DC) — compare wiki-to-wiki
            # via v2 API to avoid false positives from format
            # differences (wiki h2. vs ADF ##)
            current_wiki = _fetch_wiki_description(
                server, user, token, key)
            hist_hash = compute_content_hash(hist_desc)
            current_wiki_hash = compute_content_hash(current_wiki)
            if hist_hash == current_wiki_hash:
                # Description unchanged — use current ADF hash
                snapshot_issues[key] = data["content_hash"]
            else:
                snapshot_issues[key] = hist_hash
                hist_changed += 1

    print(f"Changelog lookups: {lookups} "
          f"({hist_changed} with changed description)",
          file=sys.stderr)
    if done_excluded:
        print(f"Excluded {done_excluded} issues (Done at run time)",
              file=sys.stderr)

    # Step 5: Merge submitted hashes from run report
    # Issues whose descriptions were changed by the run have historical
    # (pre-submit) hashes in the snapshot, but Jira now has the post-submit
    # content. Use current hashes for these so the next fetch doesn't
    # re-flag our own changes.
    run_report_path = os.path.join(
        args.results_dir, run_name, "auto-fix-runs", f"{run_name}.yaml")
    if os.path.exists(run_report_path):
        with open(run_report_path, encoding="utf-8") as f:
            report = yaml.safe_load(f)
        submitted_ids = [
            e["id"] for e in report.get("per_rfe", [])
            if e.get("recommendation") == "submit" and e.get("auto_revised")
        ]
        merged = 0
        for key in submitted_ids:
            if key in current and key in snapshot_issues:
                snapshot_issues[key] = current[key]["content_hash"]
                merged += 1
        if merged:
            print(f"Merged {merged} submitted hashes from run report",
                  file=sys.stderr)

    # Step 6: Write snapshot
    if args.dry_run:
        print(f"\nDry run — would write snapshot with "
              f"{len(snapshot_issues)} issue hashes")
        return

    snapshot_dir = (os.path.join(args.artifacts_dir, "auto-fix-runs")
                    if args.artifacts_dir else SNAPSHOT_DIR)
    os.makedirs(snapshot_dir, exist_ok=True)

    run_ts_str = run_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshot = {
        "query_timestamp": run_ts_str,
        "timestamp": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "bootstrapped_from": run_name,
        "issues": snapshot_issues,
    }
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    snapshot_path = os.path.join(snapshot_dir,
                                 f"issue-snapshot-{ts}.yaml")
    with open(snapshot_path, "w", encoding="utf-8") as f:
        yaml.dump(snapshot, f, default_flow_style=False,
                  sort_keys=False)
    print(f"Wrote snapshot: {snapshot_path}")
    print(f"Bootstrap complete. {len(snapshot_issues)} issues.")


if __name__ == "__main__":
    main()
