"""Phase-aware progress checker for agent polling.

Reports completion status for a list of RFE IDs based on the current phase.
Supports ``--wait`` mode which sleeps internally so the caller does not need
to parse ``NEXT_POLL`` values.

``PHASE_CHECKS`` (poll phase -> expected output path) is a projection of
``types/<t>/type.yaml``: ``pipeline.poll_prefix`` + phase base -> ``dirs`` x the
phase's file convention, with one row per ``pipeline.dimensions[]`` entry
(design work-item-types-unified.md §10 item 2). verify_phase.py derives its
phase tables from the same descriptors.
"""

import argparse
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

# PR #148 gave rfe.speedrun a Phase-1 "create" barrier; no other pipeline polls one, so the row
# stays rfe-only (an `initiative-create` row would change the --phase choices text). PR-5's
# generic speedrun body gives every type a create barrier keyed on dirs.tasks + id_field, and
# this grandfather goes with it.
_CREATE_BARRIER_TYPES = ("rfe",)

# Key order is CLI surface: argparse renders the --phase/--also-phase choices from it, so each
# type's rows are emitted in the order the literal table had. The rfe block is the generic
# order of _phase_rows(); the initiative block was appended split-first and gained alignment
# last when that dimension landed. Neither order is a descriptor fact, hence this table; a
# type without an entry (a drop-in) gets the generic order.
_LEGACY_ROW_ORDER = {
    "initiative": ("split", "fetch", "assess", "feasibility", "review", "revise", "alignment"),
}

# The descriptor fields a type must declare to be polled. Both shipped types declare them
# (validate_types.py gate 1 requires dirs and pipeline.poll_prefix); a drop-in root
# (RFE_CREATOR_EXTRA_TYPES, dev/test only) may register a partial descriptor, and the registry
# loader does not run the JSON-Schema gate — such a type contributes no rows, exactly as it
# contributes no artifact_utils.SCHEMAS entry, instead of breaking the import of the poll
# script every pipeline barrier runs.
_PHASE_FACTS = ("dirs.tasks", "dirs.reviews", "pipeline.poll_prefix")


def _polls(desc):
    """True when ``desc`` carries every field its phase rows are derived from."""
    return all(desc.get(dotted, None) is not None for dotted in _PHASE_FACTS)


def _phase_rows(desc):
    """phase base -> (id -> expected output path) for one type: ``dirs`` x the pipeline phases,
    plus one ``<reviews>/<id>-<name>.md`` row per ``pipeline.dimensions`` entry."""
    dirs = desc.dirs()
    rows = {"fetch": lambda id: f"{dirs['tasks']}/{id}.md"}
    if desc.name in _CREATE_BARRIER_TYPES:
        # Same path as "fetch", but a stricter check — see check_id(). Create agents
        # write the task file first and set frontmatter in a later tool call, so
        # existence alone would release the barrier mid-write.
        rows["create"] = lambda id: f"{dirs['tasks']}/{id}.md"
    rows["assess"] = lambda id: f"{ASSESS_STAGING}/{id}.result.md"
    for dimension in desc.get("pipeline.dimensions", []):
        name = dimension["name"]
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


def check_id(phase, rfe_id):
    """Check one ID. Returns 'completed', 'pending', or 'error'."""
    path = PHASE_CHECKS[phase](rfe_id)
    if not os.path.exists(path):
        return "pending"
    # The check mode follows the phase base for every type: create -> frontmatter_valid,
    # review -> score_present, revise -> revised_or_split, anything else -> exists. A phase
    # patched into PHASE_CHECKS without an owner (tests) keeps the exists mode.
    type_name, base = _PHASE_OWNER.get(phase, (None, None))
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
        try:
            data, _ = read_frontmatter(path)
        except Exception:
            return "error"
        if not data:
            return "error"
        if data.get("score") is None:
            return "pending"
        if data.get("error"):
            return "error"
    if base == "revise":
        try:
            data, _ = read_frontmatter(path)
        except Exception:
            return "error"
        if not data:
            return "error"
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
    # The interactive skills write tmp/<pipeline.state_prefix><stage>-config.yaml. This list
    # is deliberately still the literal: the descriptor projection (state_prefix x stages)
    # would ADD tmp/initiative-speedrun-config.yaml, which initiative-speedrun writes but this
    # allowlist never polled, so interactive initiative speedruns would start auto-enabling
    # fast polling. That drift is fixed on purpose by a separate change, not by this
    # behavior-neutral migration.
    for cfg in (
        "tmp/review-config.yaml",
        "tmp/split-config.yaml",
        "tmp/autofix-config.yaml",
        "tmp/speedrun-config.yaml",
        "tmp/initiative-review-config.yaml",
        "tmp/initiative-split-config.yaml",
        "tmp/initiative-autofix-config.yaml",
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
    parser.add_argument("ids", nargs="*", metavar="ID", help="RFE IDs to check")
    args = parser.parse_args()

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
