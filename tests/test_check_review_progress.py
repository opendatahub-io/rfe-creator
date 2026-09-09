#!/usr/bin/env python3
"""Tests for scripts/check_review_progress.py — poll mode, phase checking,
status formatting, and adaptive sleep intervals."""

import os
import re
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_review_progress import (  # noqa: E402
    PHASE_CHECKS,
    _check_phase,
    _detect_fast,
    _format_status,
    check_id,
)

# ── check_id ──


class TestCheckId:
    def test_missing_file_is_pending(self, tmp_path):
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            assert check_id("fetch", "RHAIRFE-1") == "pending"

    def test_existing_file_is_completed(self, tmp_path):
        f = tmp_path / "RHAIRFE-1.md"
        f.write_text("content")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            assert check_id("fetch", "RHAIRFE-1") == "completed"

    def test_review_phase_score_present(self, tmp_path):
        """Review phase: file with score → completed."""
        f = tmp_path / "RHAIRFE-1-review.md"
        f.write_text("---\nscore: 7\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"review": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("review", "RHAIRFE-1") == "completed"

    def test_review_phase_score_missing(self, tmp_path):
        """Review phase: file without score → pending."""
        f = tmp_path / "RHAIRFE-1-review.md"
        f.write_text("---\ntitle: test\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"review": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("review", "RHAIRFE-1") == "pending"

    def test_review_phase_error_flag(self, tmp_path):
        """Review phase: file with score + error → error."""
        f = tmp_path / "RHAIRFE-1-review.md"
        f.write_text("---\nscore: 5\nerror: true\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"review": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("review", "RHAIRFE-1") == "error"

    def test_review_phase_unparseable(self, tmp_path):
        """Review phase: unparseable frontmatter → error."""
        f = tmp_path / "RHAIRFE-1-review.md"
        f.write_text("---\n: bad yaml [[\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"review": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("review", "RHAIRFE-1") == "error"

    def test_revise_phase_auto_revised_true(self, tmp_path):
        """Revise phase: auto_revised=true → completed."""
        f = tmp_path / "RHAIRFE-1-review.md"
        f.write_text("---\nauto_revised: true\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"revise": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("revise", "RHAIRFE-1") == "completed"

    def test_revise_phase_auto_revised_false(self, tmp_path):
        """Revise phase: no auto_revised, no split recommendation → pending."""
        f = tmp_path / "RHAIRFE-1-review.md"
        f.write_text("---\nscore: 5\nrecommendation: improve\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"revise": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("revise", "RHAIRFE-1") == "pending"

    def test_revise_phase_recommendation_split(self, tmp_path):
        """Revise phase: recommendation=split → completed (can't fix)."""
        f = tmp_path / "RHAIRFE-1-review.md"
        f.write_text("---\nrecommendation: split\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"revise": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("revise", "RHAIRFE-1") == "completed"

    def test_revise_phase_bad_frontmatter(self, tmp_path):
        """Revise phase: unparseable frontmatter → error."""
        f = tmp_path / "RHAIRFE-1-review.md"
        f.write_text("---\n: bad [[\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"revise": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("revise", "RHAIRFE-1") == "error"

    def test_review_phase_missing_closing_delimiter(self, tmp_path):
        """Review phase: missing closing --- → error (CI #128 regression)."""
        f = tmp_path / "RHAIRFE-1-review.md"
        f.write_text(
            "---\nscore: 7\npass: true\nrecommendation: submit\n"
            "Review body without closing delimiter.\n"
        )
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"review": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("review", "RHAIRFE-1") == "error"

    def test_review_phase_empty_frontmatter(self, tmp_path):
        """Review phase: empty --- / --- → error (CI #122 regression)."""
        f = tmp_path / "RHAIRFE-1-review.md"
        f.write_text("---\n---\nReview body with empty frontmatter.\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"review": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("review", "RHAIRFE-1") == "error"

    def test_revise_phase_empty_frontmatter(self, tmp_path):
        """Revise phase: empty frontmatter → error."""
        f = tmp_path / "RHAIRFE-1-review.md"
        f.write_text("---\n---\nBody.\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"revise": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("revise", "RHAIRFE-1") == "error"


class TestCreatePhase:
    """The create phase is the Phase 1 barrier in /rfe.speedrun.

    Create agents write the task file first and set frontmatter in a later tool
    call, so this phase deliberately checks more than existence — releasing the
    barrier mid-write is what leaves the pipeline with unusable task files.
    """

    def test_missing_file_is_pending(self, tmp_path):
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"create": lambda id: str(tmp_path / f"{id}.md")},
        ):
            assert check_id("create", "RFE-001") == "pending"

    def test_valid_frontmatter_is_completed(self, tmp_path):
        f = tmp_path / "RFE-001.md"
        f.write_text("---\nrfe_id: RFE-001\ntitle: A need\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"create": lambda id: str(tmp_path / f"{id}.md")},
        ):
            assert check_id("create", "RFE-001") == "completed"

    def test_file_without_frontmatter_is_pending(self, tmp_path):
        """The agent wrote the file but has not run frontmatter.py set yet."""
        f = tmp_path / "RFE-001.md"
        f.write_text("# A need\n\nSome body text.\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"create": lambda id: str(tmp_path / f"{id}.md")},
        ):
            assert check_id("create", "RFE-001") == "pending"

    def test_frontmatter_without_rfe_id_is_pending(self, tmp_path):
        f = tmp_path / "RFE-001.md"
        f.write_text("---\ntitle: A need\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"create": lambda id: str(tmp_path / f"{id}.md")},
        ):
            assert check_id("create", "RFE-001") == "pending"

    def test_empty_frontmatter_is_pending(self, tmp_path):
        f = tmp_path / "RFE-001.md"
        f.write_text("---\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"create": lambda id: str(tmp_path / f"{id}.md")},
        ):
            assert check_id("create", "RFE-001") == "pending"

    def test_unparseable_frontmatter_is_pending(self, tmp_path):
        """Not "error": the --wait loop stops on pending == 0 and ignores errors.

        Returning "error" would release the barrier on exactly the file it
        just rejected. Pending times out into the relaunch path instead.
        """
        f = tmp_path / "RFE-001.md"
        f.write_text("---\n: bad yaml [[\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"create": lambda id: str(tmp_path / f"{id}.md")},
        ):
            assert check_id("create", "RFE-001") == "pending"

    def test_mismatched_rfe_id_is_pending(self, tmp_path):
        """A task file holding someone else's ID is not this ID's create output."""
        f = tmp_path / "RFE-001.md"
        f.write_text("---\nrfe_id: RFE-002\ntitle: A need\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"create": lambda id: str(tmp_path / f"{id}.md")},
        ):
            assert check_id("create", "RFE-001") == "pending"

    def test_create_never_reports_error(self, tmp_path):
        """No create input may return "error" — the barrier would leak past it."""
        cases = [
            "---\n: bad yaml [[\n---\nBody\n",
            "---\n---\nBody\n",
            "---\nrfe_id: RFE-002\n---\nBody\n",
            "# No frontmatter\n",
        ]
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"create": lambda id: str(tmp_path / f"{id}.md")},
        ):
            for content in cases:
                (tmp_path / "RFE-001.md").write_text(content)
                assert check_id("create", "RFE-001") != "error", content


# ── _check_phase ──


class TestCheckPhase:
    def test_all_pending(self, tmp_path):
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            completed, errors, pending, total, next_poll = _check_phase(
                "fetch", ["A", "B", "C"], fast=False
            )
            assert completed == 0
            assert pending == 3
            assert total == 3
            assert next_poll == 60

    def test_all_completed(self, tmp_path):
        for name in ["A", "B", "C"]:
            (tmp_path / f"{name}.md").write_text("done")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            completed, errors, pending, total, next_poll = _check_phase(
                "fetch", ["A", "B", "C"], fast=False
            )
            assert completed == 3
            assert pending == 0
            assert next_poll == 0

    def test_adaptive_interval_half(self, tmp_path):
        """50% complete → 30s interval."""
        for name in ["A", "B"]:
            (tmp_path / f"{name}.md").write_text("done")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            _, _, _, _, next_poll = _check_phase("fetch", ["A", "B", "C", "D"], fast=False)
            assert next_poll == 30

    def test_adaptive_interval_75pct(self, tmp_path):
        """75%+ complete → 15s interval."""
        for name in ["A", "B", "C"]:
            (tmp_path / f"{name}.md").write_text("done")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            _, _, _, _, next_poll = _check_phase("fetch", ["A", "B", "C", "D"], fast=False)
            assert next_poll == 15

    def test_fast_poll_caps_at_15(self, tmp_path):
        """Fast mode caps at 15s regardless of completion ratio."""
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            _, _, _, _, next_poll = _check_phase("fetch", ["A", "B", "C"], fast=True)
            assert next_poll == 15


# ── _format_status ──


class TestFormatStatus:
    def test_pending_format(self):
        s = _format_status("assess", 2, 0, 3, 5, 30)
        assert s == "assess: COMPLETED=2/5, PENDING=3, NEXT_POLL=30"

    def test_complete_format(self):
        s = _format_status("fetch", 5, 0, 0, 5, 0)
        assert s == "fetch: COMPLETED=5/5, NEXT_POLL=0"

    def test_error_format(self):
        s = _format_status("review", 3, 1, 1, 5, 15)
        assert s == "review: COMPLETED=3/5, PENDING=1, ERRORS=1, NEXT_POLL=15"


# ── _detect_fast ──


class TestDetectFast:
    def test_explicit_flag(self):
        assert _detect_fast(True) is True

    def test_no_config_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert _detect_fast(False) is False

    def test_headless_false_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("tmp", exist_ok=True)
        import yaml

        with open("tmp/autofix-config.yaml", "w") as f:
            yaml.dump({"headless": False}, f)
        assert _detect_fast(False) is True

    def test_headless_true_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("tmp", exist_ok=True)
        import yaml

        with open("tmp/autofix-config.yaml", "w") as f:
            yaml.dump({"headless": True}, f)
        assert _detect_fast(False) is False


# ── --wait mode (via main) ──


class TestPollMode:
    def _run_main(self, args):
        """Run main() with given args, return (exit_code, stdout)."""
        import io

        from check_review_progress import main

        old_argv = sys.argv
        sys.argv = ["check_review_progress.py"] + args
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            main()
            exit_code = 0
        except SystemExit as e:
            exit_code = e.code
        finally:
            sys.stdout = old_stdout
            sys.argv = old_argv
        return exit_code, captured.getvalue()

    def test_poll_exits_0_when_complete(self, tmp_path):
        """All IDs complete → exit 0, no sleep."""
        for name in ["RHAIRFE-1", "RHAIRFE-2"]:
            (tmp_path / f"{name}.md").write_text("done")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            code, out = self._run_main(["--wait", "--phase", "fetch", "RHAIRFE-1", "RHAIRFE-2"])
        assert code == 0
        assert "COMPLETED=2/2" in out
        assert "Sleeping" not in out

    def test_poll_sleeps_then_completes(self, tmp_path):
        """Pending IDs → sleeps, then file appears → exits 0."""

        def create_on_sleep(seconds):
            # Simulate agent completing during sleep
            (tmp_path / "RHAIRFE-1.md").write_text("done")

        with (
            patch.dict(
                "check_review_progress.PHASE_CHECKS",
                {"fetch": lambda id: str(tmp_path / f"{id}.md")},
            ),
            patch("check_review_progress.time.sleep", side_effect=create_on_sleep) as mock_sleep,
        ):
            code, out = self._run_main(["--wait", "--fast-poll", "--phase", "fetch", "RHAIRFE-1"])
        assert code == 0
        assert "PENDING=1" in out
        assert "Sleeping 15s..." in out
        assert "COMPLETED=1/1" in out
        mock_sleep.assert_called_once_with(15)

    def test_poll_uses_adaptive_interval(self, tmp_path):
        """Sleep duration adapts to completion ratio."""
        (tmp_path / "A.md").write_text("done")

        def create_on_sleep(seconds):
            (tmp_path / "B.md").write_text("done")

        with (
            patch.dict(
                "check_review_progress.PHASE_CHECKS",
                {"fetch": lambda id: str(tmp_path / f"{id}.md")},
            ),
            patch("check_review_progress.time.sleep", side_effect=create_on_sleep) as mock_sleep,
        ):
            code, _ = self._run_main(["--wait", "--phase", "fetch", "A", "B"])
        assert code == 0
        # 1/2 = 50% → 30s interval
        mock_sleep.assert_called_once_with(30)

    def test_poll_multi_phase(self, tmp_path):
        """--also-phase checks multiple phases, exits 0 only when all done."""
        for name in ["A", "B"]:
            (tmp_path / f"fetch-{name}.md").write_text("done")
            (tmp_path / f"assess-{name}.md").write_text("done")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {
                "fetch": lambda id: str(tmp_path / f"fetch-{id}.md"),
                "assess": lambda id: str(tmp_path / f"assess-{id}.md"),
            },
        ):
            code, out = self._run_main(
                ["--wait", "--phase", "fetch", "--also-phase", "assess", "A", "B"]
            )
        assert code == 0
        assert "fetch:" in out
        assert "assess:" in out
        assert "All phases complete." in out

    def test_poll_multi_phase_partial(self, tmp_path):
        """One phase done, other pending → sleeps until both complete."""
        for name in ["A", "B"]:
            (tmp_path / f"fetch-{name}.md").write_text("done")

        # assess files missing → pending
        def create_on_sleep(seconds):
            for name in ["A", "B"]:
                (tmp_path / f"assess-{name}.md").write_text("done")

        with (
            patch.dict(
                "check_review_progress.PHASE_CHECKS",
                {
                    "fetch": lambda id: str(tmp_path / f"fetch-{id}.md"),
                    "assess": lambda id: str(tmp_path / f"assess-{id}.md"),
                },
            ),
            patch("check_review_progress.time.sleep", side_effect=create_on_sleep),
        ):
            code, out = self._run_main(
                ["--wait", "--fast-poll", "--phase", "fetch", "--also-phase", "assess", "A", "B"]
            )
        assert code == 0
        assert "Sleeping" in out
        assert "All phases complete." in out

    def test_poll_max_interval_across_phases(self, tmp_path):
        """Sleep uses the longest interval across all phases."""
        # fetch: 1/2 done → 30s, assess: 0/2 done → 60s → max = 60
        (tmp_path / "fetch-A.md").write_text("done")

        def create_on_sleep(seconds):
            (tmp_path / "fetch-B.md").write_text("done")
            for name in ["A", "B"]:
                (tmp_path / f"assess-{name}.md").write_text("done")

        with (
            patch.dict(
                "check_review_progress.PHASE_CHECKS",
                {
                    "fetch": lambda id: str(tmp_path / f"fetch-{id}.md"),
                    "assess": lambda id: str(tmp_path / f"assess-{id}.md"),
                },
            ),
            patch("check_review_progress.time.sleep", side_effect=create_on_sleep) as mock_sleep,
        ):
            code, _ = self._run_main(
                ["--wait", "--phase", "fetch", "--also-phase", "assess", "A", "B"]
            )
        assert code == 0
        mock_sleep.assert_called_once_with(60)

    def test_poll_single_phase_no_all_complete_message(self, tmp_path):
        """Single phase complete → no 'All phases complete.' message."""
        (tmp_path / "A.md").write_text("done")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            code, out = self._run_main(["--wait", "--phase", "fetch", "A"])
        assert code == 0
        assert "All phases complete." not in out

    def test_poll_max_wait_exits_3_on_timeout(self, tmp_path):
        """Pending IDs + small max-wait → exit 3 with pending IDs on stdout."""
        with (
            patch.dict(
                "check_review_progress.PHASE_CHECKS",
                {"fetch": lambda id: str(tmp_path / f"{id}.md")},
            ),
            patch("check_review_progress.time.sleep") as mock_sleep,
            patch("check_review_progress.time.monotonic", side_effect=[0, 0, 0]),
        ):
            # monotonic: start=0, before-guard=0, elapsed+60>1 → timeout
            code, out = self._run_main(
                ["--wait", "--max-wait", "1", "--phase", "fetch", "RHAIRFE-1", "RHAIRFE-2"]
            )
        assert code == 3
        assert "RHAIRFE-1" in out
        assert "RHAIRFE-2" in out
        assert "Re-run this command" in out
        mock_sleep.assert_not_called()

    def test_poll_max_wait_completes_before_timeout(self, tmp_path):
        """Agents complete within max-wait → exit 0."""

        def create_on_sleep(seconds):
            (tmp_path / "RHAIRFE-1.md").write_text("done")

        with (
            patch.dict(
                "check_review_progress.PHASE_CHECKS",
                {"fetch": lambda id: str(tmp_path / f"{id}.md")},
            ),
            patch("check_review_progress.time.sleep", side_effect=create_on_sleep),
            patch("check_review_progress.time.monotonic", side_effect=[0, 0, 5, 5]),
        ):
            # start=0, guard: 0+15<90 → sleep, second iter: complete
            code, out = self._run_main(
                ["--wait", "--fast-poll", "--max-wait", "90", "--phase", "fetch", "RHAIRFE-1"]
            )
        assert code == 0
        assert "COMPLETED=1/1" in out

    def test_poll_max_wait_zero_disables(self, tmp_path):
        """--max-wait 0 disables timeout (runs until complete)."""
        call_count = [0]

        def create_on_second_sleep(seconds):
            call_count[0] += 1
            if call_count[0] >= 2:
                (tmp_path / "A.md").write_text("done")

        with (
            patch.dict(
                "check_review_progress.PHASE_CHECKS",
                {"fetch": lambda id: str(tmp_path / f"{id}.md")},
            ),
            patch("check_review_progress.time.sleep", side_effect=create_on_second_sleep),
            patch("check_review_progress.time.monotonic", side_effect=[0, 0, 100, 100, 200, 200]),
        ):
            # Even at elapsed=200, max_wait=0 means no timeout
            code, out = self._run_main(
                ["--wait", "--max-wait", "0", "--fast-poll", "--phase", "fetch", "A"]
            )
        assert code == 0
        assert call_count[0] == 2

    def test_poll_max_wait_multi_phase(self, tmp_path):
        """Multi-phase with one stalling → exit 3."""
        for name in ["A", "B"]:
            (tmp_path / f"fetch-{name}.md").write_text("done")
        # assess files missing → pending
        with (
            patch.dict(
                "check_review_progress.PHASE_CHECKS",
                {
                    "fetch": lambda id: str(tmp_path / f"fetch-{id}.md"),
                    "assess": lambda id: str(tmp_path / f"assess-{id}.md"),
                },
            ),
            patch("check_review_progress.time.sleep"),
            patch("check_review_progress.time.monotonic", side_effect=[0, 0, 0]),
        ):
            code, out = self._run_main(
                [
                    "--wait",
                    "--max-wait",
                    "1",
                    "--phase",
                    "fetch",
                    "--also-phase",
                    "assess",
                    "A",
                    "B",
                ]
            )
        assert code == 3
        assert "Re-run this command" in out

    def test_poll_max_wait_message_format(self, tmp_path):
        """Timeout message contains pending IDs and directive."""
        with (
            patch.dict(
                "check_review_progress.PHASE_CHECKS",
                {"fetch": lambda id: str(tmp_path / f"{id}.md")},
            ),
            patch("check_review_progress.time.sleep"),
            patch("check_review_progress.time.monotonic", side_effect=[0, 5]),
        ):
            # monotonic: start=0, elapsed=5-0=5, 5+60>1 → timeout
            code, out = self._run_main(
                ["--wait", "--max-wait", "1", "--phase", "fetch", "RHAIRFE-100", "RHAIRFE-200"]
            )
        assert code == 3
        assert "Waited 5s" in out
        assert "RHAIRFE-100" in out
        assert "RHAIRFE-200" in out
        assert "Re-run this command" in out

    def test_poll_max_wait_deduplicates_ids(self, tmp_path):
        """Pending in multiple phases → each ID appears only once."""
        with (
            patch.dict(
                "check_review_progress.PHASE_CHECKS",
                {
                    "fetch": lambda id: str(tmp_path / f"fetch-{id}.md"),
                    "assess": lambda id: str(tmp_path / f"assess-{id}.md"),
                },
            ),
            patch("check_review_progress.time.sleep"),
            patch("check_review_progress.time.monotonic", side_effect=[0, 0, 0]),
        ):
            code, out = self._run_main(
                ["--wait", "--max-wait", "1", "--phase", "fetch", "--also-phase", "assess", "A"]
            )
        assert code == 3
        # "A" pending in both phases but should appear only once
        timeout_line = [line for line in out.splitlines() if "Re-run" in line][0]
        assert timeout_line.count(" A") == 1

    def test_poll_max_wait_caps_id_list(self, tmp_path):
        """10+ pending IDs → message shows first 5 + '... and N more'."""
        ids = [f"RFE-{i:03d}" for i in range(10)]
        with (
            patch.dict(
                "check_review_progress.PHASE_CHECKS",
                {"fetch": lambda id: str(tmp_path / f"{id}.md")},
            ),
            patch("check_review_progress.time.sleep"),
            patch("check_review_progress.time.monotonic", side_effect=[0, 0, 0]),
        ):
            code, out = self._run_main(["--wait", "--max-wait", "1", "--phase", "fetch"] + ids)
        assert code == 3
        assert "... and 5 more" in out
        # First 5 sorted IDs should be present
        for i in range(5):
            assert f"RFE-{i:03d}" in out

    def test_poll_max_wait_negative_rejected(self):
        """--max-wait -1 should error."""
        import io

        from check_review_progress import main

        old_argv = sys.argv
        sys.argv = [
            "check_review_progress.py",
            "--wait",
            "--max-wait",
            "-1",
            "--phase",
            "fetch",
            "A",
        ]
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            main()
            exit_code = 0
        except SystemExit as e:
            exit_code = e.code
        finally:
            sys.stderr = old_stderr
            sys.argv = old_argv
        assert exit_code == 2  # argparse/validation error

    def test_poll_with_id_file(self, tmp_path):
        """--id-file reads IDs correctly."""
        id_file = tmp_path / "ids.txt"
        id_file.write_text("RHAIRFE-1 RHAIRFE-2\nRHAIRFE-3\n")
        for name in ["RHAIRFE-1", "RHAIRFE-2", "RHAIRFE-3"]:
            (tmp_path / f"{name}.md").write_text("done")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            code, out = self._run_main(["--wait", "--phase", "fetch", "--id-file", str(id_file)])
        assert code == 0
        assert "COMPLETED=3/3" in out

    def test_poll_errors_dont_block(self, tmp_path):
        """Errors count as done — only pending blocks exit."""
        # RHAIRFE-1: has score + error → error
        (tmp_path / "RHAIRFE-1-review.md").write_text("---\nscore: 5\nerror: true\n---\nBody\n")
        # RHAIRFE-2: has score → completed
        (tmp_path / "RHAIRFE-2-review.md").write_text("---\nscore: 8\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"review": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            code, out = self._run_main(["--wait", "--phase", "review", "RHAIRFE-1", "RHAIRFE-2"])
        assert code == 0
        assert "ERRORS=1" in out
        assert "COMPLETED=1/2" in out


# ── Legacy mode (no --wait) ──


class TestLegacyMode:
    def _run_main(self, args):
        import io

        from check_review_progress import main

        old_argv = sys.argv
        sys.argv = ["check_review_progress.py"] + args
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            main()
            exit_code = 0
        except SystemExit as e:
            exit_code = e.code
        finally:
            sys.stdout = old_stdout
            sys.argv = old_argv
        return exit_code, captured.getvalue()

    def test_legacy_format_unchanged(self, tmp_path):
        """Legacy mode output format is flat CSV, not prefixed by phase."""
        (tmp_path / "A.md").write_text("done")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            code, out = self._run_main(["--phase", "fetch", "A", "B"])
        assert code is None or code == 0
        assert out.strip() == "COMPLETED=1/2, PENDING=1, NEXT_POLL=30"

    def test_legacy_no_ids_exits_2(self, tmp_path):
        code, _ = self._run_main(["--phase", "fetch"])
        assert code == 2

    def test_max_wait_ignored_without_poll(self, tmp_path):
        """--max-wait without --wait runs legacy mode, no timeout behavior."""
        (tmp_path / "A.md").write_text("done")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            code, out = self._run_main(["--max-wait", "30", "--phase", "fetch", "A", "B"])
        assert code is None or code == 0
        assert "COMPLETED=1/2" in out
        assert "Re-run" not in out


# ── Initiative phases ──


class TestInitiativePhases:
    def test_initiative_fetch_pending(self, tmp_path):
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"initiative-fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            assert check_id("initiative-fetch", "INIT-001") == "pending"

    def test_initiative_fetch_completed(self, tmp_path):
        (tmp_path / "INIT-001.md").write_text("content")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"initiative-fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            assert check_id("initiative-fetch", "INIT-001") == "completed"

    def test_initiative_review_pending(self, tmp_path):
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"initiative-review": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("initiative-review", "INIT-001") == "pending"

    def test_initiative_review_completed(self, tmp_path):
        (tmp_path / "INIT-001-review.md").write_text("---\nscore: 7\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"initiative-review": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("initiative-review", "INIT-001") == "completed"

    def test_initiative_split_pending(self, tmp_path):
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"initiative-split": lambda id: str(tmp_path / f"{id}-split-status.yaml")},
        ):
            assert check_id("initiative-split", "INIT-001") == "pending"

    def test_initiative_split_completed(self, tmp_path):
        (tmp_path / "INIT-001-split-status.yaml").write_text("status: done\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"initiative-split": lambda id: str(tmp_path / f"{id}-split-status.yaml")},
        ):
            assert check_id("initiative-split", "INIT-001") == "completed"

    def test_initiative_revise_pending(self, tmp_path):
        """Initiative-revise: review exists but not yet revised → pending."""
        (tmp_path / "INIT-001-review.md").write_text(
            "---\nscore: 4\nrecommendation: revise\n---\nBody\n"
        )
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"initiative-revise": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("initiative-revise", "INIT-001") == "pending"

    def test_initiative_revise_completed(self, tmp_path):
        """Initiative-revise: auto_revised=true → completed."""
        (tmp_path / "INIT-001-review.md").write_text(
            "---\nauto_revised: true\nscore: 7\n---\nBody\n"
        )
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"initiative-revise": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("initiative-revise", "INIT-001") == "completed"

    def test_initiative_revise_split_completed(self, tmp_path):
        """Initiative-revise: recommendation=split → completed (can't fix)."""
        (tmp_path / "INIT-001-review.md").write_text("---\nrecommendation: split\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"initiative-revise": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("initiative-revise", "INIT-001") == "completed"

    def test_initiative_revise_missing_file(self, tmp_path):
        """Initiative-revise: no review file → pending."""
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"initiative-revise": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("initiative-revise", "INIT-001") == "pending"

    def test_initiative_review_score_missing(self, tmp_path):
        """Initiative-review: file without score → pending (not silent completed)."""
        (tmp_path / "INIT-001-review.md").write_text("---\ntitle: test\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"initiative-review": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("initiative-review", "INIT-001") == "pending"

    def test_check_phase_initiative_review(self, tmp_path):
        (tmp_path / "INIT-001-review.md").write_text("---\nscore: 8\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"initiative-review": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            completed, errors, pending, total, next_poll = _check_phase(
                "initiative-review", ["INIT-001", "INIT-002"], fast=False
            )
            assert completed == 1
            assert pending == 1
            assert total == 2


# ── skill prose ──


SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", ".claude", "skills")


def _skill_text(name):
    with open(os.path.join(SKILLS_DIR, name, "SKILL.md")) as f:
        return f.read()


def _phases_used(text):
    return re.findall(r"--phase\s+([a-z-]+)", text)


class TestSkillBarrierUsage:
    """The barrier is prose, so nothing else in the suite exercises it.

    A skill that polls a phase name PHASE_CHECKS doesn't know exits 2 forever,
    and a forked skill that drops its barriers falls back to counting agent
    notifications — which is not a completion signal. Both are silent.
    """

    def test_all_polled_phases_are_known(self):
        """Every --phase in every skill resolves to a PHASE_CHECKS entry."""
        unknown = []
        for root, _dirs, files in os.walk(SKILLS_DIR):
            for fn in files:
                if not fn.endswith(".md"):
                    continue
                path = os.path.join(root, fn)
                with open(path) as f:
                    for phase in _phases_used(f.read()):
                        if phase not in PHASE_CHECKS:
                            unknown.append(f"{path}: {phase}")
        assert not unknown, f"unknown poll phases: {unknown}"

    def test_initiative_review_polls_as_much_as_rfe_review(self):
        """initiative-review is a fork of rfe.review — it must not lose barriers."""
        rfe = _skill_text("rfe.review")
        init = _skill_text("initiative-review")
        assert len(_phases_used(init)) >= len(_phases_used(rfe))
        assert init.count("NEXT_POLL") >= rfe.count("NEXT_POLL")

    def test_initiative_split_polls_as_much_as_rfe_split(self):
        """Same guard for the split pair."""
        rfe = _skill_text("rfe.split")
        init = _skill_text("initiative-split")
        assert len(_phases_used(init)) >= len(_phases_used(rfe))
        assert init.count("NEXT_POLL") >= rfe.count("NEXT_POLL")


# ── Registry derivation ──


# The 14 poll phases in the order the literal table had. argparse renders the --phase /
# --also-phase choices from this order, so it is CLI surface, not an implementation detail.
_TODAYS_PHASES = [
    "fetch",
    "create",
    "assess",
    "feasibility",
    "review",
    "revise",
    "split",
    "initiative-split",
    "initiative-fetch",
    "initiative-assess",
    "initiative-feasibility",
    "initiative-review",
    "initiative-revise",
    "initiative-alignment",
]


def _hermetic_registry():
    import type_registry

    return type_registry.load(extra_roots=[], env={})


class TestDerivedFromRegistry:
    """PHASE_CHECKS is a projection of types/<t>/type.yaml (design §10 item 2).

    Nothing in check_review_progress names a directory, an id field or a poll prefix: the
    rows are dirs x poll_prefix x dimension names, plus the rfe-only create barrier (#148).
    """

    def test_keys_are_todays_choices_in_todays_order(self):
        assert list(PHASE_CHECKS) == _TODAYS_PHASES

    def test_rows_are_the_descriptor_projection(self):
        for desc in _hermetic_registry():
            pp = desc.get("pipeline.poll_prefix")
            dirs = desc.dirs()
            assert PHASE_CHECKS[f"{pp}fetch"]("X-1") == f"{dirs['tasks']}/X-1.md"
            assert PHASE_CHECKS[f"{pp}assess"]("X-1") == "tmp/rfe-assess/single/X-1.result.md"
            for base in ("review", "revise"):
                assert PHASE_CHECKS[f"{pp}{base}"]("X-1") == f"{dirs['reviews']}/X-1-review.md"
            assert PHASE_CHECKS[f"{pp}split"]("X-1") == f"{dirs['reviews']}/X-1-split-status.yaml"
            for dim in desc.get("pipeline.dimensions"):
                name = dim["name"]
                assert PHASE_CHECKS[f"{pp}{name}"]("X-1") == f"{dirs['reviews']}/X-1-{name}.md"

    def test_create_row_is_rfe_only(self):
        """#148's Phase-1 barrier is grandfathered to rfe until PR-5's generic body."""
        for desc in _hermetic_registry():
            pp = desc.get("pipeline.poll_prefix")
            assert (f"{pp}create" in PHASE_CHECKS) is (desc.name == "rfe")
        assert PHASE_CHECKS["create"]("X-1") == PHASE_CHECKS["fetch"]("X-1")

    def test_create_mode_uses_the_owning_types_id_field(self, tmp_path, monkeypatch):
        """The create check compares identity.id_field, not a literal — rfe_id for rfe."""
        monkeypatch.chdir(tmp_path)
        rfe = _hermetic_registry().get("rfe")
        assert rfe.id_field == "rfe_id"
        task = tmp_path / PHASE_CHECKS["create"]("RFE-001")
        task.parent.mkdir(parents=True)
        task.write_text("---\ninitiative_id: RFE-001\n---\nBody\n")
        assert check_id("create", "RFE-001") == "pending"
        task.write_text(f"---\n{rfe.id_field}: RFE-001\n---\nBody\n")
        assert check_id("create", "RFE-001") == "completed"

    def test_modes_follow_the_phase_base_for_every_type(self, tmp_path, monkeypatch):
        """review -> score_present and revise -> revised_or_split for each type's poll prefix."""
        monkeypatch.chdir(tmp_path)
        for desc in _hermetic_registry():
            pp = desc.get("pipeline.poll_prefix")
            review = tmp_path / PHASE_CHECKS[f"{pp}review"]("X-1")
            review.parent.mkdir(parents=True, exist_ok=True)
            review.write_text("---\ntitle: t\n---\nBody\n")
            assert check_id(f"{pp}review", "X-1") == "pending"
            assert check_id(f"{pp}revise", "X-1") == "pending"
            review.write_text("---\nscore: 7\nrecommendation: split\n---\nBody\n")
            assert check_id(f"{pp}review", "X-1") == "completed"
            assert check_id(f"{pp}revise", "X-1") == "completed"
            review.write_text("---\nscore: 0\nerror: assess_failed\n---\nBody\n")
            assert check_id(f"{pp}review", "X-1") == "error"

    def test_other_phases_use_the_exists_mode_for_every_type(self, tmp_path, monkeypatch):
        """fetch, assess, split and every dimension complete on any file at the path — the
        content is not inspected (a review-shaped error stub still counts) — and pend on a
        missing one; for every type's poll prefix. Each file is removed again: the assess
        staging dir is shared by every type (design §10), so rfe's result file would otherwise
        complete initiative-assess."""
        monkeypatch.chdir(tmp_path)
        for desc in _hermetic_registry():
            pp = desc.get("pipeline.poll_prefix")
            dimensions = [dim["name"] for dim in desc.get("pipeline.dimensions")]
            for base in ["fetch", "assess", "split", *dimensions]:
                phase = f"{pp}{base}"
                assert check_id(phase, "X-1") == "pending", phase
                path = tmp_path / PHASE_CHECKS[phase]("X-1")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("---\nscore: null\nerror: assess_failed\n---\nBody\n")
                assert check_id(phase, "X-1") == "completed", phase
                path.unlink()
                assert check_id(phase, "X-1") == "pending", phase

    def test_a_partial_drop_in_type_contributes_no_rows(self, tmp_path):
        """A drop-in descriptor with no pipeline block (identity + dirs only — the shape
        tests/test_check_conflicts.py registers) is skipped, as artifact_utils skips it for
        SCHEMAS: the poll script still imports, keeps today's rows and answers the same."""
        import subprocess

        root = tmp_path / "extra"
        (root / "docs").mkdir(parents=True)
        (root / "docs" / "type.yaml").write_text(
            "schema_version: 1\n"
            "type: docs\n"
            "identity:\n"
            "  tracker: jira\n"
            '  jira: {project: DOCS, issue_type: Task, key_prefixes: ["DOCS-"]}\n'
            '  local_prefix: "DOC-"\n'
            "  id_field: doc_id\n"
            "dirs: {tasks: artifacts/doc-tasks, originals: artifacts/doc-originals}\n"
        )
        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("RFE_CREATOR_") and k not in ("CI", "GITHUB_ACTIONS")
        }
        env["RFE_CREATOR_EXTRA_TYPES"] = str(root)
        work = tmp_path / "work"
        work.mkdir()
        script = os.path.join(scripts_dir, "check_review_progress.py")
        result = subprocess.run(
            [sys.executable, script, "--phase", "review", "RFE-001"],
            capture_output=True,
            text=True,
            env=env,
            cwd=work,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "COMPLETED=0/1, PENDING=1, NEXT_POLL=60\n"
        env["PYTHONPATH"] = scripts_dir
        probe = "import check_review_progress as c; print(list(c.PHASE_CHECKS))"
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            env=env,
            cwd=work,
            check=True,
        )
        assert result.stdout.strip() == str(_TODAYS_PHASES)

    def test_a_drop_in_type_gets_rows_and_modes(self, tmp_path):
        """A type under RFE_CREATOR_EXTRA_TYPES appears after the shipped rows with its own
        prefix, dirs, dimensions and id field, and its review/revise phases get the real check
        modes instead of falling through to exists (the literal tuples did that)."""
        import json
        import subprocess

        import yaml

        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
        with open(os.path.join(scripts_dir, "..", "types", "rfe", "type.yaml")) as f:
            data = yaml.safe_load(f)
        data["type"] = "widget"
        data["identity"]["id_field"] = "widget_id"
        data["dirs"] = {
            "tasks": "artifacts/widgets",
            "originals": "artifacts/widget-originals",
            "reviews": "artifacts/widget-reviews",
        }
        data["pipeline"]["poll_prefix"] = "widget-"
        data["pipeline"]["state_prefix"] = "widget-"
        data["pipeline"]["dimensions"] = [
            {"name": "feasibility", "prompt": "x.md", "blocking": True},
            {"name": "security", "prompt": "y.md", "blocking": False},
        ]
        root = tmp_path / "extra"
        (root / "widget").mkdir(parents=True)
        with open(root / "widget" / "type.yaml", "w") as f:
            yaml.safe_dump(data, f, sort_keys=False)

        work = tmp_path / "work"
        reviews = work / "artifacts" / "widget-reviews"
        reviews.mkdir(parents=True)
        (reviews / "W-1-review.md").write_text("---\ntitle: t\n---\nBody\n")
        (reviews / "W-2-review.md").write_text("---\nscore: 7\nrecommendation: split\n---\n")
        probe = (
            "import json, check_review_progress as c; "
            "print(json.dumps({'keys': list(c.PHASE_CHECKS), "
            "'paths': {k: f('W-1') for k, f in c.PHASE_CHECKS.items() if k.startswith('widget-')}, "
            "'review_1': c.check_id('widget-review', 'W-1'), "
            "'revise_1': c.check_id('widget-revise', 'W-1'), "
            "'review_2': c.check_id('widget-review', 'W-2'), "
            "'revise_2': c.check_id('widget-revise', 'W-2')}))"
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("RFE_CREATOR_") and k not in ("CI", "GITHUB_ACTIONS")
        }
        env["RFE_CREATOR_EXTRA_TYPES"] = str(root)
        env["PYTHONPATH"] = scripts_dir
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            env=env,
            cwd=work,
            check=True,
        )
        got = json.loads(result.stdout)
        widget_keys = [
            "widget-fetch",
            "widget-assess",
            "widget-feasibility",
            "widget-security",
            "widget-review",
            "widget-revise",
            "widget-split",
        ]
        assert got["keys"] == _TODAYS_PHASES + widget_keys  # shipped rows first, no widget-create
        assert got["paths"] == {
            "widget-fetch": "artifacts/widgets/W-1.md",
            "widget-assess": "tmp/rfe-assess/single/W-1.result.md",
            "widget-feasibility": "artifacts/widget-reviews/W-1-feasibility.md",
            "widget-security": "artifacts/widget-reviews/W-1-security.md",
            "widget-review": "artifacts/widget-reviews/W-1-review.md",
            "widget-revise": "artifacts/widget-reviews/W-1-review.md",
            "widget-split": "artifacts/widget-reviews/W-1-split-status.yaml",
        }
        assert got["review_1"] == "pending"  # score_present, not exists
        assert got["revise_1"] == "pending"  # revised_or_split, not exists
        assert got["review_2"] == "completed"
        assert got["revise_2"] == "completed"


class _FakeDesc:
    """Minimal Descriptor stand-in: dotted get(), dirs(), and the id properties."""

    def __init__(self, name, data, dirs=None):
        self.name = name
        self._data = data
        self._dirs = dirs or {
            "tasks": "artifacts/x-tasks",
            "reviews": "artifacts/x-reviews",
            "originals": "artifacts/x-originals",
        }

    def get(self, dotted, default=None):
        return self._data.get(dotted, default)

    def dirs(self, form="artifacts"):
        if form == "bare":
            return {k: v.split("/", 1)[1] for k, v in self._dirs.items()}
        return dict(self._dirs)

    @property
    def local_id_pattern(self):
        return self._data["identity.local_id_pattern"]

    @property
    def key_prefixes(self):
        return list(self._data.get("identity.jira.key_prefixes", []))

    @property
    def id_field(self):
        return self._data.get("identity.id_field", "x_id")


@pytest.mark.parametrize("bad", ["review", "assess", "split", "fetch"])
def test_dimension_named_like_an_engine_phase_is_rejected(bad):
    desc = _FakeDesc("x", {"pipeline.dimensions": [{"name": bad, "prompt": "p.md"}]})
    import check_review_progress as crp

    with pytest.raises(ValueError, match="collides with an engine phase"):
        crp._phase_rows(desc)


def test_duplicate_dimension_names_are_rejected():
    desc = _FakeDesc(
        "x", {"pipeline.dimensions": [{"name": "feasibility"}, {"name": "feasibility"}]}
    )
    import check_review_progress as crp

    with pytest.raises(ValueError, match="collides"):
        crp._phase_rows(desc)


def test_distinct_dimension_names_build_rows():
    desc = _FakeDesc("x", {"pipeline.dimensions": [{"name": "feasibility"}, {"name": "alignment"}]})
    import check_review_progress as crp

    rows = crp._phase_rows(desc)
    assert rows["feasibility"]("ID-1") == "artifacts/x-reviews/ID-1-feasibility.md"
    assert set(rows) >= {"fetch", "assess", "feasibility", "alignment", "review", "revise", "split"}
