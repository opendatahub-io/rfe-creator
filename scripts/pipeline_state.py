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
    python3 scripts/pipeline_state.py set key=value ...
    python3 scripts/pipeline_state.py get <key>
    python3 scripts/pipeline_state.py status
    python3 scripts/pipeline_state.py diagnose
"""

import argparse
import glob
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import yaml

STATE_FILE = "tmp/pipeline-state.yaml"
WAVE_IDS_FILE = "tmp/pipeline-wave-ids.txt"
DISPATCH_MARKER = "tmp/.dispatch-marker"

MAX_NEXT_ACTION_ITERATIONS = 50


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

# ---------- Pipeline type config ----------

PIPELINE_TYPES = {
    "rfe": {
        "review_prompts": ".claude/skills/rfe.review/prompts",
        "split_prompt": ".claude/skills/rfe.split/prompts/split-agent.md",
        "scorer_type": "rfe-scorer",
        "rubric_path": ".context/assess-rfe/skills/assess-rfe/scripts/agent_prompt.md",
        "feasibility_skill": ".claude/skills/rfe-feasibility-review/SKILL.md",
        "alignment_skill": None,
        "tasks_dir": "artifacts/rfe-tasks",
        "reviews_dir": "artifacts/rfe-reviews",
        "dispatch_skill": ".claude/skills/rfe.auto-fix/SKILL.md",
        "poll_prefix": "",
    },
    "initiative": {
        "review_prompts": ".claude/skills/initiative-review/prompts",
        "split_prompt": ".claude/skills/initiative-split/prompts/split-agent.md",
        "scorer_type": "initiative-scorer",
        "rubric_path": ".context/assess-rfe/skills/assess-initiative/scripts/agent_prompt.md",
        "feasibility_skill": ".claude/skills/initiative-feasibility-review/SKILL.md",
        "alignment_skill": ".claude/skills/strategic-alignment-review/SKILL.md",
        "tasks_dir": "artifacts/initiatives",
        "reviews_dir": "artifacts/initiative-reviews",
        "dispatch_skill": ".claude/skills/initiative-auto-fix/SKILL.md",
        "poll_prefix": "initiative-",
    },
}


# ---------- Conditional parallel agent helpers ----------


def _has_rhaistrat_parent(rfe_id, state):
    """Check if an initiative has a RHAISTRAT parent_key in its frontmatter."""
    if state.get("type") != "initiative":
        return False
    path = os.path.join(PIPELINE_TYPES["initiative"]["tasks_dir"], f"{rfe_id}.md")
    if not os.path.exists(path):
        return False
    with open(path) as f:
        content = f.read()
    parts = content.split("---", 2)
    if len(parts) < 3:
        return False
    try:
        fm = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return False
    pk = (fm or {}).get("parent_key", "")
    return bool(pk and pk.startswith("RHAISTRAT-"))


def _check_condition(condition, rfe_id, state):
    """Evaluate a named condition for a specific ID."""
    if condition == "has_rhaistrat_parent":
        return _has_rhaistrat_parent(rfe_id, state)
    return True


def _write_poll_stub(poll_phase, rfe_id):
    """Write a stub completion file for a skipped conditional agent."""
    from check_review_progress import PHASE_CHECKS

    path = PHASE_CHECKS[poll_phase](rfe_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(
            "---\nresult: not_assessed\nreason: skipped by pipeline (no RHAISTRAT parent)\n---\n"
        )


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
        if t["alignment_skill"]:
            v["ALIGNMENT_PATH"] = f"{t['reviews_dir']}/{{ID}}-alignment.md"
        return v

    def _assess_parallel():
        parallel = [
            {
                "prompt": t["feasibility_skill"],
                "poll_phase": f"{pp}feasibility",
                "vars": {"ID": "{ID}"},
            },
        ]
        if t["alignment_skill"]:
            parallel.append(
                {
                    "prompt": t["alignment_skill"],
                    "poll_phase": f"{pp}alignment",
                    "vars": {"ID": "{ID}"},
                    "condition": "has_rhaistrat_parent",
                }
            )
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
            "command": "python3 scripts/preserve_review_state.py restore",
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
            # Deliberately the revise file, not the reassess file: REASSESS_RESTORE
            # narrows the reassess set through filter_for_revision into
            # tmp/pipeline-revise-ids.txt, REASSESS_REVISE revises exactly that
            # subset, and the fixup must check the same subset. Pinned by
            # tests/test_pipeline_state.py::TestReassessFixupIds.
            "ids_file": "tmp/pipeline-revise-ids.txt",
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
            "command": "python3 scripts/preserve_review_state.py restore",
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


def _load_state():
    """Load pipeline state from disk."""
    if not os.path.exists(STATE_FILE):
        print(f"State file not found: {STATE_FILE}", file=sys.stderr)
        sys.exit(1)
    with open(STATE_FILE) as f:
        return yaml.safe_load(f)


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
    originals_dir = tasks_dir.replace("rfe-tasks", "rfe-originals").replace(
        "initiatives", "initiative-originals"
    )
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
        return "REVISE", "REVIEW → REVISE"

    if phase == "REASSESS_RESTORE":
        if not dry_run:
            cycle = state.get("reassess_cycle", 0)
            if cycle >= 2:
                # Last cycle: skip revise to avoid unreviewed changes
                _write_ids("tmp/pipeline-revise-ids.txt", [])
            else:
                reassess_ids = _read_ids("tmp/pipeline-reassess-ids.txt")
                out = _run_script(
                    f"python3 scripts/filter_for_revision.py {' '.join(reassess_ids)}"
                )
                revise_ids = _ids_from_output(out, "filter_for_revision.py output")
                _write_ids("tmp/pipeline-revise-ids.txt", revise_ids)
                if revise_ids:
                    _save_originals(revise_ids, pipeline_type)
        return "REASSESS_REVISE", "REASSESS_RESTORE → REASSESS_REVISE"

    if phase == "SPLIT_REVIEW":
        if not dry_run:
            child_ids = _read_ids("tmp/pipeline-split-children-ids.txt")
            out = _run_script(f"python3 scripts/filter_for_revision.py {' '.join(child_ids)}")
            revise_ids = _ids_from_output(out, "filter_for_revision.py output")
            _write_ids("tmp/pipeline-revise-ids.txt", revise_ids)
            if revise_ids:
                _save_originals(revise_ids, pipeline_type)
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
            return ("SPLIT", f"COLLECT complete: {stats}\nCOLLECT → SPLIT")
        return "BATCH_DONE", f"COLLECT complete: {stats}\nCOLLECT → BATCH_DONE"

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
        return "REPORT", f"{summary}\nBATCH_DONE → REPORT"

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
            return ("REPORT", "ERROR_COLLECT: no retryable errors\nERROR_COLLECT → REPORT")
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
    parser.add_argument("--type", choices=["rfe", "initiative"], default="rfe")
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
    print(f"Initialized pipeline state: type={opts.type} batch_size={opts.batch_size}")


def cmd_get_phase(args):
    state = _load_state()
    print(state["phase"])


def cmd_set_phase(args):
    if not args or args[0] not in PHASES:
        print(f"Usage: set-phase <PHASE>\nValid phases: {', '.join(PHASES)}", file=sys.stderr)
        sys.exit(1)
    state = _load_state()
    state["phase"] = args[0]
    _save_state(state)
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
    print(f"Wave: {len(args)} IDs")


def cmd_next_action(args):
    """Compute and return the next action for the dispatch loop.

    Chains through noop phases and completed script phases internally,
    returning only when the LLM needs to act: launch_wave, run_script,
    or done.
    """
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
            state["phase"] = next_phase
            _save_state(state)
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
                    state["phase"] = next_phase
                    _save_state(state)
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

            # Pre-filter: keep only IDs where ANY phase is still pending
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
                state["phase"] = next_phase
                _save_state(state)
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

            # Write wave IDs
            _write_ids(WAVE_IDS_FILE, wave_ids)

            # Build agent entries
            agents = []
            for rfe_id in wave_ids:
                # Main agent
                entry = {}
                if config.get("subagent_type"):
                    entry["subagent_type"] = config["subagent_type"]
                entry["prompt_file"] = config["prompt"]
                # Build vars string
                var_lines = []
                for k, v in config.get("vars", {}).items():
                    var_lines.append(f"{k}={v.replace('{ID}', rfe_id)}")
                entry["vars"] = "\n".join(var_lines) + "\n"
                agents.append(entry)

                # Parallel agents
                for par in config.get("parallel", []):
                    cond = par.get("condition")
                    if cond and not _check_condition(cond, rfe_id, state):
                        if par.get("poll_phase"):
                            _write_poll_stub(par["poll_phase"], rfe_id)
                        continue
                    pentry = {}
                    if par.get("subagent_type"):
                        pentry["subagent_type"] = par["subagent_type"]
                    pentry["prompt_file"] = par["prompt"]
                    pvar_lines = []
                    for k, v in par.get("vars", {}).items():
                        pvar_lines.append(f"{k}={v.replace('{ID}', rfe_id)}")
                    pentry["vars"] = "\n".join(pvar_lines) + "\n"
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


def cmd_wait_for_wave(args):
    """Block until all agents in the current wave complete.

    Zero-argument command. Reads phase and wave IDs from state files,
    builds the correct check_review_progress.py flags internally,
    and delegates. Exits 0 (done) or 3 (pending).
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
    cmd_parts.extend(["--id-file", WAVE_IDS_FILE])

    result = subprocess.run(cmd_parts)
    if result.returncode == 0:
        return
    if result.returncode == 3:
        print("Re-run: python3 scripts/pipeline_state.py wait-for-wave")
        sys.exit(3)
    # Unexpected exit code
    print(
        f"wait-for-wave: check_review_progress.py exited with code {result.returncode}",
        file=sys.stderr,
    )
    sys.exit(result.returncode)


def _check_agent_phase_complete(config):
    """Return True if all agents for an agent phase are complete."""
    ids_file = config.get("ids_file")
    poll_phase = config.get("poll_phase")
    if not ids_file or not poll_phase:
        return True
    ids = _read_ids(ids_file)
    if not ids:
        return True
    from check_review_progress import check_id

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
        if not _check_agent_phase_complete(config):
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
        state["phase"] = next_phase
        _save_state(state)
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
