#!/usr/bin/env python3
"""Pipeline state machine for the thin dispatcher.

Phase tracking, config, and transition logic for rfe.auto-fix and
initiative-auto-fix.

Usage:
    python3 scripts/pipeline_state.py init [--type rfe|initiative] [--batch-size N]
                                          [--headless]
    python3 scripts/pipeline_state.py get-phase
    python3 scripts/pipeline_state.py set-phase <PHASE>
    python3 scripts/pipeline_state.py get-phase-config
    python3 scripts/pipeline_state.py run-phase
    python3 scripts/pipeline_state.py advance [--dry-run]
    python3 scripts/pipeline_state.py set-wave <IDs>
    python3 scripts/pipeline_state.py next-action
    python3 scripts/pipeline_state.py wait-for-wave   # exit 0 done, 3 re-run, 1 escalation
                                                      # failed; stall guard knobs:
                                                      # PIPELINE_WAVE_STALL_SECS (900, 0 = off),
                                                      # PIPELINE_WAVE_RETRY_CAP (2)
    python3 scripts/pipeline_state.py set key=value ...
    python3 scripts/pipeline_state.py get <key>
    python3 scripts/pipeline_state.py status
    python3 scripts/pipeline_state.py diagnose
    python3 scripts/pipeline_state.py dispatch-context
"""

import argparse
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import type_registry

_TYPES = type_registry.load()

STATE_FILE = "tmp/pipeline-state.yaml"
WAVE_IDS_FILE = "tmp/pipeline-wave-ids.txt"
# Epoch of the current wave's launch (AISDLC-33): the barrier hands it to check_review_progress
# as --since so a review or assess result written before the launch — a previous reassess
# cycle's late agent — cannot release the wave.
WAVE_LAUNCH_FILE = "tmp/pipeline-wave-launch.txt"
# Epoch at which the current agent phase was entered (AISDLC-33). next-action's wave pre-filter
# and the advance guard decide which ids still need an agent against it: REASSESS_SAVE deletes
# the review and assess result files before REASSESS_ASSESS / REASSESS_REVIEW are entered, so
# any such file older than the entry is a previous cycle's late write and must not count as
# done — otherwise the id is never launched and the phase advances on the stale verdict.
PHASE_ENTRY_FILE = "tmp/pipeline-phase-entry.txt"
DISPATCH_MARKER = "tmp/.dispatch-marker"

MAX_NEXT_ACTION_ITERATIONS = 50

# ---------- Wave stall guard (docs/wave-stall-guard.md) ----------
#
# A subagent that dies silently never writes its output, so its (poll phase, id) slot stays
# pending and wait-for-wave would be re-run until the CI job timeout. The guard tracks the
# number of terminal slots across invocations (reset-on-progress) and, once a wave has made
# no progress for a full window, either re-dispatches the stuck ids or escalates them through
# the same post-barrier contract a verify_phase failure uses. Both files live under tmp/ and
# are wiped by `state.py clean`.
WAVE_PROGRESS_FILE = "tmp/pipeline-wave-progress.yaml"
STALL_RETRIES_FILE = "tmp/pipeline-stall-retries.yaml"

# No-progress window (seconds) for a retry-eligible wave. Env PIPELINE_WAVE_STALL_SECS; 0 (or a
# negative value) disables the guard and restores the unbounded wait.
DEFAULT_WAVE_STALL_SECS = 900
# Escalate-only waves wait this many windows: escalation is a degradation, and a legitimately
# slow single split or revise agent must not be cut off.
ESCALATE_ONLY_WINDOW_FACTOR = 2
# Re-dispatch budget per (pipeline phase, id) before a stuck id is escalated instead. Env
# PIPELINE_WAVE_RETRY_CAP; 0 escalates on the first stall.
DEFAULT_WAVE_RETRY_CAP = 2

RETRY = "retry"
ESCALATE = "escalate"

# Stall policy per PIPELINE PHASE (not per poll phase). Every agent phase in the phase table
# must be classified here (pinned by tests/test_pipeline_state.py::TestWaveStallPolicy).
#
# retry    — the wave's agents each write ONE output file from inputs they do not modify, so a
#            second dispatch (possibly concurrent with a hung first attempt) rewrites that file
#            and has no other side effect. Stuck ids are left pending, their counter is bumped
#            and wait-for-wave exits 0 so next-action re-derives a wave from the pending ids.
# escalate — the wave's agents mutate or mint artifacts, so a second concurrent agent would
#            double-edit the task file (and its removed-context companion) or mint duplicate
#            children. Never re-dispatched: escalated after ESCALATE_ONLY_WINDOW_FACTOR windows.
WAVE_STALL_POLICY = {
    "FETCH": RETRY,  # fetch-agent: <tasks>/<id>.md (+ companions) from Jira; a re-fetch overwrites
    "ASSESS": RETRY,  # scorer: tmp/rfe-assess/single/<id>.result.md; each dimension: one file
    "REVIEW": RETRY,  # review-agent: <reviews>/<id>-review.md from the assess + dimension files
    "REVISE": ESCALATE,  # revise-agent edits the task file in place and writes removed-context
    "REASSESS_ASSESS": RETRY,  # same scorer, over the revised task file
    "REASSESS_REVIEW": RETRY,  # same review-agent
    "REASSESS_REVISE": ESCALATE,  # same revise-agent
    "SPLIT": ESCALATE,  # split-agent mints child task files and archives the parent (also the
    #                     correction pass: SPLIT_CORRECTION_CHECK routes back to this phase)
    "SPLIT_ASSESS": RETRY,  # split children through the same scorer + dimensions
    "SPLIT_REVIEW": RETRY,  # same review-agent
    "SPLIT_REVISE": ESCALATE,  # same revise-agent
    "SPLIT_REASSESS": RETRY,  # same scorer
    "SPLIT_RE_REVIEW": RETRY,  # same review-agent
}

# The explicit headless marker the type registry reads (type_registry.HEADLESS_MARKER_VARS;
# design §3.5, PR-3 D4). A headless pipeline exports it once, at init and on every state load,
# so each subprocess it launches sees the same predicate the pipeline runs under.
HEADLESS_MARKER_ENV = "RFE_CREATOR_HEADLESS"


# ---------- YAML block-scalar dumper (scoped) ----------


