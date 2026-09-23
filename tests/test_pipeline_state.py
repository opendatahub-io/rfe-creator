#!/usr/bin/env python3
"""Tests for pipeline_state.py advance() transitions.

Focuses on complex decision points and the invariant that every
revision is followed by a review.
"""

import os
import subprocess
import sys
import types

import pytest

# Import advance() and helpers directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pipeline_state as ps


def _memo_overrides():
    from conftest import MEMO_OVERRIDES

    return dict(MEMO_OVERRIDES)


@pytest.fixture
def tmp_dir(tmp_path, monkeypatch):
    """Run tests from a temp directory with isolated state."""
    monkeypatch.chdir(tmp_path)
    os.makedirs("tmp", exist_ok=True)
    os.makedirs("artifacts/rfe-reviews", exist_ok=True)
    os.makedirs("artifacts/rfe-tasks", exist_ok=True)
    return tmp_path


@pytest.fixture(autouse=True)
def _isolate_headless_marker():
    """cmd_init --headless and _load_state() export RFE_CREATOR_HEADLESS into os.environ
    (make_state() is headless by default): keep it from leaking across tests or into the
    developer's shell-inherited environment."""
    before = os.environ.pop(ps.HEADLESS_MARKER_ENV, None)
    yield
    if before is None:
        os.environ.pop(ps.HEADLESS_MARKER_ENV, None)
    else:
        os.environ[ps.HEADLESS_MARKER_ENV] = before


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
        write_ids("tmp/pipeline-all-ids.txt", ["RHAIRFE-1001", "RHAIRFE-1002"])
        write_ids("tmp/pipeline-changed-ids.txt", ["RHAIRFE-1001"])
        import io
        from contextlib import redirect_stdout

        with redirect_stdout(io.StringIO()):
            ps.cmd_init([])
        assert read_ids("tmp/pipeline-all-ids.txt") == ["RHAIRFE-1001", "RHAIRFE-1002"]
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
        ps._save_state(make_state(phase="COLLECT", batch=3, reassess_cycle=2))
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
        write_ids("tmp/pipeline-batch-1-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
        state = make_state(phase="BATCH_START", batch=0, reassess_cycle=2, correction_cycle=1)
        next_phase, _ = ps.advance(state)
        assert next_phase == "FETCH"
        assert state["reassess_cycle"] == 0
        assert state["correction_cycle"] == 0
        assert state["batch"] == 1

    def test_copies_batch_ids_to_active(self, tmp_dir):
        write_ids("tmp/pipeline-batch-1-ids.txt", ["RHAIRFE-7", "RHAIRFE-8", "RHAIRFE-9"])
        state = make_state(phase="BATCH_START", batch=0)
        ps.advance(state)
        assert read_ids("tmp/pipeline-active-ids.txt") == ["RHAIRFE-7", "RHAIRFE-8", "RHAIRFE-9"]


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
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "REASSESS=RHAIRFE-1,RHAIRFE-2\nDONE=")
        state = make_state(phase="REASSESS_CHECK", reassess_cycle=0)
        next_phase, summary = ps.advance(state)
        assert next_phase == "REASSESS_SAVE"
        assert state["reassess_cycle"] == 1
        assert "cycle=1/2" in summary

    def test_reassess_check_exits_at_max_cycle(self, tmp_dir, monkeypatch):
        """REASSESS_CHECK goes to COLLECT when cycle >= 2."""
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "REASSESS=RHAIRFE-1\nDONE=")
        state = make_state(phase="REASSESS_CHECK", reassess_cycle=2)
        next_phase, _ = ps.advance(state)
        assert next_phase == "COLLECT"

    def test_reassess_check_exits_when_no_ids(self, tmp_dir, monkeypatch):
        """REASSESS_CHECK goes to COLLECT when no reassess IDs."""
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "REASSESS=\nDONE=RHAIRFE-1")
        state = make_state(phase="REASSESS_CHECK", reassess_cycle=0)
        next_phase, _ = ps.advance(state)
        assert next_phase == "COLLECT"

    def test_reassess_fixup_loops_back(self, tmp_dir):
        """REASSESS_FIXUP always returns to REASSESS_CHECK."""
        state = make_state(phase="REASSESS_FIXUP")
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_CHECK"

    def test_last_cycle_skips_revise(self, tmp_dir, monkeypatch):
        """On cycle 2 (max), REASSESS_RESTORE writes empty revise IDs — but still runs the
        revision filter for its regression rule (AISDLC-45: the second revision now happens,
        so a re-review that scored below before_score must become autorevise_reject)."""
        write_ids("tmp/pipeline-reassess-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
        calls = []
        monkeypatch.setattr(ps, "_run_script", lambda cmd: calls.append(cmd) or "RHAIRFE-1")
        state = make_state(phase="REASSESS_RESTORE", reassess_cycle=2)
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_REVISE"
        assert read_ids("tmp/pipeline-revise-ids.txt") == []
        assert calls == ["python3 scripts/filter_for_revision.py RHAIRFE-1 RHAIRFE-2"]

    def test_non_last_cycle_filters_for_revision(self, tmp_dir, monkeypatch):
        """On cycle < 2, REASSESS_RESTORE runs filter and writes revise IDs."""
        write_ids("tmp/pipeline-reassess-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "RHAIRFE-1")
        state = make_state(phase="REASSESS_RESTORE", reassess_cycle=1)
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_REVISE"
        assert read_ids("tmp/pipeline-revise-ids.txt") == ["RHAIRFE-1"]


class TestReassessFullCycle:
    """End-to-end reassess loop: every revision must be followed by a review."""

    def test_cycle1_revisions_are_reviewed_in_cycle2(self, tmp_dir, monkeypatch):
        """Trace: cycle 1 revises → cycle 2 reviews those revisions."""
        # Cycle 1: REASSESS_CHECK finds IDs, enters loop
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "REASSESS=RHAIRFE-1,RHAIRFE-2\nDONE=")
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
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "RHAIRFE-1")
        state["phase"] = "REASSESS_RESTORE"
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_REVISE"
        assert read_ids("tmp/pipeline-revise-ids.txt") == ["RHAIRFE-1"]  # RHAIRFE-1 needs more work

        # REASSESS_REVISE → REASSESS_FIXUP → REASSESS_CHECK
        state["phase"] = "REASSESS_FIXUP"
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_CHECK"

        # Cycle 2: enters loop again
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "REASSESS=RHAIRFE-1\nDONE=RHAIRFE-2")
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

        monkeypatch.setattr(ps, "_run_script", lambda cmd: "REASSESS=RHAIRFE-1\nDONE=")
        state["phase"] = "REASSESS_CHECK"
        next_phase, _ = ps.advance(state)
        assert next_phase == "COLLECT"  # cycle=2, exits even with reassess IDs


# ---------- REVIEW → REVISE filter ----------


