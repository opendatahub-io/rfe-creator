#!/usr/bin/env python3
"""Tests for scripts/generate_run_report.py — run report generation."""

import os
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from generate_run_report import _parse_run_id, build_report

TASK_TEMPLATE = """\
---
rfe_id: {rfe_id}
title: Test RFE
priority: Major
status: Ready
{extra}
---

## Problem Statement
Test content.
"""

REVIEW_TEMPLATE = """\
---
rfe_id: {rfe_id}
score: {score}
pass: {pass_val}
recommendation: {recommendation}
feasibility: feasible
auto_revised: false
needs_attention: false
scores:
  what: 2
  why: 2
  open_to_how: 2
  not_a_task: 2
  right_sized: {right_sized}
---

## Feedback
Looks good.
"""


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


@pytest.fixture
def art_dir(tmp_path, monkeypatch):
    """Create artifacts dir and patch the module to use it."""
    for d in ["rfe-tasks", "rfe-reviews"]:
        os.makedirs(tmp_path / "artifacts" / d)
    import generate_run_report

    monkeypatch.setattr(generate_run_report, "DEFAULT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    return str(tmp_path / "artifacts")


class TestSplitChildrenIncluded:
    def test_children_get_own_entries(self, art_dir):
        """Split children should appear as their own per_rfe entries."""
        # Parent task — was split
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            TASK_TEMPLATE.format(rfe_id="RHAIRFE-1234", extra=""),
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RHAIRFE-1234",
                score=6,
                pass_val="false",
                recommendation="split",
                right_sized=0,
            ),
        )
        # Child tasks with parent_key
        _write(
            f"{art_dir}/rfe-tasks/RFE-001.md",
            TASK_TEMPLATE.format(rfe_id="RFE-001", extra="parent_key: RHAIRFE-1234"),
        )
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RFE-001", score=9, pass_val="true", recommendation="submit", right_sized=2
            ),
        )
        _write(
            f"{art_dir}/rfe-tasks/RFE-002.md",
            TASK_TEMPLATE.format(rfe_id="RFE-002", extra="parent_key: RHAIRFE-1234"),
        )
        _write(
            f"{art_dir}/rfe-reviews/RFE-002-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RFE-002", score=8, pass_val="true", recommendation="submit", right_sized=1
            ),
        )

        # Only pass parent ID — children should be auto-discovered
        report = build_report(["RHAIRFE-1234"], "2026-04-01T22:50:53Z", 5, [], [])

        ids_in_report = [e["id"] for e in report["per_rfe"]]
        assert "RHAIRFE-1234" in ids_in_report
        assert "RFE-001" in ids_in_report
        assert "RFE-002" in ids_in_report
        assert len(report["per_rfe"]) == 3

    def test_children_not_duplicated_if_already_passed(self, art_dir):
        """If caller already includes child IDs, don't duplicate them."""
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            TASK_TEMPLATE.format(rfe_id="RHAIRFE-1234", extra=""),
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RHAIRFE-1234",
                score=6,
                pass_val="false",
                recommendation="split",
                right_sized=0,
            ),
        )
        _write(
            f"{art_dir}/rfe-tasks/RFE-001.md",
            TASK_TEMPLATE.format(rfe_id="RFE-001", extra="parent_key: RHAIRFE-1234"),
        )
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RFE-001", score=9, pass_val="true", recommendation="submit", right_sized=2
            ),
        )

        report = build_report(["RHAIRFE-1234", "RFE-001"], "2026-04-01T22:50:53Z", 5, [], [])

        ids_in_report = [e["id"] for e in report["per_rfe"]]
        assert ids_in_report.count("RFE-001") == 1
        assert len(report["per_rfe"]) == 2

    def test_input_count_reflects_original_ids(self, art_dir):
        """input_count should only count caller-supplied IDs, not children."""
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            TASK_TEMPLATE.format(rfe_id="RHAIRFE-1234", extra=""),
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RHAIRFE-1234",
                score=6,
                pass_val="false",
                recommendation="split",
                right_sized=0,
            ),
        )
        _write(
            f"{art_dir}/rfe-tasks/RFE-001.md",
            TASK_TEMPLATE.format(rfe_id="RFE-001", extra="parent_key: RHAIRFE-1234"),
        )
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RFE-001", score=9, pass_val="true", recommendation="submit", right_sized=2
            ),
        )

        report = build_report(["RHAIRFE-1234"], "2026-04-01T22:50:53Z", 5, [], [])

        assert report["input_count"] == 1
        assert len(report["per_rfe"]) == 2

    def test_no_children_no_change(self, art_dir):
        """When no splits occurred, behavior is unchanged."""
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            TASK_TEMPLATE.format(rfe_id="RHAIRFE-1234", extra=""),
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RHAIRFE-1234",
                score=9,
                pass_val="true",
                recommendation="submit",
                right_sized=2,
            ),
        )

        report = build_report(["RHAIRFE-1234"], "2026-04-01T22:50:53Z", 5, [], [])

        assert len(report["per_rfe"]) == 1
        assert report["per_rfe"][0]["id"] == "RHAIRFE-1234"


