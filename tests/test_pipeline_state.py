#!/usr/bin/env python3
"""Tests for pipeline_state.py advance() transitions.

Focuses on complex decision points and the invariant that every
revision is followed by a review.
"""
import os
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

    def test_substitutes_command_vars(self, tmp_dir):
        state = make_state(phase="REPORT", start_time="2026-04-09T00:00:00Z",
                           batch_size=50)
        ps._save_state(state)
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_get_phase_config([])
        output = buf.getvalue()
        assert "2026-04-09T00:00:00Z" in output
        assert "{start_time}" not in output


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
