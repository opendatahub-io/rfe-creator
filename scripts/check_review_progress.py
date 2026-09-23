"""Phase-aware progress checker for agent polling.

Reports completion status for a list of RFE IDs based on the current phase.
Supports ``--wait`` mode which sleeps internally so the caller does not need
to parse ``NEXT_POLL`` values.

Wave freshness (AISDLC-33): with ``--since <epoch>`` (or ``set_wave_launch()`` for
an in-process caller) an assess result or review file last modified before the
reference time is still "pending". pipeline_state.py passes the agent phase's
entry time to next-action's wave pre-filter and to the advance guard, and the
wave's launch time to the wait-for-wave barrier. REASSESS_SAVE deletes the
review and result files before a reassess phase is entered, so a file older
than the reference can only be a late write by a previous cycle's agent;
accepting it skipped the launch (pre-filter) or released the barrier before the
wave's own agent had finished, and that agent's later write then clobbered the
restored review (auto_revised and before_score lost). Dimension files
(feasibility, alignment) are reused across cycles and are never subject to the
rule; the revise slot is not subject to the time rule either: it completes on
the revise baseline (``REVISE_BASELINE_FILE``, AISDLC-45) and only then on
``auto_revised``.

``PHASE_CHECKS`` (poll phase -> expected output path) is a projection of
``types/<t>/type.yaml``: ``pipeline.poll_prefix`` + phase base -> ``dirs`` x the
phase's file convention, with one row per ``pipeline.dimensions[]`` entry
(design work-item-types-unified.md §10 item 2). verify_phase.py derives its
phase tables from the same descriptors.
"""

import argparse
import json
import os
import sys
import time

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import type_registry
from artifact_utils import read_frontmatter

_TYPES = type_registry.load()

# Type-neutral assess staging dir (design §10: kept byte-stable for every type).
ASSESS_STAGING = "tmp/rfe-assess/single"

# Key order is CLI surface: argparse renders the --phase/--also-phase choices from it, so each
# type's rows are emitted in the order the literal table had. The rfe block is the generic
# order of _phase_rows(); the initiative block was appended split-first and gained alignment
# last when that dimension landed. Neither order is a descriptor fact, hence this table; a
# type without an entry (a drop-in) gets the generic order.
_LEGACY_ROW_ORDER = {
    "initiative": (
        "split",
        "fetch",
        "create",
        "assess",
        "feasibility",
        "review",
        "revise",
        "alignment",
    ),
}

# The descriptor fields a type must declare to be polled. Both shipped types declare them
# (validate_types.py gate 1 requires dirs and pipeline.poll_prefix); a drop-in root
# (RFE_CREATOR_EXTRA_TYPES, dev/test only) may register a partial descriptor, and the registry
# loader does not run the JSON-Schema gate — such a type contributes no rows, exactly as it
# contributes no artifact_utils.SCHEMAS entry, instead of breaking the import of the poll
# script every pipeline barrier runs.
_PHASE_FACTS = ("dirs.tasks", "dirs.reviews", "pipeline.poll_prefix")

# Phase bases the engine owns; a pipeline.dimensions entry may not reuse them.
ENGINE_PHASES = frozenset({"fetch", "create", "assess", "review", "revise", "split"})

# Phase bases whose output a wave (re)writes from scratch: a file older than the reference
# time is a stale write from an earlier cycle, never this wave's result. "revise" is not
# here: its slot keys on auto_revised in a review the revise agent only edits, and the
# review REASSESS_RESTORE writes just before REASSESS_REVISE is entered sits inside the clock
# slack, so a time rule would be nondeterministic there (the revise baseline below gates that
# slot instead).
FRESHNESS_BASES = frozenset({"assess", "review"})
# Clock slack between the launch timestamp (time.time() in pipeline_state) and file mtimes.
FRESHNESS_SLACK_SECS = 2
# Epoch of the current wave's launch; None disables the freshness rule (legacy callers).
WAVE_LAUNCHED_AT = None

# Revise baseline (AISDLC-45): {id: {"review_mtime_ns": int}} taken by pipeline_state.advance()
# when it writes tmp/pipeline-revise-ids.txt. The revise slot used to complete on auto_revised
# alone, but REASSESS_RESTORE re-raises that flag from the previous cycle before the revise
# wave is even planned, so the second revision was never launched. The slot now stays pending
# until the review has been written since the baseline — the revise agent's frontmatter step,
# its last action — and only then applies the auto_revised rule. The review write is the one
# signal: a task edit alone would release the slot mid-revision (the flag is already true in
# REASSESS_REVISE), and a byte digest would miss an agent that can change nothing and re-sets
# the frontmatter to identical bytes, sending it into the stall guard instead.
REVISE_BASELINE_FILE = "tmp/pipeline-revise-baseline.json"


