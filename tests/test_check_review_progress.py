#!/usr/bin/env python3
"""Tests for scripts/check_review_progress.py — poll mode, phase checking,
status formatting, and adaptive sleep intervals."""
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_review_progress import (
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
        """Review phase: unparseable frontmatter → pending."""
        f = tmp_path / "RHAIRFE-1-review.md"
        f.write_text("---\n: bad yaml [[\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"review": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("review", "RHAIRFE-1") == "pending"

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
        """Revise phase: unparseable frontmatter → pending."""
        f = tmp_path / "RHAIRFE-1-review.md"
        f.write_text("---\n: bad [[\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"revise": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            assert check_id("revise", "RHAIRFE-1") == "pending"


# ── _check_phase ──


class TestCheckPhase:
    def test_all_pending(self, tmp_path):
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            completed, errors, pending, total, next_poll = \
                _check_phase("fetch", ["A", "B", "C"], fast=False)
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
            completed, errors, pending, total, next_poll = \
                _check_phase("fetch", ["A", "B", "C"], fast=False)
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
            _, _, _, _, next_poll = \
                _check_phase("fetch", ["A", "B", "C", "D"], fast=False)
            assert next_poll == 30

    def test_adaptive_interval_75pct(self, tmp_path):
        """75%+ complete → 15s interval."""
        for name in ["A", "B", "C"]:
            (tmp_path / f"{name}.md").write_text("done")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            _, _, _, _, next_poll = \
                _check_phase("fetch", ["A", "B", "C", "D"], fast=False)
            assert next_poll == 15

    def test_fast_poll_caps_at_15(self, tmp_path):
        """Fast mode caps at 15s regardless of completion ratio."""
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            _, _, _, _, next_poll = \
                _check_phase("fetch", ["A", "B", "C"], fast=True)
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


# ── --poll mode (via main) ──


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
            code, out = self._run_main(
                ["--poll", "--phase", "fetch",
                 "RHAIRFE-1", "RHAIRFE-2"])
        assert code == 0
        assert "COMPLETED=2/2" in out
        assert "Sleeping" not in out

    def test_poll_sleeps_then_completes(self, tmp_path):
        """Pending IDs → sleeps, then file appears → exits 0."""
        def create_on_sleep(seconds):
            # Simulate agent completing during sleep
            (tmp_path / "RHAIRFE-1.md").write_text("done")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ), patch("check_review_progress.time.sleep",
                 side_effect=create_on_sleep) as mock_sleep:
            code, out = self._run_main(
                ["--poll", "--fast-poll", "--phase", "fetch",
                 "RHAIRFE-1"])
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
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ), patch("check_review_progress.time.sleep",
                 side_effect=create_on_sleep) as mock_sleep:
            code, _ = self._run_main(
                ["--poll", "--phase", "fetch", "A", "B"])
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
                ["--poll", "--phase", "fetch",
                 "--also-phase", "assess", "A", "B"])
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
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {
                "fetch": lambda id: str(tmp_path / f"fetch-{id}.md"),
                "assess": lambda id: str(tmp_path / f"assess-{id}.md"),
            },
        ), patch("check_review_progress.time.sleep",
                 side_effect=create_on_sleep) as mock_sleep:
            code, out = self._run_main(
                ["--poll", "--fast-poll", "--phase", "fetch",
                 "--also-phase", "assess", "A", "B"])
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
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {
                "fetch": lambda id: str(tmp_path / f"fetch-{id}.md"),
                "assess": lambda id: str(tmp_path / f"assess-{id}.md"),
            },
        ), patch("check_review_progress.time.sleep",
                 side_effect=create_on_sleep) as mock_sleep:
            code, _ = self._run_main(
                ["--poll", "--phase", "fetch",
                 "--also-phase", "assess", "A", "B"])
        assert code == 0
        mock_sleep.assert_called_once_with(60)

    def test_poll_single_phase_no_all_complete_message(self, tmp_path):
        """Single phase complete → no 'All phases complete.' message."""
        (tmp_path / "A.md").write_text("done")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            code, out = self._run_main(
                ["--poll", "--phase", "fetch", "A"])
        assert code == 0
        assert "All phases complete." not in out

    def test_poll_max_wait_exits_3_on_timeout(self, tmp_path):
        """Pending IDs + small max-wait → exit 3 with pending IDs on stdout."""
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ), patch("check_review_progress.time.sleep") as mock_sleep, \
           patch("check_review_progress.time.monotonic",
                 side_effect=[0, 0, 0]):
            # monotonic: start=0, before-guard=0, elapsed+60>1 → timeout
            code, out = self._run_main(
                ["--poll", "--max-wait", "1", "--phase", "fetch",
                 "RHAIRFE-1", "RHAIRFE-2"])
        assert code == 3
        assert "RHAIRFE-1" in out
        assert "RHAIRFE-2" in out
        assert "Re-run this command" in out
        mock_sleep.assert_not_called()

    def test_poll_max_wait_completes_before_timeout(self, tmp_path):
        """Agents complete within max-wait → exit 0."""
        def create_on_sleep(seconds):
            (tmp_path / "RHAIRFE-1.md").write_text("done")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ), patch("check_review_progress.time.sleep",
                 side_effect=create_on_sleep), \
           patch("check_review_progress.time.monotonic",
                 side_effect=[0, 0, 5, 5]):
            # start=0, guard: 0+15<90 → sleep, second iter: complete
            code, out = self._run_main(
                ["--poll", "--fast-poll", "--max-wait", "90",
                 "--phase", "fetch", "RHAIRFE-1"])
        assert code == 0
        assert "COMPLETED=1/1" in out

    def test_poll_max_wait_zero_disables(self, tmp_path):
        """--max-wait 0 disables timeout (runs until complete)."""
        call_count = [0]
        def create_on_second_sleep(seconds):
            call_count[0] += 1
            if call_count[0] >= 2:
                (tmp_path / "A.md").write_text("done")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ), patch("check_review_progress.time.sleep",
                 side_effect=create_on_second_sleep), \
           patch("check_review_progress.time.monotonic",
                 side_effect=[0, 0, 100, 100, 200, 200]):
            # Even at elapsed=200, max_wait=0 means no timeout
            code, out = self._run_main(
                ["--poll", "--max-wait", "0", "--fast-poll",
                 "--phase", "fetch", "A"])
        assert code == 0
        assert call_count[0] == 2

    def test_poll_max_wait_multi_phase(self, tmp_path):
        """Multi-phase with one stalling → exit 3."""
        for name in ["A", "B"]:
            (tmp_path / f"fetch-{name}.md").write_text("done")
        # assess files missing → pending
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {
                "fetch": lambda id: str(tmp_path / f"fetch-{id}.md"),
                "assess": lambda id: str(tmp_path / f"assess-{id}.md"),
            },
        ), patch("check_review_progress.time.sleep"), \
           patch("check_review_progress.time.monotonic",
                 side_effect=[0, 0, 0]):
            code, out = self._run_main(
                ["--poll", "--max-wait", "1", "--phase", "fetch",
                 "--also-phase", "assess", "A", "B"])
        assert code == 3
        assert "Re-run this command" in out

    def test_poll_max_wait_message_format(self, tmp_path):
        """Timeout message contains pending IDs and directive."""
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ), patch("check_review_progress.time.sleep"), \
           patch("check_review_progress.time.monotonic",
                 side_effect=[0, 5]):
            # monotonic: start=0, elapsed=5-0=5, 5+60>1 → timeout
            code, out = self._run_main(
                ["--poll", "--max-wait", "1", "--phase", "fetch",
                 "RHAIRFE-100", "RHAIRFE-200"])
        assert code == 3
        assert "Waited 5s" in out
        assert "RHAIRFE-100" in out
        assert "RHAIRFE-200" in out
        assert "Re-run this command" in out

    def test_poll_max_wait_deduplicates_ids(self, tmp_path):
        """Pending in multiple phases → each ID appears only once."""
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {
                "fetch": lambda id: str(tmp_path / f"fetch-{id}.md"),
                "assess": lambda id: str(tmp_path / f"assess-{id}.md"),
            },
        ), patch("check_review_progress.time.sleep"), \
           patch("check_review_progress.time.monotonic",
                 side_effect=[0, 0, 0]):
            code, out = self._run_main(
                ["--poll", "--max-wait", "1", "--phase", "fetch",
                 "--also-phase", "assess", "A"])
        assert code == 3
        # "A" pending in both phases but should appear only once
        timeout_line = [l for l in out.splitlines() if "Re-run" in l][0]
        assert timeout_line.count(" A") == 1

    def test_poll_max_wait_caps_id_list(self, tmp_path):
        """10+ pending IDs → message shows first 5 + '... and N more'."""
        ids = [f"RFE-{i:03d}" for i in range(10)]
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ), patch("check_review_progress.time.sleep"), \
           patch("check_review_progress.time.monotonic",
                 side_effect=[0, 0, 0]):
            code, out = self._run_main(
                ["--poll", "--max-wait", "1", "--phase", "fetch"] + ids)
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
        sys.argv = ["check_review_progress.py",
                     "--poll", "--max-wait", "-1",
                     "--phase", "fetch", "A"]
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
            code, out = self._run_main(
                ["--poll", "--phase", "fetch",
                 "--id-file", str(id_file)])
        assert code == 0
        assert "COMPLETED=3/3" in out

    def test_poll_errors_dont_block(self, tmp_path):
        """Errors count as done — only pending blocks exit."""
        # RHAIRFE-1: has score + error → error
        (tmp_path / "RHAIRFE-1-review.md").write_text(
            "---\nscore: 5\nerror: true\n---\nBody\n")
        # RHAIRFE-2: has score → completed
        (tmp_path / "RHAIRFE-2-review.md").write_text(
            "---\nscore: 8\n---\nBody\n")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"review": lambda id: str(tmp_path / f"{id}-review.md")},
        ):
            code, out = self._run_main(
                ["--poll", "--phase", "review",
                 "RHAIRFE-1", "RHAIRFE-2"])
        assert code == 0
        assert "ERRORS=1" in out
        assert "COMPLETED=1/2" in out


# ── Legacy mode (no --poll) ──


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
            code, out = self._run_main(
                ["--phase", "fetch", "A", "B"])
        assert code is None or code == 0
        assert out.strip() == "COMPLETED=1/2, PENDING=1, NEXT_POLL=30"

    def test_legacy_no_ids_exits_2(self, tmp_path):
        code, _ = self._run_main(["--phase", "fetch"])
        assert code == 2

    def test_max_wait_ignored_without_poll(self, tmp_path):
        """--max-wait without --poll runs legacy mode, no timeout behavior."""
        (tmp_path / "A.md").write_text("done")
        with patch.dict(
            "check_review_progress.PHASE_CHECKS",
            {"fetch": lambda id: str(tmp_path / f"{id}.md")},
        ):
            code, out = self._run_main(
                ["--max-wait", "30", "--phase", "fetch", "A", "B"])
        assert code is None or code == 0
        assert "COMPLETED=1/2" in out
        assert "Re-run" not in out
