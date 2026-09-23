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

The type is decided by the design §5 ladder (type_registry.resolve: --type, else the
grandfathered rfe default) and the Jira project, issue type and write prefix come from that
type's EFFECTIVE binding (design §3.2.1: the descriptor overlaid by
RFE_CREATOR_BINDING_<TYPE>_{PROJECT,ISSUE_TYPE,LOCAL_PREFIX}; the bare JIRA_PROJECT /
JIRA_ISSUE_TYPE shorthand is refused — the artifact layer does not read it), proven to be the
type's own before any file or Jira access (type_registry.assert_registered_binding,
assert_not_shorthand). With no override every effective value is the descriptor value. Exit 1
before any write when the type cannot be resolved, the binding is not the resolved type's own
or came from the shorthand, or a task's frontmatter tracker_ref is a key the resolved type does
not own; a fetched issue whose (project, issue type) is neither the binding nor — for a key
carrying a descriptor read prefix, an item created before the override — the descriptor pair
is skipped, never written. The remote key of a task is its tracker_ref, else its id.
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
    read_frontmatter,
    read_frontmatter_validated,
    rebuild_index,
    rename_to_tracker_key,
    render_removed_context_comment,
    scan_tasks,
    update_frontmatter,
)
from generate_run_report import SPLIT_NOT_ATTEMPTED_PREFIX, _parse_run_id  # noqa: E402
from generate_run_report import TYPE_CONFIG as REPORT_TYPE_CONFIG  # noqa: E402
from jira_utils import (  # noqa: E402
    add_comment,
    add_labels,
    check_description_conflict,
    create_issue,
    get_issue,
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
# binding: an environment override reaches the Jira write path only through main(), after
# resolve() printed it and assert_registered_binding() proved it is the resolved type's own
# (_effective_config overlays project, issue_type and jira_prefix on the resolved type's entry).
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


# ─── Effective binding (design §3.2.1, PR-3c) ─────────────────────────────────

# The two witnesses the pre-update verification reads off a fetched issue: the same pair
# fetch_issue.py verifies after a fetch (D9: the project comes from the issue, never from
# the key stem). Request-only: nothing written derives from them.
BINDING_WITNESS_FIELDS = ("project", "issuetype")


def _effective_config(type_name, binding):
    """The resolved type's TYPE_CONFIGS entry with its tracker facts taken from ``binding`` —
    the effective binding ``type_registry.resolve`` returned for it: ``project``,
    ``issue_type`` and ``jira_prefix`` (the write prefix, ``key_prefixes[0]``: ``<PROJECT>-``
    under a project override, the descriptor prefix otherwise). Every other key is the
    descriptor projection unchanged, and so is the module-level table: with no override every
    effective value equals the descriptor value, so every plan line, label and Jira payload
    composed from the returned entry is byte-identical to one composed from TYPE_CONFIGS."""
    cfg = dict(TYPE_CONFIGS[type_name])
    cfg["project"] = binding.get("project")
    cfg["issue_type"] = binding.get("issue_type")
    prefixes = [p for p in binding.get("key_prefixes") or [] if p]
    if prefixes:
        cfg["jira_prefix"] = prefixes[0]
    return cfg


def _dry_run_key(jira_prefix):
    """The key a dry run assigns a would-be-created item: binding-derived, ``<PROJECT>-DRY``
    in the write prefix's form (design §3.2.1 d) — ``KONFLUX-DRY`` under a project override.
    Never rendered; it only keeps the rename step off (``endswith("DRY")``)."""
    return f"{jira_prefix}DRY"


class TrackerRefError(ValueError):
    """A task's frontmatter ``tracker_ref`` is a key the resolved type's binding does not own."""


def _tracker_ref(data):
    """The task's frontmatter ``tracker_ref`` as a non-empty string, else None. Absent, null
    and blank all read as "no reference" — the pre-migration shape (PR-3c-i, D7: fields are
    appended to new artifacts only, never back-filled)."""
    ref = data.get("tracker_ref") if isinstance(data, dict) else None
    if isinstance(ref, str) and ref.strip():
        return ref.strip()
    return None


def _owned_key(key, binding):
    """True when ``key`` starts with one of the effective binding's ``key_prefixes``: the write
    prefix (``<PROJECT>-`` under a project override) or a descriptor prefix kept as a read
    prefix — "a tracker key owned by the type" (design §3.2.1). Deliberately the key-prefix
    rung alone: ``Descriptor.owns_effective`` answers the wider "is this id the type's" (a local
    draft id is), which is not the question ``is_existing`` asks."""
    if not isinstance(key, str) or not key:
        return False
    return any(key.startswith(p) for p in binding.get("key_prefixes") or [] if p)


def _is_existing(data, id_field, type_name, binding, registry=None, env=None):
    """The design §5 ``is_existing`` rule for one task: does it name an issue already in Jira?

    ``tracker_ref`` is read from the frontmatter, never re-derived from the id (PR-3c-i): when
    present it decides — a reference the resolved type's effective binding owns
    (``_owned_key``) is an existing issue; one it does NOT own is a ``TrackerRefError`` naming
    the id, the reference, the resolved type and the type that owns it (a ``type: rfe``
    artifact carrying ``tracker_ref: RHOAIENG-123`` is never an update and never a create:
    main() raises this before the first Jira write). Absent (a pre-migration artifact), the
    fallback is membership of the id in the effective ``key_prefixes`` union — with no
    override exactly the single-prefix ``startswith`` this replaces.
    """
    ref = _tracker_ref(data)
    if ref is None:
        return _owned_key(data.get(id_field), binding)
    if _owned_key(ref, binding):
        return True
    registry = _TYPES if registry is None else registry
    found = registry.candidates(ref, os.environ if env is None else env)
    owners = [] if found.provisional else [n for n in found.names if n != type_name]
    owned_by = f"type {'/'.join(owners)} owns" if owners else "no registered type owns"
    prefixes = ", ".join(p for p in binding.get("key_prefixes") or [] if p) or "(none)"
    raise TrackerRefError(
        f"{data.get(id_field)} carries tracker_ref {ref!r}, a key {owned_by}, not the resolved "
        f"type {type_name} (key prefixes: {prefixes}); an artifact bound to another type is "
        f"never updated or created here — fix the artifact before re-running; nothing submitted"
    )


def _binding_skip_reason(item_id, fields, type_name, binding, jira_key=None):
    """Pre-update verification (design §5, PR-3c): None when the fetched issue's
    ``(project.key, issuetype.name)`` is one of the pairs the resolved type accepts for
    ``jira_key`` — ``Descriptor.accepted_pairs``: the effective ``(project, issue_type)``, plus
    the descriptor pair when the key carries a descriptor read prefix (an item created before
    the override, still this type's to update) — else the plan's skip reason. ``jira_key`` is
    the task's remote key (its tracker_ref, else ``item_id``). The same check fetch_issue.py
    applies after a fetch, rendered as a skip: a response without a witness cannot be verified
    and is skipped too (fail closed), naming the missing field."""
    if jira_key is None:
        jira_key = item_id
    project = fields.get("project") if isinstance(fields, dict) else None
    issuetype = fields.get("issuetype") if isinstance(fields, dict) else None
    project_key = project.get("key") if isinstance(project, dict) else None
    issue_type = issuetype.get("name") if isinstance(issuetype, dict) else None
    accepted = _TYPES.get(type_name).accepted_pairs(binding, jira_key)
    expected = type_registry.render_pairs(accepted)
    missing = [
        name for name, value in (("project", project_key), ("issuetype", issue_type)) if not value
    ]
    if missing:
        return (
            f"binding unverifiable — the fetched issue has no {' or '.join(missing)} field; "
            f"type {type_name} binds {expected}"
        )
    if (project_key, issue_type) in accepted:
        return None
    return (
        f"binding mismatch — {jira_key} is ({project_key}, {issue_type}) in Jira but type "
        f"{type_name} binds {expected}"
    )


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


def _review_error(artifacts_dir, item_id, cfg):
    """The ``error`` of an item's review as a string, or None (no review, an unreadable one,
    or no error). Read with the lax reader on purpose: the error is what matters here and a
    review the schema rejects may still carry one."""
    review_path = _find_review(artifacts_dir, item_id, cfg)
    if not review_path:
        return None
    try:
        data, _ = read_frontmatter(review_path)
    except Exception:
        return None
    error = data.get("error") if isinstance(data, dict) else None
    return str(error) if error else None


# The reason the pipeline's wave stall guard puts after ``split_not_attempted:`` (see
# pipeline_state._stall_reason). Only THAT marker means "the split agent never finished and its
# children were never reviewed"; this script's own ``split_not_attempted: Jira preflight
# failed`` / ``split phase aborted`` markers mean "not attempted yet" and a re-run over the same
# artifacts (the manual submit jobs) must still attempt those.
STALL_NOT_ATTEMPTED_PREFIX = f"{SPLIT_NOT_ATTEMPTED_PREFIX} wave stalled"


def _is_stall_escalation(error):
    """True for the review errors the pipeline's wave stall guard leaves on an item it gave
    up on (docs/wave-stall-guard.md): ``split_not_attempted: wave stalled ...`` and the
    ``<agent>_stalled`` stubs. This script's own not-attempted markers are NOT matched."""
    return bool(error) and (
        error.startswith(STALL_NOT_ATTEMPTED_PREFIX) or error.endswith("_stalled")
    )


def _feasibility_verdict(review_data):
    """The review's feasibility verdict, or None when the review carries an ``error``.

    An error-bearing review has no verdict: the registry error stub (``*_failed`` /
    ``*_stalled``) inherits ``feasibility: feasible`` from the stub shape although no
    feasibility review ran, and a stall or split marker on a real review no longer
    describes the item's state. With None, feasibility_label_changes adds no label and
    removes none, so whatever label the item carries in Jira is left alone.
    """
    if not review_data or review_data.get("error"):
        return None
    return review_data.get("feasibility")


def _generate_reports(args, type_name):
    """Regenerate the run report YAML and its HTML companion for the resolved ``type_name``."""
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
    if type_name != "rfe":
        yaml_cmd.extend(["--type", type_name])
    result = subprocess.run(yaml_cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"  YAML report: {result.stdout.strip()}")
    else:
        print(f"Warning: YAML report generation failed: {result.stderr}", file=sys.stderr)

    html_output = report_companion_path(args.artifacts_dir, run_id, type_name, "-report.html")
    html_cmd = [
        sys.executable,
        os.path.join(script_dir, "generate_review_pdf.py"),
        "--revised-only",
        "--artifacts-dir",
        args.artifacts_dir,
        "--output",
        html_output,
    ]
    if type_name != "rfe":
        html_cmd.extend(["--type", type_name])
    result = subprocess.run(html_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Warning: HTML report generation failed: {result.stderr}", file=sys.stderr)


def _reconcile_saved_review_state(artifacts_dir, type_name):
    """Run ``reconcile_reviews.py --all`` over the artifacts (see main). Local-only and
    best-effort: a failure is reported, never fatal, so a repair problem cannot block the
    submit of everything else."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cmd = [
        sys.executable,
        os.path.join(script_dir, "reconcile_reviews.py"),
        "--type",
        type_name,
        "--artifacts-dir",
        artifacts_dir,
        "--all",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # Exit status only: the child's stderr can quote a frontmatter source line.
        print(
            "Warning: review state reconcile failed"
            f" (reconcile_reviews.py exit {result.returncode}); saved state not re-applied",
            file=sys.stderr,
        )
        return
    for line in result.stdout.splitlines():
        if line.startswith("RESTORED=") and line[len("RESTORED=") :]:
            ids = line[len("RESTORED=") :].split(",")
            print(f"Re-applied saved review state for {len(ids)} item(s): {', '.join(ids)}")
        elif line.startswith("RECONCILE_ERROR "):
            print(f"Warning: {line}", file=sys.stderr)


def _lower_unrevised_flags(artifacts_dir, type_name):
    """Run ``check_revised.py --batch --lower-only`` over the artifacts (see main): a set
    ``auto_revised`` on a task whose body still equals its original is lowered before the
    first review is read. Lower-only — raising stays FIXUP's job over the revise ids. Runs
    after the state reconcile (a restore may re-raise a flag from state first) and is
    best-effort like it: a failure is reported, never fatal."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cmd = [
        sys.executable,
        os.path.join(script_dir, "check_revised.py"),
        "--type",
        type_name,
        "--batch",
        "--lower-only",
        "--artifacts-dir",
        artifacts_dir,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # Exit status only: the child's stderr can quote a frontmatter source line.
        print(
            "Warning: auto_revised content guard failed"
            f" (check_revised.py exit {result.returncode}); flags left as written",
            file=sys.stderr,
        )
        return
    for line in result.stdout.splitlines():
        if line.startswith("LOWERED=") and line[len("LOWERED=") :]:
            ids = line[len("LOWERED=") :].split(",")
            print(
                f"Lowered auto_revised on {len(ids)} item(s) whose text equals the original:"
                f" {', '.join(ids)}"
            )
        elif line.startswith("STALE_COMPANIONS=") and line[len("STALE_COMPANIONS=") :]:
            ids = line[len("STALE_COMPANIONS=") :].split(",")
            print(
                f"Removed the stale removed-context companion of {len(ids)} unrevised item(s)"
                f" (nothing to post): {', '.join(ids)}"
            )
        elif line.startswith("SKIPPED=") and line[len("SKIPPED=") :]:
            # Ids only: the child's per-id stderr line carries the exception class, and its
            # message could have quoted frontmatter.
            ids = line[len("SKIPPED=") :].split(",")
            print(
                f"Warning: auto_revised content guard skipped {len(ids)} item(s) it could not"
                f" read or update: {', '.join(ids)}",
                file=sys.stderr,
            )


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


def _finish(args, type_name, type_label, submit_errors):
    """Summarise failures, regenerate the reports, then exit. Never returns.

    Every terminating path routes through here. A path that skipped it took the run report with
    it, so successes already committed to Jira ended up recorded nowhere at all.
    """
    if submit_errors:
        print(f"\n{len(submit_errors)} {type_label}(s) failed during submit:", file=sys.stderr)
        for eid, emsg in submit_errors:
            print(f"  {eid}: {emsg}", file=sys.stderr)

    if args.generate_report:
        _generate_reports(args, type_name)

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
    # default=None: an absent flag reaches type_registry.resolve as "no signal" and lands on the
    # grandfathered legacy default rung (rfe) silently; an explicit --type is rung 1 and prints
    # the D3 line. The rendered default is unchanged.
    parser.add_argument(
        "--type",
        choices=_TYPES.choices(),
        default=None,
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

    # Design §5 ladder: --type (rung 1) else the grandfathered legacy default (rfe). The ids
    # are this type's own task files, scanned below, so there is no id signal and the result
    # is never ambiguous. The binding on the resolution is the EFFECTIVE one (§3.2.1): what
    # the plan, the creates and the pre-update verification use.
    try:
        resolution = type_registry.resolve(_TYPES, explicit_type=args.type, env=os.environ)
        # D3: the resolve line only when a non-default rung decided, and never on stdout —
        # the production autofixer passes no --type and stays silent.
        if resolution.rung != type_registry.LEGACY_DEFAULT_RUNG:
            print(resolution.line(), file=sys.stderr)
        # §3.2.1 g (runtime twin of gate-1 rule 1): the effective binding must be the
        # resolved type's OWN. An override that binds it to another registered type's pair
        # is refused here, before any file or Jira access. No override: silent.
        type_registry.assert_registered_binding(
            resolution.desc, env=os.environ, registry=_TYPES, shorthand=True
        )
        # The bare JIRA_PROJECT / JIRA_ISSUE_TYPE shorthand is a resolve-CLI verdict only: the
        # artifact layer (artifact_utils' id grammar, rename guard and lookups) reads binding()
        # without it, so a shorthand-sourced project would create the issue and then fail the
        # rename. Refused here, before any file or Jira access.
        type_registry.assert_not_shorthand(resolution.type_name, resolution.binding)
    except type_registry.RegistryError as exc:
        print(f"Error: {exc.args[0] if exc.args else exc}", file=sys.stderr)
        sys.exit(1)

    type_name = resolution.type_name
    desc = resolution.desc
    binding = resolution.binding
    cfg = _effective_config(type_name, binding)
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
            f"--auto-approve: type '{type_name}' declares no identity.jira.state_map.approved"
        )

    server, user, token = require_env()

    if not args.dry_run and not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars required.", file=sys.stderr)
        print("Set these or use --dry-run for local-only validation.", file=sys.stderr)
        sys.exit(1)

    # Re-apply any saved review state before the first review is read (AISDLC-33): a
    # review agent can rewrite its review minutes after its wave, past COLLECT and REPORT;
    # this is the last reader, and in CI it runs after the agent process is torn down.
    _reconcile_saved_review_state(args.artifacts_dir, type_name)
    # Then the content guard: a revise agent's last write can land after FIXUP lowered a
    # flag it had set on an unchanged task (2026-09-21 stage dry run), and this flag is
    # what the auto-revised label is derived from. Lower-only; never raises.
    _lower_unrevised_flags(args.artifacts_dir, type_name)

    # Scan task files
    tasks = scan_tasks(args.artifacts_dir, desc)
    if not tasks:
        print(f"Error: No {type_label} task files found.", file=sys.stderr)
        sys.exit(1)

    # is_existing (design §5), one rule for every site below: the task's frontmatter
    # tracker_ref when present, else key-prefix-union membership on the effective binding.
    tasks_by_id = {data[id_field]: data for _, data in tasks}

    def _existing(data):
        return _is_existing(data, id_field, type_name, binding)

    def _existing_key(key):
        """The rule for a key that may have no task file of its own (the ancestor walk)."""
        data = tasks_by_id.get(key)
        return _existing(data) if data is not None else _owned_key(key, binding)

    # A tracker_ref the resolved type does not own is a hard error for the whole run, raised
    # here — before Phase 1, so before the first Jira write — rather than wherever the rule
    # is first consulted for that task.
    for _, data in tasks:
        try:
            _existing(data)
        except TrackerRefError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

    # --- Phase 1: Submit splits via split_submit.py ---
    child_parent_keys = {data.get("parent_key") for _, data in tasks if data.get("parent_key")}
    split_parent_data = {
        data[id_field]: data
        for _, data in tasks
        if data.get("status") == "Archived"
        and _existing(data)
        and data[id_field] in child_parent_keys
    }
    # A parent whose review the wave stall guard marked (split_not_attempted: / *_stalled)
    # left the pipeline before its children were collected: a split agent that was slow
    # rather than dead can still archive the parent and mint children afterwards, but no
    # SPLIT_ASSESS / SPLIT_REVIEW wave ever saw them, and split_submit.py would push them
    # without review data. Such a parent is not split-submitted and not marked processed
    # (it is in no plan): the children stay local, the parent stays selectable, and the
    # run report keeps counting it failed, not split.
    stalled_split_parents = {}
    for parent_key in sorted(split_parent_data):
        error = _review_error(args.artifacts_dir, parent_key, cfg)
        if _is_stall_escalation(error):
            stalled_split_parents[parent_key] = error
            del split_parent_data[parent_key]
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
            if _existing_key(pk):
                return True
            seen.add(pk)
            pk = _parent_of.get(pk)
        return False

    # Hoisted above the split loop: both loops record into it, and every exit
    # path reports it.
    submit_errors = []

    if stalled_split_parents:
        for parent_key, error in stalled_split_parents.items():
            print(
                f"  {parent_key}: SKIP split-submit - review error {error}; children were not"
                " reviewed, left for an operator"
            )
        print()

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
                _finish(args, type_name, type_label, submit_errors)

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
            # D3, one line per run: this process resolved the type (and printed the line for
            # an explicit --type); the child skips its own line when the marker is set.
            result = subprocess.run(
                cmd, env={**os.environ, type_registry.RESOLVED_BY_PARENT_ENV: "1"}
            )
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
            _finish(args, type_name, type_label, submit_errors)

    # Record split-child hashes in the snapshot
    if split_parents and not args.dry_run:
        try:
            split_child_hashes = {}
            post_split_tasks = scan_tasks(args.artifacts_dir, desc)
            for path, data in post_split_tasks:
                if data.get("parent_key") and data.get("status") == "Submitted":
                    item_id = data.get(id_field, "")
                    if _existing(data):
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
        if split_parents or stalled_split_parents or any_submitted:
            if cfg["has_index"]:
                rebuild_index(args.artifacts_dir)
                print(f"Done. Index rebuilt at {args.artifacts_dir}/rfes.md")
            else:
                print(f"Done. {len(split_parents)} split(s) processed.")
            # A split-only batch reaches here on the happy path too, so returning early meant a
            # run whose every input was a split never produced a report at all.
            _finish(args, type_name, type_label, submit_errors)
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
                _feasibility_verdict(review_data),
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
        is_existing = _existing(task_data)
        # The ONE remote key of the task (design §5): its frontmatter tracker_ref when present
        # — the reference is_existing just accepted — else the id itself. Every Jira call below
        # (the witness / conflict fetch, the update, labels, transitions, comments) names it;
        # the local paths (task, original, review) keep the artifact id.
        jira_key = _tracker_ref(task_data) or item_id
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
                    "jira_key": jira_key,
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

        def _binding_skip(reason, jira_status=None):
            """The plan entry of an existing issue the pre-update verification refused: no
            write, and — like an unreviewed item — not disposed of, so it is not marked
            processed and the next run sees it again."""
            return {
                id_field: item_id,
                "title": title,
                "is_existing": is_existing,
                "priority": priority,
                "size": size,
                "action": "SKIP",
                "labels": [],
                "remove_labels": [],
                "skip_reason": reason,
                "task_path": task_path,
                "jira_key": jira_key,
                "attn_reason": None,
                "original_labels": original_labels,
                "auto_approve": False,
                "jira_status": jira_status,
                "leave_unprocessed": True,
            }

        def _fetch_witnesses():
            """The two binding witnesses of ``item_id``, fetched on their own: the smallest
            fetch for a write path that performs no other (the reject path's label removal,
            an update with no original to compare with)."""
            issue = get_issue(server, user, token, jira_key, fields=list(BINDING_WITNESS_FIELDS))
            return issue.get("fields") or {}

        # Both types gate on the same rule: only an explicitly feasible item
        # auto-approves. An `indeterminate` verdict means the assessment was
        # inconclusive, which is not a basis for transitioning a ticket to
        # Approved on its own. `needs_attention` is advisory here — it drives
        # the needs-attention label, not the transition. A review carrying an
        # `error` has no verdict at all (see _feasibility_verdict), whatever
        # its `pass` and `feasibility` fields say.
        auto_approve = bool(
            review_data
            and not review_data.get("error")
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
            if remove and is_existing and not args.dry_run:
                # Removing labels is a write to an existing issue: verify its (project,
                # issue type) against the resolved binding first (design §5 / PR-3c). A
                # fetch that fails is warned about and the write proceeds, as the conflict
                # check below has always done; a fetched pair that does not match is a skip.
                mismatch = None
                try:
                    mismatch = _binding_skip_reason(
                        item_id, _fetch_witnesses(), type_name, binding, jira_key
                    )
                except Exception as e:
                    print(f"Warning: binding check failed for {item_id}: {e}", file=sys.stderr)
                if mismatch:
                    plan.append(_binding_skip(mismatch))
                    continue
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
                    "jira_key": jira_key,
                    "attn_reason": None,
                    "original_labels": original_labels,
                    "auto_approve": False,
                    "jira_status": None,
                }
            )
            continue

        # For existing items, verify the binding and check for Jira conflicts. One fetch
        # serves both: the conflict check's request also carries the two binding witnesses
        # (project, issuetype), and an item with no original to compare with — the conflict
        # check makes no request then — gets the smallest fetch that carries them.
        jira_status = None
        if is_existing and not args.dry_run:
            original_path = os.path.join(args.artifacts_dir, cfg["originals_dir"], f"{item_id}.md")
            try:
                has_conflict, issue_fields = check_description_conflict(
                    server,
                    user,
                    token,
                    jira_key,
                    original_path,
                    extra_fields=["status", *BINDING_WITNESS_FIELDS],
                )
                if issue_fields:
                    jira_status = issue_fields.get("status", {}).get("name")
                if issue_fields is None:
                    # No original to compare with: the conflict check made no request, so the
                    # two witnesses are fetched on their own (a failure propagates to the
                    # handler below: an issue that cannot be verified is not written).
                    issue_fields = _fetch_witnesses()
                mismatch = _binding_skip_reason(item_id, issue_fields, type_name, binding, jira_key)
                if mismatch:
                    plan.append(_binding_skip(mismatch, jira_status))
                    continue
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
                            "jira_key": jira_key,
                            "attn_reason": None,
                            "original_labels": original_labels,
                            "auto_approve": False,
                            "jira_status": jira_status,
                        }
                    )
                    continue
            except Exception as e:
                # Fail closed, like split_submit's parent check: an existing issue whose binding
                # could not be verified (a dead instance, a 404, an unreadable original) is not
                # written; it is skipped, left unprocessed, and the next run retries it.
                plan.append(
                    _binding_skip(
                        f"could not verify {jira_key} against the {type_name} binding — "
                        f"{type(e).__name__}: {e}",
                        jira_status,
                    )
                )
                continue

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
                            _feasibility_verdict(review_data),
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
                            "jira_key": jira_key,
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
                _feasibility_verdict(review_data),
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
                "jira_key": jira_key,
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
        jira_key = entry.get("jira_key") or item_id
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
                    remove_labels(server, user, token, jira_key, remove)
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
                        swap_labels(server, user, token, jira_key, labels, remove)
                        if remove:
                            print(f"  {item_id}: Removed labels: {', '.join(remove)}")
                        if labels:
                            print(f"  {item_id}: Labels: {', '.join(labels)}")
                    update_frontmatter(
                        entry["task_path"], {"status": "Submitted"}, cfg["task_schema"]
                    )
                results[item_id] = jira_key
                _post_needs_attention_comment(
                    server, user, token, entry, results, args.dry_run, cfg
                )
                _maybe_approve(item_id, jira_key, entry)
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
                    update_issue(server, user, token, jira_key, title, description_adf)
                    print(f"  {item_id}: Updated")
                    if remove or labels:
                        swap_labels(server, user, token, jira_key, labels, remove)
                        if remove:
                            print(f"           Removed: {', '.join(remove)}")
                        if labels:
                            print(f"           Labels: {', '.join(labels)}")
                    submitted_hashes[item_id] = compute_content_hash(description_adf)
                    update_frontmatter(
                        entry["task_path"], {"status": "Submitted"}, cfg["task_schema"]
                    )
                results[item_id] = jira_key
            else:
                if args.dry_run:
                    print(
                        f"  {item_id}: Would create {cfg['project']} {cfg['issue_type']}: {title}"
                    )
                    results[item_id] = _dry_run_key(jira_prefix)
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

    _finish(args, type_name, type_label, submit_errors)


if __name__ == "__main__":
    main()