def read_revise_baseline():
    """The revise baseline mapping, or {} when there is none or it is unreadable."""
    try:
        with open(REVISE_BASELINE_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _mtime_ns(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def revise_baseline_entry(type_name, item_id):
    """The baseline an id would be recorded with now: its review file's last write time."""
    dirs = _TYPES.get(type_name).dirs()
    return {"review_mtime_ns": _mtime_ns(f"{dirs['reviews']}/{item_id}-review.md")}


def set_wave_launch(since):
    """Set the wave launch epoch check_id() compares file mtimes against (None disables)."""
    global WAVE_LAUNCHED_AT
    WAVE_LAUNCHED_AT = float(since) if since is not None else None


def _polls(desc):
    """True when ``desc`` carries every field its phase rows are derived from."""
    return all(desc.get(dotted, None) is not None for dotted in _PHASE_FACTS)


def _phase_rows(desc):
    """phase base -> (id -> expected output path) for one type: ``dirs`` x the pipeline phases,
    plus one ``<reviews>/<id>-<name>.md`` row per ``pipeline.dimensions`` entry."""
    dirs = desc.dirs()
    rows = {"fetch": lambda id: f"{dirs['tasks']}/{id}.md"}
    # Same path as "fetch", but a stricter check — see check_id(). Create agents write the
    # task file first and set frontmatter in a later tool call, so existence alone would
    # release the barrier mid-write. Every type polls it since PR-5b: the generic speedrun
    # body inherits PR #148's Phase-1 barrier (`--phase <poll_prefix>create`).
    rows["create"] = lambda id: f"{dirs['tasks']}/{id}.md"
    rows["assess"] = lambda id: f"{ASSESS_STAGING}/{id}.result.md"
    seen = set()
    for dimension in desc.get("pipeline.dimensions", []):
        name = dimension["name"]
        # A dimension named like an engine phase would silently replace that phase's
        # row and make the barrier watch the wrong file; refuse at table build.
        if name in ENGINE_PHASES or name in seen:
            raise ValueError(
                f"{desc.name}: pipeline.dimensions name {name!r} collides with an engine phase "
                f"or another dimension (reserved: {', '.join(sorted(ENGINE_PHASES))})"
            )
        seen.add(name)
        rows[name] = lambda id, name=name: f"{dirs['reviews']}/{id}-{name}.md"
    rows["review"] = lambda id: f"{dirs['reviews']}/{id}-review.md"
    rows["revise"] = lambda id: f"{dirs['reviews']}/{id}-review.md"
    rows["split"] = lambda id: f"{dirs['reviews']}/{id}-split-status.yaml"
    legacy = _LEGACY_ROW_ORDER.get(desc.name, ())
    order = [base for base in legacy if base in rows] + [b for b in rows if b not in legacy]
    return {base: rows[base] for base in order}


def _build_phase_table():
    """``PHASE_CHECKS`` plus its inverse, poll phase -> (type name, phase base), which
    check_id() uses to pick the check mode and the owning type's id field."""
    checks = {}
    owners = {}
    for name in _TYPES.names():
        desc = _TYPES.get(name)
        if not _polls(desc):
            continue
        poll_prefix = desc.get("pipeline.poll_prefix")
        for base, path_fn in _phase_rows(desc).items():
            checks[f"{poll_prefix}{base}"] = path_fn
            owners[f"{poll_prefix}{base}"] = (name, base)
    return checks, owners


PHASE_CHECKS, _PHASE_OWNER = _build_phase_table()


def check_id(phase, rfe_id, since=None):
    """Check one ID. Returns 'completed', 'pending', or 'error'.

    ``since`` (epoch seconds; defaults to WAVE_LAUNCHED_AT) makes an assess or review file
    older than the wave launch "pending"; the revise slot is gated by the revise baseline
    instead: see the module docstring.
    """
    path = PHASE_CHECKS[phase](rfe_id)
    if not os.path.exists(path):
        return "pending"
    # The check mode follows the phase base for every type: create -> frontmatter_valid,
    # review -> score_present, revise -> revised_or_split, anything else -> exists. A phase
    # patched into PHASE_CHECKS without an owner (tests) keeps the exists mode.
    type_name, base = _PHASE_OWNER.get(phase, (None, None))
    if since is None:
        since = WAVE_LAUNCHED_AT
    if since is not None and base in FRESHNESS_BASES:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return "pending"
        if mtime < float(since) - FRESHNESS_SLACK_SECS:
            return "pending"  # written before this wave launched: a previous cycle's file
    if base == "create":
        # Every not-yet-good state is "pending", never "error". The --wait loop
        # exits on pending == 0 and never consults the error count, so an
        # "error" here would release the barrier on the very file it rejected.
        # Timing out into the relaunch path in rfe.speedrun is the recovery.
        try:
            data, _ = read_frontmatter(path)
        except Exception:
            return "pending"
        # read_frontmatter returns {} for a file with no frontmatter block yet,
        # which is a half-written file, not a finished one. The id has to be
        # the one asked about: a task file carrying a different id (the owning
        # type's identity.id_field) is not this ID's create output, and
        # accepting it would break the one-item-per-preallocated-ID contract
        # the barrier exists to enforce.
        if not data or data.get(_TYPES.get(type_name).id_field) != rfe_id:
            return "pending"
        return "completed"
    if base == "review":
        # A half-written review is "pending", exactly as the create phase treats its
        # half-written task file: the review agent writes the body first and sets the
        # frontmatter in a later tool call, so for a moment the file exists with no (or an
        # unparseable, or a score-less) frontmatter block. Classifying that moment "error"
        # released the barrier on a file the agent was still writing: post_verify stubbed
        # the unfinished review as review_failed and the item took a spurious ERROR_COLLECT
        # retry batch while the real review landed underneath (seen on RHAIRFE-3333 in three
        # stage dry runs and RHAIRFE-3080 in production, 2026-09-15). 7f3cc47 (CI #122/#128)
        # had chosen "error" so a review agent that never writes frontmatter could not hang
        # the barrier forever; the wave stall guard (PIPELINE_WAVE_STALL_SECS, docs/
        # wave-stall-guard.md) now bounds that case, so only an explicit error field is an
        # error here.
        try:
            data, _ = read_frontmatter(path)
        except Exception:
            return "pending"
        if not data or data.get("score") is None:
            return "pending"
        if data.get("error"):
            return "error"
    if base == "revise":
        # AISDLC-45: the revise agent's last action is a frontmatter write on this review;
        # until the review has been written since the baseline taken with the revise ids,
        # nothing it did has landed — the auto_revised flag alone cannot say so
        # (REASSESS_RESTORE re-raises it). A baseline of another shape is ignored.
        baseline = read_revise_baseline().get(rfe_id)
        if isinstance(baseline, dict) and "review_mtime_ns" in baseline:
            if _mtime_ns(path) == baseline["review_mtime_ns"]:
                return "pending"
        # Same rule: the revise agent rewrites an existing review; a moment without a
        # readable frontmatter block is not-yet-good, not failed (bounded by the stall guard).
        try:
            data, _ = read_frontmatter(path)
        except Exception:
            return "pending"
        if not data:
            return "pending"
        if data.get("auto_revised"):
            return "completed"
        if data.get("recommendation") == "split":
            return "completed"  # revise agent can't fix right-sizing
        return "pending"
    return "completed"


def _check_phase(phase, ids, fast):
    """Check one phase and return (completed, errors, pending, total, next_poll)."""
    completed = 0
    errors = 0
    pending_ids = []

    for rfe_id in ids:
        result = check_id(phase, rfe_id)
        if result == "completed":
            completed += 1
        elif result == "error":
            errors += 1
        else:
            pending_ids.append(rfe_id)

    total = len(ids)
    pending = len(pending_ids)

    if pending == 0:
        next_poll = 0
    elif fast:
        next_poll = 15
    elif completed / total >= 0.75:
        next_poll = 15
    elif completed / total >= 0.5:
        next_poll = 30
    else:
        next_poll = 60

    return completed, errors, pending, total, next_poll


def _format_status(phase, completed, errors, pending, total, next_poll):
    """Format a status line for one phase."""
    parts = [f"COMPLETED={completed}/{total}"]
    if pending:
        parts.append(f"PENDING={pending}")
    if errors:
        parts.append(f"ERRORS={errors}")
    parts.append(f"NEXT_POLL={next_poll}")
    return f"{phase}: {', '.join(parts)}"


def _detect_fast(explicit_flag):
    """Return True if fast-poll should be used."""
    if explicit_flag:
        return True
    # The interactive skills write tmp/<pipeline.state_prefix><stage>-config.yaml. The list
    # is the descriptor projection (state_prefix x the interactive stages review/split/
    # speedrun) written out: PR-5b added tmp/initiative-speedrun-config.yaml — the generic
    # speedrun body gives an interactive initiative speedrun the same Phase-1 barrier as the
    # rfe one, which must poll at the interactive cadence. The auto-fix skills drive
    # tmp/pipeline-state.yaml through pipeline_state.py and never wrote a *-autofix-config.yaml
    # (PR-5a removed the dead entries).
    for cfg in (
        "tmp/review-config.yaml",
        "tmp/split-config.yaml",
        "tmp/speedrun-config.yaml",
        "tmp/initiative-review-config.yaml",
        "tmp/initiative-split-config.yaml",
        "tmp/initiative-speedrun-config.yaml",
    ):
        if os.path.exists(cfg):
            try:
                with open(cfg) as f:
                    data = yaml.safe_load(f)
                if data and data.get("headless") is False:
                    return True
            except Exception:
                pass
    return False


def main():
    parser = argparse.ArgumentParser(description="Check review pipeline progress by phase")
    parser.add_argument(
        "--phase", required=True, choices=list(PHASE_CHECKS.keys()), help="Pipeline phase to check"
    )
    parser.add_argument(
        "--also-phase",
        action="append",
        dest="also_phases",
        choices=list(PHASE_CHECKS.keys()),
        help="Additional phases to check (wait mode)",
    )
    parser.add_argument("--id-file", help="File containing IDs (one per line or space-separated)")
    parser.add_argument(
        "--fast-poll",
        action="store_true",
        help="Cap poll interval at 15s (interactive mode). "
        "Auto-enabled when config files show headless=false.",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Block until all agents complete, sleeping "
        "internally between checks. Exit 0 when done.",
    )
    parser.add_argument(
        "--max-wait",
        type=int,
        default=90,
        help="Max seconds to wait in --wait mode before timing out (exit 3). "
        "Default 90 (fits within 2-min bash timeout).",
    )
    parser.add_argument(
        "--since",
        type=float,
        help="Wave launch epoch: assess/review files older than it stay pending",
    )
    parser.add_argument("ids", nargs="*", metavar="ID", help="RFE IDs to check")
    args = parser.parse_args()
    set_wave_launch(args.since)

    ids = args.ids
    if args.id_file:
        with open(args.id_file) as f:
            ids = f.read().split()
    if not ids:
        print("No IDs provided", file=sys.stderr)
        sys.exit(2)

    fast = _detect_fast(args.fast_poll)
    phases = [args.phase] + (args.also_phases or [])

    if args.max_wait < 0:
        parser.error("--max-wait must be non-negative")

    if args.wait:
        # --wait mode: block until all phases show PENDING=0.
        # Keeps the LLM inside a bash execution so it can't exit early.
        start = time.monotonic()
        while True:
            all_complete = True
            max_poll = 0
            for phase in phases:
                completed, errors, pending, total, next_poll = _check_phase(phase, ids, fast)
                print(
                    _format_status(phase, completed, errors, pending, total, next_poll), flush=True
                )
                if pending > 0:
                    all_complete = False
                max_poll = max(max_poll, next_poll)

            if all_complete:
                if len(phases) > 1:
                    print("All phases complete.")
                break

            elapsed = time.monotonic() - start
            if args.max_wait > 0 and (elapsed + max_poll) > args.max_wait:
                # Collect pending IDs for the timeout message
                pending_ids = set()
                for phase in phases:
                    for rfe_id in ids:
                        if check_id(phase, rfe_id) == "pending":
                            pending_ids.add(rfe_id)
                id_list = sorted(pending_ids)
                if len(id_list) > 5:
                    id_summary = " ".join(id_list[:5]) + f" ... and {len(id_list) - 5} more"
                else:
                    id_summary = " ".join(id_list)
                print(
                    f"Waited {int(elapsed)}s, still pending: {id_summary}. Re-run this command.",
                    flush=True,
                )
                sys.exit(3)

            print(f"Sleeping {max_poll}s...", flush=True)
            time.sleep(max_poll)
    else:
        # Legacy single-phase mode
        completed, errors, pending, total, next_poll = _check_phase(args.phase, ids, fast)
        parts = [f"COMPLETED={completed}/{total}"]
        if pending:
            parts.append(f"PENDING={pending}")
        if errors:
            parts.append(f"ERRORS={errors}")
        parts.append(f"NEXT_POLL={next_poll}")
        print(", ".join(parts))


if __name__ == "__main__":
    main()
