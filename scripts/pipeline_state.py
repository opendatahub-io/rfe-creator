#!/usr/bin/env python3
"""Pipeline state machine for the thin dispatcher.

Phase tracking, config, and transition logic for rfe.auto-fix.

Usage:
    python3 scripts/pipeline_state.py init [--batch-size N] [--headless]
    python3 scripts/pipeline_state.py get-phase
    python3 scripts/pipeline_state.py set-phase <PHASE>
    python3 scripts/pipeline_state.py get-phase-config
    python3 scripts/pipeline_state.py run-phase
    python3 scripts/pipeline_state.py advance [--dry-run]
    python3 scripts/pipeline_state.py set key=value ...
    python3 scripts/pipeline_state.py get <key>
    python3 scripts/pipeline_state.py status
    python3 scripts/pipeline_state.py diagnose
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import yaml

STATE_FILE = "tmp/pipeline-state.yaml"
DISPATCH_MARKER = "tmp/.dispatch-marker"

# ---------- Phase enum ----------

PHASES = [
    "BATCH_START", "FETCH", "SETUP", "ASSESS", "REVIEW", "REVISE", "FIXUP",
    "REASSESS_CHECK", "REASSESS_SAVE", "REASSESS_ASSESS", "REASSESS_REVIEW",
    "REASSESS_RESTORE", "REASSESS_REVISE", "REASSESS_FIXUP",
    "COLLECT", "SPLIT", "SPLIT_COLLECT",
    "SPLIT_PIPELINE_START", "SPLIT_ASSESS", "SPLIT_REVIEW",
    "SPLIT_REVISE", "SPLIT_FIXUP",
    "SPLIT_SAVE", "SPLIT_REASSESS", "SPLIT_RE_REVIEW", "SPLIT_RESTORE",
    "SPLIT_CORRECTION_CHECK",
    "BATCH_DONE", "ERROR_COLLECT",
    "REPORT", "DONE",
]

# ---------- Phase config ----------

PHASE_CONFIG = {
    "BATCH_START": {"type": "noop"},
    "FETCH": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/fetch-agent.md",
        "ids_file": "tmp/pipeline-active-ids.txt",
        "poll_phase": "fetch",
        "post_verify": "python3 scripts/verify_phase.py --phase fetch"
                       " --ids-file tmp/pipeline-active-ids.txt",
        "vars": {"KEY": "{ID}"},
    },
    "SETUP": {
        "type": "script",
        "command": ("bash scripts/bootstrap-assess-rfe.sh &"
                    " bash scripts/fetch-architecture-context.sh & wait"),
    },
    "ASSESS": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/assess-agent.md",
        "ids_file": "tmp/pipeline-active-ids.txt",
        "subagent_type": "rfe-scorer",
        "poll_phase": "assess",
        "parallel": [
            {"prompt": ".claude/skills/rfe-feasibility-review/SKILL.md",
             "poll_phase": "feasibility",
             "vars": {"ID": "{ID}"}},
        ],
        "pre_script": "python3 scripts/prep_assess.py {ID}",
        "post_verify": "python3 scripts/verify_phase.py --phase assess"
                       " --ids-file tmp/pipeline-active-ids.txt",
        "vars": {
            "DATA_FILE": "/tmp/rfe-assess/single/{ID}.md",
            "RUN_DIR": "/tmp/rfe-assess/single",
            "PROMPT_PATH": ".context/assess-rfe/scripts/agent_prompt.md",
        },
    },
    "REVIEW": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/review-agent.md",
        "ids_file": "tmp/pipeline-active-ids.txt",
        "poll_phase": "review",
        "post_verify": "python3 scripts/verify_phase.py --phase review"
                       " --ids-file tmp/pipeline-active-ids.txt",
        "vars": {
            "FIRST_PASS": "true",
            "ID": "{ID}",
            "ASSESS_PATH": "/tmp/rfe-assess/single/{ID}.result.md",
            "FEASIBILITY_PATH":
                "artifacts/rfe-reviews/{ID}-feasibility.md",
        },
    },
    "REVISE": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/revise-agent.md",
        "ids_file": "tmp/pipeline-revise-ids.txt",
        "poll_phase": "revise",

        "vars": {"ID": "{ID}"},
    },
    "FIXUP": {
        "type": "script",
        "command": "python3 scripts/check_revised.py --batch",
        "ids_file": "tmp/pipeline-revise-ids.txt",
    },

    # --- Reassess loop ---
    "REASSESS_CHECK": {"type": "noop"},
    "REASSESS_SAVE": {
        "type": "script",
        "command": "python3 scripts/reassess_save.py",
        "ids_file": "tmp/pipeline-reassess-ids.txt",
    },
    "REASSESS_ASSESS": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/assess-agent.md",
        "ids_file": "tmp/pipeline-reassess-ids.txt",
        "subagent_type": "rfe-scorer",
        "poll_phase": "assess",
        "pre_script": "python3 scripts/prep_assess.py {ID}",
        # NO "parallel" — feasibility NOT re-checked (invariant 4.2/5.4)
        "post_verify": "python3 scripts/verify_phase.py --phase assess"
                       " --ids-file tmp/pipeline-reassess-ids.txt",
        "vars": {
            "DATA_FILE": "/tmp/rfe-assess/single/{ID}.md",
            "RUN_DIR": "/tmp/rfe-assess/single",
            "PROMPT_PATH": ".context/assess-rfe/scripts/agent_prompt.md",
        },
    },
    "REASSESS_REVIEW": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/review-agent.md",
        "ids_file": "tmp/pipeline-reassess-ids.txt",
        "poll_phase": "review",
        "post_verify": "python3 scripts/verify_phase.py --phase review"
                       " --ids-file tmp/pipeline-reassess-ids.txt",
        "vars": {
            "FIRST_PASS": "false",
            "ID": "{ID}",
            "ASSESS_PATH": "/tmp/rfe-assess/single/{ID}.result.md",
            "FEASIBILITY_PATH":
                "artifacts/rfe-reviews/{ID}-feasibility.md",
        },
    },
    "REASSESS_RESTORE": {
        "type": "script",
        "command": "python3 scripts/preserve_review_state.py restore",
        "ids_file": "tmp/pipeline-reassess-ids.txt",
    },
    "REASSESS_REVISE": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/revise-agent.md",
        "ids_file": "tmp/pipeline-revise-ids.txt",
        "poll_phase": "revise",

        "vars": {"ID": "{ID}"},
    },
    "REASSESS_FIXUP": {
        "type": "script",
        "command": "python3 scripts/check_revised.py --batch",
        "ids_file": "tmp/pipeline-revise-ids.txt",
    },

    # --- Collect + Split ---
    "COLLECT": {"type": "noop"},
    "SPLIT": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.split/prompts/split-agent.md",
        "ids_file": "tmp/pipeline-split-ids.txt",
        "poll_phase": "split",
        "vars": {
            "ID": "{ID}",
            "TASK_FILE": "artifacts/rfe-tasks/{ID}.md",
            "REVIEW_FILE": "artifacts/rfe-reviews/{ID}-review.md",
        },
    },
    "SPLIT_COLLECT": {
        "type": "script",
        "command": "python3 scripts/split_collect.py",
        "ids_file": "tmp/pipeline-split-ids.txt",
    },
    "SPLIT_PIPELINE_START": {"type": "noop"},
    "SPLIT_ASSESS": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/assess-agent.md",
        "ids_file": "tmp/pipeline-split-children-ids.txt",
        "subagent_type": "rfe-scorer",
        "poll_phase": "assess",
        "pre_script": "python3 scripts/prep_assess.py {ID}",
        "parallel": [
            {"prompt": ".claude/skills/rfe-feasibility-review/SKILL.md",
             "poll_phase": "feasibility",
             "vars": {"ID": "{ID}"}},
        ],
        "post_verify": "python3 scripts/verify_phase.py --phase assess"
                       " --ids-file tmp/pipeline-split-children-ids.txt",
        "vars": {
            "DATA_FILE": "/tmp/rfe-assess/single/{ID}.md",
            "RUN_DIR": "/tmp/rfe-assess/single",
            "PROMPT_PATH": ".context/assess-rfe/scripts/agent_prompt.md",
        },
    },
    "SPLIT_REVIEW": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/review-agent.md",
        "ids_file": "tmp/pipeline-split-children-ids.txt",
        "poll_phase": "review",
        "post_verify": "python3 scripts/verify_phase.py --phase review"
                       " --ids-file tmp/pipeline-split-children-ids.txt",
        "vars": {
            "FIRST_PASS": "true",
            "ID": "{ID}",
            "ASSESS_PATH": "/tmp/rfe-assess/single/{ID}.result.md",
            "FEASIBILITY_PATH":
                "artifacts/rfe-reviews/{ID}-feasibility.md",
        },
    },
    "SPLIT_REVISE": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/revise-agent.md",
        "ids_file": "tmp/pipeline-revise-ids.txt",
        "poll_phase": "revise",

        "vars": {"ID": "{ID}"},
    },
    "SPLIT_FIXUP": {
        "type": "script",
        "command": "python3 scripts/check_revised.py --batch",
        "ids_file": "tmp/pipeline-revise-ids.txt",
    },
    "SPLIT_SAVE": {
        "type": "script",
        "command": "python3 scripts/preserve_review_state.py save",
        "ids_file": "tmp/pipeline-revise-ids.txt",
    },
    "SPLIT_REASSESS": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/assess-agent.md",
        "ids_file": "tmp/pipeline-revise-ids.txt",
        "subagent_type": "rfe-scorer",
        "poll_phase": "assess",
        "pre_script": "python3 scripts/prep_assess.py {ID}",
        "post_verify": "python3 scripts/verify_phase.py --phase assess"
                       " --ids-file tmp/pipeline-revise-ids.txt",
        "vars": {
            "DATA_FILE": "/tmp/rfe-assess/single/{ID}.md",
            "RUN_DIR": "/tmp/rfe-assess/single",
            "PROMPT_PATH": ".context/assess-rfe/scripts/agent_prompt.md",
        },
    },
    "SPLIT_RE_REVIEW": {
        "type": "agent",
        "prompt": ".claude/skills/rfe.review/prompts/review-agent.md",
        "ids_file": "tmp/pipeline-revise-ids.txt",
        "poll_phase": "review",
        "post_verify": "python3 scripts/verify_phase.py --phase review"
                       " --ids-file tmp/pipeline-revise-ids.txt",
        "vars": {
            "FIRST_PASS": "false",
            "ID": "{ID}",
            "ASSESS_PATH": "/tmp/rfe-assess/single/{ID}.result.md",
            "FEASIBILITY_PATH":
                "artifacts/rfe-reviews/{ID}-feasibility.md",
        },
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
        "command": "python3 scripts/error_collect.py",
    },

    # --- Terminal ---
    "REPORT": {
        "type": "script",
        "command": ("python3 scripts/generate_run_report.py"
                    " --start-time {start_time}"
                    " --batch-size {batch_size}"),
    },
}

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


def _read_ids(path):
    """Read IDs from a file, one per line."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


