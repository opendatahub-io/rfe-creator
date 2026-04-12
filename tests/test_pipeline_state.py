#!/usr/bin/env python3
"""Tests for pipeline_state.py advance() transitions.

Focuses on complex decision points and the invariant that every
revision is followed by a review.
"""
import os
import subprocess
import sys
import textwrap

import pytest

# Import advance() and helpers directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pipeline_state as ps


@pytest.fixture
def tmp_dir(tmp_path, monkeypatch):
    """Run tests from a temp directory with isolated state."""
    monkeypatch.chdir(tmp_path)
    os.makedirs("tmp", exist_ok=True)
    os.makedirs("artifacts/rfe-reviews", exist_ok=True)
    os.makedirs("artifacts/rfe-tasks", exist_ok=True)
    return tmp_path


def write_ids(path, ids):
    os.makedirs(os.path.dirname(path) or "tmp", exist_ok=True)
    with open(path, "w") as f:
        for id_ in ids:
            f.write(f"{id_}\n")


def read_ids(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


def make_state(**overrides):
    base = {
        "phase": "INIT",
        "batch": 0,
        "total_batches": 1,
        "batch_size": 50,
        "reassess_cycle": 0,
        "correction_cycle": 0,
        "retry_cycle": 0,
        "headless": True,
        "announce_complete": False,
        "start_time": "2026-04-09T00:00:00Z",
    }
    base.update(overrides)
    return base


# ---------- Init ----------

class TestInit:
    def test_preserves_existing_id_files(self, tmp_dir):
        """init must not wipe existing files in tmp/ (required for --reprocess)."""
        write_ids("tmp/pipeline-all-ids.txt",
                  ["RHAIRFE-1001", "RHAIRFE-1002"])
        write_ids("tmp/pipeline-changed-ids.txt", ["RHAIRFE-1001"])
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            ps.cmd_init([])
        assert read_ids("tmp/pipeline-all-ids.txt") == [
            "RHAIRFE-1001", "RHAIRFE-1002"]
        assert read_ids("tmp/pipeline-changed-ids.txt") == ["RHAIRFE-1001"]

    def test_cleans_stale_batch_files(self, tmp_dir):
        """init removes pipeline-batch-*-ids.txt to prevent stale retry batches."""
        write_ids("tmp/pipeline-batch-1-ids.txt", ["RHAIRFE-1001"])
        write_ids("tmp/pipeline-batch-2-ids.txt", ["RHAIRFE-1002"])
        write_ids("tmp/pipeline-batch-retry-ids.txt", ["RHAIRFE-1003"])
        # Non-batch files should survive
        write_ids("tmp/pipeline-all-ids.txt", ["RHAIRFE-1001", "RHAIRFE-1002"])
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            ps.cmd_init([])
        assert not os.path.exists("tmp/pipeline-batch-1-ids.txt")
        assert not os.path.exists("tmp/pipeline-batch-2-ids.txt")
        assert not os.path.exists("tmp/pipeline-batch-retry-ids.txt")
        # pipeline-all-ids.txt must survive (needed for --reprocess)
        assert os.path.exists("tmp/pipeline-all-ids.txt")

    def test_cleans_stale_dispatch_marker(self, tmp_dir):
        """init removes dispatch marker from prior run."""
        os.makedirs("tmp", exist_ok=True)
        with open(ps.DISPATCH_MARKER, "w") as f:
            f.write("FIXUP")
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            ps.cmd_init([])
        assert not os.path.exists(ps.DISPATCH_MARKER)

    def test_resets_state_on_reinit(self, tmp_dir):
        """init resets pipeline state even if prior state exists."""
        ps._save_state(make_state(phase="COLLECT", batch=3,
                                  reassess_cycle=2))
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            ps.cmd_init(["--batch-size", "25"])
        state = ps._load_state()
        assert state["phase"] == "INIT"
        assert state["batch"] == 0
        assert state["reassess_cycle"] == 0
        assert state["batch_size"] == 25

    def test_advance_rejects_init_phase(self, tmp_dir):
        """advance() on INIT exits with error — INIT is not a dispatchable phase."""
        state = make_state(phase="INIT")
        with pytest.raises(SystemExit) as exc_info:
            ps.advance(state)
        assert exc_info.value.code == 1

    def test_dispatch_context_handles_init(self, tmp_dir):
        """dispatch-context during INIT says setup is in progress, not 'run advance'."""
        ps._save_state(make_state(phase="INIT"))
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_dispatch_context([])
        output = buf.getvalue()
        assert "Setup in progress" in output
        assert "SKILL.md" in output
        # Must NOT tell the LLM to run advance
        assert "advance" not in output

    def test_dispatch_context_handles_done(self, tmp_dir):
        """dispatch-context during DONE says pipeline complete, not 'run advance'."""
        ps._save_state(make_state(phase="DONE"))
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_dispatch_context([])
        output = buf.getvalue()
        assert "Pipeline complete" in output
        assert "DONE" in output
        # Must NOT tell the LLM to run advance
        assert "advance" not in output


# ---------- BATCH_START ----------

class TestBatchStart:
    def test_resets_counters(self, tmp_dir):
        write_ids("tmp/pipeline-batch-1-ids.txt", ["A", "B"])
        state = make_state(
            phase="BATCH_START", batch=0,
            reassess_cycle=2, correction_cycle=1)
        next_phase, _ = ps.advance(state)
        assert next_phase == "FETCH"
        assert state["reassess_cycle"] == 0
        assert state["correction_cycle"] == 0
        assert state["batch"] == 1

    def test_copies_batch_ids_to_active(self, tmp_dir):
        write_ids("tmp/pipeline-batch-1-ids.txt", ["X", "Y", "Z"])
        state = make_state(phase="BATCH_START", batch=0)
        ps.advance(state)
        assert read_ids("tmp/pipeline-active-ids.txt") == ["X", "Y", "Z"]


# ---------- Linear sequences ----------

class TestLinearSequences:
    def test_main_sequence(self, tmp_dir):
        """FETCH→SETUP→ASSESS follow linear sequence."""
        state = make_state(phase="FETCH")
        next_phase, _ = ps.advance(state)
        assert next_phase == "SETUP"
        state["phase"] = "SETUP"
        next_phase, _ = ps.advance(state)
        assert next_phase == "ASSESS"

    def test_reassess_sequence(self, tmp_dir):
        """REASSESS_SAVE→REASSESS_ASSESS→REASSESS_REVIEW is linear."""
        state = make_state(phase="REASSESS_SAVE")
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_ASSESS"
        state["phase"] = "REASSESS_ASSESS"
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_REVIEW"

    def test_split_sequence_includes_reassess(self, tmp_dir):
        """Split sequence includes SPLIT_SAVE..SPLIT_RESTORE after FIXUP."""
        state = make_state(phase="SPLIT_FIXUP")
        next_phase, _ = ps.advance(state)
        assert next_phase == "SPLIT_SAVE"
        state["phase"] = "SPLIT_SAVE"
        next_phase, _ = ps.advance(state)
        assert next_phase == "SPLIT_REASSESS"
        state["phase"] = "SPLIT_REASSESS"
        next_phase, _ = ps.advance(state)
        assert next_phase == "SPLIT_RE_REVIEW"
        state["phase"] = "SPLIT_RE_REVIEW"
        next_phase, _ = ps.advance(state)
        assert next_phase == "SPLIT_RESTORE"


# ---------- REASSESS loop ----------

class TestReassessLoop:
    def test_reassess_check_enters_loop(self, tmp_dir, monkeypatch):
        """REASSESS_CHECK enters reassess loop when IDs exist and cycle < 2."""
        write_ids("tmp/pipeline-active-ids.txt", ["A", "B"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "REASSESS=A,B\nDONE=")
        state = make_state(phase="REASSESS_CHECK", reassess_cycle=0)
        next_phase, summary = ps.advance(state)
        assert next_phase == "REASSESS_SAVE"
        assert state["reassess_cycle"] == 1
        assert "cycle=1/2" in summary

    def test_reassess_check_exits_at_max_cycle(self, tmp_dir, monkeypatch):
        """REASSESS_CHECK goes to COLLECT when cycle >= 2."""
        write_ids("tmp/pipeline-active-ids.txt", ["A"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "REASSESS=A\nDONE=")
        state = make_state(phase="REASSESS_CHECK", reassess_cycle=2)
        next_phase, _ = ps.advance(state)
        assert next_phase == "COLLECT"

    def test_reassess_check_exits_when_no_ids(self, tmp_dir, monkeypatch):
        """REASSESS_CHECK goes to COLLECT when no reassess IDs."""
        write_ids("tmp/pipeline-active-ids.txt", ["A"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "REASSESS=\nDONE=A")
        state = make_state(phase="REASSESS_CHECK", reassess_cycle=0)
        next_phase, _ = ps.advance(state)
        assert next_phase == "COLLECT"

    def test_reassess_fixup_loops_back(self, tmp_dir):
        """REASSESS_FIXUP always returns to REASSESS_CHECK."""
        state = make_state(phase="REASSESS_FIXUP")
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_CHECK"

    def test_last_cycle_skips_revise(self, tmp_dir, monkeypatch):
        """On cycle 2 (max), REASSESS_RESTORE writes empty revise IDs."""
        write_ids("tmp/pipeline-reassess-ids.txt", ["A", "B"])
        state = make_state(phase="REASSESS_RESTORE", reassess_cycle=2)
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_REVISE"
        assert read_ids("tmp/pipeline-revise-ids.txt") == []

    def test_non_last_cycle_filters_for_revision(self, tmp_dir, monkeypatch):
        """On cycle < 2, REASSESS_RESTORE runs filter and writes revise IDs."""
        write_ids("tmp/pipeline-reassess-ids.txt", ["A", "B"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "A")
        state = make_state(phase="REASSESS_RESTORE", reassess_cycle=1)
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_REVISE"
        assert read_ids("tmp/pipeline-revise-ids.txt") == ["A"]


class TestReassessFullCycle:
    """End-to-end reassess loop: every revision must be followed by a review."""

    def test_cycle1_revisions_are_reviewed_in_cycle2(self, tmp_dir, monkeypatch):
        """Trace: cycle 1 revises → cycle 2 reviews those revisions."""
        # Cycle 1: REASSESS_CHECK finds IDs, enters loop
        write_ids("tmp/pipeline-active-ids.txt", ["A", "B"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "REASSESS=A,B\nDONE=")
        state = make_state(phase="REASSESS_CHECK", reassess_cycle=0)
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_SAVE"
        assert state["reassess_cycle"] == 1

        # Walk through cycle 1 linear sequence
        state["phase"] = "REASSESS_SAVE"
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_ASSESS"  # re-score

        state["phase"] = "REASSESS_ASSESS"
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_REVIEW"  # re-review (scores original revisions)

        state["phase"] = "REASSESS_REVIEW"
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_RESTORE"

        # REASSESS_RESTORE: cycle=1 < 2, filters for revision
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "A")
        state["phase"] = "REASSESS_RESTORE"
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_REVISE"
        assert read_ids("tmp/pipeline-revise-ids.txt") == ["A"]  # A needs more work

        # REASSESS_REVISE → REASSESS_FIXUP → REASSESS_CHECK
        state["phase"] = "REASSESS_FIXUP"
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_CHECK"

        # Cycle 2: enters loop again
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "REASSESS=A\nDONE=B")
        state["phase"] = "REASSESS_CHECK"
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_SAVE"
        assert state["reassess_cycle"] == 2

        # Cycle 2 linear: SAVE → ASSESS → REVIEW (reviews cycle 1 revisions)
        state["phase"] = "REASSESS_SAVE"
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_ASSESS"

        state["phase"] = "REASSESS_ASSESS"
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_REVIEW"  # ← THIS reviews cycle 1's revision of A

        state["phase"] = "REASSESS_REVIEW"
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_RESTORE"

        # Cycle 2 REASSESS_RESTORE: cycle=2, skips revise
        state["phase"] = "REASSESS_RESTORE"
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_REVISE"
        assert read_ids("tmp/pipeline-revise-ids.txt") == []  # no unreviewed changes

        # REASSESS_FIXUP → REASSESS_CHECK → COLLECT (exits)
        state["phase"] = "REASSESS_FIXUP"
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_CHECK"

        monkeypatch.setattr(ps, "_run_script", lambda cmd: "REASSESS=A\nDONE=")
        state["phase"] = "REASSESS_CHECK"
        next_phase, _ = ps.advance(state)
        assert next_phase == "COLLECT"  # cycle=2, exits even with reassess IDs


# ---------- REVIEW → REVISE filter ----------

class TestReviewToRevise:
    def test_review_filters_active_ids(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-active-ids.txt", ["A", "B", "C"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "A C")
        state = make_state(phase="REVIEW")
        next_phase, _ = ps.advance(state)
        assert next_phase == "REVISE"
        assert read_ids("tmp/pipeline-revise-ids.txt") == ["A", "C"]

    def test_review_empty_filter(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-active-ids.txt", ["A"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "")
        state = make_state(phase="REVIEW")
        next_phase, _ = ps.advance(state)
        assert next_phase == "REVISE"
        assert read_ids("tmp/pipeline-revise-ids.txt") == []


# ---------- Split pipeline ----------

class TestSplitPipeline:
    def test_split_review_filters_for_revision(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-split-children-ids.txt", ["RFE-001", "RFE-002"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "RFE-001")
        state = make_state(phase="SPLIT_REVIEW")
        next_phase, _ = ps.advance(state)
        assert next_phase == "SPLIT_REVISE"
        assert read_ids("tmp/pipeline-revise-ids.txt") == ["RFE-001"]

    def test_split_sequence_revise_to_reassess(self, tmp_dir):
        """SPLIT_FIXUP → SPLIT_SAVE → SPLIT_REASSESS → SPLIT_RE_REVIEW → SPLIT_RESTORE."""
        phases = []
        state = make_state(phase="SPLIT_FIXUP")
        for _ in range(4):
            next_phase, _ = ps.advance(state)
            phases.append(next_phase)
            state["phase"] = next_phase
        assert phases == [
            "SPLIT_SAVE", "SPLIT_REASSESS", "SPLIT_RE_REVIEW", "SPLIT_RESTORE"
        ]

    def test_split_restore_to_correction_check(self, tmp_dir):
        """SPLIT_RESTORE is the last linear step before SPLIT_CORRECTION_CHECK."""
        # SPLIT_RESTORE is in seq[:-1] so it advances to the next element
        state = make_state(phase="SPLIT_RESTORE")
        next_phase, _ = ps.advance(state)
        assert next_phase == "SPLIT_CORRECTION_CHECK"


class TestSplitFullCycle:
    """End-to-end: split children revision is followed by re-review."""

    def test_revised_children_are_re_reviewed(self, tmp_dir, monkeypatch):
        """Trace: SPLIT_REVIEW filters → SPLIT_REVISE → FIXUP → re-review."""
        # SPLIT_REVIEW: 1 of 3 children needs revision
        write_ids("tmp/pipeline-split-children-ids.txt",
                  ["RFE-001", "RFE-002", "RFE-003"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "RFE-002")
        state = make_state(phase="SPLIT_REVIEW")
        next_phase, _ = ps.advance(state)
        assert next_phase == "SPLIT_REVISE"
        assert read_ids("tmp/pipeline-revise-ids.txt") == ["RFE-002"]

        # Walk through the full post-revise sequence
        expected_phases = [
            "SPLIT_FIXUP", "SPLIT_SAVE", "SPLIT_REASSESS",
            "SPLIT_RE_REVIEW", "SPLIT_RESTORE", "SPLIT_CORRECTION_CHECK",
        ]
        state["phase"] = next_phase
        for expected in expected_phases:
            next_phase, _ = ps.advance(state)
            assert next_phase == expected, (
                f"Expected {expected} after {state['phase']}, got {next_phase}")
            state["phase"] = next_phase

    def test_no_revision_skips_reassess(self, tmp_dir, monkeypatch):
        """When no children need revision, re-review phases are no-ops."""
        write_ids("tmp/pipeline-split-children-ids.txt", ["RFE-001"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "")
        state = make_state(phase="SPLIT_REVIEW")
        next_phase, _ = ps.advance(state)
        assert next_phase == "SPLIT_REVISE"
        # pipeline-revise-ids.txt is empty
        assert read_ids("tmp/pipeline-revise-ids.txt") == []
        # Walk through — all phases are no-ops with empty IDs
        phases = [next_phase]
        for _ in range(5):
            state["phase"] = phases[-1]
            next_phase, _ = ps.advance(state)
            phases.append(next_phase)
        assert phases == [
            "SPLIT_REVISE", "SPLIT_FIXUP", "SPLIT_SAVE",
            "SPLIT_REASSESS", "SPLIT_RE_REVIEW", "SPLIT_RESTORE",
        ]


# ---------- SPLIT_CORRECTION_CHECK ----------

class TestSplitCorrectionCheck:
    def test_undersized_loops_back(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-split-children-ids.txt", ["RFE-001", "RFE-002"])
        monkeypatch.setattr(ps, "_run_script",
                            lambda cmd: "RESPLIT=RFE-001")
        state = make_state(phase="SPLIT_CORRECTION_CHECK", correction_cycle=0)
        next_phase, summary = ps.advance(state)
        assert next_phase == "SPLIT"
        assert state["correction_cycle"] == 1
        assert read_ids("tmp/pipeline-split-ids.txt") == ["RFE-001"]

    def test_no_undersized_goes_to_batch_done(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-split-children-ids.txt", ["RFE-001"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "RESPLIT=")
        state = make_state(phase="SPLIT_CORRECTION_CHECK", correction_cycle=0)
        next_phase, _ = ps.advance(state)
        assert next_phase == "BATCH_DONE"

    def test_max_correction_cycle_exits(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-split-children-ids.txt", ["RFE-001"])
        monkeypatch.setattr(ps, "_run_script",
                            lambda cmd: "RESPLIT=RFE-001")
        state = make_state(phase="SPLIT_CORRECTION_CHECK", correction_cycle=1)
        next_phase, _ = ps.advance(state)
        assert next_phase == "BATCH_DONE"


# ---------- COLLECT ----------

class TestCollect:
    def test_splits_go_to_split_phase(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-active-ids.txt", ["A", "B"])
        monkeypatch.setattr(
            ps, "_run_script",
            lambda cmd: "SUBMIT=A\nSPLIT=B\nREVISE=\nREJECT=\nERRORS=")
        state = make_state(phase="COLLECT")
        next_phase, summary = ps.advance(state)
        assert next_phase == "SPLIT"
        assert read_ids("tmp/pipeline-split-ids.txt") == ["B"]
        assert "split=1" in summary

    def test_no_splits_go_to_batch_done(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-active-ids.txt", ["A"])
        monkeypatch.setattr(
            ps, "_run_script",
            lambda cmd: "SUBMIT=A\nSPLIT=\nREVISE=\nREJECT=\nERRORS=")
        state = make_state(phase="COLLECT")
        next_phase, _ = ps.advance(state)
        assert next_phase == "BATCH_DONE"


# ---------- SPLIT_COLLECT ----------

class TestSplitCollect:
    def test_children_exist(self, tmp_dir):
        write_ids("tmp/pipeline-split-children-ids.txt", ["RFE-001"])
        state = make_state(phase="SPLIT_COLLECT")
        next_phase, _ = ps.advance(state)
        assert next_phase == "SPLIT_PIPELINE_START"

    def test_no_children(self, tmp_dir):
        write_ids("tmp/pipeline-split-children-ids.txt", [])
        state = make_state(phase="SPLIT_COLLECT")
        next_phase, _ = ps.advance(state)
        assert next_phase == "BATCH_DONE"


# ---------- BATCH_DONE ----------

class TestBatchDone:
    def test_more_batches(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-active-ids.txt", ["A"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "TOTAL=1 PASSED=1")
        state = make_state(phase="BATCH_DONE", batch=1, total_batches=3)
        next_phase, _ = ps.advance(state)
        assert next_phase == "BATCH_START"

    def test_last_batch_with_errors(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-active-ids.txt", ["A"])
        write_ids("tmp/pipeline-all-ids.txt", ["A", "B"])

        def mock_run(cmd):
            if "batch_summary" in cmd:
                return "TOTAL=1 PASSED=1"
            if "collect_recommendations" in cmd:
                return "ERRORS=B"
            return ""

        monkeypatch.setattr(ps, "_run_script", mock_run)
        state = make_state(phase="BATCH_DONE", batch=2, total_batches=2,
                           retry_cycle=0)
        next_phase, _ = ps.advance(state)
        assert next_phase == "ERROR_COLLECT"

    def test_last_batch_no_errors(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-active-ids.txt", ["A"])
        write_ids("tmp/pipeline-all-ids.txt", ["A"])
        monkeypatch.setattr(
            ps, "_run_script",
            lambda cmd: "TOTAL=1 PASSED=1" if "batch_summary" in cmd
            else "ERRORS=")
        state = make_state(phase="BATCH_DONE", batch=1, total_batches=1)
        next_phase, _ = ps.advance(state)
        assert next_phase == "REPORT"

    def test_no_retry_after_max(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-active-ids.txt", ["A"])
        write_ids("tmp/pipeline-all-ids.txt", ["A", "B"])
        monkeypatch.setattr(
            ps, "_run_script",
            lambda cmd: "TOTAL=1 PASSED=1" if "batch_summary" in cmd
            else "ERRORS=B")
        state = make_state(phase="BATCH_DONE", batch=2, total_batches=2,
                           retry_cycle=1)
        next_phase, _ = ps.advance(state)
        assert next_phase == "REPORT"


# ---------- ERROR_COLLECT ----------

class TestErrorCollect:
    def test_transitions_to_batch_start(self, tmp_dir):
        write_ids("tmp/pipeline-retry-ids.txt", ["ERR-1", "ERR-2"])
        state = make_state(phase="ERROR_COLLECT", total_batches=2)
        next_phase, summary = ps.advance(state)
        assert next_phase == "BATCH_START"
        assert "2 error IDs" in summary


# ---------- get-phase-config ----------

class TestGetPhaseConfig:
    def test_includes_phase_name(self, tmp_dir):
        ps._save_state(make_state(phase="FETCH"))
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_get_phase_config([])
        output = buf.getvalue()
        assert "phase: FETCH" in output

    def test_command_hidden_from_output(self, tmp_dir):
        """Script phases must not emit command or ids_file fields."""
        ps._save_state(make_state(phase="FIXUP"))
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_get_phase_config([])
        output = buf.getvalue()
        assert "command" not in output
        assert "ids_file" not in output
        assert "type: script" in output

    def test_agent_phase_retains_prompt(self, tmp_dir):
        """Agent phases still emit prompt and ids_file fields."""
        ps._save_state(make_state(phase="ASSESS"))
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_get_phase_config([])
        output = buf.getvalue()
        assert "prompt:" in output
        assert "ids_file:" in output
        assert "type: agent" in output


# ---------- run-phase ----------

class TestRunPhase:
    def test_executes_script(self, tmp_dir, monkeypatch):
        """REPORT (no ids_file) runs the correct command."""
        ps._save_state(make_state(phase="REPORT",
                                  start_time="2026-04-09T00:00:00Z",
                                  batch_size=50))
        calls = []
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, **kw: (calls.append(cmd),
                               type("R", (), {"returncode": 0})())[1])
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_run_phase([])
        assert len(calls) == 1
        assert "generate_run_report.py" in calls[0]
        assert "[run-phase] REPORT" in buf.getvalue()

    def test_appends_ids(self, tmp_dir, monkeypatch):
        """FIXUP reads IDs from ids_file and appends them."""
        ps._save_state(make_state(phase="FIXUP"))
        write_ids("tmp/pipeline-revise-ids.txt",
                  ["RHAIRFE-1001", "RHAIRFE-1002"])
        calls = []
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, **kw: (calls.append(cmd),
                               type("R", (), {"returncode": 0})())[1])
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            ps.cmd_run_phase([])
        assert "RHAIRFE-1001" in calls[0]
        assert "RHAIRFE-1002" in calls[0]

    def test_substitutes_state_vars(self, tmp_dir, monkeypatch):
        """REPORT substitutes {start_time} and {batch_size}."""
        ps._save_state(make_state(phase="REPORT",
                                  start_time="2026-04-09T00:00:00Z",
                                  batch_size=50))
        calls = []
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, **kw: (calls.append(cmd),
                               type("R", (), {"returncode": 0})())[1])
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            ps.cmd_run_phase([])
        assert "2026-04-09T00:00:00Z" in calls[0]
        assert "{start_time}" not in calls[0]
        assert "50" in calls[0]

    def test_rejects_agent_phase(self, tmp_dir):
        """Agent phases cannot be run via run-phase."""
        ps._save_state(make_state(phase="FETCH"))
        with pytest.raises(SystemExit) as exc_info:
            ps.cmd_run_phase([])
        assert exc_info.value.code == 1

    def test_rejects_noop_phase(self, tmp_dir):
        """Noop phases cannot be run via run-phase."""
        ps._save_state(make_state(phase="BATCH_START"))
        with pytest.raises(SystemExit) as exc_info:
            ps.cmd_run_phase([])
        assert exc_info.value.code == 1

    def test_writes_dispatch_marker(self, tmp_dir, monkeypatch):
        """run-phase writes dispatch marker on success."""
        ps._save_state(make_state(phase="FIXUP"))
        write_ids("tmp/pipeline-revise-ids.txt", ["RHAIRFE-1001"])
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, **kw: type("R", (), {"returncode": 0})())
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            ps.cmd_run_phase([])
        assert os.path.exists(ps.DISPATCH_MARKER)
        with open(ps.DISPATCH_MARKER) as f:
            assert f.read().strip() == "FIXUP"

    def test_no_marker_on_failure(self, tmp_dir, monkeypatch):
        """run-phase does NOT write dispatch marker on failure."""
        ps._save_state(make_state(phase="FIXUP"))
        write_ids("tmp/pipeline-revise-ids.txt", ["RHAIRFE-1001"])
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, **kw: type("R", (), {"returncode": 1})())
        import io
        from contextlib import redirect_stdout
        with pytest.raises(SystemExit):
            with redirect_stdout(io.StringIO()):
                ps.cmd_run_phase([])
        assert not os.path.exists(ps.DISPATCH_MARKER)

    def test_propagates_exit_code(self, tmp_dir, monkeypatch):
        """Non-zero exit code from script propagates."""
        ps._save_state(make_state(phase="FIXUP"))
        write_ids("tmp/pipeline-revise-ids.txt", ["RHAIRFE-1001"])
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, **kw: type("R", (), {"returncode": 42})())
        import io
        from contextlib import redirect_stdout
        with pytest.raises(SystemExit) as exc_info:
            with redirect_stdout(io.StringIO()):
                ps.cmd_run_phase([])
        assert exc_info.value.code == 42


# ---------- Dispatch marker guard ----------

class TestDispatchMarker:
    """Verify advance refuses to proceed for script phases without dispatch."""

    def test_advance_rejects_without_marker(self, tmp_dir):
        """advance exits with error when script phase has no dispatch marker."""
        ps._save_state(make_state(phase="FIXUP"))
        # No marker file — advance should refuse
        with pytest.raises(SystemExit) as exc_info:
            ps.cmd_advance([])
        assert exc_info.value.code == 1

    def test_advance_rejects_wrong_phase_marker(self, tmp_dir):
        """advance exits with error when marker is for a different phase."""
        ps._save_state(make_state(phase="FIXUP"))
        with open(ps.DISPATCH_MARKER, "w") as f:
            f.write("SETUP")
        with pytest.raises(SystemExit) as exc_info:
            ps.cmd_advance([])
        assert exc_info.value.code == 1

    def test_advance_accepts_correct_marker(self, tmp_dir):
        """advance proceeds when marker matches current script phase."""
        ps._save_state(make_state(phase="FIXUP"))
        write_ids("tmp/pipeline-revise-ids.txt", ["RHAIRFE-1001"])
        with open(ps.DISPATCH_MARKER, "w") as f:
            f.write("FIXUP")
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_advance([])
        assert "REASSESS_CHECK" in buf.getvalue()
        # Marker consumed
        assert not os.path.exists(ps.DISPATCH_MARKER)

    def test_advance_skips_marker_for_noop(self, tmp_dir, monkeypatch):
        """Noop phases don't require a dispatch marker."""
        ps._save_state(make_state(phase="BATCH_START"))
        write_ids("tmp/pipeline-batch-1-ids.txt", ["RHAIRFE-1001"])
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_advance([])
        assert "FETCH" in buf.getvalue()

    def test_advance_skips_marker_for_agent(self, tmp_dir, monkeypatch):
        """Agent phases don't require a dispatch marker (but do check completion)."""
        ps._save_state(make_state(phase="FETCH"))
        # All IDs complete — create task files so check_id returns "completed"
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1001"])
        with open("artifacts/rfe-tasks/RHAIRFE-1001.md", "w") as f:
            f.write("fetched")
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "")
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_advance([])
        assert "SETUP" in buf.getvalue()

    def test_advance_skips_marker_for_dry_run(self, tmp_dir):
        """Dry-run bypasses the dispatch marker check."""
        ps._save_state(make_state(phase="FIXUP"))
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_advance(["--dry-run"])
        assert "REASSESS_CHECK" in buf.getvalue()


# ---------- Agent phase guard on advance ----------


class TestAgentPhaseGuard:
    """Verify advance refuses to proceed for agent phases with pending agents."""

    def test_advance_rejects_pending_agents(self, tmp_dir):
        """advance exits with error when agent phase has pending IDs."""
        ps._save_state(make_state(phase="FETCH"))
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1001"])
        # No task file → pending
        with pytest.raises(SystemExit) as exc_info:
            ps.cmd_advance([])
        assert exc_info.value.code == 1

    def test_advance_accepts_complete_agents(self, tmp_dir, monkeypatch):
        """advance proceeds when all agent IDs are complete."""
        ps._save_state(make_state(phase="FETCH"))
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1001"])
        with open("artifacts/rfe-tasks/RHAIRFE-1001.md", "w") as f:
            f.write("fetched")
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "")
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_advance([])
        assert "SETUP" in buf.getvalue()

    def test_advance_checks_parallel_phases(self, tmp_dir, monkeypatch):
        """advance checks parallel poll_phases too (e.g. ASSESS + feasibility)."""
        ps._save_state(make_state(phase="ASSESS"))
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1001"])
        # Assess file exists but feasibility file missing
        os.makedirs("/tmp/rfe-assess/single", exist_ok=True)
        with open("/tmp/rfe-assess/single/RHAIRFE-1001.result.md", "w") as f:
            f.write("assessed")
        # No feasibility file → pending on parallel phase
        with pytest.raises(SystemExit) as exc_info:
            ps.cmd_advance([])
        assert exc_info.value.code == 1

    def test_advance_parallel_all_complete(self, tmp_dir, monkeypatch):
        """advance proceeds when both main and parallel phases are complete."""
        ps._save_state(make_state(phase="ASSESS"))
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1001"])
        os.makedirs("/tmp/rfe-assess/single", exist_ok=True)
        with open("/tmp/rfe-assess/single/RHAIRFE-1001.result.md", "w") as f:
            f.write("assessed")
        with open("artifacts/rfe-reviews/RHAIRFE-1001-feasibility.md", "w") as f:
            f.write("feasibility done")
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "")
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_advance([])
        assert "REVIEW" in buf.getvalue()

    def test_advance_prints_poll_command_on_reject(self, tmp_dir):
        """Rejection message includes the exact poll command to run."""
        ps._save_state(make_state(phase="FETCH"))
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1001"])
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with pytest.raises(SystemExit), redirect_stderr(buf):
            ps.cmd_advance([])
        err = buf.getvalue()
        assert "check_review_progress.py --poll" in err
        assert "--phase fetch" in err
        assert "--id-file tmp/pipeline-active-ids.txt" in err

    def test_advance_dry_run_skips_agent_check(self, tmp_dir):
        """Dry-run bypasses the agent completion check."""
        ps._save_state(make_state(phase="FETCH"))
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1001"])
        # No task file → would fail without dry-run
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_advance(["--dry-run"])
        assert "SETUP" in buf.getvalue()

    def test_advance_empty_ids_file_passes(self, tmp_dir, monkeypatch):
        """Empty IDs file → no agents to check, advance proceeds."""
        ps._save_state(make_state(phase="FETCH"))
        write_ids("tmp/pipeline-active-ids.txt", [])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "")
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_advance([])
        assert "SETUP" in buf.getvalue()


# ---------- FIXUP → REASSESS_CHECK ----------

class TestFixup:
    def test_fixup_goes_to_reassess_check(self, tmp_dir):
        state = make_state(phase="FIXUP")
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_CHECK"


# ---------- Invariant: every revision is followed by a review ----------

class TestRevisionReviewInvariant:
    """Verify that no path through the state machine allows an unreviewed
    revision to reach a terminal decision point (COLLECT, BATCH_DONE)."""

    def test_main_revise_always_reaches_reassess_review(self, tmp_dir, monkeypatch):
        """Main REVISE → FIXUP → REASSESS_CHECK → REASSESS_REVIEW."""
        write_ids("tmp/pipeline-active-ids.txt", ["A"])
        # FIXUP → REASSESS_CHECK
        state = make_state(phase="FIXUP")
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_CHECK"
        # REASSESS_CHECK with reassess IDs → enters loop
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "REASSESS=A\nDONE=")
        state["phase"] = "REASSESS_CHECK"
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_SAVE"
        # Linear to REASSESS_REVIEW
        state["phase"] = "REASSESS_SAVE"
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_ASSESS"
        state["phase"] = "REASSESS_ASSESS"
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_REVIEW"  # revision IS reviewed

    def test_last_reassess_cycle_cannot_revise(self, tmp_dir):
        """At max cycle, REASSESS_RESTORE produces zero revise IDs."""
        write_ids("tmp/pipeline-reassess-ids.txt", ["A", "B", "C"])
        state = make_state(phase="REASSESS_RESTORE", reassess_cycle=2)
        ps.advance(state)
        assert read_ids("tmp/pipeline-revise-ids.txt") == []

    def test_split_revise_followed_by_re_review(self, tmp_dir, monkeypatch):
        """SPLIT_REVISE → SPLIT_FIXUP → SPLIT_SAVE → SPLIT_REASSESS → SPLIT_RE_REVIEW."""
        # Start after SPLIT_REVISE
        state = make_state(phase="SPLIT_REVISE")
        phases = []
        for _ in range(4):
            next_phase, _ = ps.advance(state)
            phases.append(next_phase)
            state["phase"] = next_phase
        assert "SPLIT_REASSESS" in phases
        assert "SPLIT_RE_REVIEW" in phases
        assert phases.index("SPLIT_REASSESS") < phases.index("SPLIT_RE_REVIEW")


# ---------- End-to-end dispatch loop simulation ----------

class TestDispatchLoopE2E:
    """Simulate the LLM dispatch loop: get-phase-config → run-phase/advance.

    These tests exercise the seams between CLI commands in the same
    sequence the orchestrator uses in production, verified against
    real GitLab job logs.
    """

    def _dispatch_once(self, monkeypatch, subprocess_mock):
        """Run one iteration of the dispatch loop. Returns phase name."""
        import io
        from contextlib import redirect_stdout

        # Step 1: get-phase-config
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_get_phase_config([])
        config_output = buf.getvalue()
        import yaml
        config = yaml.safe_load(config_output)

        phase = config["phase"]
        phase_type = config.get("type", "noop")

        # Verify invariant: script phases never expose command or ids_file
        if phase_type == "script":
            assert "command" not in config, \
                f"command leaked in {phase} config"
            assert "ids_file" not in config, \
                f"ids_file leaked in {phase} config"

        # Verify invariant: agent phases retain needed fields
        if phase_type == "agent":
            assert "prompt" in config, \
                f"prompt missing from {phase} config"
            assert "ids_file" in config, \
                f"ids_file missing from {phase} config"
            assert "poll_phase" in config, \
                f"poll_phase missing from {phase} config"

        # Step 2: dispatch based on type
        if phase_type == "script":
            monkeypatch.setattr(subprocess, "run", subprocess_mock)
            buf = io.StringIO()
            with redirect_stdout(buf):
                ps.cmd_run_phase([])

        # Simulate agent completion for agent phases
        if phase_type == "agent":
            ids_file = config.get("ids_file")
            if ids_file:
                ids = read_ids(ids_file)
                poll_phase = config.get("poll_phase")
                phases_to_sim = [poll_phase]
                for p in config.get("parallel", []):
                    if p.get("poll_phase"):
                        phases_to_sim.append(p["poll_phase"])
                for pp in phases_to_sim:
                    for rfe_id in ids:
                        from check_review_progress import PHASE_CHECKS
                        path = PHASE_CHECKS[pp](rfe_id)
                        os.makedirs(os.path.dirname(path), exist_ok=True)
                        if pp == "review":
                            with open(path, "w") as f:
                                f.write("---\nscore: 7\n---\n")
                        elif pp == "revise":
                            # Check if file exists (review file); set
                            # auto_revised
                            with open(path, "w") as f:
                                f.write("---\nauto_revised: true\n---\n")
                        else:
                            with open(path, "w") as f:
                                f.write("done")

        # Step 3: advance
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_advance([])

        return phase

    def _run_loop(self, monkeypatch, subprocess_mock, max_phases=80):
        """Run the dispatch loop until DONE. Returns phase sequence."""
        phases = []
        for _ in range(max_phases):
            state = ps._load_state()
            if state["phase"] == "DONE":
                break
            phase = self._dispatch_once(monkeypatch, subprocess_mock)
            phases.append(phase)
        else:
            pytest.fail(f"Dispatch loop did not reach DONE in {max_phases}"
                        f" phases. Last phases: {phases[-10:]}")
        return phases

    def test_single_batch_no_splits(self, tmp_dir, monkeypatch):
        """Happy path: 1 batch, revisions needed, no reassess, no splits.

        Expected from GitLab logs:
        BATCH_START → FETCH → SETUP → ASSESS → REVIEW → REVISE →
        FIXUP → REASSESS_CHECK → COLLECT → BATCH_DONE → REPORT → DONE
        """
        ids = ["RHAIRFE-1001", "RHAIRFE-1002", "RHAIRFE-1003"]

        # Init state
        ps._save_state(make_state(phase="BATCH_START", total_batches=1))
        write_ids("tmp/pipeline-batch-1-ids.txt", ids)
        write_ids("tmp/pipeline-all-ids.txt", ids)

        # Mock _run_script for advance() decision scripts
        def mock_run_script(cmd):
            if "filter_for_revision.py" in cmd:
                return "RHAIRFE-1001 RHAIRFE-1002"  # 2 need revision
            if "collect_recommendations.py --reassess" in cmd:
                return "REASSESS=\nDONE=RHAIRFE-1001 RHAIRFE-1002 RHAIRFE-1003"
            if "collect_recommendations.py" in cmd:
                return ("SUBMIT=RHAIRFE-1001 RHAIRFE-1002 RHAIRFE-1003\n"
                        "SPLIT=\nREVISE=\nREJECT=\nERRORS=")
            if "batch_summary.py" in cmd:
                return "submit=3 split=0 revise=0 reject=0 errors=0"
            if "collect_recommendations.py --errors" in cmd:
                return "ERRORS="
            return ""
        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        # Mock subprocess.run for run-phase (script phases)
        subprocess_mock = lambda cmd, **kw: type("R", (), {"returncode": 0})()

        phases = self._run_loop(monkeypatch, subprocess_mock)

        expected = [
            "BATCH_START", "FETCH", "SETUP", "ASSESS", "REVIEW", "REVISE",
            "FIXUP", "REASSESS_CHECK", "COLLECT", "BATCH_DONE", "REPORT",
        ]
        assert phases == expected

    def test_single_batch_with_reassess(self, tmp_dir, monkeypatch):
        """1 batch, 2 reassess cycles, no splits.

        Expected from GitLab logs:
        BATCH_START → FETCH → SETUP → ASSESS → REVIEW → REVISE →
        FIXUP → REASSESS_CHECK →
          REASSESS_SAVE → REASSESS_ASSESS → REASSESS_REVIEW →
          REASSESS_RESTORE → REASSESS_REVISE → REASSESS_FIXUP →
        REASSESS_CHECK →
          REASSESS_SAVE → REASSESS_ASSESS → REASSESS_REVIEW →
          REASSESS_RESTORE → REASSESS_REVISE → REASSESS_FIXUP →
        REASSESS_CHECK → COLLECT → BATCH_DONE → REPORT → DONE
        """
        ids = ["RHAIRFE-1001", "RHAIRFE-1002"]

        ps._save_state(make_state(phase="BATCH_START", total_batches=1))
        write_ids("tmp/pipeline-batch-1-ids.txt", ids)
        write_ids("tmp/pipeline-all-ids.txt", ids)

        reassess_calls = {"count": 0}

        def mock_run_script(cmd):
            if "filter_for_revision.py" in cmd:
                return "RHAIRFE-1001"  # 1 needs revision each time
            if "collect_recommendations.py --reassess" in cmd:
                reassess_calls["count"] += 1
                if reassess_calls["count"] <= 2:
                    return "REASSESS=RHAIRFE-1001\nDONE=RHAIRFE-1002"
                return "REASSESS=\nDONE=RHAIRFE-1001 RHAIRFE-1002"
            if "collect_recommendations.py --errors" in cmd:
                return "ERRORS="
            if "collect_recommendations.py" in cmd:
                return ("SUBMIT=RHAIRFE-1001 RHAIRFE-1002\n"
                        "SPLIT=\nREVISE=\nREJECT=\nERRORS=")
            if "batch_summary.py" in cmd:
                return "submit=2"
            return ""
        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        subprocess_mock = lambda cmd, **kw: type("R", (), {"returncode": 0})()
        phases = self._run_loop(monkeypatch, subprocess_mock)

        expected = [
            "BATCH_START", "FETCH", "SETUP", "ASSESS", "REVIEW", "REVISE",
            "FIXUP", "REASSESS_CHECK",
            # Cycle 1
            "REASSESS_SAVE", "REASSESS_ASSESS", "REASSESS_REVIEW",
            "REASSESS_RESTORE", "REASSESS_REVISE", "REASSESS_FIXUP",
            "REASSESS_CHECK",
            # Cycle 2
            "REASSESS_SAVE", "REASSESS_ASSESS", "REASSESS_REVIEW",
            "REASSESS_RESTORE", "REASSESS_REVISE", "REASSESS_FIXUP",
            "REASSESS_CHECK",
            # Exit to collect
            "COLLECT", "BATCH_DONE", "REPORT",
        ]
        assert phases == expected

    def test_two_batches_with_splits(self, tmp_dir, monkeypatch):
        """2 batches, batch 1 has splits, batch 2 is clean.

        Matches the canonical GitLab job 13870756363 flow.
        """
        batch1 = ["RHAIRFE-1001", "RHAIRFE-1002"]
        batch2 = ["RHAIRFE-1003"]

        ps._save_state(make_state(phase="BATCH_START", total_batches=2))
        write_ids("tmp/pipeline-batch-1-ids.txt", batch1)
        write_ids("tmp/pipeline-batch-2-ids.txt", batch2)
        write_ids("tmp/pipeline-all-ids.txt", batch1 + batch2)

        current_batch = {"n": 0}

        def mock_run_script(cmd):
            if "filter_for_revision.py" in cmd:
                return "RHAIRFE-1001"  # 1 needs revision
            if "collect_recommendations.py --reassess" in cmd:
                return "REASSESS=\nDONE=RHAIRFE-1001 RHAIRFE-1002"
            if "collect_recommendations.py --errors" in cmd:
                return "ERRORS="
            if "collect_recommendations.py" in cmd:
                if current_batch["n"] == 0:
                    current_batch["n"] = 1
                    return ("SUBMIT=RHAIRFE-1001\n"
                            "SPLIT=RHAIRFE-1002\nREVISE=\nREJECT=\nERRORS=")
                return ("SUBMIT=RHAIRFE-1003\n"
                        "SPLIT=\nREVISE=\nREJECT=\nERRORS=")
            if "check_right_sized.py" in cmd:
                return "RESPLIT="  # no undersized children
            if "batch_summary.py" in cmd:
                return "submit=1"
            return ""
        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        # SPLIT_COLLECT writes child IDs
        def subprocess_mock(cmd, **kw):
            if "split_collect.py" in cmd:
                write_ids("tmp/pipeline-split-children-ids.txt",
                          ["RFE-001", "RFE-002"])
            return type("R", (), {"returncode": 0})()

        phases = self._run_loop(monkeypatch, subprocess_mock)

        # Batch 1: main pipeline + split sub-pipeline
        assert phases[0] == "BATCH_START"
        assert "SPLIT" in phases
        assert "SPLIT_COLLECT" in phases
        assert "SPLIT_PIPELINE_START" in phases
        assert "SPLIT_CORRECTION_CHECK" in phases

        # Batch boundary
        batch_done_indices = [i for i, p in enumerate(phases)
                              if p == "BATCH_DONE"]
        assert len(batch_done_indices) == 2  # one per batch

        # Batch 2: no splits
        batch2_phases = phases[batch_done_indices[0] + 1:]
        assert "SPLIT" not in batch2_phases or \
            batch2_phases.index("BATCH_DONE") < batch2_phases.index("SPLIT")

        # Ends with REPORT
        assert phases[-1] == "REPORT"

    def test_script_phase_uses_run_phase(self, tmp_dir, monkeypatch):
        """Verify script phases go through run-phase, not direct execution."""
        ids = ["RHAIRFE-1001"]
        ps._save_state(make_state(phase="BATCH_START", total_batches=1))
        write_ids("tmp/pipeline-batch-1-ids.txt", ids)
        write_ids("tmp/pipeline-all-ids.txt", ids)

        # Track run-phase invocations
        run_phase_calls = []

        def subprocess_mock(cmd, **kw):
            run_phase_calls.append(cmd)
            return type("R", (), {"returncode": 0})()

        def mock_run_script(cmd):
            if "filter_for_revision.py" in cmd:
                return ""  # no revisions
            if "collect_recommendations.py --reassess" in cmd:
                return "REASSESS=\nDONE=RHAIRFE-1001"
            if "collect_recommendations.py --errors" in cmd:
                return "ERRORS="
            if "collect_recommendations.py" in cmd:
                return ("SUBMIT=RHAIRFE-1001\n"
                        "SPLIT=\nREVISE=\nREJECT=\nERRORS=")
            if "batch_summary.py" in cmd:
                return "submit=1"
            return ""
        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        phases = self._run_loop(monkeypatch, subprocess_mock)

        # Script phases that were dispatched via run-phase
        script_phases_hit = [p for p in phases
                             if ps.PHASE_CONFIG.get(p, {}).get("type")
                             == "script"]
        # Script phases with empty IDs files are skipped (no subprocess call),
        # so run_phase_calls may be fewer than script_phases_hit.
        assert len(run_phase_calls) <= len(script_phases_hit)
        assert len(run_phase_calls) > 0  # at least SETUP/REPORT

    def test_correction_loop(self, tmp_dir, monkeypatch):
        """Split correction: SPLIT_CORRECTION_CHECK → SPLIT loop-back.

        GitLab job 13870756363 showed this path: first split pass
        produces undersized children, correction loop re-splits them.
        """
        ids = ["RHAIRFE-1001"]

        ps._save_state(make_state(phase="BATCH_START", total_batches=1))
        write_ids("tmp/pipeline-batch-1-ids.txt", ids)
        write_ids("tmp/pipeline-all-ids.txt", ids)

        correction_checks = {"count": 0}

        def mock_run_script(cmd):
            if "filter_for_revision.py" in cmd:
                return "RHAIRFE-1001"
            if "collect_recommendations.py --reassess" in cmd:
                return "REASSESS=\nDONE=RHAIRFE-1001"
            if "collect_recommendations.py --errors" in cmd:
                return "ERRORS="
            if "collect_recommendations.py" in cmd:
                return ("SUBMIT=\nSPLIT=RHAIRFE-1001\n"
                        "REVISE=\nREJECT=\nERRORS=")
            if "check_right_sized.py" in cmd:
                correction_checks["count"] += 1
                if correction_checks["count"] == 1:
                    return "RESPLIT=RFE-001"  # undersized on first check
                return "RESPLIT="  # all pass on second check
            if "batch_summary.py" in cmd:
                return "submit=0"
            return ""
        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        split_collect_calls = {"count": 0}

        def subprocess_mock(cmd, **kw):
            if "split_collect.py" in cmd:
                split_collect_calls["count"] += 1
                if split_collect_calls["count"] == 1:
                    write_ids("tmp/pipeline-split-children-ids.txt",
                              ["RFE-001", "RFE-002"])
                else:
                    write_ids("tmp/pipeline-split-children-ids.txt",
                              ["RFE-003", "RFE-004"])
            return type("R", (), {"returncode": 0})()

        phases = self._run_loop(monkeypatch, subprocess_mock)

        # Should see SPLIT_CORRECTION_CHECK twice (once loops back, once exits)
        correction_indices = [i for i, p in enumerate(phases)
                              if p == "SPLIT_CORRECTION_CHECK"]
        assert len(correction_indices) == 2

        # First correction check loops back to SPLIT (undersized children)
        assert phases[correction_indices[0] + 1] == "SPLIT"
        # Second correction check exits to BATCH_DONE (all pass)
        assert phases[correction_indices[1] + 1] == "BATCH_DONE"
        # Two SPLIT phases: original + correction
        split_indices = [i for i, p in enumerate(phases) if p == "SPLIT"]
        assert len(split_indices) == 2

    def test_error_collect_path(self, tmp_dir, monkeypatch):
        """BATCH_DONE → ERROR_COLLECT → BATCH_START retry path."""
        ids = ["RHAIRFE-1001", "RHAIRFE-1002"]

        ps._save_state(make_state(phase="BATCH_START", total_batches=1))
        write_ids("tmp/pipeline-batch-1-ids.txt", ids)
        write_ids("tmp/pipeline-all-ids.txt", ids)

        batch_done_calls = {"count": 0}

        def mock_run_script(cmd):
            if "filter_for_revision.py" in cmd:
                return ""
            if "collect_recommendations.py --reassess" in cmd:
                return "REASSESS=\nDONE=RHAIRFE-1001 RHAIRFE-1002"
            if "collect_recommendations.py --errors" in cmd:
                batch_done_calls["count"] += 1
                if batch_done_calls["count"] == 1:
                    return "ERRORS=RHAIRFE-1002"  # error on first pass
                return "ERRORS="  # clean on retry
            if "collect_recommendations.py" in cmd:
                return ("SUBMIT=RHAIRFE-1001 RHAIRFE-1002\n"
                        "SPLIT=\nREVISE=\nREJECT=\nERRORS=")
            if "batch_summary.py" in cmd:
                return "submit=2"
            return ""
        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        def subprocess_mock(cmd, **kw):
            if "error_collect.py" in cmd:
                # Simulate what error_collect.py does: set retry_cycle,
                # increment total_batches, write retry batch file
                state = ps._load_state()
                state["retry_cycle"] = 1
                state["total_batches"] = state.get("total_batches", 1) + 1
                ps._save_state(state)
                write_ids("tmp/pipeline-batch-2-ids.txt", ["RHAIRFE-1002"])
                write_ids("tmp/pipeline-retry-ids.txt", ["RHAIRFE-1002"])
            return type("R", (), {"returncode": 0})()

        phases = self._run_loop(monkeypatch, subprocess_mock)

        # Should see ERROR_COLLECT between two BATCH_DONE phases
        assert "ERROR_COLLECT" in phases
        error_idx = phases.index("ERROR_COLLECT")
        # ERROR_COLLECT is followed by BATCH_START (retry)
        assert phases[error_idx + 1] == "BATCH_START"
        # Should have 2 BATCH_DONE (original + retry)
        assert phases.count("BATCH_DONE") == 2
        # Ends with REPORT
        assert phases[-1] == "REPORT"

    def test_split_collect_no_children(self, tmp_dir, monkeypatch):
        """SPLIT_COLLECT → BATCH_DONE when split produces no children."""
        ids = ["RHAIRFE-1001"]

        ps._save_state(make_state(phase="BATCH_START", total_batches=1))
        write_ids("tmp/pipeline-batch-1-ids.txt", ids)
        write_ids("tmp/pipeline-all-ids.txt", ids)

        def mock_run_script(cmd):
            if "filter_for_revision.py" in cmd:
                return ""
            if "collect_recommendations.py --reassess" in cmd:
                return "REASSESS=\nDONE=RHAIRFE-1001"
            if "collect_recommendations.py --errors" in cmd:
                return "ERRORS="
            if "collect_recommendations.py" in cmd:
                return ("SUBMIT=\nSPLIT=RHAIRFE-1001\n"
                        "REVISE=\nREJECT=\nERRORS=")
            if "batch_summary.py" in cmd:
                return "submit=0"
            return ""
        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        def subprocess_mock(cmd, **kw):
            if "split_collect.py" in cmd:
                # No children produced — empty file
                write_ids("tmp/pipeline-split-children-ids.txt", [])
            return type("R", (), {"returncode": 0})()

        phases = self._run_loop(monkeypatch, subprocess_mock)

        # SPLIT_COLLECT should go directly to BATCH_DONE (no children)
        assert "SPLIT_COLLECT" in phases
        split_collect_idx = phases.index("SPLIT_COLLECT")
        assert phases[split_collect_idx + 1] == "BATCH_DONE"
        # Should NOT enter the split sub-pipeline
        assert "SPLIT_PIPELINE_START" not in phases
        assert "SPLIT_ASSESS" not in phases

    def test_last_reassess_cycle_empty_revise(self, tmp_dir, monkeypatch):
        """Last reassess cycle writes empty revise IDs; run-phase handles it.

        Cycle 2 hits the guard in REASSESS_RESTORE that writes empty
        revise IDs. REASSESS_REVISE/REASSESS_FIXUP still run via the
        dispatch loop but operate on zero IDs.
        """
        ids = ["RHAIRFE-1001"]

        ps._save_state(make_state(phase="BATCH_START", total_batches=1))
        write_ids("tmp/pipeline-batch-1-ids.txt", ids)
        write_ids("tmp/pipeline-all-ids.txt", ids)

        reassess_calls = {"count": 0}

        def mock_run_script(cmd):
            if "filter_for_revision.py" in cmd:
                return "RHAIRFE-1001"
            if "collect_recommendations.py --reassess" in cmd:
                reassess_calls["count"] += 1
                if reassess_calls["count"] <= 2:
                    return "REASSESS=RHAIRFE-1001\nDONE="
                return "REASSESS=\nDONE=RHAIRFE-1001"
            if "collect_recommendations.py --errors" in cmd:
                return "ERRORS="
            if "collect_recommendations.py" in cmd:
                return ("SUBMIT=RHAIRFE-1001\n"
                        "SPLIT=\nREVISE=\nREJECT=\nERRORS=")
            if "batch_summary.py" in cmd:
                return "submit=1"
            return ""
        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        # Track what run-phase sees for REASSESS_FIXUP on last cycle
        fixup_commands = []

        def subprocess_mock(cmd, **kw):
            if "check_revised.py" in cmd:
                fixup_commands.append(cmd)
            return type("R", (), {"returncode": 0})()

        phases = self._run_loop(monkeypatch, subprocess_mock)

        # Should see 3 REASSESS_CHECK (enter cycle 1, enter cycle 2, exit)
        assert phases.count("REASSESS_CHECK") == 3

        # After cycle 2's REASSESS_RESTORE, revise IDs should be empty.
        # run-phase skips the command entirely when IDs file is empty,
        # so fixup_commands should only contain calls from earlier cycles
        # where IDs were present. The last cycle's FIXUP is skipped.
        # Verify we got fixup calls from cycle 1 (with IDs) but the
        # total count reflects that the empty-ID cycle was skipped.
        assert len(fixup_commands) >= 1  # at least cycle 1's FIXUP ran
        # Cycle 1's fixup should have the ID
        assert "RHAIRFE-1001" in fixup_commands[0]

    def test_dispatch_context_hides_ids_file_for_scripts(self, tmp_dir):
        """dispatch-context must not leak ids_file for script phases."""
        import io
        from contextlib import redirect_stdout

        ps._save_state(make_state(phase="FIXUP"))

        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_dispatch_context([])
        output = buf.getvalue()

        assert "FIXUP" in output
        assert "type: script" not in output or "ids_file" not in output
        assert "IDs file:" not in output
        assert "run-phase" in output

    def test_reprocess_flow(self, tmp_dir, monkeypatch):
        """Reprocess: init preserves prior IDs, pipeline processes all.

        Simulates a reprocess where all prior IDs are fed back through
        the pipeline (snapshot_fetch --reprocess marks all as changed,
        so check_resume passes them all through).
        """
        ids = ["RHAIRFE-1001", "RHAIRFE-1002", "RHAIRFE-1003"]

        # Simulate prior run: ID files already exist
        write_ids("tmp/pipeline-all-ids.txt", ids)

        # Init does NOT wipe existing files
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            ps.cmd_init(["--batch-size", "50"])

        # Prior ID files survive init
        assert read_ids("tmp/pipeline-all-ids.txt") == ids

        # Reprocess: copy all IDs directly to process IDs (skip resume check)
        write_ids("tmp/pipeline-process-ids.txt", ids)

        # Batch and start the pipeline
        write_ids("tmp/pipeline-batch-1-ids.txt", ids)
        state = ps._load_state()
        state["phase"] = "BATCH_START"
        state["total_batches"] = 1
        ps._save_state(state)

        def mock_run_script(cmd):
            if "filter_for_revision.py" in cmd:
                return ""
            if "collect_recommendations.py --reassess" in cmd:
                return "REASSESS=\nDONE=" + " ".join(ids)
            if "collect_recommendations.py --errors" in cmd:
                return "ERRORS="
            if "collect_recommendations.py" in cmd:
                return ("SUBMIT=" + " ".join(ids) + "\n"
                        "SPLIT=\nREVISE=\nREJECT=\nERRORS=")
            if "batch_summary.py" in cmd:
                return "submit=3"
            return ""
        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        subprocess_mock = lambda cmd, **kw: type("R", (), {"returncode": 0})()
        phases = self._run_loop(monkeypatch, subprocess_mock)

        # All 3 IDs processed — same flow as normal
        expected = [
            "BATCH_START", "FETCH", "SETUP", "ASSESS", "REVIEW", "REVISE",
            "FIXUP", "REASSESS_CHECK", "COLLECT", "BATCH_DONE", "REPORT",
        ]
        assert phases == expected