REVISED_REVIEW_TEMPLATE = """\
---
rfe_id: {rfe_id}
score: {score}
pass: {pass_val}
recommendation: {recommendation}
feasibility: feasible
auto_revised: true
needs_attention: false
before_score: {before_score}
scores:
  what: 2
  why: 2
  open_to_how: 2
  not_a_task: 2
  right_sized: {right_sized}
before_scores:
  what: 1
  why: 1
  open_to_how: 2
  not_a_task: 2
  right_sized: {right_sized}
---

## Feedback
Revised.
"""


class TestScoreAveragePopulations:
    """before/after averages must cover the same items.

    An unrevised RFE has no before_score, so averaging only the items that
    carry one compares 16 items against 17 and reports a regression that
    never happened — what the 2026-08-10 run showed as "9.4 -> 9.2" when
    nothing had been revised at all.
    """

    def _review(self, art_dir, rfe_id, score, right_sized=2):
        _write(
            f"{art_dir}/rfe-tasks/{rfe_id}.md",
            TASK_TEMPLATE.format(rfe_id=rfe_id, extra=""),
        )
        _write(
            f"{art_dir}/rfe-reviews/{rfe_id}-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id=rfe_id,
                score=score,
                pass_val="true",
                recommendation="submit",
                right_sized=right_sized,
            ),
        )

    def test_unrevised_low_scorer_does_not_fake_a_regression(self, art_dir):
        for i in range(1, 4):
            self._review(art_dir, f"RHAIRFE-{1000 + i}", 10)
        # The odd one out: never revised, so no before_score, and a low score.
        self._review(art_dir, "RHAIRFE-2000", 2)

        ids = [f"RHAIRFE-{1000 + i}" for i in range(1, 4)] + ["RHAIRFE-2000"]
        report = build_report(ids, "20260404-170041", 5, [], [], artifacts_dir=art_dir)

        assert report["before_scores_avg"]["total"] == report["after_scores_avg"]["total"]
        assert report["after_scores_avg"]["total"] == 8.0

    def test_unrevised_item_omits_before_score_in_its_entry(self, art_dir):
        self._review(art_dir, "RHAIRFE-2000", 7)
        report = build_report(["RHAIRFE-2000"], "20260404-170041", 5, [], [], artifacts_dir=art_dir)
        entry = report["per_rfe"][0]
        assert "before_score" not in entry
        assert entry["after_score"] == 7

    def test_revised_item_still_shows_improvement(self, art_dir):
        self._review(art_dir, "RHAIRFE-1001", 10)
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1002.md",
            TASK_TEMPLATE.format(rfe_id="RHAIRFE-1002", extra=""),
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1002-review.md",
            REVISED_REVIEW_TEMPLATE.format(
                rfe_id="RHAIRFE-1002",
                score=10,
                pass_val="true",
                recommendation="submit",
                before_score=4,
                right_sized=2,
            ),
        )

        report = build_report(
            ["RHAIRFE-1001", "RHAIRFE-1002"], "20260404-170041", 5, [], [], artifacts_dir=art_dir
        )

        # (10 + 4) / 2 vs (10 + 10) / 2 — the unrevised item contributes 10 to
        # both sides, so only the real revision moves the average.
        assert report["before_scores_avg"]["total"] == 7.0
        assert report["after_scores_avg"]["total"] == 10.0
        # Per-criterion follows the same rule.
        assert report["before_scores_avg"]["what"] == 1.5
        assert report["after_scores_avg"]["what"] == 2.0

    def test_criterion_averages_cover_the_same_items(self, art_dir):
        for i in range(1, 4):
            self._review(art_dir, f"RHAIRFE-{1000 + i}", 10)
        self._review(art_dir, "RHAIRFE-2000", 8, right_sized=0)

        ids = [f"RHAIRFE-{1000 + i}" for i in range(1, 4)] + ["RHAIRFE-2000"]
        report = build_report(ids, "20260404-170041", 5, [], [], artifacts_dir=art_dir)

        for field in ("what", "why", "open_to_how", "not_a_task", "right_sized"):
            assert report["before_scores_avg"][field] == report["after_scores_avg"][field], field
        assert report["after_scores_avg"]["right_sized"] == 1.5


class TestParseRunId:
    def test_yyyymmdd_hhmmss_format(self):
        """YYYYMMDD-HHMMSS passes through unchanged."""
        assert _parse_run_id("20260404-170041") == "20260404-170041"

    def test_iso_format(self):
        """ISO timestamp is converted to YYYYMMDD-HHMMSS."""
        assert _parse_run_id("2026-04-04T17:00:41Z") == "20260404-170041"

    def test_iso_format_with_offset(self):
        """ISO timestamp with UTC offset."""
        assert _parse_run_id("2026-04-04T17:00:41+00:00") == "20260404-170041"