def _write_ids(path, ids):
    """Write IDs to a file, one per line."""
    os.makedirs(os.path.dirname(path) or "tmp", exist_ok=True)
    with open(path, "w") as f:
        for id_ in ids:
            f.write(f"{id_}\n")


def _copy_ids(src, dst):
    """Copy an ID file."""
    os.makedirs(os.path.dirname(dst) or "tmp", exist_ok=True)
    shutil.copy2(src, dst)


def _run_script(cmd):
    """Run a script and return stdout lines."""
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print("Script failed (exit code "
              f"{result.returncode})", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def _parse_line_ids(output, prefix):
    """Parse IDs from a KEY=ID1,ID2 output line."""
    for line in output.splitlines():
        if line.startswith(f"{prefix}="):
            val = line.split("=", 1)[1].strip()
            if not val:
                return []
            return [x.strip() for x in val.split(",") if x.strip()]
    return []


# ---------- Transition logic ----------

MAIN_SEQUENCE = ["FETCH", "SETUP", "ASSESS", "REVIEW", "REVISE", "FIXUP"]
REASSESS_SEQUENCE = [
    "REASSESS_SAVE", "REASSESS_ASSESS", "REASSESS_REVIEW",
    "REASSESS_RESTORE", "REASSESS_REVISE", "REASSESS_FIXUP",
]
SPLIT_SEQUENCE = [
    "SPLIT_PIPELINE_START", "SPLIT_ASSESS", "SPLIT_REVIEW",
    "SPLIT_REVISE", "SPLIT_FIXUP",
    "SPLIT_SAVE", "SPLIT_REASSESS", "SPLIT_RE_REVIEW", "SPLIT_RESTORE",
    "SPLIT_CORRECTION_CHECK",
]


def advance(state, dry_run=False):
    """Compute and apply the next phase transition.

    Returns (next_phase, summary_line).
    """
    phase = state["phase"]

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
            out = _run_script(
                f"python3 scripts/filter_for_revision.py"
                f" {' '.join(active_ids)}")
            revise_ids = out.split() if out else []
            _write_ids("tmp/pipeline-revise-ids.txt", revise_ids)
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
                    f"python3 scripts/filter_for_revision.py"
                    f" {' '.join(reassess_ids)}")
                revise_ids = out.split() if out else []
                _write_ids("tmp/pipeline-revise-ids.txt", revise_ids)
        return "REASSESS_REVISE", "REASSESS_RESTORE → REASSESS_REVISE"

    if phase == "SPLIT_REVIEW":
        if not dry_run:
            child_ids = _read_ids("tmp/pipeline-split-children-ids.txt")
            out = _run_script(
                f"python3 scripts/filter_for_revision.py"
                f" {' '.join(child_ids)}")
            revise_ids = out.split() if out else []
            _write_ids("tmp/pipeline-revise-ids.txt", revise_ids)
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
        out = _run_script(
            f"python3 scripts/collect_recommendations.py --reassess"
            f" {' '.join(active_ids)}")
        reassess_ids = _parse_line_ids(out, "REASSESS")
        cycle = state.get("reassess_cycle", 0)
        if reassess_ids and cycle < 2:
            if not dry_run:
                state["reassess_cycle"] = cycle + 1
                _write_ids("tmp/pipeline-reassess-ids.txt", reassess_ids)
            return ("REASSESS_SAVE",
                    f"REASSESS_CHECK → REASSESS_SAVE:"
                    f" reassess={len(reassess_ids)} cycle={cycle + 1}/2")
        return "COLLECT", "REASSESS_CHECK → COLLECT: no reassess needed"

    # --- REASSESS_FIXUP loops back ---
    if phase == "REASSESS_FIXUP":
        return "REASSESS_CHECK", "REASSESS_FIXUP → REASSESS_CHECK"

    # --- COLLECT decision ---
    if phase == "COLLECT":
        active_ids = _read_ids("tmp/pipeline-active-ids.txt")
        out = _run_script(
            f"python3 scripts/collect_recommendations.py"
            f" {' '.join(active_ids)}")
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
            return ("SPLIT",
                    f"COLLECT complete: {stats}\nCOLLECT → SPLIT")
        return "BATCH_DONE", f"COLLECT complete: {stats}\nCOLLECT → BATCH_DONE"

    # --- SPLIT → SPLIT_COLLECT ---
    if phase == "SPLIT":
        return "SPLIT_COLLECT", "SPLIT → SPLIT_COLLECT"

    # --- SPLIT_COLLECT decision ---
    if phase == "SPLIT_COLLECT":
        child_ids = _read_ids("tmp/pipeline-split-children-ids.txt")
        if child_ids:
            return ("SPLIT_PIPELINE_START",
                    f"SPLIT_COLLECT → SPLIT_PIPELINE_START:"
                    f" children={len(child_ids)}")
        return ("BATCH_DONE",
                "SPLIT_COLLECT → BATCH_DONE: no children")

    # --- SPLIT_CORRECTION_CHECK ---
    if phase == "SPLIT_CORRECTION_CHECK":
        child_ids = _read_ids("tmp/pipeline-split-children-ids.txt")
        if child_ids:
            out = _run_script(
                f"python3 scripts/check_right_sized.py"
                f" {' '.join(child_ids)}")
            undersized = out.split("RESPLIT=")[1].split() \
                if "RESPLIT=" in out else []
        else:
            undersized = []
        cycle = state.get("correction_cycle", 0)
        if undersized and cycle < 1:
            if not dry_run:
                state["correction_cycle"] = cycle + 1
                _write_ids("tmp/pipeline-split-ids.txt", undersized)
            return ("SPLIT",
                    f"SPLIT_CORRECTION_CHECK → SPLIT:"
                    f" undersized={len(undersized)} correction={cycle + 1}/1")
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
                    f"python3 scripts/batch_summary.py --counts-only"
                    f" {' '.join(active_ids)}")
                batch_stats = out.strip()
            except Exception:
                pass
        prefix = "Retry batch" if retry > 0 else "Batch"
        summary = f"{prefix} {batch}/{total} complete: {batch_stats}"
        if batch < total:
            return ("BATCH_START",
                    f"{summary}\nBATCH_DONE → BATCH_START")
        if retry < 1:
            all_ids = _read_ids("tmp/pipeline-all-ids.txt")
            if all_ids:
                out = _run_script(
                    f"python3 scripts/collect_recommendations.py --errors"
                    f" {' '.join(all_ids)}")
                error_ids = _parse_line_ids(out, "ERRORS")
                if error_ids:
                    return ("ERROR_COLLECT",
                            f"{summary}\nBATCH_DONE → ERROR_COLLECT:"
                            f" errors={len(error_ids)}")
        return "REPORT", f"{summary}\nBATCH_DONE → REPORT"

    # --- ERROR_COLLECT → BATCH_START ---
    if phase == "ERROR_COLLECT":
        retry_ids = _read_ids("tmp/pipeline-retry-ids.txt")
        n = len(retry_ids)
        batch = state.get("total_batches", 0)
        return ("BATCH_START",
                f"ERROR_COLLECT: retry batch {batch} with {n} error IDs\n"
                f"ERROR_COLLECT → BATCH_START")

    # --- REPORT → DONE (with optional announce) ---
    if phase == "REPORT":
        if not dry_run and state.get("announce_complete"):
            _run_script("python3 scripts/finish.py")
        return "DONE", "REPORT → DONE"

    print(f"No transition defined for phase: {phase}", file=sys.stderr)
    sys.exit(1)