class TestReviewToRevise:
    def test_review_filters_active_ids(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1", "RHAIRFE-2", "RHAIRFE-3"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "RHAIRFE-1 RHAIRFE-3")
        state = make_state(phase="REVIEW")
        next_phase, _ = ps.advance(state)
        assert next_phase == "REVISE"
        assert read_ids("tmp/pipeline-revise-ids.txt") == ["RHAIRFE-1", "RHAIRFE-3"]

    def test_review_empty_filter(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
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
        assert phases == ["SPLIT_SAVE", "SPLIT_REASSESS", "SPLIT_RE_REVIEW", "SPLIT_RESTORE"]

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
        write_ids("tmp/pipeline-split-children-ids.txt", ["RFE-001", "RFE-002", "RFE-003"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "RFE-002")
        state = make_state(phase="SPLIT_REVIEW")
        next_phase, _ = ps.advance(state)
        assert next_phase == "SPLIT_REVISE"
        assert read_ids("tmp/pipeline-revise-ids.txt") == ["RFE-002"]

        # Walk through the full post-revise sequence
        expected_phases = [
            "SPLIT_FIXUP",
            "SPLIT_SAVE",
            "SPLIT_REASSESS",
            "SPLIT_RE_REVIEW",
            "SPLIT_RESTORE",
            "SPLIT_CORRECTION_CHECK",
        ]
        state["phase"] = next_phase
        for expected in expected_phases:
            next_phase, _ = ps.advance(state)
            assert next_phase == expected, (
                f"Expected {expected} after {state['phase']}, got {next_phase}"
            )
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
            "SPLIT_REVISE",
            "SPLIT_FIXUP",
            "SPLIT_SAVE",
            "SPLIT_REASSESS",
            "SPLIT_RE_REVIEW",
            "SPLIT_RESTORE",
        ]


# ---------- SPLIT_CORRECTION_CHECK ----------


class TestSplitCorrectionCheck:
    def test_undersized_loops_back(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-split-children-ids.txt", ["RFE-001", "RFE-002"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "RESPLIT=RFE-001")
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
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "RESPLIT=RFE-001")
        state = make_state(phase="SPLIT_CORRECTION_CHECK", correction_cycle=1)
        next_phase, _ = ps.advance(state)
        assert next_phase == "BATCH_DONE"


# ---------- COLLECT ----------


class TestCollect:
    def test_splits_go_to_split_phase(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
        monkeypatch.setattr(
            ps,
            "_run_script",
            lambda cmd: "SUBMIT=RHAIRFE-1\nSPLIT=RHAIRFE-2\nREVISE=\nREJECT=\nERRORS=",
        )
        state = make_state(phase="COLLECT")
        next_phase, summary = ps.advance(state)
        assert next_phase == "SPLIT"
        assert read_ids("tmp/pipeline-split-ids.txt") == ["RHAIRFE-2"]
        assert "split=1" in summary

    def test_no_splits_go_to_batch_done(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        monkeypatch.setattr(
            ps, "_run_script", lambda cmd: "SUBMIT=RHAIRFE-1\nSPLIT=\nREVISE=\nREJECT=\nERRORS="
        )
        state = make_state(phase="COLLECT")
        next_phase, _ = ps.advance(state)
        assert next_phase == "BATCH_DONE"

    def test_reconciles_before_routing(self, tmp_dir, monkeypatch):
        """AISDLC-33: saved review state is re-applied and cap-exhausted items flagged before
        collect_recommendations reads the reviews; the summary says what changed."""
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
        calls = []

        def mock_run(cmd):
            calls.append(cmd)
            if "reconcile_reviews.py" in cmd:
                return "RESTORED=RHAIRFE-1\nFLAGGED=RHAIRFE-2"
            return "SUBMIT=RHAIRFE-1\nSPLIT=\nREVISE=RHAIRFE-2\nREJECT=\nERRORS="

        monkeypatch.setattr(ps, "_run_script", mock_run)
        state = make_state(phase="COLLECT", reassess_cycle=2)
        next_phase, summary = ps.advance(state)
        assert next_phase == "BATCH_DONE"
        assert calls[0] == (
            "python3 scripts/reconcile_reviews.py --type rfe --keep-state --cycles 2"
            " RHAIRFE-1 RHAIRFE-2"
        )
        assert "collect_recommendations.py" in calls[1]
        assert summary.startswith("COLLECT reconcile: restored=1 flagged=1 errors=0\n")

    def test_reconcile_errors_leave_normal_routing(self, tmp_dir, monkeypatch):
        """An id the reconcile could not repair is marked error: reconcile_failed before
        collect_recommendations reads the reviews."""
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
        marked = []
        monkeypatch.setattr(
            ps,
            "_mark_review_or_stub",
            lambda rid, updates, ptype, base, error, failures=None: (
                marked.append((rid, updates["error"], base, error)) or True
            ),
        )

        def mock_run(cmd):
            if "reconcile_reviews.py" in cmd:
                return "RESTORED=\nFLAGGED=\nRECONCILE_ERRORS=RHAIRFE-2"
            return "SUBMIT=RHAIRFE-1\nSPLIT=\nREVISE=\nREJECT=\nERRORS=RHAIRFE-2"

        monkeypatch.setattr(ps, "_run_script", mock_run)
        next_phase, summary = ps.advance(make_state(phase="COLLECT", reassess_cycle=2))
        assert next_phase == "BATCH_DONE"
        assert marked == [("RHAIRFE-2", "reconcile_failed", "review", "reconcile_failed")]
        assert summary.startswith("COLLECT reconcile: restored=0 flagged=0 errors=1\n")

    def test_reconcile_is_quiet_when_nothing_changed_and_skipped_in_dry_run(
        self, tmp_dir, monkeypatch
    ):
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        calls = []

        def mock_run(cmd):
            calls.append(cmd)
            if "reconcile_reviews.py" in cmd:
                return "RESTORED=\nFLAGGED="
            return "SUBMIT=RHAIRFE-1\nSPLIT=\nREVISE=\nREJECT=\nERRORS="

        monkeypatch.setattr(ps, "_run_script", mock_run)
        _, summary = ps.advance(make_state(phase="COLLECT"))
        assert summary.startswith("COLLECT complete:")
        calls.clear()
        ps.advance(make_state(phase="COLLECT"), dry_run=True)
        assert not any("reconcile_reviews.py" in c for c in calls)


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


class TestErrorCollectAdvance:
    """ERROR_COLLECT with nothing retryable must terminate through REPORT.

    BATCH_DONE routes here on error-classified reviews, but error_collect.py
    re-checks and can find none worth retrying. The old unconditional
    transition started a retry batch whose IDs file does not exist,
    dead-ending the machine and skipping REPORT — the orchestrator had to
    improvise `set-phase DONE` (production, 2026-08-24, RHAIFIRST-581).
    """

    def test_zero_retry_ids_goes_to_report(self, tmp_dir):
        write_ids("tmp/pipeline-retry-ids.txt", [])
        state = make_state(phase="ERROR_COLLECT", batch=1, total_batches=1, retry_cycle=1)

        next_phase, msg = ps.advance(state)

        assert next_phase == "REPORT"
        assert "no retryable errors" in msg

    def test_missing_retry_file_goes_to_report(self, tmp_dir):
        """error_collect.py's early return historically wrote nothing at all."""
        state = make_state(phase="ERROR_COLLECT", batch=1, total_batches=1, retry_cycle=1)

        next_phase, _ = ps.advance(state)

        assert next_phase == "REPORT"

    def test_retry_ids_still_start_the_retry_batch(self, tmp_dir):
        write_ids("tmp/pipeline-retry-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
        state = make_state(phase="ERROR_COLLECT", batch=1, total_batches=2, retry_cycle=1)

        next_phase, msg = ps.advance(state)

        assert next_phase == "BATCH_START"
        assert "2 error IDs" in msg


class TestBatchDone:
    def test_more_batches(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "TOTAL=1 PASSED=1")
        state = make_state(phase="BATCH_DONE", batch=1, total_batches=3)
        next_phase, _ = ps.advance(state)
        assert next_phase == "BATCH_START"

    def test_last_batch_with_errors(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        write_ids("tmp/pipeline-all-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])

        def mock_run(cmd):
            if "batch_summary" in cmd:
                return "TOTAL=1 PASSED=1"
            if "collect_recommendations" in cmd:
                return "ERRORS=RHAIRFE-2"
            return ""

        monkeypatch.setattr(ps, "_run_script", mock_run)
        state = make_state(phase="BATCH_DONE", batch=2, total_batches=2, retry_cycle=0)
        next_phase, _ = ps.advance(state)
        assert next_phase == "ERROR_COLLECT"

    def test_last_batch_no_errors(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        write_ids("tmp/pipeline-all-ids.txt", ["RHAIRFE-1"])
        monkeypatch.setattr(
            ps,
            "_run_script",
            lambda cmd: "TOTAL=1 PASSED=1" if "batch_summary" in cmd else "ERRORS=",
        )
        state = make_state(phase="BATCH_DONE", batch=1, total_batches=1)
        next_phase, _ = ps.advance(state)
        assert next_phase == "REPORT"

    def test_transition_to_report_runs_the_final_reconcile(self, tmp_dir, monkeypatch):
        """AISDLC-33: the last reconcile covers every id of the run, without --keep-state,
        right before REPORT — a review agent may have written after COLLECT."""
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-2"])
        write_ids("tmp/pipeline-all-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
        calls = []

        def mock_run(cmd):
            calls.append(cmd)
            if "batch_summary" in cmd:
                return "TOTAL=1 PASSED=1"
            if "reconcile_reviews" in cmd:
                return "RESTORED=RHAIRFE-1\nFLAGGED=\nRECONCILE_ERRORS="
            return "ERRORS="

        monkeypatch.setattr(ps, "_run_script", mock_run)
        monkeypatch.setattr(ps, "_run_script_soft", lambda cmd: (0, mock_run(cmd)))
        state = make_state(phase="BATCH_DONE", batch=2, total_batches=2, reassess_cycle=1)
        next_phase, summary = ps.advance(state)
        assert next_phase == "REPORT"
        assert calls[-2] == (
            "python3 scripts/reconcile_reviews.py --type rfe --keep-state --cycles 1"
            " RHAIRFE-1 RHAIRFE-2"
        )
        # The content guard runs after the reconcile (a restore may re-raise a flag first).
        assert calls[-1] == (
            "python3 scripts/check_revised.py --type rfe --batch --lower-only RHAIRFE-1 RHAIRFE-2"
        )
        assert "REPORT reconcile: restored=1 flagged=0 errors=0\nBATCH_DONE → REPORT" in summary
        calls.clear()
        ps.advance(make_state(phase="BATCH_DONE", batch=2, total_batches=2), dry_run=True)
        assert not any("reconcile_reviews" in c for c in calls)

    def test_error_collect_to_report_runs_the_final_reconcile(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-retry-ids.txt", [])
        write_ids("tmp/pipeline-all-ids.txt", ["RHAIRFE-1"])
        calls = []
        monkeypatch.setattr(
            ps,
            "_run_script",
            lambda cmd: calls.append(cmd) or "RESTORED=\nFLAGGED=\nRECONCILE_ERRORS=",
        )
        monkeypatch.setattr(
            ps, "_run_script_soft", lambda cmd: (0, calls.append(cmd) or "LOWERED=")
        )
        state = make_state(phase="ERROR_COLLECT", batch=1, total_batches=1, retry_cycle=1)
        next_phase, _ = ps.advance(state)
        assert next_phase == "REPORT"
        assert calls == [
            "python3 scripts/reconcile_reviews.py --type rfe --keep-state --cycles 0 RHAIRFE-1",
            "python3 scripts/check_revised.py --type rfe --batch --lower-only RHAIRFE-1",
        ]

    def test_final_guard_lowers_a_set_flag_on_an_unchanged_task(self, tmp_dir, monkeypatch):
        """2026-09-21 stage dry run: a revise agent's last write restored auto_revised after
        FIXUP had lowered it on an unchanged task. The REPORT transition re-derives the flag
        from the content (lower-only) so the run report agrees with what submit labels."""
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        write_ids("tmp/pipeline-all-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
        calls = []

        def mock_run(cmd):
            calls.append(cmd)
            if "batch_summary" in cmd:
                return "TOTAL=2 PASSED=2"
            if "reconcile_reviews" in cmd:
                return "RESTORED=\nFLAGGED=\nRECONCILE_ERRORS="
            if "check_revised" in cmd:
                return "RHAIRFE-2: auto_revised True -> False\nLOWERED=RHAIRFE-2\nUPDATED=1"
            return "ERRORS="

        monkeypatch.setattr(ps, "_run_script", mock_run)
        monkeypatch.setattr(ps, "_run_script_soft", lambda cmd: (0, mock_run(cmd)))
        next_phase, summary = ps.advance(make_state(phase="BATCH_DONE", batch=1, total_batches=1))
        assert next_phase == "REPORT"
        assert "REPORT flag guard: lowered=1\nBATCH_DONE → REPORT" in summary
        assert "REPORT reconcile:" not in summary  # nothing restored, flagged or errored
        guard = [c for c in calls if "check_revised" in c]
        assert guard == [
            "python3 scripts/check_revised.py --type rfe --batch --lower-only RHAIRFE-1 RHAIRFE-2"
        ]

    def test_final_guard_reports_the_ids_it_skipped(self, tmp_dir, monkeypatch):
        """Per-id isolation: check_revised.py names the reviews it could not read or update
        on SKIPPED= and exits 0; the REPORT summary carries the count next to lowered=."""
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        write_ids("tmp/pipeline-all-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
        monkeypatch.setattr(
            ps,
            "_run_script",
            lambda cmd: (
                "TOTAL=2 PASSED=2"
                if "batch_summary" in cmd
                else (
                    "RESTORED=\nFLAGGED=\nRECONCILE_ERRORS="
                    if "reconcile_reviews" in cmd
                    else "ERRORS="
                )
            ),
        )
        monkeypatch.setattr(
            ps,
            "_run_script_soft",
            lambda cmd: (0, "LOWERED=RHAIRFE-2\nSKIPPED=RHAIRFE-1\nUPDATED=1"),
        )
        next_phase, summary = ps.advance(make_state(phase="BATCH_DONE", batch=1, total_batches=1))
        assert next_phase == "REPORT"
        assert "REPORT flag guard: lowered=1 skipped=1\nBATCH_DONE → REPORT" in summary
        # Nothing lowered but something skipped still surfaces.
        monkeypatch.setattr(
            ps, "_run_script_soft", lambda cmd: (0, "LOWERED=\nSKIPPED=RHAIRFE-1\nUPDATED=0")
        )
        _, summary = ps.advance(make_state(phase="BATCH_DONE", batch=1, total_batches=1))
        assert "REPORT flag guard: lowered=0 skipped=1\n" in summary
        # A stale removed-context companion the guard deleted (an unrevised task) is counted
        # too, between lowered= and skipped=.
        monkeypatch.setattr(
            ps,
            "_run_script_soft",
            lambda cmd: (
                0,
                "LOWERED=RHAIRFE-2\nSTALE_COMPANIONS=RHAIRFE-2\nSKIPPED=RHAIRFE-1\nUPDATED=1",
            ),
        )
        _, summary = ps.advance(make_state(phase="BATCH_DONE", batch=1, total_batches=1))
        assert "REPORT flag guard: lowered=1 stale_companions=1 skipped=1\n" in summary
        monkeypatch.setattr(
            ps,
            "_run_script_soft",
            lambda cmd: (0, "LOWERED=\nSTALE_COMPANIONS=\nSKIPPED=\nUPDATED=0"),
        )
        _, summary = ps.advance(make_state(phase="BATCH_DONE", batch=1, total_batches=1))
        assert "flag guard" not in summary

    def test_final_guard_failure_does_not_abort_report_or_echo_stderr(
        self, tmp_dir, monkeypatch, capsys
    ):
        """The guard is best-effort: a check_revised.py failure (here a frontmatter parse
        error whose message quotes the offending source line, email included) leaves the
        flags as written, logs the exit status only, and REPORT still happens."""
        import subprocess

        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        write_ids("tmp/pipeline-all-ids.txt", ["RHAIRFE-1"])

        def mock_run(cmd):
            if "batch_summary" in cmd:
                return "TOTAL=1 PASSED=1"
            if "reconcile_reviews" in cmd:
                return "RESTORED=\nFLAGGED=\nRECONCILE_ERRORS="
            return "ERRORS="

        monkeypatch.setattr(ps, "_run_script", mock_run)
        real_run = subprocess.run

        def failing_guard(argv, **kwargs):
            assert argv[1] == "scripts/check_revised.py" and "--lower-only" in argv
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr="ValidationError: near 'owner: jane.doe@example.com'"
            )

        monkeypatch.setattr(ps.subprocess, "run", failing_guard)
        try:
            next_phase, summary = ps.advance(
                make_state(phase="BATCH_DONE", batch=1, total_batches=1)
            )
        finally:
            monkeypatch.setattr(ps.subprocess, "run", real_run)
        assert next_phase == "REPORT"
        assert "flag guard" not in summary
        err = capsys.readouterr().err
        assert "REPORT flag guard: skipped (check_revised.py exit 1)" in err
        assert "jane.doe@example.com" not in err

    def test_final_reconcile_stubs_the_ids_it_could_not_repair(self, tmp_dir, monkeypatch):
        """A review with a merged generic error is still 'readable' to the run report, which
        would copy its stale scores: the errored ids get the registry error stub instead."""
        import verify_phase

        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        write_ids("tmp/pipeline-all-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
        stubbed = []
        monkeypatch.setattr(
            verify_phase,
            "write_error_stubs",
            lambda phase, ids, ptype, **kw: stubbed.append((phase, list(ids), ptype, kw)) or [],
        )
        monkeypatch.setattr(
            ps,
            "_run_script",
            lambda cmd: (
                "RESTORED=\nFLAGGED=\nRECONCILE_ERRORS=RHAIRFE-2"
                if "reconcile_reviews" in cmd
                else ("TOTAL=1 PASSED=1" if "batch_summary" in cmd else "ERRORS=")
            ),
        )
        _, summary = ps.advance(make_state(phase="BATCH_DONE", batch=1, total_batches=1))
        assert stubbed == [("review", ["RHAIRFE-2"], "rfe", {"error": "reconcile_failed"})]
        assert "REPORT reconcile: restored=0 flagged=0 errors=1" in summary

    def test_no_retry_after_max(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        write_ids("tmp/pipeline-all-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
        monkeypatch.setattr(
            ps,
            "_run_script",
            lambda cmd: "TOTAL=1 PASSED=1" if "batch_summary" in cmd else "ERRORS=RHAIRFE-2",
        )
        state = make_state(phase="BATCH_DONE", batch=2, total_batches=2, retry_cycle=1)
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
        ps._save_state(make_state(phase="REPORT", start_time="2026-04-09T00:00:00Z", batch_size=50))
        calls = []
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **kw: (
                calls.append(cmd),
                type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
            )[1],
        )
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_run_phase([])
        assert len(calls) == 1
        assert any("generate_run_report.py" in part for part in calls[0])
        assert "[run-phase] REPORT" in buf.getvalue()

    def test_appends_ids(self, tmp_dir, monkeypatch):
        """FIXUP reads IDs from ids_file and appends them."""
        ps._save_state(make_state(phase="FIXUP"))
        write_ids("tmp/pipeline-revise-ids.txt", ["RHAIRFE-1001", "RHAIRFE-1002"])
        calls = []
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **kw: (
                calls.append(cmd),
                type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
            )[1],
        )
        import io
        from contextlib import redirect_stdout

        with redirect_stdout(io.StringIO()):
            ps.cmd_run_phase([])
        assert "RHAIRFE-1001" in calls[0]
        assert "RHAIRFE-1002" in calls[0]

    def test_substitutes_state_vars(self, tmp_dir, monkeypatch):
        """REPORT substitutes {start_time} and {batch_size}."""
        ps._save_state(make_state(phase="REPORT", start_time="2026-04-09T00:00:00Z", batch_size=50))
        calls = []
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **kw: (
                calls.append(cmd),
                type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
            )[1],
        )
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
            subprocess,
            "run",
            lambda cmd, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
        )
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
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: type("R", (), {"returncode": 1})())
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
            subprocess, "run", lambda cmd, **kw: type("R", (), {"returncode": 42})()
        )
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
        os.makedirs("tmp/rfe-assess/single", exist_ok=True)
        with open("tmp/rfe-assess/single/RHAIRFE-1001.result.md", "w") as f:
            f.write("assessed")
        # No feasibility file → pending on parallel phase
        with pytest.raises(SystemExit) as exc_info:
            ps.cmd_advance([])
        assert exc_info.value.code == 1

    def test_advance_parallel_all_complete(self, tmp_dir, monkeypatch):
        """advance proceeds when both main and parallel phases are complete."""
        ps._save_state(make_state(phase="ASSESS"))
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1001"])
        os.makedirs("tmp/rfe-assess/single", exist_ok=True)
        with open("tmp/rfe-assess/single/RHAIRFE-1001.result.md", "w") as f:
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
        """Rejection message includes the wait-for-wave command."""
        ps._save_state(make_state(phase="FETCH"))
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1001"])
        import io
        from contextlib import redirect_stderr

        buf = io.StringIO()
        with pytest.raises(SystemExit), redirect_stderr(buf):
            ps.cmd_advance([])
        err = buf.getvalue()
        assert "wait-for-wave" in err

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


# ---------- set-wave ----------


class TestSetWave:
    def test_set_wave_writes_ids(self, tmp_dir):
        """set-wave writes IDs to the wave file."""
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_set_wave(["RHAIRFE-10", "RHAIRFE-20", "RHAIRFE-30"])
        assert "3 IDs" in buf.getvalue()
        assert read_ids("tmp/pipeline-wave-ids.txt") == ["RHAIRFE-10", "RHAIRFE-20", "RHAIRFE-30"]

    def test_set_wave_overwrites_previous(self, tmp_dir):
        """Successive set-wave calls replace the file."""
        ps.cmd_set_wave(["RHAIRFE-1", "RHAIRFE-2", "RHAIRFE-3"])
        ps.cmd_set_wave(["RHAIRFE-7", "RHAIRFE-8"])
        assert read_ids("tmp/pipeline-wave-ids.txt") == ["RHAIRFE-7", "RHAIRFE-8"]

    def test_set_wave_no_args_exits(self, tmp_dir):
        """set-wave with no IDs exits with error."""
        with pytest.raises(SystemExit):
            ps.cmd_set_wave([])

    def test_set_wave_records_the_launch_time(self, tmp_dir, monkeypatch):
        """AISDLC-33: the barrier ignores review/assess files older than this timestamp."""
        monkeypatch.setattr(ps, "_now", lambda: 1_700_000_000.25)
        ps.cmd_set_wave(["RHAIRFE-1"])
        assert ps._read_wave_launch() == 1_700_000_000.25


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
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        # FIXUP → REASSESS_CHECK
        state = make_state(phase="FIXUP")
        next_phase, _ = ps.advance(state)
        assert next_phase == "REASSESS_CHECK"
        # REASSESS_CHECK with reassess IDs → enters loop
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "REASSESS=RHAIRFE-1\nDONE=")
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

    def test_last_reassess_cycle_cannot_revise(self, tmp_dir, monkeypatch):
        """At max cycle, REASSESS_RESTORE produces zero revise IDs (the filter still runs for
        its regression rule, see test_last_cycle_skips_revise)."""
        write_ids("tmp/pipeline-reassess-ids.txt", ["RHAIRFE-1", "RHAIRFE-2", "RHAIRFE-3"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "RHAIRFE-1")
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
            assert "command" not in config, f"command leaked in {phase} config"
            assert "ids_file" not in config, f"ids_file leaked in {phase} config"

        # Verify invariant: agent phases retain needed fields
        if phase_type == "agent":
            assert "prompt" in config, f"prompt missing from {phase} config"
            assert "ids_file" in config, f"ids_file missing from {phase} config"
            assert "poll_phase" in config, f"poll_phase missing from {phase} config"

        # Step 2: dispatch based on type
        if phase_type == "script":
            # run-phase passes argument lists; the per-test fakes sniff the
            # command as a string, so hand them the joined form.
            monkeypatch.setattr(
                subprocess,
                "run",
                lambda cmd, **kw: subprocess_mock(
                    cmd if isinstance(cmd, str) else " ".join(cmd), **kw
                ),
            )
            # SETUP launches its two commands via Popen; keep the loop hermetic.
            monkeypatch.setattr(
                subprocess,
                "Popen",
                lambda argv, **kw: type("P", (), {"wait": lambda self: 0})(),
            )
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
            pytest.fail(
                f"Dispatch loop did not reach DONE in {max_phases}"
                f" phases. Last phases: {phases[-10:]}"
            )
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
            if "collect_recommendations.py" in cmd and "--reassess" in cmd:
                return "REASSESS=\nDONE=RHAIRFE-1001,RHAIRFE-1002,RHAIRFE-1003"
            if "collect_recommendations.py" in cmd:
                return (
                    "SUBMIT=RHAIRFE-1001,RHAIRFE-1002,RHAIRFE-1003\n"
                    "SPLIT=\nREVISE=\nREJECT=\nERRORS="
                )
            if "batch_summary.py" in cmd:
                return "submit=3 split=0 revise=0 reject=0 errors=0"
            if "collect_recommendations.py" in cmd and "--errors" in cmd:
                return "ERRORS="
            return ""

        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        # Mock subprocess.run for run-phase (script phases)
        def subprocess_mock(cmd, **kw):
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        phases = self._run_loop(monkeypatch, subprocess_mock)

        expected = [
            "BATCH_START",
            "FETCH",
            "SETUP",
            "ASSESS",
            "REVIEW",
            "REVISE",
            "FIXUP",
            "REASSESS_CHECK",
            "COLLECT",
            "BATCH_DONE",
            "REPORT",
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
            if "collect_recommendations.py" in cmd and "--reassess" in cmd:
                reassess_calls["count"] += 1
                if reassess_calls["count"] <= 2:
                    return "REASSESS=RHAIRFE-1001\nDONE=RHAIRFE-1002"
                return "REASSESS=\nDONE=RHAIRFE-1001,RHAIRFE-1002"
            if "collect_recommendations.py" in cmd and "--errors" in cmd:
                return "ERRORS="
            if "collect_recommendations.py" in cmd:
                return "SUBMIT=RHAIRFE-1001,RHAIRFE-1002\nSPLIT=\nREVISE=\nREJECT=\nERRORS="
            if "batch_summary.py" in cmd:
                return "submit=2"
            return ""

        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        def subprocess_mock(cmd, **kw):
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        phases = self._run_loop(monkeypatch, subprocess_mock)

        expected = [
            "BATCH_START",
            "FETCH",
            "SETUP",
            "ASSESS",
            "REVIEW",
            "REVISE",
            "FIXUP",
            "REASSESS_CHECK",
            # Cycle 1
            "REASSESS_SAVE",
            "REASSESS_ASSESS",
            "REASSESS_REVIEW",
            "REASSESS_RESTORE",
            "REASSESS_REVISE",
            "REASSESS_FIXUP",
            "REASSESS_CHECK",
            # Cycle 2
            "REASSESS_SAVE",
            "REASSESS_ASSESS",
            "REASSESS_REVIEW",
            "REASSESS_RESTORE",
            "REASSESS_REVISE",
            "REASSESS_FIXUP",
            "REASSESS_CHECK",
            # Exit to collect
            "COLLECT",
            "BATCH_DONE",
            "REPORT",
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
            if "collect_recommendations.py" in cmd and "--reassess" in cmd:
                return "REASSESS=\nDONE=RHAIRFE-1001,RHAIRFE-1002"
            if "collect_recommendations.py" in cmd and "--errors" in cmd:
                return "ERRORS="
            if "collect_recommendations.py" in cmd:
                if current_batch["n"] == 0:
                    current_batch["n"] = 1
                    return "SUBMIT=RHAIRFE-1001\nSPLIT=RHAIRFE-1002\nREVISE=\nREJECT=\nERRORS="
                return "SUBMIT=RHAIRFE-1003\nSPLIT=\nREVISE=\nREJECT=\nERRORS="
            if "check_right_sized.py" in cmd:
                return "RESPLIT="  # no undersized children
            if "batch_summary.py" in cmd:
                return "submit=1"
            return ""

        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        # SPLIT_COLLECT writes child IDs
        def subprocess_mock(cmd, **kw):
            if "split_collect.py" in cmd:
                write_ids("tmp/pipeline-split-children-ids.txt", ["RFE-001", "RFE-002"])
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        phases = self._run_loop(monkeypatch, subprocess_mock)

        # Batch 1: main pipeline + split sub-pipeline
        assert phases[0] == "BATCH_START"
        assert "SPLIT" in phases
        assert "SPLIT_COLLECT" in phases
        assert "SPLIT_PIPELINE_START" in phases
        assert "SPLIT_CORRECTION_CHECK" in phases

        # Batch boundary
        batch_done_indices = [i for i, p in enumerate(phases) if p == "BATCH_DONE"]
        assert len(batch_done_indices) == 2  # one per batch

        # Batch 2: no splits
        batch2_phases = phases[batch_done_indices[0] + 1 :]
        assert "SPLIT" not in batch2_phases or batch2_phases.index(
            "BATCH_DONE"
        ) < batch2_phases.index("SPLIT")

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
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        def mock_run_script(cmd):
            if "filter_for_revision.py" in cmd:
                return ""  # no revisions
            if "collect_recommendations.py" in cmd and "--reassess" in cmd:
                return "REASSESS=\nDONE=RHAIRFE-1001"
            if "collect_recommendations.py" in cmd and "--errors" in cmd:
                return "ERRORS="
            if "collect_recommendations.py" in cmd:
                return "SUBMIT=RHAIRFE-1001\nSPLIT=\nREVISE=\nREJECT=\nERRORS="
            if "batch_summary.py" in cmd:
                return "submit=1"
            return ""

        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        phases = self._run_loop(monkeypatch, subprocess_mock)

        # Script phases that were dispatched via run-phase
        phase_config = ps._build_phase_config("rfe")
        script_phases_hit = [p for p in phases if phase_config.get(p, {}).get("type") == "script"]
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
            if "collect_recommendations.py" in cmd and "--reassess" in cmd:
                return "REASSESS=\nDONE=RHAIRFE-1001"
            if "collect_recommendations.py" in cmd and "--errors" in cmd:
                return "ERRORS="
            if "collect_recommendations.py" in cmd:
                return "SUBMIT=\nSPLIT=RHAIRFE-1001\nREVISE=\nREJECT=\nERRORS="
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
                    write_ids("tmp/pipeline-split-children-ids.txt", ["RFE-001", "RFE-002"])
                else:
                    write_ids("tmp/pipeline-split-children-ids.txt", ["RFE-003", "RFE-004"])
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        phases = self._run_loop(monkeypatch, subprocess_mock)

        # Should see SPLIT_CORRECTION_CHECK twice (once loops back, once exits)
        correction_indices = [i for i, p in enumerate(phases) if p == "SPLIT_CORRECTION_CHECK"]
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
            if "collect_recommendations.py" in cmd and "--reassess" in cmd:
                return "REASSESS=\nDONE=RHAIRFE-1001,RHAIRFE-1002"
            if "collect_recommendations.py" in cmd and "--errors" in cmd:
                batch_done_calls["count"] += 1
                if batch_done_calls["count"] == 1:
                    return "ERRORS=RHAIRFE-1002"  # error on first pass
                return "ERRORS="  # clean on retry
            if "collect_recommendations.py" in cmd:
                return "SUBMIT=RHAIRFE-1001,RHAIRFE-1002\nSPLIT=\nREVISE=\nREJECT=\nERRORS="
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
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

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
            if "collect_recommendations.py" in cmd and "--reassess" in cmd:
                return "REASSESS=\nDONE=RHAIRFE-1001"
            if "collect_recommendations.py" in cmd and "--errors" in cmd:
                return "ERRORS="
            if "collect_recommendations.py" in cmd:
                return "SUBMIT=\nSPLIT=RHAIRFE-1001\nREVISE=\nREJECT=\nERRORS="
            if "batch_summary.py" in cmd:
                return "submit=0"
            return ""

        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        def subprocess_mock(cmd, **kw):
            if "split_collect.py" in cmd:
                # No children produced — empty file
                write_ids("tmp/pipeline-split-children-ids.txt", [])
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

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

        Cycle 2 hits the guard in REASSESS_RESTORE that writes empty revise
        IDs, so REASSESS_REVISE has nothing to do. REASSESS_FIXUP still rescans
        the reassess set: the re-review just recreated those review files, and
        the fixup is what puts auto_revised back (see TestReassessFixupIds).
        """
        ids = ["RHAIRFE-1001"]

        ps._save_state(make_state(phase="BATCH_START", total_batches=1))
        write_ids("tmp/pipeline-batch-1-ids.txt", ids)
        write_ids("tmp/pipeline-all-ids.txt", ids)

        reassess_calls = {"count": 0}

        def mock_run_script(cmd):
            if "filter_for_revision.py" in cmd:
                return "RHAIRFE-1001"
            if "collect_recommendations.py" in cmd and "--reassess" in cmd:
                reassess_calls["count"] += 1
                if reassess_calls["count"] <= 2:
                    return "REASSESS=RHAIRFE-1001\nDONE="
                return "REASSESS=\nDONE=RHAIRFE-1001"
            if "collect_recommendations.py" in cmd and "--errors" in cmd:
                return "ERRORS="
            if "collect_recommendations.py" in cmd:
                return "SUBMIT=RHAIRFE-1001\nSPLIT=\nREVISE=\nREJECT=\nERRORS="
            if "batch_summary.py" in cmd:
                return "submit=1"
            return ""

        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        # Track what run-phase sees for REASSESS_FIXUP on last cycle
        fixup_commands = []
        guard_commands = []

        def subprocess_mock(cmd, **kw):
            if "check_revised.py" in cmd:
                # The REPORT-transition content guard (lower-only) is not a FIXUP pass.
                (guard_commands if "--lower-only" in cmd else fixup_commands).append(cmd)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        phases = self._run_loop(monkeypatch, subprocess_mock)
        assert len(guard_commands) == 1

        # Should see 3 REASSESS_CHECK (enter cycle 1, enter cycle 2, exit)
        assert phases.count("REASSESS_CHECK") == 3

        # First-pass FIXUP (revise IDs) plus one REASSESS_FIXUP per reassess
        # cycle (reassess IDs) - including cycle 2, whose revise IDs are empty.
        assert len(fixup_commands) == 3
        assert all("RHAIRFE-1001" in cmd for cmd in fixup_commands)

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
            if "collect_recommendations.py" in cmd and "--reassess" in cmd:
                return "REASSESS=\nDONE=" + ",".join(ids)
            if "collect_recommendations.py" in cmd and "--errors" in cmd:
                return "ERRORS="
            if "collect_recommendations.py" in cmd:
                return "SUBMIT=" + ",".join(ids) + "\nSPLIT=\nREVISE=\nREJECT=\nERRORS="
            if "batch_summary.py" in cmd:
                return "submit=3"
            return ""

        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        def subprocess_mock(cmd, **kw):
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        phases = self._run_loop(monkeypatch, subprocess_mock)

        # All 3 IDs processed — same flow as normal
        expected = [
            "BATCH_START",
            "FETCH",
            "SETUP",
            "ASSESS",
            "REVIEW",
            "REVISE",
            "FIXUP",
            "REASSESS_CHECK",
            "COLLECT",
            "BATCH_DONE",
            "REPORT",
        ]
        assert phases == expected


# ---------- next-action ----------


def _run_next_action():
    """Run cmd_next_action and return parsed YAML output."""
    import io
    from contextlib import redirect_stdout

    import yaml as _yaml

    buf = io.StringIO()
    with redirect_stdout(buf):
        ps.cmd_next_action([])
    return _yaml.safe_load(buf.getvalue())


class TestNextActionDone:
    def test_done_phase(self, tmp_dir):
        """DONE phase returns action=done."""
        ps._save_state(make_state(phase="DONE"))
        result = _run_next_action()
        assert result["action"] == "done"
        assert "complete" in result["message"].lower()

    def test_init_phase_errors(self, tmp_dir):
        """INIT phase is not dispatchable — next-action exits with error."""
        ps._save_state(make_state(phase="INIT"))
        with pytest.raises(SystemExit) as exc_info:
            _run_next_action()
        assert exc_info.value.code == 1


class TestNextActionNoop:
    def test_noop_chains_to_agent(self, tmp_dir, monkeypatch):
        """BATCH_START (noop) auto-advances to FETCH (agent)."""
        write_ids("tmp/pipeline-batch-1-ids.txt", ["RHAIRFE-1001"])
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1001"])
        ps._save_state(make_state(phase="BATCH_START"))

        # FETCH needs task file to not exist (pending)
        result = _run_next_action()
        assert result["action"] == "launch_wave"
        assert result["phase"] == "FETCH"
        # State should now be FETCH
        state = ps._load_state()
        assert state["phase"] == "FETCH"

    def test_noop_chain_with_side_effects(self, tmp_dir, monkeypatch):
        """REASSESS_CHECK → COLLECT chains through noops with side-effect scripts."""
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        write_ids("tmp/pipeline-all-ids.txt", ["RHAIRFE-1"])

        def mock_run_script(cmd):
            if "collect_recommendations.py" in cmd and "--reassess" in cmd:
                return "REASSESS=\nDONE=RHAIRFE-1"
            if "collect_recommendations.py" in cmd and "--errors" in cmd:
                return "ERRORS="
            if "collect_recommendations.py" in cmd:
                return "SUBMIT=RHAIRFE-1\nSPLIT=\nREVISE=\nREJECT=\nERRORS="
            if "batch_summary.py" in cmd:
                return "submit=1"
            return ""

        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        # REASSESS_CHECK (noop) → COLLECT (noop) → BATCH_DONE (noop) → REPORT (script)
        # batch=1 matches total_batches=1, so BATCH_DONE exits to REPORT
        ps._save_state(make_state(phase="REASSESS_CHECK", batch=1))
        result = _run_next_action()
        assert result["action"] == "run_script"
        assert result["phase"] == "REPORT"

    def test_multi_batch_noop_chain(self, tmp_dir, monkeypatch):
        """BATCH_DONE → BATCH_START → FETCH chains across batch boundary."""
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        write_ids("tmp/pipeline-batch-2-ids.txt", ["RHAIRFE-2"])

        def mock_run_script(cmd):
            if "batch_summary.py" in cmd:
                return "submit=1"
            if "collect_recommendations.py" in cmd and "--errors" in cmd:
                return "ERRORS="
            return ""

        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        ps._save_state(make_state(phase="BATCH_DONE", batch=1, total_batches=2))
        result = _run_next_action()
        assert result["action"] == "launch_wave"
        assert result["phase"] == "FETCH"
        # State batch counter incremented
        state = ps._load_state()
        assert state["batch"] == 2

    def test_state_saved_per_iteration(self, tmp_dir, monkeypatch):
        """State is saved after each noop advance — verified by checking
        that a crash mid-chain leaves correct phase on disk."""
        write_ids("tmp/pipeline-batch-1-ids.txt", ["RHAIRFE-1"])
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])

        advance_calls = {"count": 0}
        original_advance = ps.advance

        def counting_advance(state, dry_run=False):
            advance_calls["count"] += 1
            return original_advance(state, dry_run=dry_run)

        monkeypatch.setattr(ps, "advance", counting_advance)
        ps._save_state(make_state(phase="BATCH_START"))
        _run_next_action()
        # BATCH_START → FETCH: one advance call for noop, then stops at agent
        assert advance_calls["count"] == 1
        # State on disk should be FETCH (saved after noop advance)
        state = ps._load_state()
        assert state["phase"] == "FETCH"


class TestNextActionScript:
    def test_script_no_marker(self, tmp_dir):
        """Script phase without marker returns run_script."""
        ps._save_state(make_state(phase="FIXUP"))
        write_ids("tmp/pipeline-revise-ids.txt", ["RHAIRFE-1"])
        result = _run_next_action()
        assert result["action"] == "run_script"
        assert result["phase"] == "FIXUP"

    def test_script_with_correct_marker(self, tmp_dir, monkeypatch):
        """Script phase with matching marker auto-advances past it."""
        ps._save_state(make_state(phase="FIXUP", batch=1))
        write_ids("tmp/pipeline-revise-ids.txt", ["RHAIRFE-1"])
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        write_ids("tmp/pipeline-all-ids.txt", ["RHAIRFE-1"])
        with open(ps.DISPATCH_MARKER, "w") as f:
            f.write("FIXUP")

        def mock_run_script(cmd):
            if "collect_recommendations.py" in cmd and "--reassess" in cmd:
                return "REASSESS=\nDONE=RHAIRFE-1"
            if "collect_recommendations.py" in cmd and "--errors" in cmd:
                return "ERRORS="
            if "collect_recommendations.py" in cmd:
                return "SUBMIT=RHAIRFE-1\nSPLIT=\nREVISE=\nREJECT=\nERRORS="
            if "batch_summary.py" in cmd:
                return "submit=1"
            return ""

        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        result = _run_next_action()
        # Should have advanced past FIXUP through noops to REPORT (script)
        assert result["action"] == "run_script"
        assert result["phase"] == "REPORT"
        # Marker consumed
        assert not os.path.exists(ps.DISPATCH_MARKER)

    def test_script_with_stale_marker(self, tmp_dir):
        """Stale marker (wrong phase) is removed and run_script returned."""
        ps._save_state(make_state(phase="FIXUP"))
        write_ids("tmp/pipeline-revise-ids.txt", ["RHAIRFE-1"])
        with open(ps.DISPATCH_MARKER, "w") as f:
            f.write("SETUP")  # stale marker from different phase
        result = _run_next_action()
        assert result["action"] == "run_script"
        assert result["phase"] == "FIXUP"
        # Stale marker removed
        assert not os.path.exists(ps.DISPATCH_MARKER)


class TestNextActionAgent:
    def test_agent_wave_output(self, tmp_dir):
        """ASSESS returns launch_wave with correct agents."""
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1001", "RHAIRFE-1002"])
        ps._save_state(make_state(phase="ASSESS", batch=1))

        # Need prep_assess.py to succeed

        # Mock _run_script for pre_script
        original_run_script = ps._run_script

        def mock_run_script(cmd):
            if "prep_assess.py" in cmd:
                return ""
            return original_run_script(cmd)

        ps._run_script = mock_run_script
        try:
            result = _run_next_action()
        finally:
            ps._run_script = original_run_script

        assert result["action"] == "launch_wave"
        assert result["phase"] == "ASSESS"
        assert "wave 1" in result["message"]
        # 2 IDs × (main + parallel) = 4 agents
        assert len(result["agents"]) == 4
        # First agent: main assess
        assert result["agents"][0]["subagent_type"] == "rfe-scorer"
        assert "assess-agent.md" in result["agents"][0]["prompt_file"]
        assert "RHAIRFE-1001" in result["agents"][0]["vars"]
        # Second agent: parallel feasibility
        assert "feasibility" in result["agents"][1]["prompt_file"].lower()
        assert "RHAIRFE-1001" in result["agents"][1]["vars"]

    def test_launch_records_the_wave_launch_time(self, tmp_dir, monkeypatch):
        """AISDLC-33: every launch_wave stamps tmp/pipeline-wave-launch.txt."""
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1001"])
        ps._save_state(make_state(phase="REVIEW", batch=1))
        monkeypatch.setattr(ps, "_now", lambda: 1_700_000_000.0)
        result = _run_next_action()
        assert result["action"] == "launch_wave"
        assert ps._read_wave_launch() == 1_700_000_000.0

    def test_multi_phase_prefilter(self, tmp_dir):
        """Pre-filter checks both assess and feasibility phases."""
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1001", "RHAIRFE-1002"])
        ps._save_state(make_state(phase="ASSESS", batch=1))

        # RHAIRFE-1001: assess complete, feasibility missing → still pending
        os.makedirs("tmp/rfe-assess/single", exist_ok=True)
        with open("tmp/rfe-assess/single/RHAIRFE-1001.result.md", "w") as f:
            f.write("assessed")
        # RHAIRFE-1002: both missing → pending

        original_run_script = ps._run_script

        def mock_run_script(cmd):
            if "prep_assess.py" in cmd:
                return ""
            return original_run_script(cmd)

        ps._run_script = mock_run_script
        try:
            result = _run_next_action()
        finally:
            ps._run_script = original_run_script

        assert result["action"] == "launch_wave"
        # Both IDs should be in the wave (1001 has assess but not feasibility)
        wave_ids = read_ids(ps.WAVE_IDS_FILE)
        assert "RHAIRFE-1001" in wave_ids
        assert "RHAIRFE-1002" in wave_ids

    def test_all_complete_auto_advances(self, tmp_dir, monkeypatch):
        """All IDs complete → runs post_verify, auto-advances."""
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1001"])
        ps._save_state(make_state(phase="FETCH", batch=1))

        # Create task file so FETCH phase is complete
        with open("artifacts/rfe-tasks/RHAIRFE-1001.md", "w") as f:
            f.write("fetched")

        monkeypatch.setattr(ps, "_run_script", lambda cmd: "")

        result = _run_next_action()
        # Should have advanced past FETCH to SETUP (script)
        assert result["action"] == "run_script"
        assert result["phase"] == "SETUP"

    def test_post_verify_runs(self, tmp_dir, monkeypatch):
        """post_verify runs when all agents complete before auto-advancing."""
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1001"])
        ps._save_state(make_state(phase="FETCH", batch=1))

        # Create task file so FETCH phase is complete
        with open("artifacts/rfe-tasks/RHAIRFE-1001.md", "w") as f:
            f.write("fetched")

        verify_calls = []

        def mock_run_script(cmd):
            if "verify_phase.py" in cmd:
                verify_calls.append(cmd)
            return ""

        monkeypatch.setattr(ps, "_run_script", mock_run_script)

        _run_next_action()
        # FETCH has post_verify — should have been called
        assert any("verify_phase.py" in c for c in verify_calls)

    def test_vars_block_scalar(self, tmp_dir, monkeypatch):
        """vars field uses YAML block scalar (|) for multi-line strings."""
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1001"])
        ps._save_state(make_state(phase="ASSESS", batch=1))

        monkeypatch.setattr(ps, "_run_script", lambda cmd: "")

        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_next_action([])
        raw_output = buf.getvalue()
        # vars should use block scalar — contains | indicator
        assert "vars: |" in raw_output or "vars: |\n" in raw_output


class TestNextActionWaveSize:
    def test_wave_size_respects_batch_size(self, tmp_dir, monkeypatch):
        """Wave size is batch_size / (1 + n_parallel)."""
        # 6 IDs, batch_size=4, ASSESS has 1 parallel → wave_size=2
        ids = [f"RHAIRFE-{i}" for i in range(1, 7)]
        write_ids("tmp/pipeline-active-ids.txt", ids)
        ps._save_state(make_state(phase="ASSESS", batch=1, batch_size=4))

        monkeypatch.setattr(ps, "_run_script", lambda cmd: "")

        result = _run_next_action()
        assert result["action"] == "launch_wave"
        wave_ids = read_ids(ps.WAVE_IDS_FILE)
        # wave_size = ceil(4 / 2) = 2
        assert len(wave_ids) == 2


class TestWaveSizeMath:
    """batch_size is spent as an agent budget: 1 + n_parallel agents per ID.

    RFE agent phases carry one parallel companion (feasibility) so the divisor
    is 2; Initiative adds alignment, making it 3. The division rounds up so a
    small batch does not strand a partial slot.
    """

    def _config(self, n_parallel):
        return {"type": "agent", "parallel": [{}] * n_parallel}

    def test_speedrun_batch_rounds_up_for_rfe(self):
        """batch_size=5, divisor 2 → 3 IDs/wave, not the 2 that flooring gives."""
        assert ps._wave_size({"batch_size": 5}, self._config(1)) == 3

    def test_speedrun_batch_rounds_up_for_initiative(self):
        """batch_size=5, divisor 3 → 2 IDs/wave, not the 1 that flooring gives."""
        assert ps._wave_size({"batch_size": 5}, self._config(2)) == 2

    def test_large_batch_is_not_capped(self):
        """A direct auto-fix at batch_size=50 keeps full throughput."""
        assert ps._wave_size({"batch_size": 50}, self._config(1)) == 25
        assert ps._wave_size({"batch_size": 50}, self._config(2)) == 17

    def test_no_parallel_companions_uses_whole_batch(self):
        """Phases with no parallel agents spend the budget on IDs alone."""
        assert ps._wave_size({"batch_size": 5}, self._config(0)) == 5

    def test_never_returns_zero(self):
        """A degenerate batch_size still launches one ID per wave."""
        assert ps._wave_size({"batch_size": 0}, self._config(2)) == 1

    def test_defaults_when_batch_size_absent(self):
        """State written before batch_size existed falls back to 50."""
        assert ps._wave_size({}, self._config(1)) == 25


# ---------- wait-for-wave ----------


class TestWaitForWave:
    def test_missing_wave_file_errors(self, tmp_dir):
        """wait-for-wave with no wave file exits with error."""
        ps._save_state(make_state(phase="ASSESS"))
        with pytest.raises(SystemExit) as exc_info:
            ps.cmd_wait_for_wave([])
        assert exc_info.value.code == 1

    def test_empty_wave_file_returns(self, tmp_dir):
        """Empty wave file = nothing to wait for, returns successfully."""
        ps._save_state(make_state(phase="ASSESS"))
        write_ids(ps.WAVE_IDS_FILE, [])
        import io
        from contextlib import redirect_stderr

        buf = io.StringIO()
        with redirect_stderr(buf):
            ps.cmd_wait_for_wave([])
        # Should not exit with error (returns normally)

    def test_builds_correct_flags(self, tmp_dir, monkeypatch):
        """wait-for-wave builds correct check_review_progress.py flags."""
        ps._save_state(make_state(phase="ASSESS", headless=True))
        write_ids(ps.WAVE_IDS_FILE, ["RHAIRFE-1001"])

        captured_cmd = {}

        def mock_subprocess_run(cmd_parts, **kw):
            captured_cmd["parts"] = cmd_parts
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", mock_subprocess_run)
        ps.cmd_wait_for_wave([])

        parts = captured_cmd["parts"]
        assert "--phase" in parts
        idx = parts.index("--phase")
        assert parts[idx + 1] == "assess"
        assert "--also-phase" in parts
        also_idx = parts.index("--also-phase")
        assert parts[also_idx + 1] == "feasibility"
        assert "--max-wait" in parts
        assert "--fast-poll" not in parts  # headless=True

    def test_fast_poll_when_not_headless(self, tmp_dir, monkeypatch):
        """--fast-poll included when headless=false."""
        ps._save_state(make_state(phase="ASSESS", headless=False))
        write_ids(ps.WAVE_IDS_FILE, ["RHAIRFE-1001"])

        captured_cmd = {}

        def mock_subprocess_run(cmd_parts, **kw):
            captured_cmd["parts"] = cmd_parts
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", mock_subprocess_run)
        ps.cmd_wait_for_wave([])

        assert "--fast-poll" in captured_cmd["parts"]

    def test_exit_3_prints_rerun(self, tmp_dir, monkeypatch):
        """Exit 3 from check_review_progress prints re-run directive."""
        ps._save_state(make_state(phase="ASSESS"))
        write_ids(ps.WAVE_IDS_FILE, ["RHAIRFE-1001"])

        def mock_subprocess_run(cmd_parts, **kw):
            return type("R", (), {"returncode": 3})()

        monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with pytest.raises(SystemExit) as exc_info:
            with redirect_stdout(buf):
                ps.cmd_wait_for_wave([])
        assert exc_info.value.code == 3
        assert "Re-run:" in buf.getvalue()
        assert "wait-for-wave" in buf.getvalue()

    def test_passes_since_when_a_launch_was_recorded(self, tmp_dir, monkeypatch):
        """AISDLC-33: the poll subprocess and the in-process slot counts share the launch."""
        import check_review_progress as crp

        ps._save_state(make_state(phase="REVIEW"))
        write_ids(ps.WAVE_IDS_FILE, ["RHAIRFE-1001"])
        monkeypatch.setattr(ps, "_now", lambda: 1_700_000_000.5)
        ps._write_wave_launch()
        captured_cmd = {}

        def mock_subprocess_run(cmd_parts, **kw):
            captured_cmd["parts"] = cmd_parts
            captured_cmd["module_since"] = crp.WAVE_LAUNCHED_AT
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", mock_subprocess_run)
        try:
            ps.cmd_wait_for_wave([])
        finally:
            crp.set_wave_launch(None)
        parts = captured_cmd["parts"]
        assert parts[parts.index("--since") + 1] == "1700000000.500"
        assert captured_cmd["module_since"] == 1_700_000_000.5

    def test_no_since_without_a_recorded_launch(self, tmp_dir, monkeypatch):
        import check_review_progress as crp

        ps._save_state(make_state(phase="REVIEW"))
        write_ids(ps.WAVE_IDS_FILE, ["RHAIRFE-1001"])
        captured_cmd = {}

        def mock_subprocess_run(cmd_parts, **kw):
            captured_cmd["parts"] = cmd_parts
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", mock_subprocess_run)
        ps.cmd_wait_for_wave([])
        assert "--since" not in captured_cmd["parts"]
        assert crp.WAVE_LAUNCHED_AT is None

    def test_no_poll_phase_errors(self, tmp_dir):
        """wait-for-wave on a phase with no poll_phase exits with error."""
        ps._save_state(make_state(phase="BATCH_START"))
        write_ids(ps.WAVE_IDS_FILE, ["RHAIRFE-1001"])
        with pytest.raises(SystemExit) as exc_info:
            ps.cmd_wait_for_wave([])
        assert exc_info.value.code == 1

    def test_review_phase_no_parallel(self, tmp_dir, monkeypatch):
        """REVIEW phase: no --also-phase flag (no parallel)."""
        ps._save_state(make_state(phase="REVIEW"))
        write_ids(ps.WAVE_IDS_FILE, ["RHAIRFE-1001"])

        captured_cmd = {}

        def mock_subprocess_run(cmd_parts, **kw):
            captured_cmd["parts"] = cmd_parts
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", mock_subprocess_run)
        ps.cmd_wait_for_wave([])

        parts = captured_cmd["parts"]
        assert "--phase" in parts
        idx = parts.index("--phase")
        assert parts[idx + 1] == "review"
        assert "--also-phase" not in parts


# ---------- dispatch-context with next-action loop ----------


class TestDispatchContextNextAction:
    def test_active_phase_shows_loop(self, tmp_dir):
        """dispatch-context for active phase shows next-action loop."""
        ps._save_state(make_state(phase="ASSESS"))
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_dispatch_context([])
        output = buf.getvalue()
        assert "next-action" in output
        assert "wait-for-wave" in output
        assert "run-phase" in output
        assert "launch_wave" in output

    def test_shows_batch_info(self, tmp_dir):
        """dispatch-context shows batch progress."""
        ps._save_state(make_state(phase="REVIEW", batch=2, total_batches=3))
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_dispatch_context([])
        output = buf.getvalue()
        assert "2/3" in output

    def test_init_unchanged(self, tmp_dir):
        """INIT phase still shows setup message, not dispatch loop."""
        ps._save_state(make_state(phase="INIT"))
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_dispatch_context([])
        output = buf.getvalue()
        assert "Setup in progress" in output
        assert "next-action" not in output

    def test_done_unchanged(self, tmp_dir):
        """DONE phase still shows pipeline complete."""
        ps._save_state(make_state(phase="DONE"))
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_dispatch_context([])
        output = buf.getvalue()
        assert "Pipeline complete" in output
        assert "next-action" not in output


# ---------- get-phase-config hygiene ----------


class TestGetPhaseConfigHygiene:
    def test_strips_pre_script(self, tmp_dir):
        """get-phase-config strips pre_script from output."""
        ps._save_state(make_state(phase="ASSESS"))
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_get_phase_config([])
        output = buf.getvalue()
        assert "pre_script" not in output

    def test_strips_post_verify(self, tmp_dir):
        """get-phase-config strips post_verify from output."""
        ps._save_state(make_state(phase="ASSESS"))
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_get_phase_config([])
        output = buf.getvalue()
        assert "post_verify" not in output


# ---------- ID validation ----------


class TestValidateIds:
    """Every id list is interpolated into shell=True commands, so _read_ids validates."""

    def test_accepts_every_id_shape_the_pipeline_produces(self):
        ids = ["RFE-001", "INIT-004", "RHAIRFE-2685", "RHOAIENG-9876", "RHAISTRAT-42"]
        assert ps._validate_ids(ids) == ids

    @pytest.mark.parametrize(
        "bad",
        [
            "RHAIRFE-1001; rm -rf /",
            "RHAIRFE-1001 | cat /etc/passwd",
            "`id`",
            "$(whoami)",
            "../../outside",
            "not-an-id",
            "rfe-001",
            "RHAIRFE-",
            "-123",
            "RHAIRFE-1001\n",
            "RHAIRFE-١",  # Arabic-Indic digit one: \d would accept it, [0-9] must not
            "RHAIRFE-１",  # fullwidth digit one
        ],
    )
    def test_rejects_shell_metacharacters_traversal_and_arbitrary_strings(self, bad, capsys):
        with pytest.raises(SystemExit) as exc_info:
            ps._validate_ids(["RHAIRFE-1001", bad], source="tmp/x.txt")
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "[validate-ids]" in err
        assert "tmp/x.txt" in err

    def test_message_previews_are_bounded(self, capsys):
        long_bad = "X" * 200
        with pytest.raises(SystemExit):
            ps._validate_ids([long_bad])
        err = capsys.readouterr().err
        assert "X" * 41 not in err
        assert "…" in err

    def test_read_ids_validates(self, tmp_dir):
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1001", "$(whoami)"])
        with pytest.raises(SystemExit) as exc_info:
            ps._read_ids("tmp/pipeline-active-ids.txt")
        assert exc_info.value.code == 1

    def test_read_ids_missing_file_is_empty(self, tmp_dir):
        assert ps._read_ids("tmp/does-not-exist.txt") == []

    def test_run_phase_rejects_before_subprocess(self, tmp_dir, monkeypatch):
        """Script phases never reach subprocess.run with a bad id in the command."""
        ps._save_state(make_state(phase="FIXUP"))
        write_ids("tmp/pipeline-revise-ids.txt", ["RHAIRFE-1001", "$(whoami)"])
        calls = []
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **kw: (
                calls.append(cmd),
                type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
            )[1],
        )
        with pytest.raises(SystemExit) as exc_info:
            ps.cmd_run_phase([])
        assert exc_info.value.code == 1
        assert calls == []

    def test_next_action_rejects_before_wave_substitution(self, tmp_dir, monkeypatch):
        """Agent phases never substitute a bad id into pre_script or vars."""
        ps._save_state(make_state(phase="FETCH", batch=1))
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1001", "../../outside"])
        ran = []
        monkeypatch.setattr(ps, "_run_script", lambda cmd: ran.append(cmd) or "")
        with pytest.raises(SystemExit) as exc_info:
            ps.cmd_next_action([])
        assert exc_info.value.code == 1
        assert ran == []


# ---------- REASSESS_FIXUP reads the revised subset ----------


class TestReassessFixupIds:
    """REASSESS_FIXUP rescans every re-reviewed item, not only the re-revised subset.

    REASSESS_REVIEW recreates each re-reviewed item's review file, which resets
    auto_revised to the schema default (false). REASSESS_RESTORE then narrows the
    reassess set through filter_for_revision into tmp/pipeline-revise-ids.txt for
    REASSESS_REVISE, so an item that passes after its revision is absent from that
    file. Pointing the fixup at the revise file left exactly those items with
    auto_revised=false (5 of 5 revised items in the 2026-09-04 initiative eval;
    most production RFEs with a moved score since April 2026), and submit.py
    derives the auto-revised Jira label from the flag. An earlier version of this
    class pinned the revise file and called PR #146's reassess-file change wrong;
    the evidence went the other way.
    """

    @pytest.mark.parametrize("ptype", ["rfe", "initiative"])
    def test_fixup_rescans_the_whole_reassess_set(self, ptype):
        cfg = ps._build_phase_config(ptype)
        assert cfg["REASSESS_FIXUP"]["ids_file"] == "tmp/pipeline-reassess-ids.txt"
        assert cfg["REASSESS_RESTORE"]["ids_file"] == "tmp/pipeline-reassess-ids.txt"
        # AISDLC-33: the state file outlives this phase for the COLLECT reconcile.
        assert cfg["REASSESS_RESTORE"]["command"].endswith("restore --keep-state")
        assert cfg["REASSESS_REVISE"]["ids_file"] == "tmp/pipeline-revise-ids.txt"
        # The first-pass FIXUP still checks exactly what REVISE revised: nothing
        # has recreated those review files yet.
        assert (
            cfg["FIXUP"]["ids_file"] == cfg["REVISE"]["ids_file"] == "tmp/pipeline-revise-ids.txt"
        )

    def test_reassess_restore_narrows_into_the_revise_file(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-reassess-ids.txt", ["RHAIRFE-1", "RHAIRFE-2", "RHAIRFE-3"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "RHAIRFE-2")
        monkeypatch.setattr(ps, "_save_originals", lambda ids, ptype: None)
        nxt, _ = ps.advance(make_state(phase="REASSESS_RESTORE", reassess_cycle=1))
        assert nxt == "REASSESS_REVISE"
        assert read_ids("tmp/pipeline-revise-ids.txt") == ["RHAIRFE-2"]

    def test_item_that_passed_after_revision_reaches_the_fixup(self, tmp_dir, monkeypatch):
        """RHAIRFE-1 passed on re-review (not re-revised); check_revised must still see it."""
        write_ids("tmp/pipeline-reassess-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
        write_ids("tmp/pipeline-revise-ids.txt", ["RHAIRFE-2"])
        ps._save_state(make_state(phase="REASSESS_FIXUP", reassess_cycle=1))
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", fake_run)
        ps.cmd_run_phase([])
        assert len(calls) == 1
        assert calls[0][:3] == ["python3", "scripts/check_revised.py", "--batch"]
        assert calls[0][-2:] == ["RHAIRFE-1", "RHAIRFE-2"]


# ---------- State values formatted into shell commands ----------


class TestValidateStateValues:
    """cmd_run_phase formats start_time/batch_size into the REPORT command."""

    def _run_report(self, monkeypatch, **overrides):
        fields = {"phase": "REPORT", "start_time": "2026-04-09T00:00:00Z", "batch_size": 50}
        fields.update(overrides)
        ps._save_state(make_state(**fields))
        calls = []
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **kw: (
                calls.append(cmd),
                type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
            )[1],
        )
        import io
        from contextlib import redirect_stdout

        with redirect_stdout(io.StringIO()):
            ps.cmd_run_phase([])
        return calls

    def test_valid_state_runs(self, tmp_dir, monkeypatch):
        calls = self._run_report(monkeypatch)
        assert len(calls) == 1 and any("generate_run_report.py" in p for p in calls[0])

    @pytest.mark.parametrize(
        "overrides",
        [
            {"start_time": "x; touch /tmp/pwned #"},
            {"start_time": "2026-04-09T00:00:00Z; id"},
            {"batch_size": "50; id"},
            {"batch_size": 5.5},
            {"batch_size": "²"},  # str.isdigit() is True, int() raises
            {"batch_size": 0},  # _wave_size would silently make this one id per wave
            {"batch_size": -1},
            {"batch_size": "0"},
            {"type": "rfe; id"},
            {"type": []},  # unhashable: must be a controlled rejection, not a TypeError
            {"type": {"rfe": 1}},
            {"start_time": "2026-02-30T25:61:61Z"},  # shape-valid, impossible values
            {"start_time": "2026-13-01T00:00:00Z"},
            {"start_time": ["2026-04-09T00:00:00Z"]},
        ],
    )
    def test_tampered_state_never_reaches_subprocess(self, tmp_dir, monkeypatch, overrides, capsys):
        with pytest.raises(SystemExit) as exc_info:
            self._run_report(monkeypatch, **overrides)
        assert exc_info.value.code == 1
        assert "[validate-state]" in capsys.readouterr().err

    def test_leap_day_and_boundaries_are_accepted(self, tmp_dir, monkeypatch):
        for ts_ in ("2028-02-29T23:59:59Z", "2026-12-31T00:00:00Z"):
            assert len(self._run_report(monkeypatch, start_time=ts_)) == 1

    def test_string_digit_batch_size_is_accepted(self, tmp_dir, monkeypatch):
        calls = self._run_report(monkeypatch, batch_size="25")
        assert len(calls) == 1 and "25" in calls[0]


# ---------- No shell anywhere ----------


class TestNoShell:
    """Commands are argument lists; nothing is interpreted by a shell."""

    def test_run_script_uses_argv_without_shell(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"], seen["kw"] = cmd, kw
            return type("R", (), {"returncode": 0, "stdout": "ok\n", "stderr": ""})()

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert ps._run_script("python3 scripts/filter_for_revision.py RHAIRFE-1 RHAIRFE-2") == "ok"
        assert seen["cmd"] == [
            "python3",
            "scripts/filter_for_revision.py",
            "RHAIRFE-1",
            "RHAIRFE-2",
        ]
        assert seen["kw"].get("shell") is not True

    def test_run_phase_passes_state_values_as_separate_args(self, tmp_dir, monkeypatch):
        ps._save_state(make_state(phase="REPORT", start_time="2026-04-09T00:00:00Z", batch_size=50))
        seen = {}
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **kw: (
                seen.update(cmd=cmd, kw=kw),
                type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
            )[1],
        )
        import io
        from contextlib import redirect_stdout

        with redirect_stdout(io.StringIO()):
            ps.cmd_run_phase([])
        assert isinstance(seen["cmd"], list)
        assert seen["cmd"][:2] == ["python3", "scripts/generate_run_report.py"]
        assert seen["cmd"][seen["cmd"].index("--start-time") + 1] == "2026-04-09T00:00:00Z"
        assert seen["kw"].get("shell") is not True

    def test_setup_runs_both_commands_concurrently_and_logs_failures(
        self, tmp_dir, monkeypatch, capsys
    ):
        ps._save_state(make_state(phase="SETUP"))
        launched = []

        class FakeProc:
            def __init__(self, argv):
                self.argv = argv

            def wait(self):
                return 3 if any("fetch-architecture-context" in a for a in self.argv) else 0

        monkeypatch.setattr(
            subprocess, "Popen", lambda argv, **kw: launched.append(argv) or FakeProc(argv)
        )
        ps.cmd_run_phase([])  # must not raise: exit codes are logged, not fatal
        assert [a[:2] for a in launched] == [
            ["bash", "scripts/bootstrap-assess-rfe.sh"],
            ["bash", "scripts/fetch-architecture-context.sh"],
        ]
        assert all(isinstance(a, list) for a in launched)
        assert "exit 3 from: bash scripts/fetch-architecture-context.sh" in capsys.readouterr().err
        assert open(ps.DISPATCH_MARKER).read() == "SETUP"

    def test_no_shell_true_left_in_module(self):
        import inspect

        src = inspect.getsource(ps)
        assert "shell=True" not in src.replace("``shell=True``", "")


# ---------- Ids parsed from script output are validated too ----------


class TestScriptOutputIsValidated:
    """Malformed ids in a decision script's stdout abort before any file or path is touched."""

    def _arm(self, tmp_dir, monkeypatch, phase, **state):
        if phase == "REVIEW":
            write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
        elif phase == "REASSESS_RESTORE":
            write_ids("tmp/pipeline-reassess-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
            state.setdefault("reassess_cycle", 1)
        elif phase == "SPLIT_REVIEW":
            write_ids("tmp/pipeline-split-children-ids.txt", ["RFE-002", "RFE-003"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "RHAIRFE-1 ../../tmp/x")
        saved = []
        monkeypatch.setattr(ps, "_save_originals", lambda ids, ptype: saved.append(ids))
        return make_state(phase=phase, **state), saved

    @pytest.mark.parametrize("phase", ["REVIEW", "REASSESS_RESTORE", "SPLIT_REVIEW"])
    def test_malformed_filter_output_exits_before_save_originals(
        self, tmp_dir, monkeypatch, phase, capsys
    ):
        state, saved = self._arm(tmp_dir, monkeypatch, phase)
        with pytest.raises(SystemExit) as exc_info:
            ps.advance(state)
        assert exc_info.value.code == 1
        assert saved == []
        assert not os.path.exists("tmp/pipeline-revise-ids.txt")
        assert "filter_for_revision.py output" in capsys.readouterr().err

    def test_parse_line_ids_validates(self, capsys):
        assert ps._parse_line_ids("REASSESS=RHAIRFE-1,RHAIRFE-2\nDONE=", "REASSESS") == [
            "RHAIRFE-1",
            "RHAIRFE-2",
        ]
        assert ps._parse_line_ids("REASSESS=\nDONE=", "REASSESS") == []
        with pytest.raises(SystemExit):
            ps._parse_line_ids("REASSESS=RHAIRFE-1,$(id)", "REASSESS")
        assert "REASSESS= line" in capsys.readouterr().err

    def test_write_ids_refuses_malformed_and_writes_nothing(self, tmp_dir):
        with pytest.raises(SystemExit):
            ps._write_ids("tmp/pipeline-x-ids.txt", ["RHAIRFE-1", "../../y"])
        assert not os.path.exists("tmp/pipeline-x-ids.txt")

    def test_save_originals_refuses_path_components(self, tmp_dir):
        with pytest.raises(SystemExit):
            ps._save_originals(["../../etc/passwd"], "rfe")


# ---------- Tampered state never reaches decision-script argv ----------


class TestStateValidatedBeforeDecisionScripts:
    def test_advance_rejects_tampered_type_before_run_script(self, tmp_dir, monkeypatch, capsys):
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        ran = []
        monkeypatch.setattr(ps, "_run_script", lambda cmd: ran.append(cmd) or "")
        with pytest.raises(SystemExit) as exc_info:
            ps.advance(make_state(phase="REVIEW", type="rfe --extra-option"))
        assert exc_info.value.code == 1
        assert ran == []
        assert "[validate-state]" in capsys.readouterr().err

    def test_next_action_rejects_tampered_type_before_any_script(self, tmp_dir, monkeypatch):
        ps._save_state(make_state(phase="FETCH", batch=1, type="rfe --extra-option"))
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        ran = []
        monkeypatch.setattr(ps, "_run_script", lambda cmd: ran.append(cmd) or "")
        with pytest.raises(SystemExit) as exc_info:
            ps.cmd_next_action([])
        assert exc_info.value.code == 1
        assert ran == []

    def test_get_config_validates(self):
        with pytest.raises(SystemExit):
            ps._get_config(make_state(type="initiative; id"))
        assert "REPORT" in ps._get_config(make_state(type="initiative"))


# ---------- init --type (registry choices) ----------


class TestInitTypeChoices:
    def test_help_renders_the_registered_types(self, tmp_dir, capsys):
        """The choices come from the registry; the rendered help is byte-identical to the
        literal list it replaced ({rfe,initiative} — rfe first, names() order)."""
        with pytest.raises(SystemExit) as exc_info:
            ps.cmd_init(["--help"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "--type {rfe,initiative}" in out
        assert "--type {" + ",".join(ps._TYPES.choices()) + "}" in out

    def test_unknown_type_exits_2_with_the_registered_list(self, tmp_dir, capsys):
        with pytest.raises(SystemExit) as exc_info:
            ps.cmd_init(["--type", "bogus"])
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "argument --type: invalid choice: 'bogus'" in err
        assert "choose from 'rfe', 'initiative'" in err
        assert not os.path.exists(ps.STATE_FILE)

    @pytest.mark.parametrize("ptype", ["rfe", "initiative"])
    def test_registered_type_is_persisted(self, tmp_dir, ptype):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.cmd_init(["--type", ptype])
        assert ps._load_state()["type"] == ptype
        assert buf.getvalue() == f"Initialized pipeline state: type={ptype} batch_size=50\n"

    def test_pipeline_types_cover_the_choices(self):
        """PIPELINE_TYPES is projected from the registry (PR-5b): every registered choice that
        carries the phase-table facts has a row — for the shipped registry, all of them."""
        assert list(ps.PIPELINE_TYPES) == ps._TYPES.choices()


# ---------- headless marker export (PR-3 D4) ----------


class TestHeadlessMarker:
    def test_marker_is_the_registry_marker(self):
        """One predicate: the variable the pipeline exports is one the registry reads."""
        import type_registry

        assert ps.HEADLESS_MARKER_ENV in type_registry.HEADLESS_MARKER_VARS
        assert type_registry.is_headless({ps.HEADLESS_MARKER_ENV: "1"})

    def test_init_headless_exports_the_marker(self, tmp_dir, monkeypatch):
        import io
        from contextlib import redirect_stdout

        monkeypatch.delenv(ps.HEADLESS_MARKER_ENV, raising=False)
        with redirect_stdout(io.StringIO()):
            ps.cmd_init(["--headless"])
        assert os.environ.get(ps.HEADLESS_MARKER_ENV) == "1"

    def test_init_without_headless_exports_nothing(self, tmp_dir, monkeypatch):
        import io
        from contextlib import redirect_stdout

        monkeypatch.delenv(ps.HEADLESS_MARKER_ENV, raising=False)
        with redirect_stdout(io.StringIO()):
            ps.cmd_init(["--type", "initiative", "--announce-complete"])
        assert ps.HEADLESS_MARKER_ENV not in os.environ

    def test_loading_a_headless_state_exports_the_marker(self, tmp_dir, monkeypatch):
        """Every command that spawns subprocesses loads state first, so a later process
        (wait-for-wave, run-phase, advance) re-exports the marker for its children."""
        monkeypatch.delenv(ps.HEADLESS_MARKER_ENV, raising=False)
        ps._save_state(make_state(headless=True))
        assert ps.HEADLESS_MARKER_ENV not in os.environ
        state = ps._load_state()
        assert state["headless"] is True
        assert os.environ.get(ps.HEADLESS_MARKER_ENV) == "1"

    def test_loading_an_interactive_state_exports_nothing(self, tmp_dir, monkeypatch):
        monkeypatch.delenv(ps.HEADLESS_MARKER_ENV, raising=False)
        ps._save_state(make_state(headless=False))
        ps._load_state()
        assert ps.HEADLESS_MARKER_ENV not in os.environ

    def test_a_stale_false_value_is_overridden_for_a_headless_state(self, tmp_dir, monkeypatch):
        """The state file is authoritative (D4): a ``0`` the environment already carried would
        make a child resolve interactively and stall the headless run, so it is overwritten."""
        import io
        from contextlib import redirect_stdout

        monkeypatch.setenv(ps.HEADLESS_MARKER_ENV, "0")
        with redirect_stdout(io.StringIO()):
            ps.cmd_init(["--headless"])
        assert os.environ[ps.HEADLESS_MARKER_ENV] == "1"
        monkeypatch.setenv(ps.HEADLESS_MARKER_ENV, "0")
        ps._load_state()
        assert os.environ[ps.HEADLESS_MARKER_ENV] == "1"

    def test_helper_never_unsets_or_touches_non_headless_state(self, monkeypatch):
        monkeypatch.delenv(ps.HEADLESS_MARKER_ENV, raising=False)
        ps._export_headless_marker(None)
        ps._export_headless_marker({})
        ps._export_headless_marker({"headless": False})
        assert ps.HEADLESS_MARKER_ENV not in os.environ
        monkeypatch.setenv(ps.HEADLESS_MARKER_ENV, "yes")
        ps._export_headless_marker({"headless": False})
        assert os.environ[ps.HEADLESS_MARKER_ENV] == "yes"
        ps._export_headless_marker({"headless": True})
        assert os.environ[ps.HEADLESS_MARKER_ENV] == "1"


class TestInitAndTheRegistry:
    """D12: PIPELINE_TYPES is a projection of the registry. A registered drop-in descriptor
    (RFE_CREATOR_EXTRA_TYPES) that carries the phase-table facts gets a phase table and `init
    --type` accepts it; an unregistered name, a partial descriptor and one whose pipeline.stages
    omits a stage the table launches are refused with exit 2 before any state is written."""

    def _registry(self, monkeypatch, root):
        import type_registry

        reg = type_registry.load(extra_roots=[root], env={})
        monkeypatch.setattr(ps, "_TYPES", reg)
        monkeypatch.setattr(ps, "PIPELINE_TYPES", ps._pipeline_types(reg))
        monkeypatch.setattr(ps, "_LAUNCH_BLOCKS", {})
        return reg

    def _init(self, argv):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()), pytest.raises(SystemExit) as e:
            ps.cmd_init(argv)
        return e.value.code, err.getvalue()

    def test_a_registered_drop_in_with_the_facts_gets_a_phase_table(
        self, tmp_dir, monkeypatch, drop_in_root
    ):
        import io
        from contextlib import redirect_stdout

        self._registry(monkeypatch, drop_in_root.memo())
        assert list(ps.PIPELINE_TYPES) == ["rfe", "initiative", "memo"]
        row = ps.PIPELINE_TYPES["memo"]
        assert row["tasks_dir"] == "artifacts/memo-tasks"
        assert row["reviews_dir"] == "artifacts/memo-reviews"
        assert row["dispatch_skill"] == ps.GENERIC_DISPATCH_SKILL  # no legacy body to drive it
        with redirect_stdout(io.StringIO()):
            ps.cmd_init(["--type", "memo"])
        state = ps._load_state()
        assert state["type"] == "memo"
        cfg = ps._build_phase_config("memo")
        assert cfg["REVIEW"]["prompt"] == f"{ps.REVIEW_PROMPTS}/review-agent.md"
        assert cfg["REVIEW"]["vars"]["FEASIBILITY_PATH"] == (
            "artifacts/memo-reviews/{ID}-feasibility.md"
        )
        assert dict(ps._launch_block(state))["TYPE"] == "memo"

    def test_a_type_without_dimensions_gets_a_scorer_only_wave(
        self, tmp_dir, monkeypatch, drop_in_root
    ):
        drop_in_root.add("memo", overrides={**_memo_overrides(), "pipeline.dimensions": []})
        self._registry(monkeypatch, drop_in_root.path)
        cfg = ps._build_phase_config("memo")
        assert cfg["ASSESS"]["parallel"] == []
        assert [k for k in cfg["REVIEW"]["vars"] if k.endswith("_PATH")] == ["ASSESS_PATH"]

    def test_an_unregistered_name_exits_2_with_the_registered_list(
        self, tmp_dir, monkeypatch, drop_in_root
    ):
        self._registry(monkeypatch, drop_in_root.memo())
        code, err = self._init(["--type", "bogus"])
        assert code == 2
        assert "invalid choice: 'bogus'" in err and "'memo'" in err
        assert not os.path.exists(ps.STATE_FILE)

    def test_a_partial_drop_in_is_refused_before_any_state_is_written(
        self, tmp_dir, monkeypatch, drop_in_root
    ):
        self._registry(monkeypatch, drop_in_root.memo(drop=("pipeline.prompts.split_rules",)))
        assert "memo" in ps._TYPES.choices() and "memo" not in ps.PIPELINE_TYPES
        code, err = self._init(["--type", "memo"])
        assert code == 2 and "invalid choice: 'memo'" in err
        assert not os.path.exists(ps.STATE_FILE)

    def test_a_type_whose_stages_omit_an_engine_stage_is_refused_at_init(
        self, tmp_dir, monkeypatch, drop_in_root
    ):
        """The phase table renders the auto-fix launch block on every agent: a registered type
        that does not ship the stage would raise an uncaught ResolveError at the first wave."""
        stages = ["create", "review", "submit", "split", "speedrun"]
        drop_in_root.add("memo", overrides={**_memo_overrides(), "pipeline.stages": stages})
        self._registry(monkeypatch, drop_in_root.path)
        assert "memo" in ps.PIPELINE_TYPES
        assert ps._missing_engine_stages("memo") == ["auto-fix"]
        code, err = self._init(["--type", "memo"])
        assert code == 2
        assert "type 'memo' declares pipeline.stages without auto-fix" in err
        assert "create, review, split, auto-fix" in err
        assert not os.path.exists(ps.STATE_FILE)
        assert (
            ps._missing_engine_stages("rfe") == [] and ps._missing_engine_stages("initiative") == []
        )

    def test_children_inherit_the_marker(self, tmp_dir, monkeypatch):
        """The point of the export: a subprocess launched after init --headless sees it."""
        import io
        from contextlib import redirect_stdout

        monkeypatch.delenv(ps.HEADLESS_MARKER_ENV, raising=False)
        with redirect_stdout(io.StringIO()):
            ps.cmd_init(["--headless"])
        probe = subprocess.run(
            [sys.executable, "-c", f"import os; print(os.environ.get({ps.HEADLESS_MARKER_ENV!r}))"],
            capture_output=True,
            text=True,
        )
        assert probe.stdout.strip() == "1"


# ---------- Wave stall guard ----------

SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
POLL_SECS = 90  # the --max-wait the barrier passes; one fake poll consumes this much fake time
RFE_SCORES = {"what": 2, "why": 1, "open_to_how": 1, "not_a_task": 0, "right_sized": 1}


@pytest.fixture
def stall_dir(tmp_dir, monkeypatch):
    """tmp_dir plus a scripts/ symlink and clean knobs.

    The escalation stub is written by ``python3 scripts/frontmatter.py`` (verify_phase's
    writer) and the consumer runs below shell out to ``scripts/*.py`` relative to cwd.
    """
    os.symlink(SCRIPTS_DIR, tmp_dir / "scripts")
    for d in ("artifacts/initiative-reviews", "artifacts/initiatives", "artifacts/rfe-originals"):
        os.makedirs(d, exist_ok=True)
    for var in ("PIPELINE_WAVE_STALL_SECS", "PIPELINE_WAVE_RETRY_CAP"):
        monkeypatch.delenv(var, raising=False)
    return tmp_dir


class _Clock:
    def __init__(self):
        self.t = 1_700_000_000.0

    def now(self):
        return self.t

    def advance(self, secs):
        self.t += secs


def _arm(monkeypatch, clock, pending, poll_rc=3, during_poll=None):
    """Fake clock, fake check_id, fake barrier subprocess.

    ``pending`` is a live set of ids or (poll phase, id) pairs that check_id reports pending on
    every call (everything else is completed), so a test can flip an id to completed. The fake
    ``check_review_progress.py`` run consumes POLL_SECS of fake time and returns ``poll_rc``;
    ``during_poll(n)``, if given, runs inside the n-th poll once that time has passed, so a test
    can complete a slot while the barrier is blocked. Every other subprocess (frontmatter.py,
    verify_phase.py, the consumer scripts) runs for real. Returns the list of barrier
    invocations and the real check_id for consumer assertions.
    """
    import check_review_progress as crp

    monkeypatch.setattr(ps, "_now", clock.now)
    real_check_id = crp.check_id

    def fake_check_id(phase, rid):
        return "pending" if rid in pending or (phase, rid) in pending else "completed"

    monkeypatch.setattr(crp, "check_id", fake_check_id)
    real_run = subprocess.run
    polls = []

    def fake_run(cmd, **kw):
        if any(str(c).endswith("check_review_progress.py") for c in cmd):
            polls.append(list(cmd))
            clock.advance(POLL_SECS)
            if during_poll:
                during_poll(len(polls))
            return types.SimpleNamespace(returncode=poll_rc)
        return real_run(cmd, **kw)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return polls, real_check_id


def _drive(max_calls=100):
    """Re-run wait-for-wave on exit 3 exactly as the orchestrator does.

    Returns the number of calls it took to return (exit 0); fails if the barrier does not
    terminate within ``max_calls`` — the bounded-barrier property itself.
    """
    for n in range(1, max_calls + 1):
        try:
            ps.cmd_wait_for_wave([])
        except SystemExit as exc:
            assert exc.code == 3
            continue
        return n
    pytest.fail(f"wait-for-wave did not terminate within {max_calls} calls")


def _stall_lines(capsys):
    captured = capsys.readouterr()
    return captured.out, [
        ln for ln in captured.err.splitlines() if ln.startswith("wait-for-wave: STALL")
    ]


def _write_review(rid, **over):
    from artifact_utils import write_frontmatter

    data = {
        "rfe_id": rid,
        "score": 5,
        "pass": False,
        "recommendation": "revise",
        "feasibility": "feasible",
        "scores": dict(RFE_SCORES),
    }
    data.update(over)
    write_frontmatter(f"artifacts/rfe-reviews/{rid}-review.md", data, "rfe-review")


def _write_raw_review(rid, **over):
    """A review written by hand, outside the schema: ``feasibility: likely`` is not in the
    enum, so update_frontmatter refuses to touch it, yet check_id's review row (score present,
    no error) reads it as completed and the REVISE / SPLIT waves are reachable."""
    data = {
        "rfe_id": rid,
        "score": 5,
        "pass": False,
        "recommendation": "revise",
        "feasibility": "likely",
        "scores": dict(RFE_SCORES),
    }
    data.update(over)
    import yaml

    with open(f"artifacts/rfe-reviews/{rid}-review.md", "w") as f:
        f.write("---\n" + yaml.safe_dump(data, sort_keys=False) + "---\nbody\n")


def _write_task(rid, body="body\n", **over):
    fm = {"rfe_id": rid, "title": f"T {rid}", "priority": "Major", "status": "Ready"}
    fm.update(over)
    import yaml

    text = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n" + body
    with open(f"artifacts/rfe-tasks/{rid}.md", "w") as f:
        f.write(text)
    with open(f"artifacts/rfe-originals/{rid}.md", "w") as f:
        f.write(text)


def _sh(*argv):
    result = subprocess.run(["python3", *argv], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _expected_stub(pipeline_type, rid, error):
    """The registry error stub for ``rid`` as frontmatter.py writes it (defaults applied)."""
    import artifact_utils
    import type_registry
    import validate_types

    desc = type_registry.load().get(pipeline_type)
    stub = validate_types.build_error_stub(desc, phase=error.rsplit("_", 1)[0])
    stub[desc.id_field] = rid
    stub["error"] = error
    stub["needs_attention_reason"] = f"Agent failed: {error}"
    return artifact_utils.apply_defaults(stub, f"{pipeline_type}-review")


class TestStallRetryCounters:
    def test_malformed_persisted_counts_read_as_zero(self, tmp_dir):
        """A hand-edited or truncated counter file must not make a stalled wave fail on
        ``None < cap`` or buy/deny a retry: only a non-negative non-bool int counts."""
        os.makedirs("tmp", exist_ok=True)
        with open(ps.STALL_RETRIES_FILE, "w") as f:
            f.write(
                "ASSESS:RHAIRFE-1002:\n"  # null
                "ASSESS:RHAIRFE-1003: '2'\n"  # string
                "ASSESS:RHAIRFE-1004: true\n"  # bool
                "ASSESS:RHAIRFE-1005: -3\n"  # negative
                "ASSESS:RHAIRFE-1006: 1.5\n"  # float
                "ASSESS:RHAIRFE-1007: 1\n"  # the only valid one
                "7: 3\n"  # non-string key is dropped
            )
        assert ps._read_stall_retries() == {
            "ASSESS:RHAIRFE-1002": 0,
            "ASSESS:RHAIRFE-1003": 0,
            "ASSESS:RHAIRFE-1004": 0,
            "ASSESS:RHAIRFE-1005": 0,
            "ASSESS:RHAIRFE-1006": 0,
            "ASSESS:RHAIRFE-1007": 1,
        }

    def test_non_mapping_file_reads_as_empty(self, tmp_dir):
        os.makedirs("tmp", exist_ok=True)
        with open(ps.STALL_RETRIES_FILE, "w") as f:
            f.write("- not\n- a\n- mapping\n")
        assert ps._read_stall_retries() == {}


class TestWaveStallPolicy:
    """WAVE_STALL_POLICY is keyed by pipeline phase and must classify every agent phase."""

    @pytest.mark.parametrize("ptype", sorted(ps.PIPELINE_TYPES))
    def test_every_agent_phase_is_classified(self, ptype):
        config = ps._build_phase_config(ptype)
        agent_phases = {p for p, c in config.items() if c.get("type") == "agent"}
        assert set(ps.WAVE_STALL_POLICY) == agent_phases
        assert set(ps.WAVE_STALL_POLICY.values()) <= {ps.RETRY, ps.ESCALATE}

    @pytest.mark.parametrize("ptype", sorted(ps.PIPELINE_TYPES))
    def test_escalate_only_is_exactly_the_mutating_phases(self, ptype):
        """Retry re-dispatches concurrently, so only single-output-file agents may be retried:
        the phases polling the revise or split base are the ones that edit or mint artifacts."""
        prefix = ps.PIPELINE_TYPES[ptype]["poll_prefix"]
        for phase, config in ps._build_phase_config(ptype).items():
            if config.get("type") != "agent":
                continue
            base = config["poll_phase"][len(prefix) :]
            mutating = base in ("revise", "split")
            assert (ps.WAVE_STALL_POLICY[phase] == ps.ESCALATE) == mutating, phase

    def test_knobs(self, monkeypatch):
        for var in ("PIPELINE_WAVE_STALL_SECS", "PIPELINE_WAVE_RETRY_CAP"):
            monkeypatch.delenv(var, raising=False)
        assert ps._stall_window(ps.RETRY) == 900
        assert ps._stall_window(ps.ESCALATE) == 1800
        assert ps._wave_retry_cap() == 2
        monkeypatch.setenv("PIPELINE_WAVE_STALL_SECS", "600")
        monkeypatch.setenv("PIPELINE_WAVE_RETRY_CAP", "1")
        assert (ps._stall_window(ps.RETRY), ps._stall_window(ps.ESCALATE)) == (600, 1200)
        assert ps._wave_retry_cap() == 1
        monkeypatch.setenv("PIPELINE_WAVE_STALL_SECS", "0")
        assert ps._stall_window(ps.RETRY) == 0 and ps._stall_window(ps.ESCALATE) == 0
        monkeypatch.setenv("PIPELINE_WAVE_STALL_SECS", "-5")
        assert ps._stall_window(ps.ESCALATE) == 0
        monkeypatch.setenv("PIPELINE_WAVE_STALL_SECS", "soon")
        monkeypatch.setenv("PIPELINE_WAVE_RETRY_CAP", "-1")
        assert ps._stall_window(ps.RETRY) == 900  # unparsable -> default
        assert ps._wave_retry_cap() == 0


class TestWaveStall:
    """wait-for-wave terminates on a silently-dead subagent (design D14 / R2).

    Retry-eligible phases re-dispatch the stuck id up to the cap, then escalate it through the
    verify_phase failure contract; escalate-only phases wait a double window and escalate with
    the marker their consumers already handle. No fake agent output is ever written.
    """

    def _assess(self, ids, pipeline_type="rfe", phase="ASSESS"):
        ps._save_state(make_state(phase=phase, type=pipeline_type, batch=1))
        write_ids("tmp/pipeline-active-ids.txt", ids)
        write_ids("tmp/pipeline-all-ids.txt", ids)
        write_ids(ps.WAVE_IDS_FILE, ids)

    def test_bounded_barrier_retries_then_escalates(self, stall_dir, monkeypatch, capsys):
        """The plan's bounded-barrier test: one id never completes; the loop still ends."""
        ids = ["RHAIRFE-1001", "RHAIRFE-1002"]
        self._assess(ids)
        _write_review("RHAIRFE-1001", score=9, **{"pass": True, "recommendation": "submit"})
        clock, pending = _Clock(), {"RHAIRFE-1002"}
        _arm(monkeypatch, clock, pending)
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "")  # pre_script / post_verify

        # Stall 1 and 2: the id is left pending, its counter bumped, no marker written, and
        # next-action re-derives a wave that contains it.
        rerun = "Re-run: python3 scripts/pipeline_state.py wait-for-wave\n"
        for attempt in (1, 2):
            assert _drive() == 900 // POLL_SECS
            out, lines = _stall_lines(capsys)
            assert out == rerun * 9  # nine exit-3 polls; the call that returned prints no Re-run
            assert lines == [
                "wait-for-wave: STALL in ASSESS (assess+feasibility): no wave slot reached a"
                " terminal state for 900s (window 900s, policy retry); re-dispatching"
                f" RHAIRFE-1002 (attempt {attempt}/2)"
            ]
            assert ps._read_stall_retries() == {"ASSESS:RHAIRFE-1002": attempt}
            assert "RHAIRFE-1002" in read_ids("tmp/pipeline-active-ids.txt")
            assert not os.path.exists("artifacts/rfe-reviews/RHAIRFE-1002-review.md")
            assert not os.path.exists(ps.WAVE_PROGRESS_FILE)  # fresh window for the retry
            action = _run_next_action()
            assert action["action"] == "launch_wave"
            assert all("RHAIRFE-1002" in a["vars"] for a in action["agents"])
            assert read_ids(ps.WAVE_IDS_FILE) == ["RHAIRFE-1002"]

        # Stall 3: past the cap -> escalated exactly like a verify_phase failure.
        assert _drive() == 900 // POLL_SECS
        out, lines = _stall_lines(capsys)
        assert out == rerun * 9
        assert lines == [
            "wait-for-wave: STALL in ASSESS (assess+feasibility): no wave slot reached a"
            " terminal state for 900s (window 900s, policy retry); escalating RHAIRFE-1002"
            " (retry cap 2 reached) -> assess_stalled error-stub review, removed from the wave"
            " and tmp/pipeline-active-ids.txt"
        ]
        from artifact_utils import read_frontmatter

        data, _ = read_frontmatter("artifacts/rfe-reviews/RHAIRFE-1002-review.md")
        assert data == _expected_stub("rfe", "RHAIRFE-1002", "assess_stalled")
        assert not os.path.exists("tmp/rfe-assess/single/RHAIRFE-1002.result.md")
        assert not os.path.exists("artifacts/rfe-reviews/RHAIRFE-1002-feasibility.md")
        assert read_ids(ps.WAVE_IDS_FILE) == []
        assert read_ids("tmp/pipeline-active-ids.txt") == ["RHAIRFE-1001"]
        assert ps._read_stall_retries() == {"ASSESS:RHAIRFE-1002": 2}

        # The barrier is released: advance proceeds on the remaining id.
        config = ps._get_config(ps._load_state())["ASSESS"]
        assert ps._check_agent_phase_complete(config)
        ps.cmd_advance([])
        assert "ASSESS → REVIEW" in capsys.readouterr().out
        assert ps._load_state()["phase"] == "REVIEW"

        # ERROR_COLLECT classifies the stall as retryable and queues the one retry batch.
        ps._save_state(make_state(phase="BATCH_DONE", type="rfe", batch=1, total_batches=1))
        out = _sh("scripts/error_collect.py", "--type", "rfe")
        assert "retry batch with 1 error IDs [RHAIRFE-1002]" in out
        assert read_ids("tmp/pipeline-retry-ids.txt") == ["RHAIRFE-1002"]
        import yaml

        with open("tmp/pipeline-retry-errors.yaml") as f:
            assert yaml.safe_load(f) == {"RHAIRFE-1002": {"error": "assess_stalled"}}

    def test_post_verify_runs_on_the_survivors_only_after_escalation(
        self, stall_dir, monkeypatch, capsys
    ):
        """The leg the bounded-barrier test stubs out. After the escalation next-action finds
        ASSESS complete and runs the REAL post_verify over the ids file the guard rewrote: the
        survivor's outputs satisfy it and it drops nothing, the escalated id (no longer in that
        file) is not re-stubbed as assess_failed over its assess_stalled stub, and the phase
        advances to a REVIEW wave of the survivor alone."""
        ids = ["RHAIRFE-1001", "RHAIRFE-1002"]
        self._assess(ids)
        monkeypatch.setenv("PIPELINE_WAVE_RETRY_CAP", "0")
        outputs = [
            "tmp/rfe-assess/single/RHAIRFE-1001.result.md",
            "artifacts/rfe-reviews/RHAIRFE-1001-feasibility.md",
        ]
        for path in outputs:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write("output\n")
        # RHAIRFE-1002 never finishes; RHAIRFE-1001's review slot stays pending so next-action
        # stops at REVIEW's first wave instead of walking the rest of the batch.
        clock, pending = _Clock(), {"RHAIRFE-1002", ("review", "RHAIRFE-1001")}
        _arm(monkeypatch, clock, pending)
        scripts, real_run_script = [], ps._run_script

        def spy(cmd):
            out = real_run_script(cmd)
            scripts.append((cmd, out))
            return out

        monkeypatch.setattr(ps, "_run_script", spy)

        assert _drive() == 10
        _, lines = _stall_lines(capsys)
        assert "escalating RHAIRFE-1002 (retry cap 0 reached) -> assess_stalled" in lines[0]
        assert read_ids("tmp/pipeline-active-ids.txt") == ["RHAIRFE-1001"]
        assert scripts == []  # the escalation itself runs no script

        action = _run_next_action()
        assert scripts == [
            (
                "python3 scripts/verify_phase.py --type rfe --phase assess"
                " --ids-file tmp/pipeline-active-ids.txt",
                "FAILED=",
            )
        ]
        assert "ASSESS → REVIEW" in capsys.readouterr().err
        assert ps._load_state()["phase"] == "REVIEW"
        assert action["action"] == "launch_wave" and action["phase"] == "REVIEW"
        # the launch block precedes the phase vars (PR-5b); the survivor is the only id
        assert ["ID=RHAIRFE-1001" in a["vars"].splitlines() for a in action["agents"]] == [True]
        assert read_ids("tmp/pipeline-active-ids.txt") == ["RHAIRFE-1001"]
        assert all(os.path.exists(p) for p in outputs)
        from artifact_utils import read_frontmatter

        data, _ = read_frontmatter("artifacts/rfe-reviews/RHAIRFE-1002-review.md")
        assert data == _expected_stub("rfe", "RHAIRFE-1002", "assess_stalled")
        assert not os.path.exists("artifacts/rfe-reviews/RHAIRFE-1001-review.md")

    def test_mixed_wave_retries_one_and_escalates_the_other(self, stall_dir, monkeypatch, capsys):
        ids = ["RHAIRFE-1002", "RHAIRFE-1003"]
        self._assess(ids)
        ps._write_stall_retries({"ASSESS:RHAIRFE-1002": 2})  # already used its retries
        clock, pending = _Clock(), set(ids)
        _arm(monkeypatch, clock, pending)
        assert _drive() == 10
        _, lines = _stall_lines(capsys)
        assert lines == [
            "wait-for-wave: STALL in ASSESS (assess+feasibility): no wave slot reached a"
            " terminal state for 900s (window 900s, policy retry); re-dispatching RHAIRFE-1003"
            " (attempt 1/2); escalating RHAIRFE-1002 (retry cap 2 reached) -> assess_stalled"
            " error-stub review, removed from the wave and tmp/pipeline-active-ids.txt"
        ]
        assert read_ids(ps.WAVE_IDS_FILE) == ["RHAIRFE-1003"]
        assert read_ids("tmp/pipeline-active-ids.txt") == ["RHAIRFE-1003"]
        assert ps._read_stall_retries() == {"ASSESS:RHAIRFE-1002": 2, "ASSESS:RHAIRFE-1003": 1}

    def test_stub_names_the_agent_that_died(self, stall_dir, monkeypatch, capsys):
        """Only the feasibility companion is stuck: the stub says feasibility_stalled, and no
        feasibility file is fabricated (the assess result the scorer wrote is untouched)."""
        self._assess(["RHAIRFE-1002"])
        monkeypatch.setenv("PIPELINE_WAVE_RETRY_CAP", "0")
        clock, pending = _Clock(), {("feasibility", "RHAIRFE-1002")}
        _arm(monkeypatch, clock, pending)
        assert _drive() == 10
        from artifact_utils import read_frontmatter

        data, _ = read_frontmatter("artifacts/rfe-reviews/RHAIRFE-1002-review.md")
        assert data == _expected_stub("rfe", "RHAIRFE-1002", "feasibility_stalled")
        assert not os.path.exists("artifacts/rfe-reviews/RHAIRFE-1002-feasibility.md")
        _, lines = _stall_lines(capsys)
        assert "-> feasibility_stalled error-stub review" in lines[0]
        assert "(retry cap 0 reached)" in lines[0]

    def test_initiative_stub_uses_its_id_field_and_score_fields(
        self, stall_dir, monkeypatch, capsys
    ):
        self._assess(["RHOAIENG-1002"], pipeline_type="initiative")
        monkeypatch.setenv("PIPELINE_WAVE_RETRY_CAP", "0")
        clock, pending = _Clock(), {"RHOAIENG-1002"}
        _arm(monkeypatch, clock, pending)
        assert _drive() == 10
        from artifact_utils import read_frontmatter

        data, _ = read_frontmatter("artifacts/initiative-reviews/RHOAIENG-1002-review.md")
        assert data == _expected_stub("initiative", "RHOAIENG-1002", "assess_stalled")
        assert data["initiative_id"] == "RHOAIENG-1002"
        assert "scope" in data["scores"] and "not_a_task" not in data["scores"]
        assert not os.path.exists("artifacts/rfe-reviews/RHOAIENG-1002-review.md")
        _, lines = _stall_lines(capsys)
        assert lines[0].startswith(
            "wait-for-wave: STALL in ASSESS"
            " (initiative-assess+initiative-feasibility+initiative-alignment):"
        )
        assert read_ids("tmp/pipeline-active-ids.txt") == []

    def test_reset_on_progress(self, stall_dir, monkeypatch, capsys):
        """A completion inside the window moves the deadline; it is noticed on the pre-poll
        count of the next call (the post-poll re-check covers completions during the poll)."""
        ids = ["RHAIRFE-1001", "RHAIRFE-1002"]
        self._assess(ids)
        monkeypatch.setenv("PIPELINE_WAVE_RETRY_CAP", "0")
        clock, pending = _Clock(), set(ids)
        _arm(monkeypatch, clock, pending)
        for _ in range(5):  # 450s of no progress
            with pytest.raises(SystemExit):
                ps.cmd_wait_for_wave([])
        pending.discard("RHAIRFE-1001")  # progress at t=450
        # Without the reset the stall would fire 5 calls later; with it, 10 calls later.
        assert _drive() == 10
        _, lines = _stall_lines(capsys)
        assert "escalating RHAIRFE-1002" in lines[0] and "RHAIRFE-1001" not in lines[0]
        assert read_ids("tmp/pipeline-active-ids.txt") == ["RHAIRFE-1001"]

    def test_completion_during_the_poll_resets(self, stall_dir, monkeypatch, capsys):
        """The rule's other half: the count is re-checked when the 90-second poll returns, so a
        slot that turns terminal while the barrier is blocked in check_review_progress resets
        the deadline on that same call. Without the post-poll re-check this call would be the
        one that completes the window and fires the stall."""
        ids = ["RHAIRFE-1001", "RHAIRFE-1002"]
        self._assess(ids)
        monkeypatch.setenv("PIPELINE_WAVE_RETRY_CAP", "0")
        clock, pending = _Clock(), set(ids)
        polls_per_window = 900 // POLL_SECS

        def finish_1001_inside_the_window_completing_poll(n):
            if n == polls_per_window:
                pending.discard("RHAIRFE-1001")

        _arm(monkeypatch, clock, pending, during_poll=finish_1001_inside_the_window_completing_poll)
        for _ in range(polls_per_window - 1):  # 810s of no progress: one poll short of the window
            with pytest.raises(SystemExit) as exc:
                ps.cmd_wait_for_wave([])
            assert exc.value.code == 3
        assert ps._read_wave_progress()["done"] == 0
        capsys.readouterr()

        with pytest.raises(SystemExit) as exc:  # RHAIRFE-1001 finishes during this poll
            ps.cmd_wait_for_wave([])
        assert exc.value.code == 3
        out, lines = _stall_lines(capsys)
        assert lines == []
        assert out == "Re-run: python3 scripts/pipeline_state.py wait-for-wave\n"
        prog = ps._read_wave_progress()
        assert prog["done"] == 2  # RHAIRFE-1001's assess and feasibility slots
        assert prog["last_progress_ts"] == clock.now()  # reset by the post-poll re-check
        assert not os.path.exists("artifacts/rfe-reviews/RHAIRFE-1002-review.md")
        assert read_ids("tmp/pipeline-active-ids.txt") == ids

        # The stall fires a full window after that completion, not before.
        assert _drive() == polls_per_window
        _, lines = _stall_lines(capsys)
        assert "escalating RHAIRFE-1002" in lines[0] and "RHAIRFE-1001" not in lines[0]
        assert read_ids("tmp/pipeline-active-ids.txt") == ["RHAIRFE-1001"]

    def test_split_is_escalate_only(self, stall_dir, monkeypatch, capsys):
        """SPLIT: no re-dispatch (a second agent would mint duplicate children), a double
        window, the non-retryable split_not_attempted class and a no-split status file that
        every split consumer accepts."""
        parent = "RHAIRFE-1001"
        ps._save_state(make_state(phase="SPLIT", type="rfe", batch=1))
        write_ids("tmp/pipeline-split-ids.txt", [parent])
        write_ids("tmp/pipeline-all-ids.txt", [parent])
        write_ids(ps.WAVE_IDS_FILE, [parent])
        _write_review(parent, recommendation="split")
        clock, pending = _Clock(), {parent}
        _, real_check_id = _arm(monkeypatch, clock, pending)

        assert _drive() == 1800 // POLL_SECS  # twice the retry window
        assert not os.path.exists(ps.STALL_RETRIES_FILE)  # never counted as a retry
        _, lines = _stall_lines(capsys)
        assert lines == [
            "wait-for-wave: STALL in SPLIT (split): no wave slot reached a terminal state for"
            " 1800s (window 1800s, policy escalate-only); escalating RHAIRFE-1001 ->"
            " split_not_attempted error + no-split status file, removed from the wave and"
            " tmp/pipeline-split-ids.txt"
        ]
        from artifact_utils import read_frontmatter

        data, _ = read_frontmatter(f"artifacts/rfe-reviews/{parent}-review.md")
        assert data["error"].startswith("split_not_attempted: wave stalled in SPLIT")
        assert data["recommendation"] == "split" and data["score"] == 5  # review kept intact
        assert data["needs_attention"] is True  # submit.py labels + comments the parent
        assert data["needs_attention_reason"] == f"Agent failed: {data['error']}"
        assert read_ids(ps.WAVE_IDS_FILE) == [] and read_ids("tmp/pipeline-split-ids.txt") == []
        assert not [f for f in os.listdir("artifacts/rfe-tasks")]  # no child was minted

        # The barrier is released and the phase moves on.
        ps.cmd_advance([])
        assert "SPLIT → SPLIT_COLLECT" in capsys.readouterr().out

        # Consumers of the marker.
        assert real_check_id("split", parent) == "completed"  # the slot is terminal
        import yaml

        with open(f"artifacts/rfe-reviews/{parent}-split-status.yaml") as f:
            status = yaml.safe_load(f)
        assert status["action"] == "no-split" and status["status"] == "failed"
        assert status["reason"] == data["error"]
        write_ids("tmp/pipeline-split-ids.txt", [parent])  # were split_collect to read it...
        assert _sh("scripts/split_collect.py", "--type", "rfe").strip() == "CHILDREN=0"
        assert read_ids("tmp/pipeline-split-children-ids.txt") == []
        assert (
            read_frontmatter(f"artifacts/rfe-reviews/{parent}-review.md")[0]["recommendation"]
            == "revise"
        )  # ...the R8 no-split branch, never collect_children
        assert _sh("scripts/collect_children.py", "--type", "rfe", parent).strip() == f"{parent}:"
        ps._save_state(make_state(phase="BATCH_DONE", type="rfe", batch=1, total_batches=1))
        out = _sh("scripts/error_collect.py", "--type", "rfe")
        assert f"excluded from retry (non-retryable): {parent}" in out
        assert read_ids("tmp/pipeline-retry-ids.txt") == []

    def test_revise_is_escalate_only(self, stall_dir, monkeypatch, capsys):
        """REVISE: no re-dispatch (a second agent would double-edit the task file), a double
        window, the retryable revise_stalled error on the real review with auto_revised left
        false; ERROR_COLLECT's revise path restores the task file before the retry."""
        rid = "RHAIRFE-1001"
        ps._save_state(make_state(phase="REVISE", type="rfe", batch=1))
        write_ids("tmp/pipeline-revise-ids.txt", [rid])
        write_ids("tmp/pipeline-active-ids.txt", [rid])
        write_ids("tmp/pipeline-all-ids.txt", [rid])
        write_ids(ps.WAVE_IDS_FILE, [rid])
        _write_review(rid)
        fm = f"---\nrfe_id: {rid}\ntitle: T\npriority: Major\nstatus: Ready\n---\n"
        with open(f"artifacts/rfe-originals/{rid}.md", "w") as f:
            f.write(fm + "original body\n")
        with open(f"artifacts/rfe-tasks/{rid}.md", "w") as f:
            f.write(fm + "half-edited body\n")  # the dead agent got this far
        with open(f"artifacts/rfe-tasks/{rid}-removed-context.yaml", "w") as f:
            f.write("items: []\n")
        clock, pending = _Clock(), {rid}
        _, real_check_id = _arm(monkeypatch, clock, pending)

        assert _drive() == 1800 // POLL_SECS
        assert not os.path.exists(ps.STALL_RETRIES_FILE)
        _, lines = _stall_lines(capsys)
        assert lines == [
            "wait-for-wave: STALL in REVISE (revise): no wave slot reached a terminal state for"
            " 1800s (window 1800s, policy escalate-only); escalating RHAIRFE-1001 ->"
            " revise_stalled error (auto_revised untouched), removed from the wave and"
            " tmp/pipeline-revise-ids.txt"
        ]
        from artifact_utils import read_frontmatter

        data, _ = read_frontmatter(f"artifacts/rfe-reviews/{rid}-review.md")
        assert data["error"] == "revise_stalled"
        assert data["auto_revised"] is False  # no revision is claimed
        assert data["needs_attention"] is True
        assert data["score"] == 5 and data["recommendation"] == "revise"  # review kept intact
        assert read_ids(ps.WAVE_IDS_FILE) == [] and read_ids("tmp/pipeline-revise-ids.txt") == []
        assert read_ids("tmp/pipeline-active-ids.txt") == [rid]  # still in the batch

        # The revise row of check_id keys on auto_revised / recommendation=split, so the
        # marker alone would still poll as pending: the barrier releases because the id left
        # the wave and the phase's ids file, which is what advance's guard reads.
        assert real_check_id("revise", rid) == "pending"
        config = ps._get_config(ps._load_state())["REVISE"]
        assert ps._check_agent_phase_complete(config)
        ps.cmd_advance([])
        assert "REVISE → FIXUP" in capsys.readouterr().out

        # Consumers: not reassessed, bucketed as an error, retried after a restore.
        out = _sh("scripts/collect_recommendations.py", "--type", "rfe", "--reassess", rid)
        assert "REASSESS=\n" in out and f"DONE={rid}" in out
        assert f"ERRORS={rid}" in _sh("scripts/collect_recommendations.py", "--type", "rfe", rid)
        ps._save_state(make_state(phase="BATCH_DONE", type="rfe", batch=1, total_batches=1))
        out = _sh("scripts/error_collect.py", "--type", "rfe")
        assert f"retry batch with 1 error IDs [{rid}]" in out
        with open(f"artifacts/rfe-tasks/{rid}.md") as f:
            assert f.read().endswith("original body\n")
        assert not os.path.exists(f"artifacts/rfe-tasks/{rid}-removed-context.yaml")

    def test_zero_window_disables_the_guard(self, stall_dir, monkeypatch, capsys):
        self._assess(["RHAIRFE-1002"])
        monkeypatch.setenv("PIPELINE_WAVE_STALL_SECS", "0")
        clock, pending = _Clock(), {"RHAIRFE-1002"}
        _arm(monkeypatch, clock, pending)
        before = sorted(os.listdir("tmp"))
        for _ in range(50):  # 4500s, five retry windows: still the old unbounded wait
            with pytest.raises(SystemExit) as exc:
                ps.cmd_wait_for_wave([])
            assert exc.value.code == 3
        out, lines = _stall_lines(capsys)
        assert lines == []
        assert out == "Re-run: python3 scripts/pipeline_state.py wait-for-wave\n" * 50
        assert sorted(os.listdir("tmp")) == before  # not even the tracker
        assert not os.path.exists("artifacts/rfe-reviews/RHAIRFE-1002-review.md")

    def test_new_wave_signature_resets_the_tracker(self, stall_dir, monkeypatch):
        self._assess(["RHAIRFE-1002"])
        clock, pending = _Clock(), {"RHAIRFE-1002"}
        _arm(monkeypatch, clock, pending)
        stale = {
            "sig": "ASSESS|RHAIRFE-0999",
            "phase": "ASSESS",
            "last_progress_ts": 1.0,
            "done": 0,
        }
        ps._write_wave_progress(stale)
        with pytest.raises(SystemExit) as exc:  # not a stall, despite the ancient timestamp
            ps.cmd_wait_for_wave([])
        assert exc.value.code == 3
        prog = ps._read_wave_progress()
        assert prog["sig"] == "ASSESS|RHAIRFE-1002"
        assert prog["last_progress_ts"] == clock.now() - POLL_SECS  # the pre-poll timestamp
        assert prog["done"] == 0

    def test_no_stall_leaves_no_trace(self, stall_dir, monkeypatch, capsys):
        """Byte-identical behaviour when nothing stalls: same output, same exit codes, and
        the only file the guard adds is its own tmp/ tracker while a wave is pending."""
        self._assess(["RHAIRFE-1001", "RHAIRFE-1002"])
        before_tmp = sorted(os.listdir("tmp"))
        before_reviews = sorted(os.listdir("artifacts/rfe-reviews"))
        clock, pending = _Clock(), {"RHAIRFE-1002"}
        _arm(monkeypatch, clock, pending)
        with pytest.raises(SystemExit) as exc:
            ps.cmd_wait_for_wave([])
        assert exc.value.code == 3
        captured = capsys.readouterr()
        assert captured.out == "Re-run: python3 scripts/pipeline_state.py wait-for-wave\n"
        assert captured.err == ""
        assert sorted(os.listdir("tmp")) == sorted(before_tmp + ["pipeline-wave-progress.yaml"])
        assert sorted(os.listdir("artifacts/rfe-reviews")) == before_reviews
        assert read_ids("tmp/pipeline-active-ids.txt") == ["RHAIRFE-1001", "RHAIRFE-1002"]

        pending.clear()
        _arm(monkeypatch, clock, pending, poll_rc=0)
        ps.cmd_wait_for_wave([])  # returns
        captured = capsys.readouterr()
        assert captured.out == "" and captured.err == ""
        assert sorted(os.listdir("tmp")) == before_tmp  # tracker cleared on completion
        assert not os.path.exists(ps.STALL_RETRIES_FILE)

    def test_state_clean_wipes_the_tracker_files(self, stall_dir):
        ps._write_wave_progress({"sig": "x", "phase": "ASSESS", "last_progress_ts": 1.0, "done": 0})
        ps._write_stall_retries({"ASSESS:RHAIRFE-1": 1})
        _sh("scripts/state.py", "clean")
        assert not os.path.exists(ps.WAVE_PROGRESS_FILE)
        assert not os.path.exists(ps.STALL_RETRIES_FILE)

    # ----- a new wave never inherits a previous wave's deadline -----

    def test_wave_written_by_next_action_starts_a_fresh_window(
        self, stall_dir, monkeypatch, capsys
    ):
        """The previous barrier never observed exit 0: the agent finished and the orchestrator
        ran next-action instead of re-running wait-for-wave (the deviation class seen in
        production), so the tracker stays on disk. When the same phase later runs the same id
        (the retry batch, a later reassess cycle) the signature matches, and the new wave must
        not inherit the old deadline and be declared stalled on its first poll."""
        rid = "RHAIRFE-1002"
        self._assess([rid])
        clock, pending = _Clock(), {rid}
        _arm(monkeypatch, clock, pending)
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "")
        with pytest.raises(SystemExit) as exc:
            ps.cmd_wait_for_wave([])
        assert exc.value.code == 3
        stale = ps._read_wave_progress()
        assert stale["sig"] == f"ASSESS|{rid}"

        pending.clear()  # the agent finishes...
        action = _run_next_action()  # ...and the orchestrator deviates to next-action
        assert action["action"] != "launch_wave"  # ASSESS (and REVIEW) complete, no new wave
        assert ps._read_wave_progress() == stale  # the abandoned tracker is still there

        # Much later the retry batch runs ASSESS over the same id: fresh agents, fresh wave.
        clock.advance(4000)
        ps._save_state(make_state(phase="ASSESS", type="rfe", batch=2, retry_cycle=1))
        write_ids("tmp/pipeline-active-ids.txt", [rid])
        pending.add(rid)
        action = _run_next_action()
        assert action["action"] == "launch_wave" and read_ids(ps.WAVE_IDS_FILE) == [rid]
        assert not os.path.exists(ps.WAVE_PROGRESS_FILE)  # writing the wave dropped it
        capsys.readouterr()

        with pytest.raises(SystemExit) as exc:  # first poll: pending, not stalled
            ps.cmd_wait_for_wave([])
        assert exc.value.code == 3
        out, lines = _stall_lines(capsys)
        assert lines == [] and out == "Re-run: python3 scripts/pipeline_state.py wait-for-wave\n"
        assert not os.path.exists(ps.STALL_RETRIES_FILE)
        prog = ps._read_wave_progress()
        assert prog["sig"] == f"ASSESS|{rid}"
        assert prog["last_progress_ts"] == clock.now() - POLL_SECS  # the new wave's own clock

    def test_wave_written_by_set_wave_starts_a_fresh_window(self, stall_dir, monkeypatch, capsys):
        rid = "RHAIRFE-1002"
        self._assess([rid])
        clock, pending = _Clock(), {rid}
        _arm(monkeypatch, clock, pending)
        stale_ts = clock.now() - 5000
        ps._write_wave_progress(
            {"sig": f"ASSESS|{rid}", "phase": "ASSESS", "last_progress_ts": stale_ts, "done": 0}
        )
        ps.cmd_set_wave([rid])
        assert not os.path.exists(ps.WAVE_PROGRESS_FILE)
        capsys.readouterr()
        with pytest.raises(SystemExit) as exc:
            ps.cmd_wait_for_wave([])
        assert exc.value.code == 3
        _, lines = _stall_lines(capsys)
        assert lines == []
        assert ps._read_wave_progress()["last_progress_ts"] == clock.now() - POLL_SECS

    # ----- escalation never raises: a review the schema rejects gets the stub -----

    def _single_wave(self, phase, ids_file, rid):
        ps._save_state(make_state(phase=phase, type="rfe", batch=1))
        write_ids(ids_file, [rid])
        write_ids("tmp/pipeline-active-ids.txt", [rid])
        write_ids("tmp/pipeline-all-ids.txt", [rid])
        write_ids(ps.WAVE_IDS_FILE, [rid])

    def test_revise_escalation_survives_a_schema_invalid_review(
        self, stall_dir, monkeypatch, capsys
    ):
        """update_frontmatter refuses the review; without the fallback the ValidationError fired
        before the wave and ids files were rewritten and every re-run crashed the same way (a
        crash loop instead of a pending loop). The marker falls back to the registry stub with
        the same retryable error and the slot is released."""
        rid = "RHAIRFE-1001"
        self._single_wave("REVISE", "tmp/pipeline-revise-ids.txt", rid)
        _write_raw_review(rid)
        _write_task(rid)
        clock, pending = _Clock(), {rid}
        _arm(monkeypatch, clock, pending)

        assert _drive() == 1800 // POLL_SECS
        err = capsys.readouterr().err.splitlines()
        warn = [ln for ln in err if ln.startswith("wait-for-wave: could not mark")]
        assert warn == [
            f"wait-for-wave: could not mark {rid}'s review (Frontmatter validation failed after"
            f" update in artifacts/rfe-reviews/{rid}-review.md: - feasibility: 'likely' not in"
            " ['feasible', 'infeasible', 'indeterminate']); replacing it with the revise error"
            " stub (error=revise_stalled)"
        ]
        stall = [ln for ln in err if ln.startswith("wait-for-wave: STALL")]
        assert len(stall) == 1 and f"escalating {rid} -> revise_stalled error" in stall[0]
        from artifact_utils import read_frontmatter

        data, _ = read_frontmatter(f"artifacts/rfe-reviews/{rid}-review.md")
        assert data == _expected_stub("rfe", rid, "revise_stalled")
        assert read_ids(ps.WAVE_IDS_FILE) == [] and read_ids("tmp/pipeline-revise-ids.txt") == []
        assert not os.path.exists(ps.WAVE_PROGRESS_FILE)
        ps._save_state(make_state(phase="BATCH_DONE", type="rfe", batch=1, total_batches=1))
        out = _sh("scripts/error_collect.py", "--type", "rfe")
        assert f"retry batch with 1 error IDs [{rid}]" in out

    def test_split_escalation_survives_a_schema_invalid_review(
        self, stall_dir, monkeypatch, capsys
    ):
        """Same fallback on the split path, keeping the non-retryable class: the stub carries
        the split_not_attempted: error, the no-split status file is written first (it does not
        go through the schema, so the slot is terminal even if the review cannot be marked)."""
        parent = "RHAIRFE-1001"
        self._single_wave("SPLIT", "tmp/pipeline-split-ids.txt", parent)
        _write_raw_review(parent, recommendation="split")
        clock, pending = _Clock(), {parent}
        _, real_check_id = _arm(monkeypatch, clock, pending)

        assert _drive() == 1800 // POLL_SECS
        err = capsys.readouterr().err.splitlines()
        warn = [ln for ln in err if ln.startswith("wait-for-wave: could not mark")]
        assert len(warn) == 1
        assert warn[0].endswith(
            "replacing it with the split error stub (error=split_not_attempted: wave stalled in"
            " SPLIT: no agent reached a terminal state for 1800s (window 1800s); the subagent"
            " produced no output)"
        )
        assert len([ln for ln in err if ln.startswith("wait-for-wave: STALL")]) == 1
        from artifact_utils import read_frontmatter

        data, _ = read_frontmatter(f"artifacts/rfe-reviews/{parent}-review.md")
        error = data["error"]
        assert error.startswith("split_not_attempted: wave stalled in SPLIT")
        expected = _expected_stub("rfe", parent, "split_stalled")
        expected.update({"error": error, "needs_attention_reason": f"Agent failed: {error}"})
        assert data == expected
        import yaml

        with open(f"artifacts/rfe-reviews/{parent}-split-status.yaml") as f:
            status = yaml.safe_load(f)
        assert status == {"status": "failed", "action": "no-split", "reason": error}
        assert real_check_id("split", parent) == "completed"
        assert read_ids(ps.WAVE_IDS_FILE) == [] and read_ids("tmp/pipeline-split-ids.txt") == []
        assert not os.listdir("artifacts/rfe-tasks")
        ps._save_state(make_state(phase="BATCH_DONE", type="rfe", batch=1, total_batches=1))
        out = _sh("scripts/error_collect.py", "--type", "rfe")
        assert f"excluded from retry (non-retryable): {parent}" in out

    def test_review_escalation_replaces_an_unmergeable_half_written_review(
        self, stall_dir, monkeypatch, capsys
    ):
        """A review agent that died after writing a partial file with a ``scores`` member the
        schema does not know: check_id reads it as pending (no score), and frontmatter.py's
        merge-then-validate cannot turn it into the stub. Before the fallback the escalation
        dropped the id from the ids file and left no error marker: error_collect never retried
        it and the run report showed it failed for no reason. Now the frontmatter is replaced
        with the review_stalled stub and ERROR_COLLECT queues the retry."""
        rid = "RHAIRFE-1002"
        self._assess([rid], phase="REVIEW")
        monkeypatch.setenv("PIPELINE_WAVE_RETRY_CAP", "0")
        with open(f"artifacts/rfe-reviews/{rid}-review.md", "w") as f:
            f.write(f"---\nrfe_id: {rid}\nscores:\n  bogus: 1\n---\n")
        clock, pending = _Clock(), {rid}
        _, real_check_id = _arm(monkeypatch, clock, pending)
        assert real_check_id("review", rid) == "pending"  # what the fake stands in for

        assert _drive() == 900 // POLL_SECS
        assert capsys.readouterr().err.splitlines() == [
            f"verify_phase: {rid}: frontmatter.py set failed (Error: Frontmatter validation"
            f" failed after update in artifacts/rfe-reviews/{rid}-review.md: - scores: unknown"
            " field 'bogus'); replaced the review frontmatter with the review_stalled stub",
            "wait-for-wave: STALL in REVIEW (review): no wave slot reached a terminal state for"
            f" 900s (window 900s, policy retry); escalating {rid} (retry cap 0 reached) ->"
            " review_stalled error-stub review, removed from the wave and"
            " tmp/pipeline-active-ids.txt",
        ]
        from artifact_utils import read_frontmatter

        data, _ = read_frontmatter(f"artifacts/rfe-reviews/{rid}-review.md")
        assert data == _expected_stub("rfe", rid, "review_stalled")
        assert real_check_id("review", rid) == "error"  # the slot is terminal
        assert read_ids(ps.WAVE_IDS_FILE) == [] and read_ids("tmp/pipeline-active-ids.txt") == []

        # ERROR_COLLECT classifies it as retryable and cleans the stub for the retry.
        ps._save_state(make_state(phase="BATCH_DONE", type="rfe", batch=1, total_batches=1))
        out = _sh("scripts/error_collect.py", "--type", "rfe")
        assert f"retry batch with 1 error IDs [{rid}]" in out
        assert read_ids("tmp/pipeline-retry-ids.txt") == [rid]
        import yaml

        with open("tmp/pipeline-retry-errors.yaml") as f:
            assert yaml.safe_load(f) == {rid: {"error": "review_stalled"}}
        assert not os.path.exists(f"artifacts/rfe-reviews/{rid}-review.md")

    # ----- what submit.py does with a stall-escalated split parent -----

    def test_split_marker_flags_the_parent_for_attention(self, stall_dir):
        """A stall-escalated split parent is still ``status: Ready`` with no children, so it is
        not a Phase 1 split parent for submit.py: it reaches Phase 2 as a regular item and is
        disposed there ("Label only", marked processed in the snapshot, so it is not
        re-selected until its Jira content changes — docs/wave-stall-guard.md). needs_attention
        on the marker is what turns that into a visible needs-attention label and comment
        instead of a silent feasibility-pass label."""
        parent = "RHAIRFE-1001"
        _write_task(parent, original_labels=["rfe-rubric-pass"])
        _write_review(parent, recommendation="split", score=6)
        reason = "wave stalled in SPLIT: test"
        assert ps._mark_split_not_attempted([parent], "rfe", reason) == ("split_not_attempted", {})
        from artifact_utils import read_frontmatter

        data, _ = read_frontmatter(f"artifacts/rfe-reviews/{parent}-review.md")
        assert data["error"] == f"split_not_attempted: {reason}"
        assert data["needs_attention"] is True
        assert data["needs_attention_reason"] == f"Agent failed: split_not_attempted: {reason}"
        assert data["recommendation"] == "split" and data["score"] == 6  # review kept intact

        import type_registry

        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("RFE_CREATOR_") and k not in type_registry.HEADLESS_MARKER_VARS
        }
        env.update(
            JIRA_SERVER="https://fake.atlassian.net", JIRA_USER="fake@example.com", JIRA_TOKEN="t"
        )
        result = subprocess.run(
            ["python3", "scripts/submit.py", "--type", "rfe", "--dry-run"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        plan_row = next(ln for ln in result.stdout.splitlines() if ln.startswith(parent))
        assert "Label only" in plan_row  # disposed in Phase 2, not re-selected
        assert f"{parent}: Would add labels: rfe-creator-needs-attention\n" in result.stdout
        assert f"{parent}: Would post needs-attention comment" in result.stdout
        # A review carrying an error has no feasibility verdict: the marker's inherited
        # feasibility: feasible earns no feasibility-pass label (submit.py, finding R2-4).
        assert "rfe-creator-feasibility" not in result.stdout

    # ----- the tracker record is normalized, never trusted -----

    @pytest.mark.parametrize("bad_ts", ["missing", None, "not-a-number", True])
    def test_tracker_without_a_usable_timestamp_starts_a_fresh_record(
        self, stall_dir, monkeypatch, capsys, bad_ts
    ):
        """A tracker for the current wave whose last_progress_ts is missing, null or not a
        number (a hand-edited or truncated file) used to survive _track_wave_progress and
        make every wait-for-wave call die with KeyError / TypeError on the idle computation:
        a crash loop instead of a bounded barrier. It is treated as absent: a fresh record,
        a fresh window, exit 3 as on any first poll."""
        rid = "RHAIRFE-1002"
        self._assess([rid])
        clock, pending = _Clock(), {rid}
        _arm(monkeypatch, clock, pending)
        record = {"sig": f"ASSESS|{rid}", "phase": "ASSESS", "done": 0}
        if bad_ts != "missing":
            record["last_progress_ts"] = bad_ts
        ps._write_wave_progress(record)

        with pytest.raises(SystemExit) as exc:
            ps.cmd_wait_for_wave([])
        assert exc.value.code == 3
        out, lines = _stall_lines(capsys)
        assert lines == [] and out == "Re-run: python3 scripts/pipeline_state.py wait-for-wave\n"
        prog = ps._read_wave_progress()
        assert prog == {
            "sig": f"ASSESS|{rid}",
            "phase": "ASSESS",
            "last_progress_ts": clock.now() - POLL_SECS,  # the pre-poll clock: a fresh record
            "done": 0,
        }
        assert not os.path.exists(f"artifacts/rfe-reviews/{rid}-review.md")

    @pytest.mark.parametrize("bad_done", ["3", None, 2.5, False])
    def test_tracker_done_is_coerced_to_an_int(self, stall_dir, monkeypatch, capsys, bad_done):
        """A ``done`` that is not an int is read as 0, so the comparison never raises and the
        worst case is one extra deadline reset (progress is counted from zero again)."""
        rid = "RHAIRFE-1002"
        self._assess([rid])
        clock, pending = _Clock(), {rid}
        _arm(monkeypatch, clock, pending)
        stale_ts = clock.now() - 100
        ps._write_wave_progress(
            {
                "sig": f"ASSESS|{rid}",
                "phase": "ASSESS",
                "last_progress_ts": stale_ts,
                "done": bad_done,
            }
        )
        with pytest.raises(SystemExit) as exc:
            ps.cmd_wait_for_wave([])
        assert exc.value.code == 3
        prog = ps._read_wave_progress()
        assert prog["done"] == 0 and isinstance(prog["done"], int)
        assert prog["last_progress_ts"] == stale_ts  # no progress (0 -> 0): the deadline stands
        assert prog["sig"] == f"ASSESS|{rid}"

    # ----- an id no marker could be written for is never retired silently -----

    def _drive_to_failure(self, max_calls=100):
        """Re-run wait-for-wave on exit 3 until it exits with another code; return that code."""
        for _ in range(max_calls):
            try:
                ps.cmd_wait_for_wave([])
            except SystemExit as exc:
                if exc.code == 3:
                    continue
                return exc.code
            pytest.fail("wait-for-wave returned 0 although an escalation failed")
        pytest.fail(f"wait-for-wave did not terminate within {max_calls} calls")

    def test_review_class_escalation_failure_is_loud_and_leaves_the_id_in_place(
        self, stall_dir, monkeypatch, capsys
    ):
        """The stub writer reports it could not write RHAIRFE-1003's stub. Before: the id was
        removed from the wave and the ids file anyway, so with no marker on disk it vanished
        from collect_recommendations --errors, error_collect and the report. Now: the sibling
        whose stub landed is retired as before, the unrecorded id stays in both files, one
        ESCALATION FAILED line names it and the command exits 1 instead of 0."""
        import verify_phase

        ids = ["RHAIRFE-1002", "RHAIRFE-1003"]
        self._assess(ids)
        monkeypatch.setenv("PIPELINE_WAVE_RETRY_CAP", "0")
        clock, pending = _Clock(), set(ids)
        _arm(monkeypatch, clock, pending)
        real_writer, calls = verify_phase.write_error_stubs, []

        def failing_writer(phase, ids, pipeline_type="rfe", **kw):
            calls.append((phase, list(ids), kw.get("outcome")))
            if "RHAIRFE-1003" in ids:
                return list(ids)  # nothing written, no reason given
            return real_writer(phase, ids, pipeline_type, **kw)

        monkeypatch.setattr(verify_phase, "write_error_stubs", failing_writer)

        assert self._drive_to_failure() == 1
        err = capsys.readouterr().err.splitlines()
        assert err == [
            "wait-for-wave: STALL in ASSESS (assess+feasibility): no wave slot reached a"
            " terminal state for 900s (window 900s, policy retry); escalating RHAIRFE-1002"
            " (retry cap 0 reached) -> assess_stalled error-stub review, removed from the wave"
            " and tmp/pipeline-active-ids.txt; escalation FAILED for RHAIRFE-1003 (retry cap 0"
            " reached) (see next line)",
            "wait-for-wave: ESCALATION FAILED for RHAIRFE-1003: no error marker could be"
            " written; left in the wave and tmp/pipeline-active-ids.txt - fix the reviews"
            " directory and re-run",
        ]
        assert calls == [
            ("assess", ["RHAIRFE-1002"], "stalled"),
            ("assess", ["RHAIRFE-1003"], "stalled"),
        ]
        from artifact_utils import read_frontmatter

        data, _ = read_frontmatter("artifacts/rfe-reviews/RHAIRFE-1002-review.md")
        assert data == _expected_stub("rfe", "RHAIRFE-1002", "assess_stalled")  # sibling marked
        assert not os.path.exists("artifacts/rfe-reviews/RHAIRFE-1003-review.md")
        assert read_ids(ps.WAVE_IDS_FILE) == ["RHAIRFE-1003"]  # left in the wave...
        assert read_ids("tmp/pipeline-active-ids.txt") == ["RHAIRFE-1003"]  # ...and the ids file
        assert not os.path.exists(ps.WAVE_PROGRESS_FILE)  # tracker cleared all the same
        assert not os.path.exists("tmp/rfe-assess/single/RHAIRFE-1003.result.md")

        # After the operator fixes the reviews directory, the re-run escalates it for real.
        monkeypatch.setattr(verify_phase, "write_error_stubs", real_writer)
        assert _drive() == 900 // POLL_SECS  # a fresh window, not an instant stall
        _, lines = _stall_lines(capsys)
        assert "escalating RHAIRFE-1003 (retry cap 0 reached) -> assess_stalled" in lines[0]
        data, _ = read_frontmatter("artifacts/rfe-reviews/RHAIRFE-1003-review.md")
        assert data == _expected_stub("rfe", "RHAIRFE-1003", "assess_stalled")
        assert read_ids(ps.WAVE_IDS_FILE) == [] and read_ids("tmp/pipeline-active-ids.txt") == []

    def test_revise_escalation_failure_carries_the_writers_reason(
        self, stall_dir, monkeypatch, capsys
    ):
        """Both writers fail for real (the review path is a directory): update_frontmatter
        raises, then frontmatter.py set and the replace both fail, and the ESCALATION FAILED
        line carries the writer's reason. The marked sibling is retired; the other stays in
        the wave and tmp/pipeline-revise-ids.txt and still shows up as an error nowhere, which
        is exactly why the exit code is 1."""
        ids = ["RHAIRFE-1001", "RHAIRFE-1002"]
        ps._save_state(make_state(phase="REVISE", type="rfe", batch=1))
        write_ids("tmp/pipeline-revise-ids.txt", ids)
        write_ids("tmp/pipeline-active-ids.txt", ids)
        write_ids("tmp/pipeline-all-ids.txt", ids)
        write_ids(ps.WAVE_IDS_FILE, ids)
        _write_review("RHAIRFE-1001")
        os.makedirs("artifacts/rfe-reviews/RHAIRFE-1002-review.md")  # neither writer can win
        clock, pending = _Clock(), set(ids)
        _arm(monkeypatch, clock, pending)

        assert self._drive_to_failure() == 1
        err = capsys.readouterr().err.splitlines()
        assert err[0].startswith("wait-for-wave: could not mark RHAIRFE-1002's review (")
        assert err[0].endswith("replacing it with the revise error stub (error=revise_stalled)")
        assert err[1].startswith(
            "verify_phase: RHAIRFE-1002: no revise_stalled stub written: frontmatter.py set"
            " failed ("
        )
        assert err[2] == (
            "wait-for-wave: STALL in REVISE (revise): no wave slot reached a terminal state for"
            " 1800s (window 1800s, policy escalate-only); escalating RHAIRFE-1001 ->"
            " revise_stalled error (auto_revised untouched), removed from the wave and"
            " tmp/pipeline-revise-ids.txt; escalation FAILED for RHAIRFE-1002 (see next line)"
        )
        assert err[3].startswith(
            "wait-for-wave: ESCALATION FAILED for RHAIRFE-1002: no error marker could be"
            " written (RHAIRFE-1002: frontmatter.py set failed ("
        )
        assert "and replacing the review frontmatter failed too (IsADirectoryError:" in err[3]
        assert err[3].endswith(
            "); left in the wave and tmp/pipeline-revise-ids.txt - fix the reviews directory"
            " and re-run"
        )
        assert len(err) == 4
        from artifact_utils import read_frontmatter

        data, _ = read_frontmatter("artifacts/rfe-reviews/RHAIRFE-1001-review.md")
        assert data["error"] == "revise_stalled" and data["score"] == 5  # sibling marked
        assert read_ids(ps.WAVE_IDS_FILE) == ["RHAIRFE-1002"]
        assert read_ids("tmp/pipeline-revise-ids.txt") == ["RHAIRFE-1002"]
        assert read_ids("tmp/pipeline-active-ids.txt") == ids
        assert not os.path.exists(ps.WAVE_PROGRESS_FILE)
        assert os.path.isdir("artifacts/rfe-reviews/RHAIRFE-1002-review.md")  # untouched
        # The barrier is NOT released for the phase: the id is still in the ids file.
        config = ps._get_config(ps._load_state())["REVISE"]
        assert not ps._check_agent_phase_complete(config)

    def test_split_escalation_failure_writes_no_status_file_for_the_unrecorded_parent(
        self, stall_dir, monkeypatch, capsys
    ):
        """Split path with the writers stubbed as the finding describes: update_frontmatter
        raises for one parent and the stub writer returns it as failed. That parent gets no
        no-split status file either (a status file would make its slot terminal and let the
        next poll release it with no error recorded), stays in the wave and
        tmp/pipeline-split-ids.txt, and the command exits 1. The sibling gets marker and
        status file and is retired."""
        import artifact_utils
        import verify_phase

        parents = ["RHAIRFE-1001", "RHAIRFE-1002"]
        ps._save_state(make_state(phase="SPLIT", type="rfe", batch=1))
        write_ids("tmp/pipeline-split-ids.txt", parents)
        write_ids("tmp/pipeline-all-ids.txt", parents)
        write_ids(ps.WAVE_IDS_FILE, parents)
        for parent in parents:
            _write_review(parent, recommendation="split")
        clock, pending = _Clock(), set(parents)
        _, real_check_id = _arm(monkeypatch, clock, pending)
        real_update = artifact_utils.update_frontmatter

        def update(path, updates, schema):
            if "RHAIRFE-1002" in path:
                raise OSError("read-only reviews directory")
            return real_update(path, updates, schema)

        def writer(phase, ids, pipeline_type="rfe", **kw):
            failures = kw.get("failures")
            if failures is not None:
                for rid in ids:
                    failures[rid] = "frontmatter.py set failed (read-only reviews directory)"
            return list(ids)

        monkeypatch.setattr(artifact_utils, "update_frontmatter", update)
        monkeypatch.setattr(verify_phase, "write_error_stubs", writer)

        assert self._drive_to_failure() == 1
        err = capsys.readouterr().err.splitlines()
        assert err == [
            "wait-for-wave: could not mark RHAIRFE-1002's review (read-only reviews directory);"
            " replacing it with the split error stub (error=split_not_attempted: wave stalled in"
            " SPLIT: no agent reached a terminal state for 1800s (window 1800s); the subagent"
            " produced no output)",
            "wait-for-wave: STALL in SPLIT (split): no wave slot reached a terminal state for"
            " 1800s (window 1800s, policy escalate-only); escalating RHAIRFE-1001 ->"
            " split_not_attempted error + no-split status file, removed from the wave and"
            " tmp/pipeline-split-ids.txt; escalation FAILED for RHAIRFE-1002 (see next line)",
            "wait-for-wave: ESCALATION FAILED for RHAIRFE-1002: no error marker could be"
            " written (RHAIRFE-1002: frontmatter.py set failed (read-only reviews directory));"
            " left in the wave and tmp/pipeline-split-ids.txt - fix the reviews directory and"
            " re-run",
        ]
        from artifact_utils import read_frontmatter

        data, _ = read_frontmatter("artifacts/rfe-reviews/RHAIRFE-1001-review.md")
        assert data["error"].startswith("split_not_attempted: wave stalled in SPLIT")
        assert os.path.exists("artifacts/rfe-reviews/RHAIRFE-1001-split-status.yaml")
        assert real_check_id("split", "RHAIRFE-1001") == "completed"
        data, _ = read_frontmatter("artifacts/rfe-reviews/RHAIRFE-1002-review.md")
        assert data.get("error") is None  # untouched: no marker, no claim
        assert not os.path.exists("artifacts/rfe-reviews/RHAIRFE-1002-split-status.yaml")
        assert real_check_id("split", "RHAIRFE-1002") == "pending"  # the slot stays pending
        assert read_ids(ps.WAVE_IDS_FILE) == ["RHAIRFE-1002"]
        assert read_ids("tmp/pipeline-split-ids.txt") == ["RHAIRFE-1002"]
        assert not os.path.exists(ps.WAVE_PROGRESS_FILE)
        assert not os.listdir("artifacts/rfe-tasks")

    def test_split_marker_return_shape_names_the_unrecorded_parents(self, stall_dir, monkeypatch):
        """Unit view of the contract _escalate_stuck relies on: both markers return their
        label and ``id -> reason`` for the ids they could not mark; a writer that gives no
        reason yields None."""
        import verify_phase

        monkeypatch.setattr(
            verify_phase, "write_error_stubs", lambda phase, ids, *a, **kw: list(ids)
        )
        _write_review("RHAIRFE-1001")
        label, failures = ps._mark_revise_stalled(["RHAIRFE-1001", "RHAIRFE-1002"], "rfe")
        assert label == "revise_stalled" and failures == {"RHAIRFE-1002": None}
        label, failures = ps._mark_split_not_attempted(["RHAIRFE-1003"], "rfe", "test")
        assert label == "split_not_attempted" and failures == {"RHAIRFE-1003": None}
        assert not os.path.exists("artifacts/rfe-reviews/RHAIRFE-1003-split-status.yaml")


# ---------- AISDLC-33: phase-entry freshness, keep-state, sweep ----------


def _scored_review(rid, mtime, **over):
    fm = {"rfe_id": rid, "score": 4, "pass": False, "recommendation": "revise"}
    fm.update(over)
    body = "---\n" + "".join(
        f"{k}: {str(v).lower() if isinstance(v, bool) else v}\n" for k, v in fm.items()
    )
    path = f"artifacts/rfe-reviews/{rid}-review.md"
    with open(path, "w") as f:
        f.write(body + "---\nBody\n\n## Revision History\nnone\n")
    os.utime(path, (mtime, mtime))
    return path


class TestPhaseEntryFreshness:
    """A review or assess result older than the agent phase's entry is a previous cycle's late
    write: it keeps its id in the wave (next-action) and blocks advance, instead of skipping
    the agent and advancing on the stale verdict (the AISDLC-33 sequence)."""

    T = 1_700_000_000.0

    @pytest.fixture(autouse=True)
    def _reset(self):
        import check_review_progress as crp

        yield
        crp.set_wave_launch(None)

    def test_enter_phase_stamps_agent_phases_only_and_once(self, tmp_dir, monkeypatch):
        state = make_state(phase="REASSESS_CHECK")
        monkeypatch.setattr(ps, "_now", lambda: self.T)
        ps._enter_phase(state, "COLLECT")  # noop phase: no stamp
        assert ps._read_phase_entry("COLLECT") is None
        ps._enter_phase(state, "REVIEW")
        assert ps._read_phase_entry("REVIEW") == self.T
        assert ps._read_phase_entry("ASSESS") is None  # the record names one phase
        monkeypatch.setattr(ps, "_now", lambda: self.T + 100)
        ps._enter_phase(state, "REASSESS_RESTORE")  # script phase: stamp untouched
        assert ps._read_phase_entry("REVIEW") == self.T
        assert state["phase"] == "REASSESS_RESTORE"

    def test_set_phase_stamps_an_agent_phase(self, tmp_dir, monkeypatch):
        ps._save_state(make_state(phase="BATCH_START"))
        monkeypatch.setattr(ps, "_now", lambda: self.T)
        ps.cmd_set_phase(["REASSESS_REVIEW"])
        assert ps._read_phase_entry("REASSESS_REVIEW") == self.T

    def _reassess_review(self, monkeypatch, review_mtime):
        ps._save_state(make_state(phase="REASSESS_REVIEW", batch=1, reassess_cycle=1))
        write_ids("tmp/pipeline-reassess-ids.txt", ["RHAIRFE-1"])
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        monkeypatch.setattr(ps, "_now", lambda: self.T)
        ps._write_phase_entry("REASSESS_REVIEW")
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "")  # post_verify, filter, restore
        _scored_review("RHAIRFE-1", review_mtime)

    def test_stale_review_at_entry_still_launches_the_agent(self, tmp_dir, monkeypatch):
        # The reviewer's reproduction: REASSESS_SAVE deleted the review, a previous cycle's
        # agent rewrote it during REASSESS_ASSESS, next-action now plans REASSESS_REVIEW.
        self._reassess_review(monkeypatch, self.T - 300)
        result = _run_next_action()
        assert result["action"] == "launch_wave" and result["phase"] == "REASSESS_REVIEW"
        assert "RHAIRFE-1" in result["agents"][0]["vars"]
        assert ps._read_wave_launch() == self.T  # the barrier's reference
        assert ps._read_phase_entry("REASSESS_REVIEW") == self.T  # not re-stamped

    def test_fresh_review_advances(self, tmp_dir, monkeypatch, capsys):
        self._reassess_review(monkeypatch, self.T + 30)
        result = _run_next_action()
        assert result["action"] == "run_script" and result["phase"] == "REASSESS_RESTORE"
        assert "REASSESS_REVIEW → REASSESS_RESTORE" in capsys.readouterr().err

    def test_advance_guard_holds_on_a_stale_review(self, tmp_dir, monkeypatch, capsys):
        self._reassess_review(monkeypatch, self.T - 300)
        with pytest.raises(SystemExit) as exc:
            ps.cmd_advance([])
        assert exc.value.code == 1
        assert "pending agents" in capsys.readouterr().err
        os.utime("artifacts/rfe-reviews/RHAIRFE-1-review.md", (self.T + 30, self.T + 30))
        ps.cmd_advance([])
        assert ps._load_state()["phase"] == "REASSESS_RESTORE"

    def test_no_entry_record_keeps_the_legacy_rule(self, tmp_dir, monkeypatch):
        self._reassess_review(monkeypatch, self.T - 300)
        os.remove(ps.PHASE_ENTRY_FILE)
        assert _run_next_action()["action"] == "run_script"  # stale file accepted as before

    def test_revise_slot_is_not_time_gated(self, tmp_dir, monkeypatch):
        """REASSESS_RESTORE re-raises auto_revised just before REASSESS_REVISE is entered, so
        the revise slot is gated by the revise baseline (AISDLC-45) rather than by time."""
        import check_review_progress as crp

        _scored_review("RHAIRFE-1", self.T - 300, auto_revised=True)
        crp.set_wave_launch(self.T)
        assert crp.check_id("revise", "RHAIRFE-1") == "completed"
        assert crp.check_id("review", "RHAIRFE-1") == "pending"


class TestReviewStateLifetime:
    def test_split_restore_keeps_state_and_correction_check_reconciles(self, tmp_dir, monkeypatch):
        cfg = ps._build_phase_config("rfe")
        assert cfg["SPLIT_RESTORE"]["command"].endswith("restore --keep-state")
        write_ids("tmp/pipeline-split-children-ids.txt", ["RFE-002", "RFE-003"])
        calls = []

        def mock_run(cmd):
            calls.append(cmd)
            return "RESPLIT=" if "check_right_sized" in cmd else "RESTORED=\nFLAGGED="

        monkeypatch.setattr(ps, "_run_script", mock_run)
        next_phase, _ = ps.advance(make_state(phase="SPLIT_CORRECTION_CHECK"))
        assert next_phase == "BATCH_DONE"
        assert (
            calls[0] == "python3 scripts/reconcile_reviews.py --type rfe --keep-state --cycles 1"
            " RFE-002 RFE-003"
        )
        assert "check_right_sized" in calls[1]
        calls.clear()
        ps.advance(make_state(phase="SPLIT_CORRECTION_CHECK"), dry_run=True)
        assert not any("reconcile_reviews" in c for c in calls)

    def test_batch_start_sweeps_leftover_state_files(self, tmp_dir):
        write_ids("tmp/pipeline-batch-1-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
        stale = "artifacts/rfe-reviews/RHAIRFE-1-review-state.json"
        with open(stale, "w") as f:
            f.write('{"before_score": 3, "auto_revised": true}')
        state = make_state(phase="BATCH_START", batch=0, total_batches=1)
        assert ps.advance(state, dry_run=True)[0] == "FETCH"
        assert os.path.exists(stale)  # a preview touches nothing
        assert ps.advance(state)[0] == "FETCH"
        assert not os.path.exists(stale)


# ---------- AISDLC-45: revise baseline at the three revise transitions ----------


class TestReviseBaseline:
    def _files(self, rid):
        with open(f"artifacts/rfe-tasks/{rid}.md", "w") as f:
            f.write(f"---\nrfe_id: {rid}\n---\nbody\n")
        _scored_review(rid, 1_700_000_000.0, auto_revised=True)

    def _baseline(self):
        import json

        from check_review_progress import REVISE_BASELINE_FILE

        return json.load(open(REVISE_BASELINE_FILE))

    def test_review_to_revise_records_the_review_write_time(self, tmp_dir, monkeypatch):
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1", "RHAIRFE-2"])
        self._files("RHAIRFE-1")
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "RHAIRFE-1")
        monkeypatch.setattr(ps, "_save_originals", lambda ids, ptype: None)
        assert ps.advance(make_state(phase="REVIEW"))[0] == "REVISE"
        base = self._baseline()
        assert base == {"RHAIRFE-1": {"review_mtime_ns": 1_700_000_000 * 10**9}}

    def test_reassess_restore_records_after_the_restore_and_empties_at_the_cap(
        self, tmp_dir, monkeypatch
    ):
        write_ids("tmp/pipeline-reassess-ids.txt", ["RHAIRFE-1"])
        self._files("RHAIRFE-1")
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "RHAIRFE-1")
        monkeypatch.setattr(ps, "_save_originals", lambda ids, ptype: None)
        assert ps.advance(make_state(phase="REASSESS_RESTORE", reassess_cycle=1))[0] == (
            "REASSESS_REVISE"
        )
        assert list(self._baseline()) == ["RHAIRFE-1"]
        assert ps.advance(make_state(phase="REASSESS_RESTORE", reassess_cycle=2))[0] == (
            "REASSESS_REVISE"
        )
        assert self._baseline() == {}

    def test_split_review_records_and_dry_run_writes_nothing(self, tmp_dir, monkeypatch):
        from check_review_progress import REVISE_BASELINE_FILE

        write_ids("tmp/pipeline-split-children-ids.txt", ["RFE-002"])
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "RFE-002")
        monkeypatch.setattr(ps, "_save_originals", lambda ids, ptype: None)
        ps.advance(make_state(phase="SPLIT_REVIEW"), dry_run=True)
        assert not os.path.exists(REVISE_BASELINE_FILE)
        assert ps.advance(make_state(phase="SPLIT_REVIEW"))[0] == "SPLIT_REVISE"
        assert list(self._baseline()) == ["RFE-002"]

    def test_second_revision_is_launched(self, tmp_dir, monkeypatch, capsys):
        """The AISDLC-45 reproduction: REASSESS_RESTORE re-raised auto_revised, the item still
        fails, and the pre-filter used to read the flag as a finished revision."""
        write_ids("tmp/pipeline-revise-ids.txt", ["RHAIRFE-1"])
        write_ids("tmp/pipeline-active-ids.txt", ["RHAIRFE-1"])
        self._files("RHAIRFE-1")
        ps._write_revise_baseline(["RHAIRFE-1"], "rfe")
        ps._save_state(make_state(phase="REASSESS_REVISE", batch=1, reassess_cycle=1))
        monkeypatch.setattr(ps, "_run_script", lambda cmd: "")
        result = _run_next_action()
        assert result["action"] == "launch_wave" and result["phase"] == "REASSESS_REVISE"
        assert "RHAIRFE-1" in result["agents"][0]["vars"]
        # The agent edits the task first: the slot must stay pending (the flag is true).
        with open("artifacts/rfe-tasks/RHAIRFE-1.md", "a") as f:
            f.write("second revision\n")
        assert _run_next_action()["action"] == "launch_wave"
        # Its frontmatter step is a review write; the slot completes.
        _scored_review("RHAIRFE-1", 1_700_000_300.0, auto_revised=True)
        result = _run_next_action()
        assert result["action"] == "run_script" and result["phase"] == "REASSESS_FIXUP"
        assert "REASSESS_REVISE → REASSESS_FIXUP" in capsys.readouterr().err