class TestScanReviewFiles:
    def test_build_report_with_artifacts_dir(self, art_dir):
        """build_report accepts artifacts_dir parameter."""
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            TASK_TEMPLATE.format(rfe_id="RHAIRFE-1234", extra=""),
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RHAIRFE-1234",
                score=9,
                pass_val="true",
                recommendation="submit",
                right_sized=2,
            ),
        )

        report = build_report(["RHAIRFE-1234"], "20260404-170041", 5, [], [], artifacts_dir=art_dir)

        assert report["run_id"] == "20260404-170041"
        assert len(report["per_rfe"]) == 1

    def test_cli_scan_review_files(self, tmp_path):
        """When no IDs passed on CLI, scan review files."""
        art = str(tmp_path / "artifacts")
        for d in ["rfe-tasks", "rfe-reviews"]:
            os.makedirs(os.path.join(art, d))
        _write(
            f"{art}/rfe-tasks/RHAIRFE-100.md", TASK_TEMPLATE.format(rfe_id="RHAIRFE-100", extra="")
        )
        _write(
            f"{art}/rfe-reviews/RHAIRFE-100-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RHAIRFE-100",
                score=8,
                pass_val="true",
                recommendation="submit",
                right_sized=2,
            ),
        )
        _write(
            f"{art}/rfe-tasks/RHAIRFE-200.md", TASK_TEMPLATE.format(rfe_id="RHAIRFE-200", extra="")
        )
        _write(
            f"{art}/rfe-reviews/RHAIRFE-200-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RHAIRFE-200",
                score=7,
                pass_val="true",
                recommendation="submit",
                right_sized=1,
            ),
        )

        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                os.path.join(os.path.dirname(__file__), "..", "scripts", "generate_run_report.py"),
                "--start-time",
                "20260404-170041",
                "--artifacts-dir",
                art,
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        # Should have found both review files
        import yaml

        out_path = result.stdout.strip()
        with open(out_path) as f:
            report = yaml.safe_load(f)
        ids = [e["id"] for e in report["per_rfe"]]
        assert "RHAIRFE-100" in ids
        assert "RHAIRFE-200" in ids


class TestNoReviewsToScan:
    """A run with nothing to review still reports; a missing dir is an error.

    REPORT is a script phase, so pipeline_state.cmd_run_phase propagates a
    nonzero exit — erroring on an empty batch would fail the run at its last step.
    """

    def _run(self, art):
        return subprocess.run(
            [
                sys.executable,
                os.path.join(os.path.dirname(__file__), "..", "scripts", "generate_run_report.py"),
                "--start-time",
                "20260404-170041",
                "--artifacts-dir",
                art,
            ],
            capture_output=True,
            text=True,
        )

    def test_empty_reviews_dir_writes_a_report(self, tmp_path):
        art = str(tmp_path / "artifacts")
        os.makedirs(os.path.join(art, "rfe-reviews"))

        result = self._run(art)

        assert result.returncode == 0
        with open(result.stdout.strip()) as f:
            report = yaml.safe_load(f)
        assert report["input_count"] == 0
        assert report["per_rfe"] == []
        assert report["results"]["passed"] == 0

    def test_missing_reviews_dir_is_an_error(self, tmp_path):
        art = str(tmp_path / "artifacts")
        os.makedirs(art)

        result = self._run(art)

        assert result.returncode == 2
        assert "no reviews directory" in result.stderr

    def test_the_error_names_the_path(self, tmp_path):
        art = str(tmp_path / "artifacts")
        os.makedirs(art)
        assert os.path.join(art, "rfe-reviews") in self._run(art).stderr


class TestGenerateReviewPdfArtifactsDir:
    def test_artifacts_dir_uses_correct_paths(self, tmp_path):
        """generate_review_pdf.py --artifacts-dir reads from the given dir."""
        art = str(tmp_path / "custom-artifacts")
        for d in ["rfe-tasks", "rfe-reviews", "rfe-originals"]:
            os.makedirs(os.path.join(art, d))
        _write(
            f"{art}/rfe-tasks/RHAIRFE-500.md", TASK_TEMPLATE.format(rfe_id="RHAIRFE-500", extra="")
        )
        _write(
            f"{art}/rfe-reviews/RHAIRFE-500-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RHAIRFE-500",
                score=8,
                pass_val="true",
                recommendation="submit",
                right_sized=2,
            ),
        )

        out_file = str(tmp_path / "report.html")
        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                os.path.join(os.path.dirname(__file__), "..", "scripts", "generate_review_pdf.py"),
                "--artifacts-dir",
                art,
                "--output",
                out_file,
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert os.path.exists(out_file)
        with open(out_file) as f:
            html = f.read()
        assert "RHAIRFE-500" in html