# ---------- CLI commands ----------


def cmd_init(args):
    parser = argparse.ArgumentParser(prog="pipeline_state.py init")
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
        "batch": 0,
        "total_batches": 0,
        "headless": opts.headless,
        "announce_complete": opts.announce_complete,
        "batch_size": opts.batch_size,
        "start_time": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "reassess_cycle": 0,
        "correction_cycle": 0,
        "retry_cycle": 0,
    }
    _save_state(state)
    print(f"Initialized pipeline state: batch_size={opts.batch_size}")


def cmd_get_phase(args):
    state = _load_state()
    print(state["phase"])


def cmd_set_phase(args):
    if not args or args[0] not in PHASES:
        print(f"Usage: set-phase <PHASE>\nValid phases: {', '.join(PHASES)}",
              file=sys.stderr)
        sys.exit(1)
    state = _load_state()
    state["phase"] = args[0]
    _save_state(state)
    print(args[0])


def cmd_get_phase_config(args):
    state = _load_state()
    phase = state["phase"]
    config = dict(PHASE_CONFIG.get(phase, {"type": "noop"}))
    config["phase"] = phase
    config.pop("command", None)
    if config.get("type") == "script":
        config.pop("ids_file", None)
    if config.get("type") == "agent":
        max_concurrent = int(state.get("batch_size", 50))
        n_parallel = len(config.get("parallel", []))
        config["wave_size"] = max(1, max_concurrent // (1 + n_parallel))
    print(yaml.dump(config, default_flow_style=False, sort_keys=False),
          end="")


def cmd_run_phase(args):
    """Execute the current script phase's command internally.

    Loads state, resolves the command from PHASE_CONFIG, appends IDs
    from ids_file if configured, and runs the command. The orchestrator
    never sees the underlying script name.
    """
    state = _load_state()
    phase = state["phase"]
    config = PHASE_CONFIG.get(phase, {"type": "noop"})
    phase_type = config.get("type", "noop")
    if phase_type != "script":
        print(f"run-phase: phase {phase} is type '{phase_type}', not 'script'",
              file=sys.stderr)
        sys.exit(1)
    cmd = config["command"].format_map(state)
    if config.get("ids_file"):
        ids = _read_ids(config["ids_file"])
        if ids:
            cmd += " " + " ".join(ids)
        else:
            print(f"[run-phase] {phase}: no IDs, skipping")
            # Write dispatch marker and return — nothing to do
            with open(DISPATCH_MARKER, "w") as f:
                f.write(phase)
            return
    print(f"[run-phase] {phase}")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        sys.exit(result.returncode)
    # Write dispatch marker — advance checks this for script phases
    with open(DISPATCH_MARKER, "w") as f:
        f.write(phase)


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
    config = PHASE_CONFIG.get(phase, {"type": "noop"})
    phase_type = config.get("type", "noop")
    # Guard: script phases must be dispatched via run-phase first
    if phase_type == "script" and not dry_run:
        if not os.path.exists(DISPATCH_MARKER):
            print(f"advance: script phase {phase} was not dispatched."
                  " Run: python3 scripts/pipeline_state.py run-phase",
                  file=sys.stderr)
            sys.exit(1)
        with open(DISPATCH_MARKER) as f:
            marker_phase = f.read().strip()
        os.remove(DISPATCH_MARKER)
        if marker_phase != phase:
            print(f"advance: dispatch marker is for {marker_phase},"
                  f" not current phase {phase}", file=sys.stderr)
            sys.exit(1)
    # Guard: agent phases must have all agents complete before advancing
    if phase_type == "agent" and not dry_run:
        if not _check_agent_phase_complete(config):
            poll_phase = config.get("poll_phase", "")
            ids_file = config.get("ids_file", "")
            also = ""
            for p in config.get("parallel", []):
                if p.get("poll_phase"):
                    also += f" --also-phase {p['poll_phase']}"
            print(f"advance: agent phase {phase} has pending agents."
                  f" Run: python3 scripts/check_review_progress.py --poll"
                  f" --phase {poll_phase}{also}"
                  f" --id-file {ids_file}",
                  file=sys.stderr)
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
    print(yaml.dump(state, default_flow_style=False, sort_keys=False),
          end="")


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
    if active:
        missing_task = []
        missing_review = []
        error_ids = []
        for rfe_id in active:
            if not os.path.exists(f"artifacts/rfe-tasks/{rfe_id}.md"):
                missing_task.append(rfe_id)
            review = f"artifacts/rfe-reviews/{rfe_id}-review.md"
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


DISPATCH_PROTOCOL = {
    "noop": "No action needed. Run: python3 scripts/pipeline_state.py advance",
    "script": (
        "Run: python3 scripts/pipeline_state.py run-phase"
        " Then run: python3 scripts/pipeline_state.py advance"
    ),
    "agent": (
        "1. Read IDs from ids_file."
        " 2. Pre-filter: python3 scripts/check_review_progress.py"
        " --phase <poll_phase> <IDs> — remove COMPLETED IDs."
        " 3. Process IDs in waves of wave_size (from config output)."
        " For each ID: replace {ID} in vars to build KEY=VALUE lines,"
        " run pre_script if set, launch background Agent with prompt:"
        ' "<vars>\\n\\nRead <prompt> and follow all instructions exactly."'
        " If parallel entries exist, launch one additional Agent per entry"
        " using the entry's own vars (not the parent's) with {ID} replaced."
        " 4. Poll: python3 scripts/check_review_progress.py --poll"
        " --phase <poll_phase> [--also-phase <p> for each parallel"
        " entry's poll_phase] [--fast-poll if not headless]"
        " --id-file <ids_file>"
        " — exit 0 means complete, exit 3 means pending."
        " On exit 3, re-run step 4's poll command until exit 0."
        " 5. After all waves: run post_verify if set."
        " 6. Run: python3 scripts/pipeline_state.py advance"
    ),
}


def cmd_dispatch_context(args):
    """Print current phase + dispatch instructions for post-compaction recovery."""
    if not os.path.exists(STATE_FILE):
        return  # Not in a pipeline run — nothing to inject
    state = _load_state()
    phase = state["phase"]
    # INIT is a setup marker, not a dispatchable phase
    if phase not in PHASES:
        print(f"[PIPELINE STATE RECOVERY] Setup in progress (phase: {phase})")
        print("Setup is not yet complete. Re-read SKILL.md"
              " (.claude/skills/rfe.auto-fix/SKILL.md) and resume"
              " the setup steps from where you left off.")
        return
    # DONE is terminal — nothing to dispatch
    if phase == "DONE":
        print("[PIPELINE STATE RECOVERY] Pipeline complete (phase: DONE)")
        return
    config = PHASE_CONFIG.get(phase, {"type": "noop"})
    phase_type = config.get("type", "noop")
    protocol = DISPATCH_PROTOCOL.get(phase_type, "")
    print(f"[PIPELINE STATE RECOVERY] Current phase: {phase} (type: {phase_type})")
    print(f"Batch: {state.get('batch', 0)}/{state.get('total_batches', 0)}")
    if config.get("ids_file") and phase_type != "script":
        print(f"IDs file: {config['ids_file']}")
    if config.get("poll_phase"):
        print(f"Poll phase: {config['poll_phase']}")
    print(f"Dispatch: {protocol}")
    print("IMPORTANT: Re-read SKILL.md (.claude/skills/rfe.auto-fix/SKILL.md)"
          " now before taking any action.")


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