def _str_representer(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


class _BlockDumper(yaml.Dumper):
    """Dumper that uses | for multi-line strings. Scoped to next-action."""

    pass


_BlockDumper.add_representer(str, _str_representer)

# ---------- Phase enum ----------

PHASES = [
    "BATCH_START",
    "FETCH",
    "SETUP",
    "ASSESS",
    "REVIEW",
    "REVISE",
    "FIXUP",
    "REASSESS_CHECK",
    "REASSESS_SAVE",
    "REASSESS_ASSESS",
    "REASSESS_REVIEW",
    "REASSESS_RESTORE",
    "REASSESS_REVISE",
    "REASSESS_FIXUP",
    "COLLECT",
    "SPLIT",
    "SPLIT_COLLECT",
    "SPLIT_PIPELINE_START",
    "SPLIT_ASSESS",
    "SPLIT_REVIEW",
    "SPLIT_REVISE",
    "SPLIT_FIXUP",
    "SPLIT_SAVE",
    "SPLIT_REASSESS",
    "SPLIT_RE_REVIEW",
    "SPLIT_RESTORE",
    "SPLIT_CORRECTION_CHECK",
    "BATCH_DONE",
    "ERROR_COLLECT",
    "REPORT",
    "DONE",
]

# ---------- Pipeline type config (projected from the registry, PR-5b) ----------

# The Tier-1/2 prompt skeletons are type-invariant since PR-5b: one review prompt directory
# for every type (design §4.2; plan D7 — no descriptor field). The typed fragments reach the
# agents through the launch block (type_registry.launch_vars), the split prompt and the
# dimension files are the typed files themselves (prompt_file).
REVIEW_PROMPTS = ".claude/skills/rfe-review/prompts"
# The post-compaction recovery target names the body DRIVING the run. Until PR-5c turns the
# legacy bodies into shims, the production job is driven by rfe.auto-fix and an initiative run
# by initiative-auto-fix, so the target stays per type here; a type without a legacy body is
# driven by the generic rfe-auto-fix. Collapses to one constant with the shims (D7).
_LEGACY_DISPATCH_SKILL = {
    "rfe": ".claude/skills/rfe.auto-fix/SKILL.md",
    "initiative": ".claude/skills/initiative-auto-fix/SKILL.md",
}
GENERIC_DISPATCH_SKILL = ".claude/skills/rfe-auto-fix/SKILL.md"
# A descriptor must carry these to get a phase table (a drop-in root may register a partial
# one; it is then refused at `init`, before any state is written — D12).
_PHASE_TABLE_FACTS = (
    "dirs.tasks",
    "dirs.reviews",
    "dirs.originals",
    "pipeline.poll_prefix",
    "pipeline.scorer_agent",
    "pipeline.rubric.path",
    "pipeline.prompts.split_rules",
)


def _pipeline_type_row(desc):
    """One PIPELINE_TYPES row: the eight descriptor projections plus the two constants."""
    dirs = desc.dirs()
    dims = [
        {
            "name": d["name"],
            "prompt": d["prompt"],
            "blocking": bool(d.get("blocking", True)),
            "condition": d.get("condition"),
            "skip_stub": d.get("skip_stub"),
        }
        for d in desc.get("pipeline.dimensions", None) or []
    ]
    by_name = {d["name"]: d for d in dims}
    return {
        "review_prompts": REVIEW_PROMPTS,
        "split_prompt": desc.get("pipeline.prompts.split_rules"),
        "scorer_type": desc.get("pipeline.scorer_agent"),
        "rubric_path": f"{type_registry.CONTEXT_DIR}/{desc.get('pipeline.rubric.path')}",
        "dimensions": dims,
        "feasibility_skill": by_name.get("feasibility", {}).get("prompt"),
        "alignment_skill": by_name.get("alignment", {}).get("prompt"),
        "tasks_dir": dirs["tasks"],
        "reviews_dir": dirs["reviews"],
        "originals_dir": dirs["originals"],
        "dispatch_skill": _LEGACY_DISPATCH_SKILL.get(desc.name, GENERIC_DISPATCH_SKILL),
        "poll_prefix": desc.get("pipeline.poll_prefix"),
    }


def _has_phase_table_facts(desc):
    return all(desc.get(dotted, None) is not None for dotted in _PHASE_TABLE_FACTS)


PIPELINE_TYPES = {
    name: _pipeline_type_row(_TYPES.get(name))
    for name in _TYPES.names()
    if _has_phase_table_facts(_TYPES.get(name))
}


def _launch_block(state):
    """The type's launch block for the auto-fix stage (KEY, value) pairs; cached per type."""
    ptype = state.get("type", "rfe")
    if ptype not in _LAUNCH_BLOCKS:
        _LAUNCH_BLOCKS[ptype] = type_registry.launch_vars(_TYPES.get(ptype), "auto-fix")
    return _LAUNCH_BLOCKS[ptype]


_LAUNCH_BLOCKS = {}


def _render_vars(launch_block, phase_vars, rfe_id):
    """``KEY=value`` lines: the launch block, then the phase's values; ``{ID}`` substituted."""
    lines = [f"{k}={v.replace('{ID}', rfe_id)}" for k, v in launch_block]
    lines += [f"{k}={v.replace('{ID}', rfe_id)}" for k, v in phase_vars.items()]
    return "\n".join(lines) + "\n"


# ---------- Conditional parallel agent helpers ----------


def _frontmatter_field(path, field):
    """One frontmatter field of a task file, or None when the file or block is unreadable."""
    if not os.path.exists(path):
        return None
    with open(path) as f:
        content = f.read()
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        fm = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None
    return (fm or {}).get(field)


def _check_condition(condition, rfe_id, state):
    """Evaluate a dimension's descriptor condition (pipeline.dimensions[].condition) for one id:
    ``{frontmatter_field, prefix}`` — the task file's field starts with the prefix — or
    ``{context_exists}`` — the path exists. No condition means always."""
    if not condition:
        return True
    if "frontmatter_field" in condition:
        t = PIPELINE_TYPES[state.get("type", "rfe")]
        value = _frontmatter_field(
            os.path.join(t["tasks_dir"], f"{rfe_id}.md"), condition["frontmatter_field"]
        )
        return bool(isinstance(value, str) and value.startswith(condition["prefix"]))
    if "context_exists" in condition:
        return os.path.exists(condition["context_exists"])
    return True


def _has_rhaistrat_parent(rfe_id, state):
    """The initiative alignment condition, evaluated from the descriptor (kept as a named
    helper for its callers and tests)."""
    t = PIPELINE_TYPES.get(state.get("type", "rfe"))
    if not t:
        return False
    dim = next((d for d in t["dimensions"] if d["name"] == "alignment"), None)
    if not dim or not dim.get("condition"):
        return False
    return _check_condition(dim["condition"], rfe_id, state)


def _write_poll_stub(poll_phase, rfe_id, skip_stub=None):
    """Write a stub completion file for a skipped conditional agent: the dimension's
    descriptor ``skip_stub`` (``result``/``reason``) as the file's frontmatter."""
    from check_review_progress import PHASE_CHECKS

    path = PHASE_CHECKS[poll_phase](rfe_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if skip_stub is None:
        skip_stub = next(
            (
                dim.get("skip_stub")
                for row in PIPELINE_TYPES.values()
                for dim in row["dimensions"]
                if f"{row['poll_prefix']}{dim['name']}" == poll_phase
            ),
            None,
        )
    stub = skip_stub or {"result": "not_assessed", "reason": "skipped by pipeline"}
    body = "".join(f"{k}: {v}\n" for k, v in stub.items())
    with open(path, "w") as f:
        f.write(f"---\n{body}---\n")


# ---------- Phase config (built from pipeline type) ----------


def _build_phase_config(pipeline_type):
    """Build PHASE_CONFIG for the given pipeline type."""
    t = PIPELINE_TYPES[pipeline_type]
    pp = t["poll_prefix"]
    vfy = f"python3 scripts/verify_phase.py --type {pipeline_type}"

    def _assess_vars():
        return {
            "KEY": "{ID}",
            "DATA_FILE": "tmp/rfe-assess/single/{ID}.md",
            "RUN_DIR": "tmp/rfe-assess/single",
            "PROMPT_PATH": t["rubric_path"],
        }

    def _review_vars(first_pass):
        v = {
            "FIRST_PASS": first_pass,
            "ID": "{ID}",
            "ASSESS_PATH": "tmp/rfe-assess/single/{ID}.result.md",
            "FEASIBILITY_PATH": f"{t['reviews_dir']}/{{ID}}-feasibility.md",
        }
        for dim in t["dimensions"]:
            if dim["name"] != "feasibility":
                key = dim["name"].upper().replace("-", "_")
                v[f"{key}_PATH"] = f"{t['reviews_dir']}/{{ID}}-{dim['name']}.md"
        return v

    def _assess_parallel():
        # One companion per declared dimension (absent dimensions are simply not launched —
        # a drop-in type without a feasibility dimension gets a scorer-only wave, D12).
        parallel = []
        for dim in t["dimensions"]:
            entry = {
                "prompt": dim["prompt"],
                "poll_phase": f"{pp}{dim['name']}",
                "vars": {"ID": "{ID}"},
            }
            if dim.get("condition"):
                entry["condition"] = dim["condition"]
                entry["skip_stub"] = dim.get("skip_stub")
            parallel.append(entry)
        return parallel

    fixup_cmd = f"python3 scripts/check_revised.py --batch --type {pipeline_type}"

    return {
        "BATCH_START": {"type": "noop"},
        "FETCH": {
            "type": "agent",
            "prompt": f"{t['review_prompts']}/fetch-agent.md",
            "ids_file": "tmp/pipeline-active-ids.txt",
            "poll_phase": f"{pp}fetch",
            "post_verify": f"{vfy} --phase fetch --ids-file tmp/pipeline-active-ids.txt",
            "vars": {"KEY": "{ID}"},
        },
        "SETUP": {
            "type": "script",
            # Run concurrently. Exit codes are logged but do not fail the phase:
            # the previous `a & b & wait` form returned wait's status, which is
            # always 0, so this preserves behaviour. Fail-fast SETUP belongs to
            # the registry's validate_types.py --verify gate.
            "commands": [
                f"bash scripts/bootstrap-assess-rfe.sh --type {pipeline_type}",
                "bash scripts/fetch-architecture-context.sh",
            ],
        },
        "ASSESS": {
            "type": "agent",
            "prompt": f"{t['review_prompts']}/assess-agent.md",
            "ids_file": "tmp/pipeline-active-ids.txt",
            "subagent_type": t["scorer_type"],
            "poll_phase": f"{pp}assess",
            "parallel": _assess_parallel(),
            "parallel_timeout": 300,
            "pre_script": "python3 scripts/prep_assess.py {ID}",
            "post_verify": f"{vfy} --phase assess --ids-file tmp/pipeline-active-ids.txt",
            "vars": _assess_vars(),
        },
        "REVIEW": {
            "type": "agent",
            "prompt": f"{t['review_prompts']}/review-agent.md",
            "ids_file": "tmp/pipeline-active-ids.txt",
            "poll_phase": f"{pp}review",
            "post_verify": f"{vfy} --phase review --ids-file tmp/pipeline-active-ids.txt",
            "vars": _review_vars("true"),
        },
        "REVISE": {
            "type": "agent",
            "prompt": f"{t['review_prompts']}/revise-agent.md",
            "ids_file": "tmp/pipeline-revise-ids.txt",
            "poll_phase": f"{pp}revise",
            "vars": {"ID": "{ID}"},
        },
        "FIXUP": {
            "type": "script",
            "command": fixup_cmd,
            "ids_file": "tmp/pipeline-revise-ids.txt",
        },
        # --- Reassess loop ---
        "REASSESS_CHECK": {"type": "noop"},
        "REASSESS_SAVE": {
            "type": "script",
            "command": f"python3 scripts/reassess_save.py --type {pipeline_type}",
        },
        "REASSESS_ASSESS": {
            "type": "agent",
            "prompt": f"{t['review_prompts']}/assess-agent.md",
            "ids_file": "tmp/pipeline-reassess-ids.txt",
            "subagent_type": t["scorer_type"],
            "poll_phase": f"{pp}assess",
            "pre_script": "python3 scripts/prep_assess.py {ID}",
            # NO "parallel" — feasibility NOT re-checked (invariant 4.2/5.4)
            "post_verify": f"{vfy} --phase assess --ids-file tmp/pipeline-reassess-ids.txt",
            "vars": _assess_vars(),
        },
        "REASSESS_REVIEW": {
            "type": "agent",
            "prompt": f"{t['review_prompts']}/review-agent.md",
            "ids_file": "tmp/pipeline-reassess-ids.txt",
            "poll_phase": f"{pp}review",
            "post_verify": f"{vfy} --phase review --ids-file tmp/pipeline-reassess-ids.txt",
            "vars": _review_vars("false"),
        },
        "REASSESS_RESTORE": {
            "type": "script",
            # --keep-state: the state file survives until the COLLECT reconcile re-applies it
            # (AISDLC-33: a review agent's late write after this phase clobbered the restore).
            "command": "python3 scripts/preserve_review_state.py restore --keep-state",
            "ids_file": "tmp/pipeline-reassess-ids.txt",
        },
        "REASSESS_REVISE": {
            "type": "agent",
            "prompt": f"{t['review_prompts']}/revise-agent.md",
            "ids_file": "tmp/pipeline-revise-ids.txt",
            "poll_phase": f"{pp}revise",
            "vars": {"ID": "{ID}"},
        },
        "REASSESS_FIXUP": {
            "type": "script",
            "command": fixup_cmd,
            # The reassess file, not the revise file. REASSESS_REVIEW recreates
            # every re-reviewed item's review file, which resets auto_revised to
            # the schema default (false); REASSESS_RESTORE then narrows the
            # reassess set through filter_for_revision into
            # tmp/pipeline-revise-ids.txt for REASSESS_REVISE, so an item that
            # PASSED after its revision is absent from that file. Scanning only
            # that subset left exactly those items with auto_revised=false (5 of
            # 5 revised items in the 2026-09-04 initiative eval; most production
            # RFEs with a moved score since April), and submit.py derives the
            # auto-revised Jira label from the flag. preserve_review_state.py
            # restore carries the flag across the re-review as well; this is the
            # diff-based safety net. Pinned by
            # tests/test_pipeline_state.py::TestReassessFixupIds.
            "ids_file": "tmp/pipeline-reassess-ids.txt",
        },
        # --- Collect + Split ---
        "COLLECT": {"type": "noop"},
        "SPLIT": {
            "type": "agent",
            "prompt": t["split_prompt"],
            "ids_file": "tmp/pipeline-split-ids.txt",
            "poll_phase": f"{pp}split",
            "vars": {
                "ID": "{ID}",
                "TASK_FILE": f"{t['tasks_dir']}/{{ID}}.md",
                "REVIEW_FILE": f"{t['reviews_dir']}/{{ID}}-review.md",
            },
        },
        "SPLIT_COLLECT": {
            "type": "script",
            "command": f"python3 scripts/split_collect.py --type {pipeline_type}",
        },
        "SPLIT_PIPELINE_START": {"type": "noop"},
        "SPLIT_ASSESS": {
            "type": "agent",
            "prompt": f"{t['review_prompts']}/assess-agent.md",
            "ids_file": "tmp/pipeline-split-children-ids.txt",
            "subagent_type": t["scorer_type"],
            "poll_phase": f"{pp}assess",
            "pre_script": "python3 scripts/prep_assess.py {ID}",
            "parallel": _assess_parallel(),
            "parallel_timeout": 300,
            "post_verify": f"{vfy} --phase assess --ids-file tmp/pipeline-split-children-ids.txt",
            "vars": _assess_vars(),
        },
        "SPLIT_REVIEW": {
            "type": "agent",
            "prompt": f"{t['review_prompts']}/review-agent.md",
            "ids_file": "tmp/pipeline-split-children-ids.txt",
            "poll_phase": f"{pp}review",
            "post_verify": f"{vfy} --phase review --ids-file tmp/pipeline-split-children-ids.txt",
            "vars": _review_vars("true"),
        },
        "SPLIT_REVISE": {
            "type": "agent",
            "prompt": f"{t['review_prompts']}/revise-agent.md",
            "ids_file": "tmp/pipeline-revise-ids.txt",
            "poll_phase": f"{pp}revise",
            "vars": {"ID": "{ID}"},
        },
        "SPLIT_FIXUP": {
            "type": "script",
            "command": fixup_cmd,
            "ids_file": "tmp/pipeline-revise-ids.txt",
        },
        "SPLIT_SAVE": {
            "type": "script",
            "command": "python3 scripts/preserve_review_state.py save",
            "ids_file": "tmp/pipeline-revise-ids.txt",
        },
        "SPLIT_REASSESS": {
            "type": "agent",
            "prompt": f"{t['review_prompts']}/assess-agent.md",
            "ids_file": "tmp/pipeline-revise-ids.txt",
            "subagent_type": t["scorer_type"],
            "poll_phase": f"{pp}assess",
            "pre_script": "python3 scripts/prep_assess.py {ID}",
            "post_verify": f"{vfy} --phase assess --ids-file tmp/pipeline-revise-ids.txt",
            "vars": _assess_vars(),
        },
        "SPLIT_RE_REVIEW": {
            "type": "agent",
            "prompt": f"{t['review_prompts']}/review-agent.md",
            "ids_file": "tmp/pipeline-revise-ids.txt",
            "poll_phase": f"{pp}review",
            "post_verify": f"{vfy} --phase review --ids-file tmp/pipeline-revise-ids.txt",
            "vars": _review_vars("false"),
        },
        "SPLIT_RESTORE": {
            "type": "script",
            # --keep-state, as REASSESS_RESTORE: the SPLIT_CORRECTION_CHECK reconcile re-applies
            # and removes the state file (AISDLC-33).
            "command": "python3 scripts/preserve_review_state.py restore --keep-state",
            "ids_file": "tmp/pipeline-revise-ids.txt",
        },
        "SPLIT_CORRECTION_CHECK": {"type": "noop"},
        # --- Batch control + retry ---
        "BATCH_DONE": {"type": "noop"},
        "ERROR_COLLECT": {
            "type": "script",
            "command": f"python3 scripts/error_collect.py --type {pipeline_type}",
        },
        # --- Terminal ---
        "REPORT": {
            "type": "script",
            "command": (
                f"python3 scripts/generate_run_report.py --type {pipeline_type}"
                " --start-time {start_time}"
                " --batch-size {batch_size}"
                # This phase runs before submit — nothing is in Jira yet.
                " --report-stage pre_submit"
            ),
        },
    }


def _get_config(state):
    """Get PHASE_CONFIG for the pipeline type in the current state (validated first)."""
    _validate_state_values(state)
    return _build_phase_config(state.get("type", "rfe"))


def _wave_size(state, config):
    """IDs to launch per wave for an agent phase.

    Each ID costs 1 + n_parallel concurrent agents (the main agent plus its
    parallel companions — feasibility, and alignment for initiatives), so
    batch_size is spent as an agent budget rather than an ID count.

    The division rounds UP: flooring strands a partial slot on every batch
    that isn't an exact multiple of the divisor, which is what made small
    batches crawl — a speedrun batch of 5 ran 2 IDs per wave for RFEs and 1
    for initiatives. Rounding up costs at most n_parallel agents over budget
    and keeps a small batch to a single wave more often.
    """
    batch_size = int(state.get("batch_size", 50))
    n_parallel = len(config.get("parallel", []))
    return max(1, -(-batch_size // (1 + n_parallel)))


# ---------- State helpers ----------


def _export_headless_marker(state):
    """Export ``RFE_CREATOR_HEADLESS=1`` into this process when ``state`` says ``headless``.

    Every subprocess the pipeline launches inherits ``os.environ``, so a headless run marks
    itself here — at ``init --headless`` and on every state load — and the registry's
    ``is_headless`` predicate agrees with the pipeline's own flag in every child (design §3.5,
    PR-3 D4: one headless predicate). The state file is authoritative: ``init --headless``
    wrote it, so the marker is set to ``1`` even over a stale or false value the environment
    already carried — a child that saw ``0`` would treat the run as interactive and could
    stall a headless pipeline. A non-headless state exports nothing and never unsets a value.
    """
    if isinstance(state, dict) and state.get("headless"):
        os.environ[HEADLESS_MARKER_ENV] = "1"


def _load_state():
    """Load pipeline state from disk (and export the headless marker for a headless run)."""
    if not os.path.exists(STATE_FILE):
        print(f"State file not found: {STATE_FILE}", file=sys.stderr)
        sys.exit(1)
    with open(STATE_FILE) as f:
        state = yaml.safe_load(f)
    _export_headless_marker(state)
    return state


def _save_state(state):
    """Write pipeline state to disk."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        yaml.dump(state, f, default_flow_style=False, sort_keys=False)


_VALID_ID_RE = re.compile(r"[A-Z][A-Z0-9]*-[0-9]+")  # ASCII only: \d would admit e.g. RHAIRFE-١
_START_TIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_ASCII_DIGITS_RE = re.compile(r"[0-9]+")  # str.isdigit() accepts "²", which int() rejects


def _validate_ids(ids, source=""):
    """Reject any id that is not ``<PREFIX>-<digits>`` before it can reach a shell.

    Every id list the pipeline reads — from ``tmp/*-ids.txt`` files or from a
    decision script's stdout — becomes subprocess arguments, prompt variables,
    or path components (``advance()``, ``cmd_run_phase``, ``_save_originals``),
    so both are argument-injection and path-traversal vectors. The grammar is
    deliberately type-neutral: it accepts every local and Jira id shape the
    pipeline produces (``RFE-001``, ``INIT-004``, ``RHAIRFE-2685``,
    ``RHOAIENG-9876``) and nothing else. Per-type grammars belong to the type
    registry (design-proposals/work-item-types-unified.md §3.2); this is the floor.
    """
    bad = [i for i in ids if not _VALID_ID_RE.fullmatch(i)]
    if bad:
        where = f" in {source}" if source else ""
        # Show a bounded preview so the failure is diagnosable in a headless log
        # (these files are written by the pipeline itself from Jira keys), but
        # never echo a long line back verbatim.
        preview = [b[:40] + ("…" if len(b) > 40 else "") for b in bad[:5]]
        print(
            f"[validate-ids] rejected {len(bad)} invalid id(s){where}: {preview!r}",
            file=sys.stderr,
        )
        sys.exit(1)
    return ids


def _validate_state_values(state):
    """Reject state values that are formatted into subprocess commands.

    ``cmd_run_phase`` formats ``{start_time}`` and ``{batch_size}`` from
    ``tmp/pipeline-state.yaml`` into the REPORT command, and the phase table is
    selected by ``type``. Commands no longer go through a shell, but a tampered
    value would still become extra argv tokens (``--start-time x --foo``), so the
    three fields ``init`` writes are checked before use.
    """
    problems = []
    ptype = state.get("type", "rfe")
    if not isinstance(ptype, str) or ptype not in PIPELINE_TYPES:
        problems.append(f"type={ptype!r} is not one of {sorted(PIPELINE_TYPES)}")
    start_time = state.get("start_time", "")
    if not isinstance(start_time, str) or not _START_TIME_RE.fullmatch(start_time):
        problems.append("start_time is not an ISO-8601 UTC timestamp (YYYY-MM-DDTHH:MM:SSZ)")
    else:
        try:  # the regex checks shape only; reject impossible dates and clock values
            datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            problems.append(f"start_time {start_time!r} is not a real date/time")
    batch_size = state.get("batch_size", 50)
    is_int = isinstance(batch_size, int) and not isinstance(batch_size, bool)
    is_digits = isinstance(batch_size, str) and _ASCII_DIGITS_RE.fullmatch(batch_size)
    if not (is_int or is_digits) or int(batch_size) < 1:
        problems.append("batch_size is not a positive integer")
    if problems:
        print("[validate-state] refusing to run: " + "; ".join(problems), file=sys.stderr)
        sys.exit(1)
    return state


def _read_ids(path):
    """Read IDs from a file, one per line. Invalid ids abort (see _validate_ids)."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return _validate_ids([line.strip() for line in f if line.strip()], source=path)


def _write_ids(path, ids):
    """Write IDs to a file, one per line. Validated first: nothing malformed is persisted."""
    _validate_ids(ids, source=path)
    os.makedirs(os.path.dirname(path) or "tmp", exist_ok=True)
    with open(path, "w") as f:
        for id_ in ids:
            f.write(f"{id_}\n")


def _write_wave_launch():
    """Record the launch time of the wave just written to WAVE_IDS_FILE."""
    os.makedirs("tmp", exist_ok=True)
    with open(WAVE_LAUNCH_FILE, "w") as f:
        f.write(f"{_now():.3f}\n")


def _read_wave_launch():
    """The current wave's launch epoch, or None when no launch was recorded."""
    try:
        with open(WAVE_LAUNCH_FILE) as f:
            return float(f.read().strip())
    except (OSError, ValueError):
        return None


def _write_phase_entry(phase):
    """Record that ``phase`` (an agent phase) was entered now."""
    os.makedirs("tmp", exist_ok=True)
    with open(PHASE_ENTRY_FILE, "w") as f:
        f.write(f"{phase}\n{_now():.3f}\n")


def _read_phase_entry(phase):
    """Epoch at which ``phase`` was entered, or None when the record is for another phase
    (or there is none): a repeat next-action for the same phase never re-stamps it."""
    try:
        with open(PHASE_ENTRY_FILE) as f:
            recorded, ts = f.read().split()[:2]
        return float(ts) if recorded == phase else None
    except (OSError, ValueError):
        return None


def _enter_phase(state, next_phase):
    """Move ``state`` to ``next_phase`` and save; stamp the entry time of an agent phase."""
    state["phase"] = next_phase
    _save_state(state)
    if _get_config(state).get(next_phase, {}).get("type") == "agent":
        _write_phase_entry(next_phase)


def _write_revise_baseline(ids, pipeline_type):
    """Record each revise id's review write time (AISDLC-45, check_review_progress).

    Written every time tmp/pipeline-revise-ids.txt is written, so the revise slot of the wave
    about to launch is pending until the revise agent has written the review — the re-raised
    auto_revised flag no longer counts as this wave's revision.
    """
    from check_review_progress import REVISE_BASELINE_FILE, revise_baseline_entry

    os.makedirs("tmp", exist_ok=True)
    baseline = {rid: revise_baseline_entry(pipeline_type, rid) for rid in ids}
    with open(REVISE_BASELINE_FILE, "w") as f:
        json.dump(baseline, f, indent=2)


def _final_reconcile(state, type_flag):
    """The run-level reconcile before REPORT (AISDLC-33): re-apply every kept review state
    over all ids of the run. A review agent can keep writing for minutes after its wave —
    past COLLECT — so the state files are kept once more: submit.py, production's last
    reader, reconciles again at start-up (after the agent process is torn down) and removes
    them. An id the reconcile could not repair gets the registry error stub, not a merged
    error field: the run report treats a review with a generic ``error`` as readable and
    would copy its stale scores. After the reconcile, the content guard
    (``check_revised.py --batch --lower-only``) lowers a set ``auto_revised`` on every task
    whose body still equals its original: a revise agent's last write can land after FIXUP
    (2026-09-21 stage dry run), and the run report must agree with what submit will
    label. Lower-only — raising stays FIXUP's job over the revise ids. The guard is
    best-effort: if it cannot run, REPORT goes ahead with the flags as written and the
    failure is logged with its exit status only."""
    all_ids = _read_ids("tmp/pipeline-all-ids.txt")
    if not all_ids:
        return ""
    out = _run_script(
        f"python3 scripts/reconcile_reviews.py {type_flag} --keep-state"
        f" --cycles {state.get('reassess_cycle', 0)} {' '.join(all_ids)}"
    )
    restored = _parse_line_ids(out, "RESTORED")
    flagged = _parse_line_ids(out, "FLAGGED")
    errored = _parse_line_ids(out, "RECONCILE_ERRORS")
    if errored:
        import verify_phase

        verify_phase.write_error_stubs(
            "review", errored, state.get("type", "rfe"), error="reconcile_failed"
        )
    rc, guard_out = _run_script_soft(
        f"python3 scripts/check_revised.py {type_flag} --batch --lower-only {' '.join(all_ids)}"
    )
    if rc != 0:
        print(
            f"REPORT flag guard: skipped (check_revised.py exit {rc}); flags left as written",
            file=sys.stderr,
        )
        lowered, skipped = [], []
    else:
        lowered = _parse_line_ids(guard_out, "LOWERED")
        # Ids the guard could not read or update (per-id isolation; the rest were checked).
        skipped = _parse_line_ids(guard_out, "SKIPPED")
    lines = ""
    if restored or flagged or errored:
        lines += (
            f"REPORT reconcile: restored={len(restored)} flagged={len(flagged)}"
            f" errors={len(errored)}\n"
        )
    if lowered or skipped:
        lines += f"REPORT flag guard: lowered={len(lowered)}"
        if skipped:
            lines += f" skipped={len(skipped)}"
        lines += "\n"
    return lines


def _sweep_review_state(ids):
    """Remove leftover ``{ID}-review-state.json`` files before a batch starts.

    Only REASSESS_SAVE / SPLIT_SAVE, which run strictly after BATCH_START for the same ids,
    may write one; a file present here is from an interrupted earlier run or batch and would
    be re-applied by the COLLECT reconcile onto a review it does not belong to.
    """
    from preserve_review_state import state_path

    for rid in ids:
        try:
            path = state_path(rid)
        except Exception:
            continue
        if os.path.exists(path):
            os.remove(path)


def _copy_ids(src, dst):
    """Copy an ID file."""
    os.makedirs(os.path.dirname(dst) or "tmp", exist_ok=True)
    shutil.copy2(src, dst)


def _save_originals(ids, pipeline_type):
    """Save task files to originals dir for IDs that don't have one yet.

    Needed for newly-created items (not fetched from Jira) so that
    check_revised.py --batch can detect whether the revise agent changed
    anything.
    """
    _validate_ids(ids, source="_save_originals")  # ids become path components below
    t = PIPELINE_TYPES[pipeline_type]
    tasks_dir = t["tasks_dir"]
    originals_dir = t["originals_dir"]
    os.makedirs(originals_dir, exist_ok=True)
    for rfe_id in ids:
        orig = os.path.join(originals_dir, f"{rfe_id}.md")
        task = os.path.join(tasks_dir, f"{rfe_id}.md")
        if not os.path.exists(orig) and os.path.exists(task):
            shutil.copy2(task, orig)


def _argv(cmd):
    """Split a command template into an argument list; nothing goes through a shell."""
    return shlex.split(cmd)


def _run_script(cmd):
    """Run a script (given as a string, split with shlex, no shell) and return stdout."""
    result = subprocess.run(_argv(cmd), capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Script failed (exit code {result.returncode})", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def _run_script_soft(cmd):
    """Run a best-effort script and return ``(returncode, stdout)``.

    Unlike ``_run_script`` it neither exits on failure nor echoes the child's stderr: a
    frontmatter parse error quotes the offending source line, which can carry issue
    content, and a guard that cannot run must not abort the transition it guards."""
    result = subprocess.run(_argv(cmd), capture_output=True, text=True)
    return result.returncode, result.stdout.strip()


def _ids_from_output(output, source):
    """Whitespace-separated ids printed by a decision script, validated."""
    return _validate_ids(output.split() if output else [], source=source)


def _parse_line_ids(output, prefix):
    """Parse IDs from a KEY=ID1,ID2 output line, validated."""
    for line in output.splitlines():
        if line.startswith(f"{prefix}="):
            val = line.split("=", 1)[1].strip()
            if not val:
                return []
            return _validate_ids(
                [x.strip() for x in val.split(",") if x.strip()], source=f"{prefix}= line"
            )
    return []


# ---------- Transition logic ----------

MAIN_SEQUENCE = ["FETCH", "SETUP", "ASSESS", "REVIEW", "REVISE", "FIXUP"]
REASSESS_SEQUENCE = [
    "REASSESS_SAVE",
    "REASSESS_ASSESS",
    "REASSESS_REVIEW",
    "REASSESS_RESTORE",
    "REASSESS_REVISE",
    "REASSESS_FIXUP",
]
SPLIT_SEQUENCE = [
    "SPLIT_PIPELINE_START",
    "SPLIT_ASSESS",
    "SPLIT_REVIEW",
    "SPLIT_REVISE",
    "SPLIT_FIXUP",
    "SPLIT_SAVE",
    "SPLIT_REASSESS",
    "SPLIT_RE_REVIEW",
    "SPLIT_RESTORE",
    "SPLIT_CORRECTION_CHECK",
]


def advance(state, dry_run=False):
    """Compute and apply the next phase transition.

    Returns (next_phase, summary_line).
    """
    _validate_state_values(state)  # type/start_time/batch_size feed decision-script argv
    phase = state["phase"]
    pipeline_type = state.get("type", "rfe")
    type_flag = f"--type {pipeline_type}"

    # --- BATCH_START: reset counters, populate active IDs ---
    if phase == "BATCH_START":
        batch = state.get("batch", 0) + 1
        if not dry_run:
            state["batch"] = batch
            state["reassess_cycle"] = 0
            state["correction_cycle"] = 0
            batch_file = f"tmp/pipeline-batch-{batch}-ids.txt"
            _copy_ids(batch_file, "tmp/pipeline-active-ids.txt")
            _sweep_review_state(_read_ids("tmp/pipeline-active-ids.txt"))
        return "FETCH", f"BATCH_START → FETCH: batch={batch}"

    # --- Filter before REVISE phases ---
    if phase == "REVIEW":
        if not dry_run:
            active_ids = _read_ids("tmp/pipeline-active-ids.txt")
            out = _run_script(f"python3 scripts/filter_for_revision.py {' '.join(active_ids)}")
            revise_ids = _ids_from_output(out, "filter_for_revision.py output")
            _write_ids("tmp/pipeline-revise-ids.txt", revise_ids)
            if revise_ids:
                _save_originals(revise_ids, pipeline_type)
            _write_revise_baseline(revise_ids, pipeline_type)
        return "REVISE", "REVIEW → REVISE"

    if phase == "REASSESS_RESTORE":
        if not dry_run:
            cycle = state.get("reassess_cycle", 0)
            if cycle >= 2:
                # Last cycle: no further revision (it would go unreviewed), but the filter's
                # regression rule must still run — a second revision that scored below
                # before_score becomes autorevise_reject instead of shipping as revise.
                reassess_ids = _read_ids("tmp/pipeline-reassess-ids.txt")
                if reassess_ids:
                    _run_script(f"python3 scripts/filter_for_revision.py {' '.join(reassess_ids)}")
                _write_ids("tmp/pipeline-revise-ids.txt", [])
                _write_revise_baseline([], pipeline_type)
            else:
                reassess_ids = _read_ids("tmp/pipeline-reassess-ids.txt")
                out = _run_script(
                    f"python3 scripts/filter_for_revision.py {' '.join(reassess_ids)}"
                )
                revise_ids = _ids_from_output(out, "filter_for_revision.py output")
                _write_ids("tmp/pipeline-revise-ids.txt", revise_ids)
                if revise_ids:
                    _save_originals(revise_ids, pipeline_type)
                # Taken after REASSESS_RESTORE rewrote the review: the re-raised flag is in
                # the baseline, so only this wave's revise agent can move the slot.
                _write_revise_baseline(revise_ids, pipeline_type)
        return "REASSESS_REVISE", "REASSESS_RESTORE → REASSESS_REVISE"

    if phase == "SPLIT_REVIEW":
        if not dry_run:
            child_ids = _read_ids("tmp/pipeline-split-children-ids.txt")
            out = _run_script(f"python3 scripts/filter_for_revision.py {' '.join(child_ids)}")
            revise_ids = _ids_from_output(out, "filter_for_revision.py output")
            _write_ids("tmp/pipeline-revise-ids.txt", revise_ids)
            if revise_ids:
                _save_originals(revise_ids, pipeline_type)
            _write_revise_baseline(revise_ids, pipeline_type)
        return "SPLIT_REVISE", "SPLIT_REVIEW → SPLIT_REVISE"

    # --- Linear sequences ---
    for seq in [MAIN_SEQUENCE, REASSESS_SEQUENCE, SPLIT_SEQUENCE]:
        if phase in seq[:-1]:
            nxt = seq[seq.index(phase) + 1]
            return nxt, f"{phase} → {nxt}"

    # --- FIXUP → REASSESS_CHECK ---
    if phase == "FIXUP":
        return "REASSESS_CHECK", "FIXUP → REASSESS_CHECK"

    # --- REASSESS_CHECK decision ---
    if phase == "REASSESS_CHECK":
        active_ids = _read_ids("tmp/pipeline-active-ids.txt")
        ids_str = " ".join(active_ids)
        out = _run_script(
            f"python3 scripts/collect_recommendations.py {type_flag} --reassess {ids_str}"
        )
        reassess_ids = _parse_line_ids(out, "REASSESS")
        cycle = state.get("reassess_cycle", 0)
        if reassess_ids and cycle < 2:
            if not dry_run:
                state["reassess_cycle"] = cycle + 1
                _write_ids("tmp/pipeline-reassess-ids.txt", reassess_ids)
            return (
                "REASSESS_SAVE",
                f"REASSESS_CHECK → REASSESS_SAVE: reassess={len(reassess_ids)} cycle={cycle + 1}/2",
            )
        return "COLLECT", "REASSESS_CHECK → COLLECT: no reassess needed"

    # --- REASSESS_FIXUP loops back ---
    if phase == "REASSESS_FIXUP":
        return "REASSESS_CHECK", "REASSESS_FIXUP → REASSESS_CHECK"

    # --- COLLECT decision ---
    if phase == "COLLECT":
        active_ids = _read_ids("tmp/pipeline-active-ids.txt")
        # Reconcile before routing (AISDLC-33): re-apply every saved review state a late
        # agent write may have clobbered since REASSESS_RESTORE, and flag every item still
        # failing now that no further revision can happen in this batch.
        reconcile = ""
        if active_ids and not dry_run:
            out = _run_script(
                f"python3 scripts/reconcile_reviews.py {type_flag} --keep-state"
                f" --cycles {state.get('reassess_cycle', 0)} {' '.join(active_ids)}"
            )
            restored = _parse_line_ids(out, "RESTORED")
            flagged = _parse_line_ids(out, "FLAGGED")
            errored = _parse_line_ids(out, "RECONCILE_ERRORS")
            # An item the reconcile could not repair may still hold a readable but stale
            # review: mark it through the error contract so collect_recommendations routes
            # it to ERRORS (retryable) instead of submitting it on that review.
            for rid in errored:
                _mark_review_or_stub(
                    rid,
                    {
                        "error": "reconcile_failed",
                        "needs_attention": True,
                        "needs_attention_reason": "COLLECT reconcile could not repair this"
                        " review (see the RECONCILE_ERROR line in the run log)",
                    },
                    pipeline_type,
                    "review",
                    error="reconcile_failed",
                )
            if restored or flagged or errored:
                reconcile = (
                    f"COLLECT reconcile: restored={len(restored)} flagged={len(flagged)}"
                    f" errors={len(errored)}\n"
                )
        out = _run_script(
            f"python3 scripts/collect_recommendations.py {type_flag} {' '.join(active_ids)}"
        )
        split_ids = _parse_line_ids(out, "SPLIT")
        # Build summary counts from collect output
        counts = {}
        for key in ("SUBMIT", "SPLIT", "REVISE", "REJECT", "ERRORS"):
            ids = _parse_line_ids(out, key)
            counts[key.lower()] = len(ids)
        stats = " ".join(f"{k}={v}" for k, v in counts.items())
        if split_ids:
            if not dry_run:
                _write_ids("tmp/pipeline-split-ids.txt", split_ids)
            return ("SPLIT", f"{reconcile}COLLECT complete: {stats}\nCOLLECT → SPLIT")
        return "BATCH_DONE", f"{reconcile}COLLECT complete: {stats}\nCOLLECT → BATCH_DONE"

    # --- SPLIT → SPLIT_COLLECT ---
    if phase == "SPLIT":
        return "SPLIT_COLLECT", "SPLIT → SPLIT_COLLECT"

    # --- SPLIT_COLLECT decision ---
    if phase == "SPLIT_COLLECT":
        child_ids = _read_ids("tmp/pipeline-split-children-ids.txt")
        if child_ids:
            return (
                "SPLIT_PIPELINE_START",
                f"SPLIT_COLLECT → SPLIT_PIPELINE_START: children={len(child_ids)}",
            )
        return ("BATCH_DONE", "SPLIT_COLLECT → BATCH_DONE: no children")

    # --- SPLIT_CORRECTION_CHECK ---
    if phase == "SPLIT_CORRECTION_CHECK":
        child_ids = _read_ids("tmp/pipeline-split-children-ids.txt")
        if child_ids and not dry_run:
            # The children's COLLECT equivalent (AISDLC-33): re-apply kept review state, flag
            # what still fails, before their sizes and recommendations are read.
            _run_script(
                f"python3 scripts/reconcile_reviews.py {type_flag} --keep-state --cycles 1"
                f" {' '.join(child_ids)}"
            )
        if child_ids:
            out = _run_script(
                f"python3 scripts/check_right_sized.py {type_flag} {' '.join(child_ids)}"
            )
            undersized = out.split("RESPLIT=")[1].split() if "RESPLIT=" in out else []
        else:
            undersized = []
        cycle = state.get("correction_cycle", 0)
        if undersized and cycle < 1:
            if not dry_run:
                state["correction_cycle"] = cycle + 1
                _write_ids("tmp/pipeline-split-ids.txt", undersized)
            return (
                "SPLIT",
                f"SPLIT_CORRECTION_CHECK → SPLIT:"
                f" undersized={len(undersized)} correction={cycle + 1}/1",
            )
        return "BATCH_DONE", "SPLIT_CORRECTION_CHECK → BATCH_DONE"

    # --- BATCH_DONE decision ---
    if phase == "BATCH_DONE":
        batch = state.get("batch", 0)
        total = state.get("total_batches", 1)
        retry = state.get("retry_cycle", 0)
        # Batch completion summary
        active_ids = _read_ids("tmp/pipeline-active-ids.txt")
        batch_stats = ""
        if active_ids:
            try:
                out = _run_script(
                    f"python3 scripts/batch_summary.py {type_flag}"
                    f" --counts-only {' '.join(active_ids)}"
                )
                batch_stats = out.strip()
            except Exception:
                pass
        prefix = "Retry batch" if retry > 0 else "Batch"
        summary = f"{prefix} {batch}/{total} complete: {batch_stats}"
        if batch < total:
            return ("BATCH_START", f"{summary}\nBATCH_DONE → BATCH_START")
        if retry < 1:
            all_ids = _read_ids("tmp/pipeline-all-ids.txt")
            if all_ids:
                out = _run_script(
                    f"python3 scripts/collect_recommendations.py {type_flag}"
                    f" --errors {' '.join(all_ids)}"
                )
                error_ids = _parse_line_ids(out, "ERRORS")
                if error_ids:
                    return (
                        "ERROR_COLLECT",
                        f"{summary}\nBATCH_DONE → ERROR_COLLECT: errors={len(error_ids)}",
                    )
        final = _final_reconcile(state, type_flag) if not dry_run else ""
        return "REPORT", f"{summary}\n{final}BATCH_DONE → REPORT"

    # --- ERROR_COLLECT → BATCH_START (or REPORT when nothing is retryable) ---
    if phase == "ERROR_COLLECT":
        retry_ids = _read_ids("tmp/pipeline-retry-ids.txt")
        n = len(retry_ids)
        if n == 0:
            # BATCH_DONE routed here on error-classified reviews, but
            # error_collect.py found none worth retrying (the two checks can
            # disagree — reviews change under them). Starting a retry batch
            # with no IDs dead-ends the machine at a batch file that does not
            # exist, and the run's report never gets generated. Observed in
            # production 2026-08-24 (RHAIFIRST-581).
            final = _final_reconcile(state, type_flag) if not dry_run else ""
            return (
                "REPORT",
                f"ERROR_COLLECT: no retryable errors\n{final}ERROR_COLLECT → REPORT",
            )
        batch = state.get("total_batches", 0)
        return (
            "BATCH_START",
            f"ERROR_COLLECT: retry batch {batch} with {n} error IDs\nERROR_COLLECT → BATCH_START",
        )

    # --- REPORT → DONE (optional announce) ---
    if phase == "REPORT":
        if not dry_run and state.get("announce_complete"):
            _run_script("python3 scripts/finish.py")
        return "DONE", "REPORT → DONE"

    print(f"No transition defined for phase: {phase}", file=sys.stderr)
    sys.exit(1)


# ---------- CLI commands ----------


def cmd_init(args):
    parser = argparse.ArgumentParser(prog="pipeline_state.py init")
    # Registered type names (rfe first) that this script has a phase table for: an unknown
    # --type fails with that list (design §5 rung 1). PIPELINE_TYPES is projected from the
    # registry (PR-5b), so a registered descriptor that carries the phase-table facts gets a
    # table automatically (D12); one that lacks them (a partial drop-in) is refused HERE,
    # before any state is written, rather than by _validate_state_values later.
    parser.add_argument(
        "--type", choices=[n for n in _TYPES.choices() if n in PIPELINE_TYPES], default="rfe"
    )
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--announce-complete", action="store_true")
    opts = parser.parse_args(args)

    os.makedirs("tmp", exist_ok=True)
    # Clean stale artifacts from prior runs.
    for f in glob.glob("tmp/pipeline-batch-*-ids.txt"):
        os.remove(f)
    if os.path.exists(DISPATCH_MARKER):
        os.remove(DISPATCH_MARKER)
    state = {
        "phase": "INIT",
        "type": opts.type,
        "batch": 0,
        "total_batches": 0,
        "headless": opts.headless,
        "announce_complete": opts.announce_complete,
        "batch_size": opts.batch_size,
        "start_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reassess_cycle": 0,
        "correction_cycle": 0,
        "retry_cycle": 0,
        # Batch slot error_collect allocates for the retry pass. Recorded so a
        # re-run refills that slot instead of allocating a second one.
        "retry_batch": None,
    }
    _save_state(state)
    _export_headless_marker(state)
    print(f"Initialized pipeline state: type={opts.type} batch_size={opts.batch_size}")


def cmd_get_phase(args):
    state = _load_state()
    print(state["phase"])


def cmd_set_phase(args):
    if not args or args[0] not in PHASES:
        print(f"Usage: set-phase <PHASE>\nValid phases: {', '.join(PHASES)}", file=sys.stderr)
        sys.exit(1)
    state = _load_state()
    _enter_phase(state, args[0])
    print(args[0])


def cmd_get_phase_config(args):
    state = _load_state()
    phase = state["phase"]
    config = dict(_get_config(state).get(phase, {"type": "noop"}))
    config["phase"] = phase
    config.pop("command", None)
    config.pop("pre_script", None)
    config.pop("post_verify", None)
    if config.get("type") == "script":
        config.pop("ids_file", None)
    if config.get("type") == "agent":
        config["wave_size"] = _wave_size(state, config)
    print(yaml.dump(config, default_flow_style=False, sort_keys=False), end="")


def cmd_run_phase(args):
    """Execute the current script phase's command internally.

    Loads state, resolves the command from PHASE_CONFIG, appends IDs
    from ids_file if configured, and runs the command. The orchestrator
    never sees the underlying script name.
    """
    state = _validate_state_values(_load_state())
    phase = state["phase"]
    config = _get_config(state).get(phase, {"type": "noop"})
    phase_type = config.get("type", "noop")
    if phase_type != "script":
        print(f"run-phase: phase {phase} is type '{phase_type}', not 'script'", file=sys.stderr)
        sys.exit(1)
    if config.get("commands"):
        # Concurrent commands (SETUP). See the phase config for why exit codes
        # are logged rather than fatal.
        print(f"[run-phase] {phase}")
        procs = [(c, subprocess.Popen(_argv(c.format_map(state)))) for c in config["commands"]]
        for c, proc in procs:
            rc = proc.wait()
            if rc != 0:
                print(f"[run-phase] {phase}: exit {rc} from: {c}", file=sys.stderr)
        with open(DISPATCH_MARKER, "w") as f:
            f.write(phase)
        return
    argv = _argv(config["command"].format_map(state))
    if config.get("ids_file"):
        ids = _read_ids(config["ids_file"])
        if ids:
            argv += ids
        else:
            print(f"[run-phase] {phase}: no IDs, skipping")
            # Write dispatch marker and return — nothing to do
            with open(DISPATCH_MARKER, "w") as f:
                f.write(phase)
            return
    print(f"[run-phase] {phase}")
    result = subprocess.run(argv)
    if result.returncode != 0:
        sys.exit(result.returncode)
    # Write dispatch marker — advance checks this for script phases
    with open(DISPATCH_MARKER, "w") as f:
        f.write(phase)


def cmd_set_wave(args):
    """Write the current wave's IDs to the wave file.

    Called before launching agents for a wave so the wait command
    can use --id-file without the caller passing IDs.
    """
    if not args:
        print("Usage: set-wave ID1 ID2 ...", file=sys.stderr)
        sys.exit(1)
    _write_ids(WAVE_IDS_FILE, args)
    _write_wave_launch()
    _clear_wave_progress()  # a new wave starts a fresh stall window (see cmd_next_action)
    print(f"Wave: {len(args)} IDs")


def cmd_next_action(args):
    """Compute and return the next action for the dispatch loop.

    Chains through noop phases and completed script phases internally,
    returning only when the LLM needs to act: launch_wave, run_script,
    or done.
    """
    import check_review_progress as crp
    from check_review_progress import check_id

    state = _load_state()
    phase = state["phase"]

    if phase == "DONE":
        print(
            yaml.dump(
                {"action": "done", "message": "Pipeline complete"},
                default_flow_style=False,
                sort_keys=False,
            ),
            end="",
        )
        return

    if phase not in PHASES:
        print(
            f"next-action: phase '{phase}' is not dispatchable."
            " Run init and set-phase BATCH_START first.",
            file=sys.stderr,
        )
        sys.exit(1)

    phase_config = _get_config(state)
    for _ in range(MAX_NEXT_ACTION_ITERATIONS):
        phase = state["phase"]
        config = phase_config.get(phase, {"type": "noop"})
        phase_type = config.get("type", "noop")

        # --- DONE ---
        if phase == "DONE":
            print(
                yaml.dump(
                    {"action": "done", "message": "Pipeline complete"},
                    default_flow_style=False,
                    sort_keys=False,
                ),
                end="",
            )
            return

        # --- Noop: advance and loop ---
        if phase_type == "noop":
            next_phase, summary = advance(state)
            _enter_phase(state, next_phase)
            print(summary, file=sys.stderr)
            continue

        # --- Script: check dispatch marker ---
        if phase_type == "script":
            if os.path.exists(DISPATCH_MARKER):
                with open(DISPATCH_MARKER) as f:
                    marker_phase = f.read().strip()
                if marker_phase == phase:
                    # Script already ran — advance past it
                    os.remove(DISPATCH_MARKER)
                    next_phase, summary = advance(state)
                    _enter_phase(state, next_phase)
                    print(summary, file=sys.stderr)
                    continue
                else:
                    # Stale marker from a different phase — remove it
                    os.remove(DISPATCH_MARKER)
            # No marker (or stale removed) — tell LLM to run the script
            print(
                yaml.dump(
                    {"action": "run_script", "phase": phase, "message": f"{phase}: run-phase"},
                    default_flow_style=False,
                    sort_keys=False,
                ),
                end="",
            )
            return

        # --- Agent: compute next wave ---
        if phase_type == "agent":
            ids_file = config.get("ids_file", "")
            all_ids = _read_ids(ids_file)
            poll_phase = config.get("poll_phase", "")

            # Build list of all phases to check (main + parallel)
            phases_to_check = [poll_phase] if poll_phase else []
            for p in config.get("parallel", []):
                if p.get("poll_phase"):
                    phases_to_check.append(p["poll_phase"])

            # Pre-filter: keep only IDs where ANY phase is still pending. Assess/review files
            # older than this phase's entry are stale (AISDLC-33) and keep their id in the wave.
            crp.set_wave_launch(_read_phase_entry(phase))
            remaining = []
            for rfe_id in all_ids:
                for pphase in phases_to_check:
                    if check_id(pphase, rfe_id) == "pending":
                        remaining.append(rfe_id)
                        break

            if not remaining:
                # All done — run post_verify if set, then advance
                if config.get("post_verify"):
                    _run_script(config["post_verify"])
                next_phase, summary = advance(state)
                _enter_phase(state, next_phase)
                print(summary, file=sys.stderr)
                continue

            wave_size = _wave_size(state, config)

            wave_ids = remaining[:wave_size]
            wave_num = 1 + (len(all_ids) - len(remaining)) // wave_size
            total_waves = max(1, -(-len(all_ids) // wave_size))  # ceil div

            # Run pre_script for each ID in the wave
            if config.get("pre_script"):
                for rfe_id in wave_ids:
                    cmd = config["pre_script"].replace("{ID}", rfe_id)
                    _run_script(cmd)

            # Write wave IDs. Writing a wave also drops the stall guard's progress tracker:
            # the previous barrier may never have observed exit 0 (an orchestrator that ran
            # next-action instead of re-running wait-for-wave leaves the tracker behind), and
            # a later wave over the same phase and ids — the retry batch, the next reassess
            # cycle — would otherwise inherit its deadline and be declared stalled on its
            # first poll.
            _write_ids(WAVE_IDS_FILE, wave_ids)
            _write_wave_launch()
            _clear_wave_progress()

            # Build agent entries. Every agent's vars are the type's launch block (the typed
            # literals the skeletons and typed files read — design §8.3, PR-5b) followed by
            # the phase's own values, with {ID} substituted throughout.
            launch_block = _launch_block(state)
            agents = []
            for rfe_id in wave_ids:
                # Main agent
                entry = {}
                if config.get("subagent_type"):
                    entry["subagent_type"] = config["subagent_type"]
                entry["prompt_file"] = config["prompt"]
                entry["vars"] = _render_vars(launch_block, config.get("vars", {}), rfe_id)
                agents.append(entry)

                # Parallel agents
                for par in config.get("parallel", []):
                    cond = par.get("condition")
                    if cond and not _check_condition(cond, rfe_id, state):
                        if par.get("poll_phase"):
                            _write_poll_stub(par["poll_phase"], rfe_id, par.get("skip_stub"))
                        continue
                    pentry = {}
                    if par.get("subagent_type"):
                        pentry["subagent_type"] = par["subagent_type"]
                    pentry["prompt_file"] = par["prompt"]
                    pentry["vars"] = _render_vars(launch_block, par.get("vars", {}), rfe_id)
                    agents.append(pentry)

            msg = f"{phase}: wave {wave_num}/{total_waves} ({len(wave_ids)} IDs)"
            output = {
                "action": "launch_wave",
                "phase": phase,
                "message": msg,
                "agents": agents,
            }
            print(
                yaml.dump(output, Dumper=_BlockDumper, default_flow_style=False, sort_keys=False),
                end="",
            )
            return

    # Safety: should never reach here
    print(
        f"next-action: exceeded {MAX_NEXT_ACTION_ITERATIONS} iterations at phase {state['phase']}",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------- Wave stall guard ----------


def _now():
    """Wall clock (the tracker persists across processes, so monotonic time cannot be used)."""
    return time.time()


def _env_int(var, default):
    raw = os.environ.get(var)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        print(
            f"wait-for-wave: ignoring {var}={raw!r} (not an integer), using {default}",
            file=sys.stderr,
        )
        return default


def _wave_stall_secs():
    return max(0, _env_int("PIPELINE_WAVE_STALL_SECS", DEFAULT_WAVE_STALL_SECS))


def _wave_retry_cap():
    return max(0, _env_int("PIPELINE_WAVE_RETRY_CAP", DEFAULT_WAVE_RETRY_CAP))


def _stall_window(policy):
    """No-progress window in seconds for a wave under ``policy``; 0 when the guard is off."""
    secs = _wave_stall_secs()
    if secs <= 0:
        return 0
    return secs if policy == RETRY else secs * ESCALATE_ONLY_WINDOW_FACTOR


def _wave_poll_phases(config):
    """The poll phases a wave barrier watches: the phase's own plus its parallel agents'."""
    phases = [config["poll_phase"]] if config.get("poll_phase") else []
    for p in config.get("parallel", []) or []:
        if p.get("poll_phase"):
            phases.append(p["poll_phase"])
    return phases


def _terminal_slots(poll_phases, ids):
    """Number of (poll phase, id) slots no longer pending — the wave's progress metric."""
    from check_review_progress import check_id

    return sum(1 for ph in poll_phases for rid in ids if check_id(ph, rid) != "pending")


def _stuck_ids(poll_phases, ids):
    """id -> its still-pending poll phases, in wave order, for the ids that have any."""
    from check_review_progress import check_id

    stuck = {}
    for rid in ids:
        pending = [ph for ph in poll_phases if check_id(ph, rid) == "pending"]
        if pending:
            stuck[rid] = pending
    return stuck


def _read_yaml_file(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _write_yaml_file(path, data, **dump_kwargs):
    os.makedirs(os.path.dirname(path) or "tmp", exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, **dump_kwargs)


def _read_wave_progress():
    prog = _read_yaml_file(WAVE_PROGRESS_FILE)
    return prog if isinstance(prog, dict) else None


def _write_wave_progress(prog):
    _write_yaml_file(WAVE_PROGRESS_FILE, prog, sort_keys=False)


def _clear_wave_progress():
    try:
        os.remove(WAVE_PROGRESS_FILE)
    except OSError:
        pass


def _read_stall_retries():
    """Per (phase, id) retry counters, normalized: only a non-negative non-bool int counts;
    a missing, null, string, float, bool or negative value (a hand-edited or truncated file)
    reads as 0, so a stalled wave can never fail on ``None < cap`` and a bogus value cannot
    buy or deny a retry."""
    counts = _read_yaml_file(STALL_RETRIES_FILE)
    if not isinstance(counts, dict):
        return {}
    clean = {}
    for key, value in counts.items():
        if not isinstance(key, str):
            continue
        ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0
        clean[key] = value if ok else 0
    return clean


def _write_stall_retries(counts):
    _write_yaml_file(STALL_RETRIES_FILE, counts, sort_keys=True)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _track_wave_progress(sig, phase, done_now):
    """Record the wave's terminal-slot count and when it last grew (reset-on-progress).

    Keyed by a signature of the wave so a new wave starts a fresh window; the deadline resets
    whenever the count of terminal slots increases, never on a poll that merely returned.
    A record for the current wave without a usable ``last_progress_ts`` (key missing, null, or
    not a number: a hand-edited or truncated file) is treated as absent and replaced by a
    fresh record, so the caller never reads a timestamp that is not there; a ``done`` that is
    not an integer is normalized to 0, which at worst resets the deadline once.
    """
    prog = _read_wave_progress()
    now = _now()
    if not prog or prog.get("sig") != sig or not _is_number(prog.get("last_progress_ts")):
        prog = {"sig": sig, "phase": phase, "last_progress_ts": now, "done": done_now}
    else:
        done = prog.get("done")
        prog["done"] = done if isinstance(done, int) and not isinstance(done, bool) else 0
        if done_now > prog["done"]:
            prog["last_progress_ts"], prog["done"] = now, done_now
    _write_wave_progress(prog)
    return prog


def _stall_reason(phase, idle, window):
    return (
        f"wave stalled in {phase}: no agent reached a terminal state for {int(idle)}s"
        f" (window {window}s); the subagent produced no output"
    )


def _mark_review_or_stub(rid, updates, pipeline_type, base, error, failures=None):
    """Merge ``updates`` into ``rid``'s review, or write the registry error stub carrying ``error``.

    The stub is the fallback when there is no review to update or the review is one the
    schema rejects (a hand-written or half-written file). Escalation must never raise: it
    runs before the wave and ids files are rewritten, so an exception here would turn the
    bounded barrier into a crash loop in which every re-run repeats the 90s poll and the same
    traceback. Returns True when a marker is on disk (the merge succeeded, or the stub writer
    reported no unwritten id) and False when neither writer could produce one; in that case
    the writer's reason, when it gave one, is recorded in ``failures[rid]``. The caller must
    not retire an id this returns False for: with no marker, error_collect would never
    retry it and the run report would show it failed for no reason.
    """
    import verify_phase
    from artifact_utils import update_frontmatter

    review_path = f"{_TYPES.get(pipeline_type).dirs()['reviews']}/{rid}-review.md"
    if os.path.exists(review_path):
        try:
            update_frontmatter(review_path, updates, f"{pipeline_type}-review")
            return True
        except Exception as exc:
            detail = " ".join(str(exc).split())
            print(
                f"wait-for-wave: could not mark {rid}'s review ({detail}); replacing it with"
                f" the {base} error stub (error={error})",
                file=sys.stderr,
            )
    unwritten = verify_phase.write_error_stubs(
        base, [rid], pipeline_type, error=error, failures=failures
    )
    return rid not in (unwritten or [])


def _mark_revise_stalled(ids, pipeline_type):
    """Revise escalation: the review keeps its real score and recommendation and gains the
    retryable ``revise_stalled`` error; ``auto_revised`` is left as it is (no revision is
    claimed). error_collect restores the task file from its original before the retry, which
    also undoes anything a half-finished revise agent may have written.
    Returns ``(error, failures)``: the marker's name for the operator line and ``id ->
    reason`` (reason may be None) for the ids no marker could be written for."""
    error = "revise_stalled"
    updates = {
        "error": error,
        "needs_attention": True,
        "needs_attention_reason": f"Agent failed: {error}",
    }
    failures = {}
    for rid in ids:
        if not _mark_review_or_stub(rid, updates, pipeline_type, "revise", error, failures):
            failures.setdefault(rid, None)
    return error, failures


def _mark_split_not_attempted(ids, pipeline_type, reason):
    """Split escalation: the non-retryable ``split_not_attempted:`` class submit.py already
    records for parents a split pass skipped (error_collect keeps it out of the retry batch,
    generate_run_report counts it failed, not split), plus the ``no-split`` status file that
    makes the split slot terminal and routes the parent to the safe R8 branch of
    split_collect if anything reads it. ``needs_attention`` is set because, unlike
    submit.py's own not-attempted path (which exits before Phase 2), a parent escalated here
    is still ``status: Ready`` with no children and reaches Phase 2 as a regular item: the
    flag is what gets it the needs-attention label and comment instead of a silent
    label-only disposal. Nothing else is created or cleaned up: the agent may still be
    alive, so a cleanup here would race it.
    The review marker comes first and the status file only once it is on disk: a parent no
    marker could be written for stays in the wave (see _escalate_stuck), and a status file
    would make its slot terminal and let the next poll release it with no error recorded.
    Returns ``("split_not_attempted", failures)`` like _mark_revise_stalled."""
    error = f"split_not_attempted: {reason}"
    updates = {
        "error": error,
        "needs_attention": True,
        "needs_attention_reason": f"Agent failed: {error}",
    }
    reviews_dir = _TYPES.get(pipeline_type).dirs()["reviews"]
    failures = {}
    for rid in ids:
        if not _mark_review_or_stub(rid, updates, pipeline_type, "split", error, failures):
            failures.setdefault(rid, None)
            continue
        try:
            _write_yaml_file(
                f"{reviews_dir}/{rid}-split-status.yaml",
                {"status": "failed", "action": "no-split", "reason": error},
                sort_keys=False,
            )
        except OSError as exc:
            # The review marker is the record; the status file only serves a consumer that
            # re-reads the parent, so its loss is reported, not treated as an escalation failure.
            print(
                f"wait-for-wave: could not write {rid}'s no-split status file"
                f" ({type(exc).__name__}: {exc}); the review marker stands",
                file=sys.stderr,
            )
    return "split_not_attempted", failures


def _escalate_stuck(state, phase, config, wave_ids, stuck, idle, window):
    """Escalate ``stuck`` ids through the post-barrier failure contract and release their slots.

    fetch / assess / review-class waves get the registry error-stub review (the exact shape a
    verify_phase failure writes, with ``<phase base>_stalled`` naming the agent that died: the
    stuck poll phase minus the type's ``poll_prefix``, so ``assess_stalled`` for ``assess`` and
    ``initiative-assess`` alike); revise and split waves get the truthful terminal marking their
    consumers already handle.
    The ids whose marker is on disk are then removed from the wave file and from the phase's
    ids file, exactly as verify_phase drops a failed id: the barrier releases, next-action
    does not re-dispatch them, post_verify runs on the remaining ids and ERROR_COLLECT picks
    the error up at BATCH_DONE. An id no marker could be written for is left in both files:
    retiring it would make it vanish from every consumer (collect_recommendations --errors,
    error_collect, the run report) with no trace, so the caller reports it and exits 1
    instead. No assess result, dimension file or fetch output is ever fabricated.
    Returns ``(marker, unrecorded)``: a short description of the marker for the operator
    line, and ``id -> reason`` (reason may be None) for the ids left in place.
    """
    import verify_phase

    pipeline_type = state.get("type", "rfe")
    prefix = PIPELINE_TYPES[pipeline_type]["poll_prefix"]
    base = config["poll_phase"][len(prefix) :]
    ids = list(stuck)
    if base == "split":
        marker, unrecorded = _mark_split_not_attempted(
            ids, pipeline_type, _stall_reason(phase, idle, window)
        )
        marker += " error + no-split status file"
    elif base == "revise":
        marker, unrecorded = _mark_revise_stalled(ids, pipeline_type)
        marker += " error (auto_revised untouched)"
    else:
        errors, unrecorded = [], {}
        for rid in ids:
            stuck_base = stuck[rid][0][len(prefix) :]  # the first agent that never finished
            unwritten = verify_phase.write_error_stubs(
                stuck_base, [rid], pipeline_type, outcome="stalled", failures=unrecorded
            )
            if rid in (unwritten or []):
                unrecorded.setdefault(rid, None)
            else:
                errors.append(f"{stuck_base}_stalled")
        marker = "+".join(sorted(set(errors))) + " error-stub review"
    recorded = {i for i in ids if i not in unrecorded}
    _write_ids(WAVE_IDS_FILE, [i for i in wave_ids if i not in recorded])
    ids_file = config.get("ids_file")
    dropped_from = "the wave"
    if ids_file:
        _write_ids(ids_file, [i for i in _read_ids(ids_file) if i not in recorded])
        dropped_from += f" and {ids_file}"
    return f"{marker}, removed from {dropped_from}", unrecorded


def _handle_stall(state, phase, config, poll_phases, wave_ids, policy, window, idle):
    """A wave made no progress for a full window: re-dispatch or escalate every stuck id.

    Under the retry policy a stuck id below the per (phase, id) cap is left pending with its
    counter bumped — the caller exits 0, the orchestrator runs next-action, and next-action
    re-derives a wave from the still-pending ids and launches them again. Ids at the cap, and
    every stuck id of an escalate-only phase, go through ``_escalate_stuck``. The progress
    tracker is cleared so the next barrier starts a fresh window. One stderr line reports it.
    """
    stuck = _stuck_ids(poll_phases, wave_ids)
    cap = _wave_retry_cap()
    counts = _read_stall_retries()
    retried, escalated = [], {}
    for rid, pending in stuck.items():
        key = f"{phase}:{rid}"
        if policy == RETRY and counts.get(key, 0) < cap:
            counts[key] = counts.get(key, 0) + 1
            retried.append((rid, counts[key]))
        else:
            escalated[rid] = pending
    if retried:
        _write_stall_retries(counts)
    marker, unrecorded = None, {}
    if escalated:
        marker, unrecorded = _escalate_stuck(
            state, phase, config, wave_ids, escalated, idle, window
        )
    _clear_wave_progress()

    parts = []
    if retried:
        parts.append(
            "re-dispatching " + ", ".join(f"{rid} (attempt {n}/{cap})" for rid, n in retried)
        )
    if escalated:
        why = f" (retry cap {cap} reached)" if policy == RETRY else ""
        recorded = [rid for rid in escalated if rid not in unrecorded]
        if recorded:
            parts.append(f"escalating {', '.join(recorded)}{why} -> {marker}")
        if unrecorded:
            parts.append(f"escalation FAILED for {', '.join(unrecorded)}{why} (see next line)")
    if not parts:
        parts.append("nothing pending any more")
    label = "retry" if policy == RETRY else "escalate-only"
    print(
        f"wait-for-wave: STALL in {phase} ({'+'.join(poll_phases)}): no wave slot reached a"
        f" terminal state for {int(idle)}s (window {window}s, policy {label}); " + "; ".join(parts),
        file=sys.stderr,
    )
    if unrecorded:
        # Loud, not silent: the id is still in the wave and its ids file (the files for the
        # recorded ids are already written and the tracker cleared), so nothing downstream
        # can mistake it for done or retired. Exit 1 stops the orchestrator's re-run loop.
        reasons = "; ".join(f"{rid}: {why}" for rid, why in unrecorded.items() if why)
        detail = f" ({reasons})" if reasons else ""
        left_in = "the wave" + (f" and {config['ids_file']}" if config.get("ids_file") else "")
        print(
            f"wait-for-wave: ESCALATION FAILED for {', '.join(unrecorded)}: no error marker"
            f" could be written{detail}; left in {left_in} - fix the reviews directory and"
            " re-run",
            file=sys.stderr,
        )
        sys.exit(1)


def cmd_wait_for_wave(args):
    """Block until all agents in the current wave complete.

    Zero-argument command. Reads phase and wave IDs from state files,
    builds the correct check_review_progress.py flags internally,
    and delegates. Exits 0 (done) or 3 (pending).

    Stall guard (docs/wave-stall-guard.md): each call is one bounded poll, so a
    subagent that silently dies would otherwise keep the barrier on exit 3 until
    the job timeout. The number of terminal (poll phase, id) slots is tracked
    across calls in tmp/pipeline-wave-progress.yaml and re-checked after the poll
    returns; the deadline resets whenever it grows. Once a wave has made no
    progress for PIPELINE_WAVE_STALL_SECS (default 900; escalate-only phases use
    twice that; 0 disables), the stuck ids are re-dispatched (retry-eligible
    phases, up to PIPELINE_WAVE_RETRY_CAP per phase and id, counted in
    tmp/pipeline-stall-retries.yaml) or escalated through the post-barrier
    failure contract (see WAVE_STALL_POLICY and _escalate_stuck), one stderr line
    says which, and the command exits 0 so the orchestrator runs next-action.
    When no error marker could be written for an escalated id, that id is left
    in the wave and its ids file, a ``wait-for-wave: ESCALATION FAILED`` line
    names it, and the command exits 1. Without a stall, the command's files,
    output and exit codes are unchanged.
    """
    if not os.path.exists(WAVE_IDS_FILE):
        print(
            f"wait-for-wave: no wave file found ({WAVE_IDS_FILE}). Run next-action first.",
            file=sys.stderr,
        )
        sys.exit(1)

    wave_ids = _read_ids(WAVE_IDS_FILE)
    if not wave_ids:
        print(
            "wait-for-wave: wave file is empty. All agents may already be complete.",
            file=sys.stderr,
        )
        # Empty wave = nothing to wait for
        return

    state = _load_state()
    phase = state["phase"]
    config = _get_config(state).get(phase, {"type": "noop"})

    poll_phase = config.get("poll_phase")
    if not poll_phase:
        print(f"wait-for-wave: phase {phase} has no poll_phase", file=sys.stderr)
        sys.exit(1)

    # Wave freshness (AISDLC-33): the in-process slot counts and the poll subprocess both
    # ignore assess/review/revise files older than this wave's launch.
    since = _read_wave_launch()
    import check_review_progress as crp

    crp.set_wave_launch(since)

    # Stall tracking. A phase missing from the policy table is treated as escalate-only (the
    # policy that never launches a second concurrent agent); the pin test keeps the table full.
    poll_phases = _wave_poll_phases(config)
    policy = WAVE_STALL_POLICY.get(phase, ESCALATE)
    window = _stall_window(policy)
    sig = f"{phase}|{','.join(sorted(wave_ids))}"
    if window:
        _track_wave_progress(sig, phase, _terminal_slots(poll_phases, wave_ids))

    # Build check_review_progress.py command
    cmd_parts = [
        sys.executable,
        os.path.join(os.path.dirname(__file__), "check_review_progress.py"),
        "--wait",
        "--max-wait",
        "90",
        "--phase",
        poll_phase,
    ]
    for p in config.get("parallel", []):
        if p.get("poll_phase"):
            cmd_parts.extend(["--also-phase", p["poll_phase"]])
    if not state.get("headless", True):
        cmd_parts.append("--fast-poll")
    if since is not None:
        cmd_parts.extend(["--since", f"{since:.3f}"])
    cmd_parts.extend(["--id-file", WAVE_IDS_FILE])

    result = subprocess.run(cmd_parts)
    if result.returncode == 0:
        if window:
            _clear_wave_progress()
        return
    if result.returncode == 3:
        if window:
            # The poll blocked for up to 90s; count again before judging the wave stalled.
            prog = _track_wave_progress(sig, phase, _terminal_slots(poll_phases, wave_ids))
            idle = _now() - prog["last_progress_ts"]
            if idle >= window:
                _handle_stall(state, phase, config, poll_phases, wave_ids, policy, window, idle)
                return
        print("Re-run: python3 scripts/pipeline_state.py wait-for-wave")
        sys.exit(3)
    # Unexpected exit code
    print(
        f"wait-for-wave: check_review_progress.py exited with code {result.returncode}",
        file=sys.stderr,
    )
    sys.exit(result.returncode)


def _check_agent_phase_complete(config, phase=None):
    """Return True if all agents for an agent phase are complete (files older than the
    phase's entry do not count, see PHASE_ENTRY_FILE)."""
    ids_file = config.get("ids_file")
    poll_phase = config.get("poll_phase")
    if not ids_file or not poll_phase:
        return True
    ids = _read_ids(ids_file)
    if not ids:
        return True
    import check_review_progress as crp
    from check_review_progress import check_id

    crp.set_wave_launch(_read_phase_entry(phase) if phase else None)

    phases_to_check = [poll_phase]
    for p in config.get("parallel", []):
        if p.get("poll_phase"):
            phases_to_check.append(p["poll_phase"])
    for phase in phases_to_check:
        for rfe_id in ids:
            if check_id(phase, rfe_id) == "pending":
                return False
    return True


def cmd_advance(args):
    dry_run = "--dry-run" in args
    state = _load_state()
    phase = state["phase"]
    config = _get_config(state).get(phase, {"type": "noop"})
    phase_type = config.get("type", "noop")
    # Guard: script phases must be dispatched via run-phase first
    if phase_type == "script" and not dry_run:
        if not os.path.exists(DISPATCH_MARKER):
            print(
                f"advance: script phase {phase} was not dispatched."
                " Run: python3 scripts/pipeline_state.py next-action",
                file=sys.stderr,
            )
            sys.exit(1)
        with open(DISPATCH_MARKER) as f:
            marker_phase = f.read().strip()
        os.remove(DISPATCH_MARKER)
        if marker_phase != phase:
            print(
                f"advance: dispatch marker is for {marker_phase}, not current phase {phase}",
                file=sys.stderr,
            )
            sys.exit(1)
    # Guard: agent phases must have all agents complete before advancing
    if phase_type == "agent" and not dry_run:
        if not _check_agent_phase_complete(config, phase):
            config.get("poll_phase", "")
            config.get("ids_file", "")
            also = ""
            for p in config.get("parallel", []):
                if p.get("poll_phase"):
                    also += f" --also-phase {p['poll_phase']}"
            print(
                f"advance: agent phase {phase} has pending agents."
                f" Run: python3 scripts/pipeline_state.py"
                f" wait-for-wave",
                file=sys.stderr,
            )
            sys.exit(1)
    next_phase, summary = advance(state, dry_run=dry_run)
    if not dry_run:
        _enter_phase(state, next_phase)
    print(summary)


def cmd_set(args):
    if not args:
        print("Usage: set key=value ...", file=sys.stderr)
        sys.exit(1)
    state = _load_state()
    for arg in args:
        if "=" not in arg:
            print(f"Invalid key=value: {arg}", file=sys.stderr)
            sys.exit(1)
        k, v = arg.split("=", 1)
        # Auto-convert numeric and boolean values
        if v.isdigit():
            v = int(v)
        elif v.lower() in ("true", "false"):
            v = v.lower() == "true"
        state[k] = v
    _save_state(state)


def cmd_get(args):
    if not args:
        print("Usage: get <key>", file=sys.stderr)
        sys.exit(1)
    state = _load_state()
    val = state.get(args[0])
    if val is None:
        sys.exit(1)
    print(val)


def cmd_status(args):
    state = _load_state()
    print(yaml.dump(state, default_flow_style=False, sort_keys=False), end="")


def cmd_diagnose(args):
    """Cross-reference state with disk artifacts for debugging."""
    state = _load_state()
    phase = state["phase"]
    print(f"Phase: {phase}")
    print(f"Batch: {state.get('batch', 0)}/{state.get('total_batches', 0)}")
    print(f"Reassess cycle: {state.get('reassess_cycle', 0)}/2")
    print(f"Correction cycle: {state.get('correction_cycle', 0)}/1")
    print(f"Retry cycle: {state.get('retry_cycle', 0)}/1")

    # Check ID files
    id_files = [
        "tmp/pipeline-all-ids.txt",
        "tmp/pipeline-active-ids.txt",
        "tmp/pipeline-revise-ids.txt",
        "tmp/pipeline-reassess-ids.txt",
        "tmp/pipeline-split-ids.txt",
        "tmp/pipeline-split-children-ids.txt",
        "tmp/pipeline-retry-ids.txt",
    ]
    print("\nID files:")
    for f in id_files:
        if os.path.exists(f):
            ids = _read_ids(f)
            print(f"  {f}: {len(ids)} IDs")
        else:
            print(f"  {f}: (missing)")

    # Check for retry errors
    retry_err = "tmp/pipeline-retry-errors.yaml"
    if os.path.exists(retry_err):
        with open(retry_err) as fh:
            data = yaml.safe_load(fh) or {}
        print(f"\nRetry errors: {len(data)} IDs")

    # Check active IDs against artifacts
    active = _read_ids("tmp/pipeline-active-ids.txt")
    t = PIPELINE_TYPES.get(state.get("type", "rfe"), PIPELINE_TYPES["rfe"])
    if active:
        missing_task = []
        missing_review = []
        error_ids = []
        for rfe_id in active:
            if not os.path.exists(f"{t['tasks_dir']}/{rfe_id}.md"):
                missing_task.append(rfe_id)
            review = f"{t['reviews_dir']}/{rfe_id}-review.md"
            if os.path.exists(review):
                try:
                    from artifact_utils import read_frontmatter

                    data, _ = read_frontmatter(review)
                    if data.get("error"):
                        error_ids.append(rfe_id)
                except Exception:
                    pass
            else:
                missing_review.append(rfe_id)
        print(f"\nActive IDs: {len(active)}")
        if missing_task:
            print(f"  Missing task files: {', '.join(missing_task)}")
        if missing_review:
            print(f"  Missing review files: {', '.join(missing_review)}")
        if error_ids:
            print(f"  Error IDs: {', '.join(error_ids)}")


DISPATCH_LOOP = """\
Resume the dispatch loop:
  1. python3 scripts/pipeline_state.py next-action
  2. If action == done: exit loop, run teardown
  3. If action == run_script: python3 scripts/pipeline_state.py run-phase, then go to 1
  4. If action == launch_wave:
     a. For each agent in agents: launch background Agent(prompt=vars \
+ "\\n\\nRead " + prompt_file + " and follow all instructions exactly.", \
subagent_type if present)
     b. python3 scripts/pipeline_state.py wait-for-wave \
(re-run on exit 3), then go to 1"""


def cmd_dispatch_context(args):
    """Print current phase + dispatch instructions for post-compaction recovery."""
    if not os.path.exists(STATE_FILE):
        return  # Not in a pipeline run — nothing to inject
    state = _load_state()
    phase = state["phase"]
    t = PIPELINE_TYPES.get(state.get("type", "rfe"), PIPELINE_TYPES["rfe"])
    # INIT is a setup marker, not a dispatchable phase
    if phase not in PHASES:
        print(f"[PIPELINE STATE RECOVERY] Setup in progress (phase: {phase})")
        print(
            "Setup is not yet complete. Re-read SKILL.md"
            f" ({t['dispatch_skill']}) and resume"
            " the setup steps from where you left off."
        )
        return
    # DONE is terminal — nothing to dispatch
    if phase == "DONE":
        print("[PIPELINE STATE RECOVERY] Pipeline complete (phase: DONE)")
        return
    config = _get_config(state).get(phase, {"type": "noop"})
    phase_type = config.get("type", "noop")
    print(f"[PIPELINE STATE RECOVERY] Current phase: {phase} (type: {phase_type})")
    print(f"Batch: {state.get('batch', 0)}/{state.get('total_batches', 0)}")
    print()
    print(DISPATCH_LOOP)


def cmd_post_compact_hook(args):
    """Entry point for SessionStart compact hook — guarded by env var."""
    if not os.environ.get("RFE_CREATOR_ENABLE_CONTEXT_HOOK"):
        return
    cmd_dispatch_context(args)


COMMANDS = {
    "init": cmd_init,
    "get-phase": cmd_get_phase,
    "set-phase": cmd_set_phase,
    "get-phase-config": cmd_get_phase_config,
    "run-phase": cmd_run_phase,
    "set-wave": cmd_set_wave,
    "next-action": cmd_next_action,
    "wait-for-wave": cmd_wait_for_wave,
    "advance": cmd_advance,
    "set": cmd_set,
    "get": cmd_get,
    "status": cmd_status,
    "diagnose": cmd_diagnose,
    "dispatch-context": cmd_dispatch_context,
    "post-compact-hook": cmd_post_compact_hook,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Commands: {', '.join(COMMANDS)}", file=sys.stderr)
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])
