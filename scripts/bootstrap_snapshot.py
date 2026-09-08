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
import type_registry
from jira_utils import api_call_with_retry, make_request, require_env
from snapshot_fetch import (
    SNAPSHOT_CONFIG,
    SNAPSHOT_DIR,
    _fetch_paginated,
    compute_content_hash,
    fetch_all_issues,
)

_TYPES = type_registry.load()

# Per-type run-report facts, projected from the type descriptors: the report file prefix
# is snapshot.report_prefix (rfe's is the grandfathered empty string — its reports are
# bare <run>.yaml) and the entry-list key is reporting.item_key. Same keys, same order and
# same values as the literal table this replaces; generate_run_report.py writes from the
# same two fields and tests/test_report_roundtrip.py round-trips the pair through the CLI.
BOOTSTRAP_CONFIG = {
    name: {
        "report_prefix": _TYPES.get(name).get("snapshot.report_prefix"),
        "item_key": _TYPES.get(name).get("reporting.item_key"),
    }
    for name in _TYPES.names()
}

# Grandfathered probe prefix: _run_dir_has_snapshots looks for the rfe snapshot prefix
# ("issue-snapshot-") whatever --type is running, exactly as the pre-registry literal did,
# so an initiative results directory is never recognised as partial. Reading the value
# from the rfe descriptor changes nothing; making the probe per-type (the selected type's
# snapshot.prefix) is a deliberate follow-up (design §10 PR-10, latent initiative-CI bugs).
_RFE_SNAPSHOT_PREFIX = _TYPES.get("rfe").get("snapshot.prefix")


def _load_run_report(results_dir, run_name, config=None):
    """Load processed IDs and report from the run's item list.

    Returns (set_of_ids, report_dict) — the set may be EMPTY for a legitimate
    zero-count run — or (None, None) if no report file exists. Absence and
    emptiness are different signals: an absent report has the documented
    include-all fallback, while an empty one is positive evidence the run
    processed nothing, so the caller walks back to an older run instead
    (RHAIFIRST-569).

    Error entries do not count as processed: the run recorded them precisely
    because it could NOT dispose of them (no readable review), so counting
    them here would freeze them out of every future fetch — the
    RHAIRFE-3201 shape, reachable through bootstrap (RHAIFIRST-582).

    Raises ValueError when the file exists but cannot be understood — unreadable, malformed YAML,
    not a mapping, or entries without an id. That is a different situation from absence: absence
    has a documented fallback, whereas a corrupt report says nothing about what the run processed,
    and treating it as absent would misdiagnose it (0 of the 278 published reports have any of
    these shapes, so this is defence, not compatibility).
    """
    cfg = config or BOOTSTRAP_CONFIG["rfe"]
    report_prefix = cfg["report_prefix"]
    item_key = cfg["item_key"]
    path = os.path.join(results_dir, run_name, "auto-fix-runs", f"{report_prefix}{run_name}.yaml")
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, encoding="utf-8") as f:
            report = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"run report {path} is unreadable: {e}") from e
    if not isinstance(report, dict):
        raise ValueError(f"run report {path} is not a mapping (got {type(report).__name__})")
    if item_key not in report:
        # The drift shape: a report written under a different item key. An empty LIST is a
        # legitimate zero-count run (the caller walks back); a missing KEY means the
        # writer and this reader disagree about the schema, and pretending the report is absent
        # would hide exactly that.
        raise ValueError(f"run report {path} has no {item_key} item list")
    items = report.get(item_key)
    if not isinstance(items, list):
        # A null or mapping-typed item list is the same writer/reader schema
        # disagreement as a missing key — not a zero-count run. The old
        # comprehension caught the null case as TypeError inside its try;
        # the explicit loop must not regress it to a bare traceback, and {}
        # must not masquerade as a legitimate empty LIST.
        raise ValueError(
            f"run report {path} has a non-list {item_key} value ({type(items).__name__})"
        )
    ids = set()
    for entry in items:
        if not isinstance(entry, dict):
            raise ValueError(
                f"run report {path} has malformed {item_key} entries: "
                f"expected a mapping, got {type(entry).__name__}"
            )
        try:
            # Validate BEFORE the outcome-key skip: an id-less or unhashable
            # id is corrupt input whatever the entry's outcome, and skipping
            # it silently would build a snapshot from an incomplete
            # processed-id set instead of refusing (CodeRabbit, CWE-20).
            # TypeError included: an unhashable id ({} / []) must follow the
            # same malformed-report path as a missing one.
            entry_id = entry["id"]
            hash(entry_id)
        except (KeyError, TypeError) as e:
            raise ValueError(f"run report {path} has malformed {item_key} entries: {e}") from e
        if "error" in entry or "failed_reason" in entry or "blocked_reason" in entry:
            # None of these were disposed of: error entries could not even be
            # reported on; failed_reason marks a crashed or never-attempted
            # split; blocked_reason a refusal. The live snapshot leaves all
            # of them processed:false — bootstrap recovery must agree, or a
            # quarantine-cleared parent bootstrapped from this report would
            # be frozen at processed:true (review finding, RHAIFIRST-571).
            continue
        ids.add(entry_id)
    return ids, report


