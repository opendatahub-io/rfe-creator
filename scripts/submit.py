#!/usr/bin/env python3
"""Submit RFE or Initiative artifacts to Jira — create new or update existing.

Handles both split and standard submissions in one pass. Split parents
(Archived issues with children) are submitted via split_submit.py first,
then regular items are updated/created directly.

Reads all structured metadata from YAML frontmatter on task and review files.
No regex parsing of markdown prose.

Usage:
    python scripts/submit.py [--type rfe|initiative] [--dry-run] [--artifacts-dir DIR]

Environment variables:
    JIRA_SERVER  Jira server URL (e.g. https://mysite.atlassian.net)
    JIRA_USER    Jira username/email
    JIRA_TOKEN   Jira API token
"""

import argparse
import os
import subprocess
import sys

# Ensure progress output is visible immediately when stdout is redirected
# to a file or pipe (Python defaults to full buffering in that case).
sys.stdout.reconfigure(line_buffering=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import type_registry  # noqa: E402
from artifact_utils import (  # noqa: E402
    ValidationError,
    find_removed_context_yaml,
    read_frontmatter_validated,
    rebuild_index,
    rename_to_tracker_key,
    render_removed_context_comment,
    scan_tasks,
    update_frontmatter,
)
from generate_run_report import TYPE_CONFIG as REPORT_TYPE_CONFIG  # noqa: E402
from generate_run_report import _parse_run_id  # noqa: E402
from jira_utils import (  # noqa: E402
    add_comment,
    add_labels,
    check_description_conflict,
    create_issue,
    get_myself,
    markdown_to_adf,
    remove_labels,
    require_env,
    strip_metadata,
    swap_labels,
    transition_issue,
    update_issue,
)
from snapshot_fetch import compute_content_hash, update_snapshot_hashes  # noqa: E402

# ─── Type Configurations ─────────────────────────────────────────────────────

# The work-item type registry (types/<name>/type.yaml), read once at import. Every
# per-type value below is a projection of a descriptor (design
# work-item-types-unified.md §10 item 2) — DESCRIPTOR values only, never the effective
# binding: an environment override must not reach the Jira write path before resolve()
# (a later PR) prints and checks it.
_TYPES = type_registry.load()


def _type_config(desc):
    """Project one descriptor onto the per-type table main() reads.

    Same keys, same key order and same values as the literal table this replaces, so
    every label, comment, plan line and Jira payload composed from it is unchanged byte
    for byte. Two entries are conventions rather than descriptor fields, both
    grandfathered for rfe: ``snapshot_prefix`` is "" for rfe — a sentinel for
    snapshot_fetch's default prefix (only a truthy value is forwarded as ``prefix=``) —
    and ``split_type_arg`` is the argv convention for spawning split_submit.py (no
    ``--type`` for rfe, the type name otherwise). The labels composed elsewhere in this
    file (auto-created, auto-revised, needs-attention, split-quarantine) keep composing
    from ``label_prefix`` exactly as before.
    """
    dirs = desc.dirs("bare")
    alignment = desc.get("conventions.labels.alignment", None)
    return {
        "project": desc.get("identity.jira.project"),
        "issue_type": desc.get("identity.jira.issue_type"),
        "type_label": desc.get("conventions.type_label"),
        "id_field": desc.id_field,
        "local_prefix": desc.local_prefix,
        "jira_prefix": desc.write_prefix,
        "tasks_dir": dirs["tasks"],
        "reviews_dir": dirs["reviews"],
        "originals_dir": dirs["originals"],
        "task_schema": f"{desc.name}-task",
        "review_schema": f"{desc.name}-review",
        "snapshot_prefix": "" if desc.name == "rfe" else desc.get("snapshot.prefix"),
        "split_type_arg": None if desc.name == "rfe" else desc.name,
        "label_prefix": desc.get("conventions.label_prefix"),
        "rubric_pass_label": desc.get("conventions.labels.rubric_pass"),
        "feasibility_labels": dict(desc.get("conventions.labels.feasibility")),
        "alignment_labels": None if alignment is None else dict(alignment),
        "removed_context_preamble": desc.get("conventions.removed_context_preamble"),
        "comment_prefix": desc.get("conventions.comment_prefix"),
        "has_index": desc.get("index.enabled"),
    }


TYPE_CONFIGS = {name: _type_config(_TYPES.get(name)) for name in _TYPES.names()}

# Module-level alias for backward compat (used by test_submit.py direct imports)
FEASIBILITY_LABELS = TYPE_CONFIGS["rfe"]["feasibility_labels"]


# ─── Helpers ──────────────────────────────────────────────────────────────────


def feasibility_label_changes(verdict, *, is_reject, original_labels, feasibility_labels=None):
    """Return (label_to_add_or_None, [labels_to_remove]) for feasibility labels.

    Conditional removal: only labels actually in original_labels.
    """
    if feasibility_labels is None:
        feasibility_labels = FEASIBILITY_LABELS
    original = original_labels or []
    if is_reject:
        return None, [lbl for lbl in feasibility_labels.values() if lbl in original]
    if verdict not in feasibility_labels:
        return None, []
    new_label = feasibility_labels[verdict]
    stale = [lbl for lbl in feasibility_labels.values() if lbl != new_label and lbl in original]
    return new_label, stale


def _find_review(artifacts_dir, item_id, cfg):
    """Find review file path for an item, or None."""
    path = os.path.join(artifacts_dir, cfg["reviews_dir"], f"{item_id}-review.md")
    return path if os.path.isfile(path) else None


def _generate_reports(args):
    """Regenerate the run report YAML and its HTML companion."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ts = args.report_timestamp
    run_id = _parse_run_id(ts)

    print("\nGenerating reports...")

    yaml_cmd = [
        sys.executable,
        os.path.join(script_dir, "generate_run_report.py"),
        "--start-time",
        ts,
        "--artifacts-dir",
        args.artifacts_dir,
        # Regenerated after submit, so per-entry values are authoritative.
        "--report-stage",
        "final",
    ]
    # Both report scripts default to rfe; the rfe invocation carries no --type (grandfathered
    # argv, kept byte-identical) and every other type is named explicitly.
    if args.type != "rfe":
        yaml_cmd.extend(["--type", args.type])
    result = subprocess.run(yaml_cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"  YAML report: {result.stdout.strip()}")
    else:
        print(f"Warning: YAML report generation failed: {result.stderr}", file=sys.stderr)

    html_output = report_companion_path(args.artifacts_dir, run_id, args.type, "-report.html")
    html_cmd = [
        sys.executable,
        os.path.join(script_dir, "generate_review_pdf.py"),
        "--revised-only",
        "--artifacts-dir",
        args.artifacts_dir,
        "--output",
        html_output,
    ]
    if args.type != "rfe":
        html_cmd.extend(["--type", args.type])
    result = subprocess.run(html_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Warning: HTML report generation failed: {result.stderr}", file=sys.stderr)


def _record_not_attempted(args, cfg, parent_keys, error):
    """Local-only record for split parents a phase abort skipped.

    Deliberately touches nothing in Jira — the whole point of aborting is to
    avoid a fan-out of flags on parents that were never examined. The review
    error keeps the run report truthful (they count failed, not split) and
    keeps bootstrap from treating them as disposed. The next nightly
    re-selects them normally: their snapshot entries stay unprocessed and
    no quarantine label is applied.
    """
    for parent_key in parent_keys:
        review_path = _find_review(args.artifacts_dir, parent_key, cfg)
        if not review_path:
            continue
        try:
            update_frontmatter(review_path, {"error": error}, cfg["review_schema"])
        except Exception as e:
            print(
                f"  Warning: could not record not-attempted on {parent_key} ({e}).",
                file=sys.stderr,
            )


def _record_split_failure(
    server, user, token, parent_key, reason, error, args, cfg, parent_data, quarantine=False
):
    """Record a split that did not complete: review frontmatter, then Jira comment and label.

    Both halves are best-effort on purpose. The point of recording is to survive a failure, and
    an unguarded write here would replace the diagnosis with a traceback — the Jira calls raise
    under the failures most worth recording (expired token, unreachable instance), and the local
    write raises on a corrupt or read-only review file.

    quarantine=True additionally labels the parent {label_prefix}-split-quarantine, which the
    fetch hard filters exclude — a partially-applied split must not be silently re-attempted
    by the nightly (each retry can mint duplicate children) until a human removes the label
    (RHAIFIRST-570). Refusals stay un-quarantined: nothing was created, and a re-attempt after
    the backlog changes is cheap and safe.
    """
    review_path = _find_review(args.artifacts_dir, parent_key, cfg)
    if review_path:
        try:
            update_frontmatter(
                review_path,
                {"error": error, "needs_attention": True, "needs_attention_reason": reason},
                cfg["review_schema"],
            )
        except Exception as e:
            print(
                f"  Warning: could not record the failure on {parent_key}'s review ({e}).",
                file=sys.stderr,
            )

    entry = {
        cfg["id_field"]: parent_key,
        "attn_reason": reason,
        "original_labels": parent_data.get("original_labels") or [],
    }
    # The labels go FIRST and in their own try: the quarantine label is the
    # load-bearing write — it is what stops the nightly from re-selecting a
    # partially-applied parent — and it must not be lost to a failure in the
    # advisory comment posting (CodeRabbit, CWE-703).
    try:
        if not args.dry_run:
            flag_labels = [f"{cfg['label_prefix']}-needs-attention"]
            if quarantine:
                flag_labels.append(f"{cfg['label_prefix']}-split-quarantine")
            add_labels(server, user, token, parent_key, flag_labels)
    except Exception as e:
        print(
            f"  Warning: could not label {parent_key} in Jira ({e}). Recorded locally.",
            file=sys.stderr,
        )
    try:
        _post_needs_attention_comment(
            server, user, token, entry, {parent_key: parent_key}, args.dry_run, cfg
        )
    except Exception as e:
        print(
            f"  Warning: could not flag {parent_key} in Jira ({e}). Recorded locally.",
            file=sys.stderr,
        )


def _finish(args, type_label, submit_errors):
    """Summarise failures, regenerate the reports, then exit. Never returns.

    Every terminating path routes through here. A path that skipped it took the run report with
    it, so successes already committed to Jira ended up recorded nowhere at all.
    """
    if submit_errors:
        print(f"\n{len(submit_errors)} {type_label}(s) failed during submit:", file=sys.stderr)
        for eid, emsg in submit_errors:
            print(f"  {eid}: {emsg}", file=sys.stderr)

    if args.generate_report:
        _generate_reports(args)

    sys.exit(1 if submit_errors else 0)


def report_companion_path(artifacts_dir, run_id, work_type, suffix):
    """Path for a run report companion file, beside the YAML it belongs to.

    generate_run_report owns the report filename, prefix included. Re-deriving that prefix here
    would be a second copy of a per-type constant, so the companion resolves it from the same
    config the writer uses.
    """
    prefix = REPORT_TYPE_CONFIG[work_type]["output_prefix"]
    return os.path.join(artifacts_dir, "auto-fix-runs", f"{prefix}{run_id}{suffix}")


def _post_needs_attention_comment(server, user, token, entry, results, dry_run, cfg):
    """Post a Jira comment explaining why human attention is needed."""
    reason = entry.get("attn_reason")
    if not reason:
        return

    original_labels = entry.get("original_labels") or []
    needs_attn_label = f"{cfg['label_prefix']}-needs-attention"
    if needs_attn_label in original_labels:
        return

    item_id = entry[cfg["id_field"]]
    target_key = results.get(item_id)
    if dry_run:
        print(f"  {item_id}: Would post needs-attention comment")
        return

    if not target_key:
        return

    comment_md = (
        f"*{cfg['comment_prefix']}* This {cfg['type_label']} "
        f"has been flagged for human review:\n\n{reason}"
    )
    comment_adf = markdown_to_adf(comment_md)
    add_comment(server, user, token, target_key, comment_adf)
    print(f"  {item_id}: Posted needs-attention comment")


# ─── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--type",
        choices=_TYPES.choices(),
        default="rfe",
        help="Item type to submit (default: rfe)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print planned actions without making API calls"
    )
    parser.add_argument(
        "--artifacts-dir", default="artifacts", help="Artifacts directory (default: artifacts)"
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Transition qualifying items to Approved status in Jira",
    )
    parser.add_argument(
        "--generate-report",
        action="store_true",
        help="Generate YAML and HTML reports after submission",
    )
    parser.add_argument(
        "--report-timestamp",
        help="Run timestamp for report naming (required with --generate-report)",
    )
    args = parser.parse_args()

    cfg = TYPE_CONFIGS[args.type]
    desc = _TYPES.get(args.type)
    id_field = cfg["id_field"]
    jira_prefix = cfg["jira_prefix"]
    type_label = cfg["type_label"]
    # The one transition this script performs, declared per tracker binding (design §3.4):
    # the target status of --auto-approve and the already-there short-circuit. Optional in
    # the schema (a type that never approves omits it), so it is only required by the flag.
    approved_status = desc.get("identity.jira.state_map.approved", None)

    if args.generate_report and not args.report_timestamp:
        parser.error("--report-timestamp is required when --generate-report is set")
    if args.auto_approve and not approved_status:
        parser.error(
            f"--auto-approve: type '{args.type}' declares no identity.jira.state_map.approved"
        )

    server, user, token = require_env()

    if not args.dry_run and not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars required.", file=sys.stderr)
        print("Set these or use --dry-run for local-only validation.", file=sys.stderr)
        sys.exit(1)

    # Scan task files
    tasks = scan_tasks(args.artifacts_dir, desc)
    if not tasks:
        print(f"Error: No {type_label} task files found.", file=sys.stderr)
        sys.exit(1)

    # --- Phase 1: Submit splits via split_submit.py ---
    child_parent_keys = {data.get("parent_key") for _, data in tasks if data.get("parent_key")}
    split_parent_data = {
        data[id_field]: data
        for _, data in tasks
        if data.get("status") == "Archived"
        and data[id_field].startswith(jira_prefix)
        and data[id_field] in child_parent_keys
    }
    split_parents = list(split_parent_data.keys())

    _parent_of = {}
    for _, data in tasks:
        pk = data.get("parent_key")
        if pk:
            _parent_of[data[id_field]] = pk

    def _has_jira_ancestor(item_id):
        """True if item_id descends from any Jira-keyed parent."""
        seen = set()
        pk = _parent_of.get(item_id)
        while pk and pk not in seen:
            if pk.startswith(jira_prefix):
                return True
            seen.add(pk)
            pk = _parent_of.get(pk)
        return False

    # Hoisted above the split loop: both loops record into it, and every exit
    # path reports it.
    submit_errors = []

    if split_parents:
        print(f"Phase 1: Submitting {len(split_parents)} split parent(s)\n")
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Test seam: the loop policy (systemic abort, circuit breaker) cannot
        # be exercised end-to-end with the real script, which classifies
        # everything it can reach into 0/2/3/4/5. Honored only under pytest —
        # in production a stray env var must not silently swap the script.
        split_script = os.path.join(script_dir, "split_submit.py")
        if os.environ.get("RFE_SPLIT_SUBMIT_SCRIPT") and os.environ.get("PYTEST_CURRENT_TEST"):
            split_script = os.environ["RFE_SPLIT_SUBMIT_SCRIPT"]

        # Preflight one cheap authenticated call: a dead token or unreachable
        # instance must cost one request and one accurate message, not a
        # fan-out of N failures and N bogus needs-attention flags
        # (RHAIFIRST-571).
        if not args.dry_run:
            try:
                get_myself(server, user, token)
            except Exception as e:
                print(
                    f"Error: Jira preflight failed — {type(e).__name__}: {e}. "
                    f"Skipping all {len(split_parents)} split parent(s); nothing "
                    f"was attempted.",
                    file=sys.stderr,
                )
                submit_errors.append(("jira-preflight", f"{type(e).__name__}: {e}"))
                _record_not_attempted(
                    args,
                    cfg,
                    sorted(split_parents),
                    "split_not_attempted: Jira preflight failed",
                )
                _finish(args, type_label, submit_errors)

        # Circuit breaker for failures the exit-code classifier cannot see
        # (signal deaths, unexpected codes): consecutive-ness is the
        # discriminator — a one-off continues, a streak of K aborts. break,
        # never sys.exit, so _finish() still writes the report.
        consecutive_generic = 0
        abort_splits = False
        attempted_parents = set()

        for parent_key in sorted(split_parents):
            attempted_parents.add(parent_key)
            cmd = [
                sys.executable,
                split_script,
                parent_key,
                "--artifacts-dir",
                args.artifacts_dir,
            ]
            if cfg["split_type_arg"]:
                cmd.extend(["--type", cfg["split_type_arg"]])
            if args.dry_run:
                cmd.append("--dry-run")
            print(f"--- {parent_key} ---")
            result = subprocess.run(cmd)
            if result.returncode in (0, 2, 3, 4, 5):
                # Any CLASSIFIED outcome resets the streak: the breaker's
                # discriminator is "the classifier missed twice in a row",
                # and a refusal or per-parent failure in between proves the
                # classifier is alive (review finding: two isolated signal
                # deaths separated by a healthy refusal are not a streak).
                consecutive_generic = 0
            # argparse used to exit 2 as well; split_submit now routes usage
            # errors to 64, so 2 is unambiguously the leaf cap.
            if result.returncode == 2:
                print(f"  {parent_key}: Split refused — too many children")
                _record_split_failure(
                    server,
                    user,
                    token,
                    parent_key,
                    f"Automatic splitting produced too many child {type_label}s. "
                    "The decomposition needs human review to determine "
                    "the right granularity.",
                    "split_refused: too many leaf children",
                    args,
                    cfg,
                    split_parent_data[parent_key],
                )
                continue
            elif result.returncode == 3:
                print(f"  {parent_key}: Split refused — Jira conflict")
                _record_split_failure(
                    server,
                    user,
                    token,
                    parent_key,
                    f"Parent {type_label} description was modified in Jira since "
                    "fetch. Split submission skipped to avoid "
                    "overwriting human edits.",
                    "split_refused: jira conflict",
                    args,
                    cfg,
                    split_parent_data[parent_key],
                )
                continue
            elif result.returncode == 5:
                # Systemic: Jira itself is unusable (dead auth, outage). The
                # FAILING parent is recorded and quarantined like a
                # per-parent failure — the classify wrapper spans discovery
                # through phase 3, so a mid-run token death can exit 5 AFTER
                # real Jira writes, and an unquarantined partial split is
                # re-decomposed by the next nightly into duplicates
                # (reproduced in review). The Jira half of the record is
                # best-effort and likely fails here too; the LOCAL review
                # error is what keeps the report truthful. Then abort: every
                # remaining parent would fail identically, ~3 requests and
                # ~21s of backoff each, leaving bogus flags behind.
                print(
                    f"Error: systemic Jira failure on {parent_key} — aborting the "
                    f"split phase; remaining parents were not attempted.",
                    file=sys.stderr,
                )
                _record_split_failure(
                    server,
                    user,
                    token,
                    parent_key,
                    "Systemic Jira failure during split submission (exit 5). Jira may "
                    f"be partially updated — check {parent_key} before retrying.",
                    "split_submit_failed: exit 5",
                    args,
                    cfg,
                    split_parent_data[parent_key],
                    quarantine=True,
                )
                submit_errors.append((parent_key, "systemic Jira failure (split phase aborted)"))
                abort_splits = True
                break
            elif result.returncode == 4:
                # Per-parent: this parent failed, the others are unaffected —
                # record, quarantine (Jira may be partially updated), continue.
                print(
                    f"Error: split_submit.py failed for {parent_key} (per-parent, exit 4)",
                    file=sys.stderr,
                )
                _record_split_failure(
                    server,
                    user,
                    token,
                    parent_key,
                    f"Split submission failed partway (exit 4). Jira may be "
                    f"partially updated — check {parent_key} for child {type_label}s that were "
                    "already created, and for unlinked children, before retrying.",
                    "split_submit_failed: exit 4",
                    args,
                    cfg,
                    split_parent_data[parent_key],
                    quarantine=True,
                )
                submit_errors.append((parent_key, "split_submit exit 4 (may be partial)"))
            elif result.returncode != 0:
                # Unclassified (signal death, usage error, unexpected code):
                # may be PARTIALLY APPLIED, so record and quarantine like a
                # per-parent failure — but count the streak: the classifier
                # missing twice in a row is the systemic shape it cannot see.
                print(
                    f"Error: split_submit.py failed for {parent_key} "
                    f"(exit code {result.returncode})",
                    file=sys.stderr,
                )
                _record_split_failure(
                    server,
                    user,
                    token,
                    parent_key,
                    f"Split submission failed partway (exit {result.returncode}). Jira may be "
                    f"partially updated — check {parent_key} for child {type_label}s that were "
                    "already created, and for unlinked children, before retrying.",
                    f"split_submit_failed: exit {result.returncode}",
                    args,
                    cfg,
                    split_parent_data[parent_key],
                    quarantine=True,
                )
                submit_errors.append(
                    (parent_key, f"split_submit exit {result.returncode} (may be partial)")
                )
                consecutive_generic += 1
                if consecutive_generic >= 2:
                    print(
                        "Error: 2 consecutive unclassified split failures — stopping the "
                        "split phase; remaining parents were not attempted.",
                        file=sys.stderr,
                    )
                    abort_splits = True
                    break
            print()

        if abort_splits:
            # The parents the abort skipped still carry recommendation:
            # split with no error — the final report would count them as
            # SUCCESSFUL splits (review finding). Record a LOCAL-ONLY error
            # on each: no Jira flags (that fan-out is what the abort
            # avoids), but the report shows them failed and bootstrap does
            # not count them disposed.
            _record_not_attempted(
                args,
                cfg,
                [p for p in sorted(split_parents) if p not in attempted_parents],
                "split_not_attempted: split phase aborted before this parent",
            )
            # The report is still written — the parents that succeeded
            # earlier in this loop are real in Jira and this is the only
            # place they are recorded.
            _finish(args, type_label, submit_errors)

    # Record split-child hashes in the snapshot
    if split_parents and not args.dry_run:
        try:
            split_child_hashes = {}
            post_split_tasks = scan_tasks(args.artifacts_dir, desc)
            for path, data in post_split_tasks:
                if data.get("parent_key") and data.get("status") == "Submitted":
                    item_id = data.get(id_field, "")
                    if item_id.startswith(jira_prefix):
                        with open(path, encoding="utf-8") as f:
                            raw = f.read()
                        cleaned = strip_metadata(raw)
                        desc_adf = markdown_to_adf(cleaned)
                        split_child_hashes[item_id] = compute_content_hash(desc_adf)
            if split_child_hashes:
                snap_dir = os.path.join(args.artifacts_dir, "auto-fix-runs")
                snap_kwargs = {}
                if cfg["snapshot_prefix"]:
                    snap_kwargs["prefix"] = cfg["snapshot_prefix"]
                updated = update_snapshot_hashes(split_child_hashes, snap_dir, **snap_kwargs)
                if updated:
                    print(
                        f"  Updated snapshot with "
                        f"{len(split_child_hashes)} split-child "
                        f"hashes: {updated}"
                    )
                else:
                    print("  Warning: no snapshot found for split-child hashes", file=sys.stderr)
        except Exception as exc:
            print(
                f"  Warning: failed to record split-child hashes in snapshot: {exc}",
                file=sys.stderr,
            )

    # --- Phase 2: Submit regular items ---
    tasks = scan_tasks(args.artifacts_dir, desc)

    submittable = [
        (path, data)
        for path, data in tasks
        if data.get("status") not in ("Archived", "Submitted")
        and not _has_jira_ancestor(data[id_field])
    ]
    any_submitted = any(
        data.get("status") == "Submitted"
        for _, data in tasks
        if not _has_jira_ancestor(data[id_field])
    )
    if not submittable:
        if split_parents or any_submitted:
            if cfg["has_index"]:
                rebuild_index(args.artifacts_dir)
                print(f"Done. Index rebuilt at {args.artifacts_dir}/rfes.md")
            else:
                print(f"Done. {len(split_parents)} split(s) processed.")
            # A split-only batch reaches here on the happy path too, so returning early meant a
            # run whose every input was a split never produced a report at all.
            _finish(args, type_label, submit_errors)
        print(f"Error: No submittable {type_label}s found.", file=sys.stderr)
        sys.exit(1)

    if split_parents:
        print(f"Phase 2: Submitting {len(submittable)} regular {type_label}(s)\n")

    # Build submission plan
    def _build_labels(item_id, review_data, is_existing, rec, original_labels):
        labels = []
        if not is_existing:
            labels.append(f"{cfg['label_prefix']}-auto-created")
        if review_data and review_data.get("auto_revised", False):
            labels.append(f"{cfg['label_prefix']}-auto-revised")
        if review_data and review_data.get("needs_attention", False):
            labels.append(f"{cfg['label_prefix']}-needs-attention")
        if cfg["rubric_pass_label"] and review_data and rec == "submit":
            labels.append(cfg["rubric_pass_label"])
        if review_data:
            feas_add, _ = feasibility_label_changes(
                review_data.get("feasibility"),
                is_reject=False,
                original_labels=original_labels,
                feasibility_labels=cfg["feasibility_labels"],
            )
            if feas_add:
                labels.append(feas_add)
        if cfg["alignment_labels"] and review_data:
            alignment = review_data.get("alignment")
            if alignment in cfg["alignment_labels"]:
                labels.append(cfg["alignment_labels"][alignment])
        return labels

    plan = []
    for task_path, task_data in submittable:
        item_id = task_data[id_field]
        title = task_data["title"]
        is_existing = item_id.startswith(jira_prefix)
        priority = task_data["priority"]
        size = task_data.get("size", "M")

        review_path = _find_review(args.artifacts_dir, item_id, cfg)
        review_data = None
        if review_path:
            try:
                review_data, _ = read_frontmatter_validated(review_path, cfg["review_schema"])
            except (ValidationError, Exception) as e:
                print(f"Warning: cannot read review for {item_id}: {e}", file=sys.stderr)

        if review_path is None:
            # No review file at all means this item never finished the
            # pipeline — interrupted mid-review, or its review was cleared
            # for a reassess cycle that never ran. Submitting would push
            # content no review approved, and marking it processed would
            # freeze it out of every future run at an unchanged hash
            # (RHAIRFE-3201, RHAIFIRST-582). Leave it untouched and
            # unprocessed: the next fetch selects it as NEW, and check_resume
            # finds no passing review to skip it on. Deliberately narrower
            # than review_data is None: a PRESENT but schema-invalid review
            # keeps the old warn-and-proceed behavior, because check_resume
            # reads reviews with a laxer bar and would keep skipping the
            # re-review — leaving such an item unprocessed here would make
            # it re-selected but never disposed of, forever.
            plan.append(
                {
                    id_field: item_id,
                    "title": title,
                    "is_existing": is_existing,
                    "priority": priority,
                    "size": size,
                    "action": "SKIP",
                    "labels": [],
                    "remove_labels": [],
                    "skip_reason": "no readable review — left unprocessed for the next run",
                    "task_path": task_path,
                    "attn_reason": None,
                    "original_labels": task_data.get("original_labels") or [],
                    "auto_approve": False,
                    "jira_status": None,
                    "leave_unprocessed": True,
                }
            )
            continue

        rec = "submit"
        if review_data:
            rec = review_data.get("recommendation", "submit")

        original_labels = task_data.get("original_labels") or []
        attn_reason = None
        if review_data and review_data.get("needs_attention", False):
            attn_reason = review_data.get("needs_attention_reason")

        # Both types gate on the same rule: only an explicitly feasible item
        # auto-approves. An `indeterminate` verdict means the assessment was
        # inconclusive, which is not a basis for transitioning a ticket to
        # Approved on its own. `needs_attention` is advisory here — it drives
        # the needs-attention label, not the transition.
        auto_approve = (
            review_data
            and review_data.get("pass", False)
            and review_data.get("feasibility") == "feasible"
        )

        # Skip rejected items
        if rec in ("reject", "autorevise_reject"):
            remove = []
            if is_existing and cfg["rubric_pass_label"]:
                if cfg["rubric_pass_label"] in original_labels:
                    remove.append(cfg["rubric_pass_label"])
            _, feas_remove = feasibility_label_changes(
                None,
                is_reject=True,
                original_labels=original_labels,
                feasibility_labels=cfg["feasibility_labels"],
            )
            remove.extend(feas_remove)
            plan.append(
                {
                    id_field: item_id,
                    "title": title,
                    "is_existing": is_existing,
                    "priority": priority,
                    "size": size,
                    "action": "Remove labels" if remove else "SKIP",
                    "labels": [],
                    "remove_labels": remove,
                    "skip_reason": None if remove else "rejected",
                    "task_path": task_path,
                    "attn_reason": None,
                    "original_labels": original_labels,
                    "auto_approve": False,
                    "jira_status": None,
                }
            )
            continue

        # For existing items, check for Jira conflicts
        jira_status = None
        if is_existing and not args.dry_run:
            original_path = os.path.join(args.artifacts_dir, cfg["originals_dir"], f"{item_id}.md")
            try:
                has_conflict, issue_fields = check_description_conflict(
                    server, user, token, item_id, original_path, extra_fields=["status"]
                )
                if issue_fields:
                    jira_status = issue_fields.get("status", {}).get("name")
                if has_conflict:
                    plan.append(
                        {
                            id_field: item_id,
                            "title": title,
                            "is_existing": is_existing,
                            "priority": priority,
                            "size": size,
                            "action": "SKIP",
                            "labels": [],
                            "remove_labels": [],
                            "skip_reason": "Jira conflict — description modified since fetch",
                            "task_path": task_path,
                            "attn_reason": None,
                            "original_labels": original_labels,
                            "auto_approve": False,
                            "jira_status": jira_status,
                        }
                    )
                    continue
            except Exception as e:
                print(f"Warning: conflict check failed for {item_id}: {e}", file=sys.stderr)

        # For existing items, check if content has changed
        if is_existing:
            original_path = os.path.join(args.artifacts_dir, cfg["originals_dir"], f"{item_id}.md")
            if os.path.exists(original_path):
                with open(original_path, encoding="utf-8") as f:
                    original_body = strip_metadata(f.read())
                with open(task_path, encoding="utf-8") as f:
                    current_body = strip_metadata(f.read())
                if original_body.strip() == current_body.strip():
                    no_change_labels = _build_labels(
                        item_id, review_data, is_existing, rec, original_labels
                    )
                    feas_remove = []
                    if review_data:
                        _, feas_remove = feasibility_label_changes(
                            review_data.get("feasibility"),
                            is_reject=False,
                            original_labels=original_labels,
                            feasibility_labels=cfg["feasibility_labels"],
                        )
                    has_work = no_change_labels or feas_remove
                    plan.append(
                        {
                            id_field: item_id,
                            "title": title,
                            "is_existing": is_existing,
                            "priority": priority,
                            "size": size,
                            "action": "Label only" if has_work else "SKIP",
                            "labels": no_change_labels,
                            "remove_labels": feas_remove,
                            "skip_reason": None if has_work else "no changes",
                            "task_path": task_path,
                            "attn_reason": attn_reason,
                            "original_labels": original_labels,
                            "auto_approve": auto_approve,
                            "jira_status": jira_status,
                        }
                    )
                    continue

        labels = _build_labels(item_id, review_data, is_existing, rec, original_labels)
        feas_remove = []
        if review_data:
            _, feas_remove = feasibility_label_changes(
                review_data.get("feasibility"),
                is_reject=False,
                original_labels=original_labels,
                feasibility_labels=cfg["feasibility_labels"],
            )

        action = f"Update {item_id}" if is_existing else "Create"
        plan.append(
            {
                id_field: item_id,
                "title": title,
                "is_existing": is_existing,
                "priority": priority,
                "size": size,
                "action": action,
                "labels": labels,
                "remove_labels": feas_remove,
                "skip_reason": None,
                "task_path": task_path,
                "attn_reason": attn_reason,
                "original_labels": original_labels,
                "auto_approve": auto_approve,
                "jira_status": jira_status,
            }
        )

    # Print summary
    print(f"Submission plan: {len(plan)} {type_label}(s)")
    print(f"{'ID':<16} {'Title':<44} {'Priority':<10} {'Action':<20}")
    print("-" * 90)
    for entry in plan:
        item_id = entry[id_field]
        t = entry["title"]
        display_title = t[:41] + "..." if len(t) > 44 else t
        print(f"{item_id:<16} {display_title:<44} {entry['priority']:<10} {entry['action']:<20}")
        if entry["labels"]:
            print(f"{'':>16} Labels: {', '.join(entry['labels'])}")
        if entry.get("remove_labels"):
            print(f"{'':>16} Remove: {', '.join(entry['remove_labels'])}")
        if entry["skip_reason"]:
            print(f"{'':>16} Reason: {entry['skip_reason']}")
    print()

    approve_comment = (
        f"*{cfg['comment_prefix']}* This {type_label} has been automatically "
        f"transitioned to {approved_status} status based on passing rubric scoring and "
        "technical feasibility checks. Approval does not constitute a commitment "
        "to customers until this item is prioritized into a product release "
        "by product management."
    )

    def _maybe_approve(item_id, jira_key, entry):
        if not args.auto_approve or not entry.get("auto_approve"):
            return
        if entry.get("jira_status") == approved_status:
            print(f"  {item_id}: Already {approved_status}, skipping transition")
            return
        if args.dry_run:
            print(f"  {item_id}: Would transition to {approved_status}")
            return
        if transition_issue(server, user, token, jira_key, approved_status):
            print(f"  {item_id}: Transitioned to {approved_status}")
            comment_adf = markdown_to_adf(approve_comment)
            add_comment(server, user, token, jira_key, comment_adf)
            print(f"  {item_id}: Posted auto-approve comment")

    # Execute
    results = {}
    submitted_hashes = {}
    mark_processed_ids = []
    for entry in plan:
        item_id = entry[id_field]
        if entry["skip_reason"]:
            # A conflict or an unreviewed item is not disposed of — marking it
            # processed would exclude it from every future fetch (invariant 7:
            # only a hash change resets the flag). Everything else skipped
            # here was a decision (e.g. rejected) and counts as disposal.
            if "Jira conflict" not in entry["skip_reason"] and not entry.get("leave_unprocessed"):
                mark_processed_ids.append(item_id)
            print(f"  {item_id}: Skipping — {entry['skip_reason']}")
            continue

        try:
            if entry["action"] == "Remove labels":
                remove = entry["remove_labels"]
                if args.dry_run:
                    print(f"  {item_id}: Would remove labels: {', '.join(remove)}")
                else:
                    remove_labels(server, user, token, item_id, remove)
                    print(f"  {item_id}: Removed labels: {', '.join(remove)}")
                mark_processed_ids.append(item_id)
                continue
            if entry["action"] == "Label only":
                labels = entry["labels"]
                remove = entry.get("remove_labels") or []
                if args.dry_run:
                    if remove:
                        print(f"  {item_id}: Would remove labels: {', '.join(remove)}")
                    if labels:
                        print(f"  {item_id}: Would add labels: {', '.join(labels)}")
                else:
                    if remove or labels:
                        swap_labels(server, user, token, item_id, labels, remove)
                        if remove:
                            print(f"  {item_id}: Removed labels: {', '.join(remove)}")
                        if labels:
                            print(f"  {item_id}: Labels: {', '.join(labels)}")
                    update_frontmatter(
                        entry["task_path"], {"status": "Submitted"}, cfg["task_schema"]
                    )
                results[item_id] = item_id
                _post_needs_attention_comment(
                    server, user, token, entry, results, args.dry_run, cfg
                )
                _maybe_approve(item_id, item_id, entry)
                mark_processed_ids.append(item_id)
                continue

            # Read and clean artifact content
            with open(entry["task_path"], encoding="utf-8") as f:
                raw_content = f.read()
            cleaned = strip_metadata(raw_content)
            description_adf = markdown_to_adf(cleaned)

            title = entry["title"]
            labels = entry["labels"]
            remove = entry.get("remove_labels") or []

            if entry["is_existing"]:
                if args.dry_run:
                    print(f"  {item_id}: Would update")
                    if remove:
                        print(f"           Would remove: {', '.join(remove)}")
                else:
                    update_issue(server, user, token, item_id, title, description_adf)
                    print(f"  {item_id}: Updated")
                    if remove or labels:
                        swap_labels(server, user, token, item_id, labels, remove)
                        if remove:
                            print(f"           Removed: {', '.join(remove)}")
                        if labels:
                            print(f"           Labels: {', '.join(labels)}")
                    submitted_hashes[item_id] = compute_content_hash(description_adf)
                    update_frontmatter(
                        entry["task_path"], {"status": "Submitted"}, cfg["task_schema"]
                    )
                results[item_id] = item_id
            else:
                if args.dry_run:
                    print(
                        f"  {item_id}: Would create {cfg['project']} {cfg['issue_type']}: {title}"
                    )
                    results[item_id] = f"{jira_prefix}DRY"
                else:
                    create_kwargs = {}
                    # Read parent_key from frontmatter for new items. Only a
                    # Jira key can be sent as the parent — a local id (RFE-NNN
                    # / INIT-NNN) names an item that does not exist in Jira,
                    # and Jira rejects the create with a 400. Children of a
                    # local split parent are created standalone instead. The
                    # test excludes the local prefix rather than requiring
                    # cfg["jira_prefix"], because an Initiative's parent is
                    # legitimately a RHAISTRAT Outcome key.
                    task_fm, _ = read_frontmatter_validated(entry["task_path"], cfg["task_schema"])
                    pk = task_fm.get("parent_key")
                    if pk and not pk.startswith(cfg["local_prefix"]):
                        create_kwargs["parent_key"] = pk
                    elif pk:
                        print(f"  {item_id}: Parent {pk} is not in Jira — creating without parent")
                    new_key = create_issue(
                        server,
                        user,
                        token,
                        cfg["project"],
                        cfg["issue_type"],
                        title,
                        description_adf,
                        entry["priority"],
                        labels=labels,
                        **create_kwargs,
                    )
                    print(f"  {item_id}: Created {new_key}")
                    if labels:
                        print(f"           Labels: {', '.join(labels)}")
                    results[item_id] = new_key
                    submitted_hashes[new_key] = compute_content_hash(description_adf)

            # Post removed-context Jira comment if applicable
            yaml_path = find_removed_context_yaml(args.artifacts_dir, item_id)
            if yaml_path:
                comment_md = render_removed_context_comment(
                    yaml_path, cfg["removed_context_preamble"]
                )
                target_key = results.get(item_id)
                if not comment_md:
                    pass
                elif args.dry_run:
                    print(
                        f"  {item_id}: Would post removed-context comment ({len(comment_md)} chars)"
                    )
                elif target_key:
                    comment_adf = markdown_to_adf(comment_md)
                    add_comment(server, user, token, target_key, comment_adf)
                    print(f"  {item_id}: Posted removed-context comment")

            _post_needs_attention_comment(server, user, token, entry, results, args.dry_run, cfg)

            target_key = results.get(item_id)
            if target_key:
                _maybe_approve(item_id, target_key, entry)

            # Rename local IDs after all Jira ops succeed
            if not entry["is_existing"] and not args.dry_run:
                new_key = results.get(item_id)
                if new_key and not new_key.endswith("DRY"):
                    rename_to_tracker_key(args.artifacts_dir, item_id, new_key, desc)
                    print(f"  {item_id}: Renamed to {new_key}")

            mark_processed_ids.append(item_id)

        except Exception as exc:
            msg = str(exc)
            print(f"  {item_id}: ERROR — {msg}", file=sys.stderr)
            submit_errors.append((item_id, msg))
            review_path = _find_review(args.artifacts_dir, item_id, cfg)
            if review_path:
                try:
                    update_frontmatter(
                        review_path,
                        {
                            "error": f"submit_failed: {msg}",
                        },
                        cfg["review_schema"],
                    )
                except Exception:
                    pass

    print()

    # Update snapshot
    if (submitted_hashes or mark_processed_ids) and not args.dry_run:
        snap_dir = os.path.join(args.artifacts_dir, "auto-fix-runs")
        snap_kwargs = {"mark_processed": mark_processed_ids}
        if cfg["snapshot_prefix"]:
            snap_kwargs["prefix"] = cfg["snapshot_prefix"]
        updated = update_snapshot_hashes(submitted_hashes, snap_dir, **snap_kwargs)
        if updated:
            print(
                f"  Updated snapshot with {len(submitted_hashes)} "
                f"post-submit hashes, {len(mark_processed_ids)} "
                f"mark-processed: {updated}"
            )
        else:
            print("  Warning: no snapshot found to update", file=sys.stderr)

    # Rebuild index (RFE only)
    if cfg["has_index"]:
        rebuild_index(args.artifacts_dir)
        print(f"Done. Index rebuilt at {args.artifacts_dir}/rfes.md")
    else:
        print(f"\nDone. {len(results)} {type_label.lower()}(s) processed.")

    _finish(args, type_label, submit_errors)


if __name__ == "__main__":
    main()