def _older_run_names(results_dir, run_name):
    """Run directory names strictly older than run_name, newest first.

    Same eligibility rules as find_latest_run_timestamp; the strict name
    comparison also excludes replay directories newer than the `latest`
    symlink target (pushed with --no-update-latest).
    """
    names = []
    for name in sorted(os.listdir(results_dir), reverse=True):
        if name.startswith(".") or name in ("latest", "test-data"):
            continue
        path = os.path.join(results_dir, name)
        # isdir follows symlinks: a planted timestamp-named symlink would
        # otherwise route the walk-back into a report OUTSIDE results_dir
        # and let it rewrite the processed-ID filter (CWE-59). `latest` is
        # the one symlink this layout legitimately contains, and it is
        # already excluded by name above.
        if os.path.islink(path) or not os.path.isdir(path):
            continue
        try:
            datetime.strptime(name, "%Y%m%d-%H%M%S")
        except ValueError:
            continue
        if name < run_name:
            names.append(name)
    return names


def _run_dir_has_snapshots(results_dir, run_name):
    """True if the run directory holds issue snapshots.

    A run published by the pipeline always carries both a snapshot and a run report, so snapshots
    without a report means the results directory is partial — most often a clone made by
    clone_results_repo.py, whose sparse-checkout set deliberately materializes
    `issue-snapshot-*.yaml` and not the run report. Bootstrap cannot filter against a report it
    cannot see, and including every issue instead is not a safe default at this scale.
    """
    run_dir = os.path.join(results_dir, run_name, "auto-fix-runs")
    if not os.path.isdir(run_dir):
        return False
    return any(
        name.startswith(_RFE_SNAPSHOT_PREFIX) and name.endswith(".yaml")
        for name in os.listdir(run_dir)
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
        if name.startswith(".") or name in ("latest", "test-data"):
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
        path = (
            f"/issue/{urllib.parse.quote(key, safe='')}/changelog?startAt={start_at}&maxResults=100"
        )
        data = api_call_with_retry(server, path, user, token)

        for history in data.get("values", []):
            created_str = history.get("created", "")
            try:
                created = datetime.fromisoformat(
                    re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", created_str)
                )
            except (ValueError, TypeError):
                continue
            entries.append(
                {
                    "created": created,
                    "items": history.get("items", []),
                }
            )

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
                desc_changes.append(
                    {
                        "created": entry["created"],
                        "from": item.get("from")
                        if item.get("from") is not None
                        else item.get("fromString"),
                        "to": item.get("to")
                        if item.get("to") is not None
                        else item.get("toString"),
                    }
                )

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
    "done",
    "closed",
    "resolved",
    "completed",
    "won't do",
    "won't fix",
    "rejected",
    "cancelled",
    "canceled",
    "archived",
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
                status_changes.append(
                    {
                        "created": entry["created"],
                        "fromString": item.get("fromString", ""),
                        "toString": item.get("toString", ""),
                    }
                )

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
    url = (
        f"{server.rstrip('/')}/rest/api/2/issue/"
        f"{urllib.parse.quote(key, safe='')}?fields=description"
    )
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
    parser.add_argument(
        "--results-dir", required=True, help="Path to results repo with run directories"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print what would be done without writing"
    )
    parser.add_argument(
        "--artifacts-dir", default=None, help="Output directory (default: repo artifacts/)"
    )
    parser.add_argument(
        "--type",
        choices=_TYPES.choices(),
        default="rfe",
        help="Issue type (default: rfe)",
    )
    parser.add_argument(
        "--include-all",
        action="store_true",
        help=(
            "Proceed without the run-report filter even when the results directory looks "
            "incomplete. Escape hatch for recovery — the snapshot will cover every fetched "
            "issue, marked unprocessed"
        ),
    )
    args = parser.parse_args()

    snap_config = SNAPSHOT_CONFIG[args.type]
    boot_config = BOOTSTRAP_CONFIG[args.type]

    server, user, token = require_env()
    if not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN required", file=sys.stderr)
        sys.exit(1)

    # Step 1: Find the last run timestamp
    run_name, run_dt = find_latest_run_timestamp(args.results_dir)
    if not run_dt:
        print("Error: no valid run directories found", file=sys.stderr)
        sys.exit(1)
    print(f"Last run: {run_name} ({run_dt.isoformat()})", file=sys.stderr)
    # The snapshot FILE is always named after the tip run, even when the
    # walk-back reconstructs from an older one: find_previous_snapshot picks
    # the reverse-lexically newest name, so a file named after the older run
    # would be shadowed forever by any stale snapshot a pre-walk-back
    # bootstrap left under the tip name — and a re-run must overwrite that
    # file in place. bootstrapped_from records the run actually used.
    tip_name = run_name

    # Step 2: Fetch all current issues with hard filters
    excluded = f"{snap_config['ignore_label']}, {snap_config['quarantine_label']}"
    jql = (
        f"({args.jql}) AND statusCategory != Done "
        f"AND (labels not in ({excluded}) OR labels is EMPTY)"
    )
    print(f"JQL: {jql}", file=sys.stderr)

    current = fetch_all_issues(server, user, token, jql)
    print(f"Fetched {len(current)} issues from Jira", file=sys.stderr)

    # Filter to issues that were actually processed in the run
    run_report = None
    try:
        processed_ids, run_report = _load_run_report(args.results_dir, run_name, config=boot_config)
        # An empty item list is a legitimate zero-count run (a valid outcome
        # since bc2f553), not missing evidence: falling through to include-all
        # would re-surface the whole backlog that earlier runs already
        # processed. Walk back to the most recent run that processed anything
        # and reconstruct state as of THAT run — using the empty run's newer
        # timestamp instead would hash issues at post-edit state, hiding any
        # edit made between the two runs from change detection (RHAIFIRST-569).
        if processed_ids is not None and not processed_ids:
            for older_name in _older_run_names(args.results_dir, run_name):
                older_ids, older_report = _load_run_report(
                    args.results_dir, older_name, config=boot_config
                )
                if older_ids is None:
                    if _run_dir_has_snapshots(args.results_dir, older_name):
                        # Same shape the tip-level guard refuses: snapshots
                        # prove a real pipeline run whose report is invisible
                        # (partial clone, or an aborted run that submitted
                        # before it could publish). Its work is unknown, so
                        # "include everything as unprocessed" would re-select
                        # a backlog that run may already have disposed of.
                        if not args.include_all:
                            print(
                                f"Error: walk-back reached {older_name}, which has issue "
                                f"snapshots but no readable run report — the results "
                                f"directory looks partial. Point --results-dir at a full "
                                f"clone, or pass --include-all to snapshot every fetched "
                                f"issue as unprocessed.",
                                file=sys.stderr,
                            )
                            sys.exit(1)
                        print(
                            f"Warning: {older_name} has snapshots but no run report — "
                            f"--include-all set, including all issues",
                            file=sys.stderr,
                        )
                        processed_ids = None
                        break
                    print(
                        f"Warning: {older_name} has no run report — skipped in walk-back",
                        file=sys.stderr,
                    )
                    continue
                if not older_ids:
                    continue
                print(
                    f"Run {run_name} processed nothing — walking back to {older_name}",
                    file=sys.stderr,
                )
                run_name = older_name
                run_dt = datetime.strptime(older_name, "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
                processed_ids, run_report = older_ids, older_report
                break
            else:
                # Every report in history says "processed nothing" — that is
                # positive evidence, not absence, so neither the --include-all
                # gate nor the partial-clone guard applies (run_report stays
                # set to mark that a report WAS read): snapshotting everything
                # as unprocessed states exactly what the reports state.
                print(
                    "Warning: no run with a non-empty item list found — "
                    "including all issues as unprocessed",
                    file=sys.stderr,
                )
                processed_ids = None
    except ValueError as e:
        # Present but not understood is not the same as absent: absence has a documented
        # fallback, a corrupt report does not. Only an explicit --include-all proceeds.
        # The same strictness applies to a corrupt report met during walk-back — the
        # chain of evidence stops at the first report that cannot be read.
        if not args.include_all:
            print(
                f"Error: {e}. Pass --include-all to snapshot every fetched issue "
                f"as unprocessed instead.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"Warning: {e} — --include-all set, including all issues", file=sys.stderr)
        processed_ids = None
    unfiltered = processed_ids is None
    if unfiltered:
        if (
            not args.include_all
            and run_report is None
            and _run_dir_has_snapshots(args.results_dir, run_name)
        ):
            print(
                f"Error: {run_name} has issue snapshots but no readable run report — "
                f"the results directory looks partial (clone_results_repo.py omits run "
                f"reports by design). Point --results-dir at a full clone, or pass "
                f"--include-all to snapshot every fetched issue as unprocessed.",
                file=sys.stderr,
            )
            sys.exit(1)
        if run_report is None:
            print("Warning: no run report — including all issues", file=sys.stderr)
    else:
        before = len(current)
        current = {k: v for k, v in current.items() if k in processed_ids}
        print(f"Filtered to {len(current)}/{before} issues from run report", file=sys.stderr)

    if run_report is not None and run_report.get("report_stage") == "pre_submit":
        # One line, absence of the field is silent: pre-versioned reports say
        # nothing about their stage. A pre_submit tip predates that run's Jira
        # writes, so split children may appear under local ids and be missing
        # from this snapshot; they will surface as NEW on the next fetch.
        print(
            f"Warning: run report {run_name} is report_stage: pre_submit — it predates "
            f"that run's Jira writes; split children may appear under local ids and "
            f"be missing from this snapshot",
            file=sys.stderr,
        )

    # Step 3: Find which issues were updated since the run
    run_jql_ts = run_dt.strftime("%Y-%m-%d %H:%M")
    updated_jql = f'{jql} AND updated >= "{run_jql_ts}"'
    updated_keys = set()
    for issue in _fetch_paginated(server, user, token, updated_jql, "key"):
        updated_keys.add(issue["key"])
    print(f"Issues updated since run: {len(updated_keys)}", file=sys.stderr)

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
            current_wiki = _fetch_wiki_description(server, user, token, key)
            hist_hash = compute_content_hash(hist_desc)
            current_wiki_hash = compute_content_hash(current_wiki)
            if hist_hash == current_wiki_hash:
                # Description unchanged — use current ADF hash
                snapshot_issues[key] = data["content_hash"]
            else:
                snapshot_issues[key] = hist_hash
                hist_changed += 1

    print(
        f"Changelog lookups: {lookups} ({hist_changed} with changed description)", file=sys.stderr
    )
    if done_excluded:
        print(f"Excluded {done_excluded} issues (Done at run time)", file=sys.stderr)

    if unfiltered:
        # No run report, so there is no evidence any of these were processed. A bare hash would
        # claim otherwise: snapshot_fetch.diff_snapshots reads a missing `processed` as True
        # ("old format entries are implicitly processed"), and only a hash change ever resets it,
        # so the issues would be excluded from selection permanently and silently. Recording them
        # unprocessed surfaces them as NEW on the first incremental fetch, where check_resume can
        # still skip the ones that already have a passing review.
        snapshot_issues = {
            key: {"hash": content_hash, "processed": False}
            for key, content_hash in snapshot_issues.items()
        }

    # Step 5: Write snapshot
    if args.dry_run:
        scope = (
            "every fetched issue, marked unprocessed (no run-report filter)"
            if unfiltered
            else "issues from the run report"
        )
        print(f"\nDry run — would write snapshot with {len(snapshot_issues)} hashes: {scope}")
        return

    snapshot_dir = (
        os.path.join(args.artifacts_dir, "auto-fix-runs") if args.artifacts_dir else SNAPSHOT_DIR
    )
    os.makedirs(snapshot_dir, exist_ok=True)

    run_ts_str = run_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshot = {
        "query_timestamp": run_ts_str,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bootstrapped_from": run_name,
        "issues": snapshot_issues,
    }
    snapshot_prefix = snap_config["snapshot_prefix"]
    snapshot_path = os.path.join(snapshot_dir, f"{snapshot_prefix}{tip_name}.yaml")
    with open(snapshot_path, "w", encoding="utf-8") as f:
        yaml.dump(snapshot, f, default_flow_style=False, sort_keys=False)
    print(f"Wrote snapshot: {snapshot_path}")
    print(f"Bootstrap complete. {len(snapshot_issues)} issues.")


if __name__ == "__main__":
    main()
