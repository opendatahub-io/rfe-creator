#!/usr/bin/env python3
"""Tests for scripts/generate_run_report.py — run report generation."""

import os
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import artifact_utils
import generate_run_report
import type_registry
from generate_run_report import TYPE_CONFIG, _parse_run_id, build_report

# A descriptor that ships outside types/ (never enumerated by default): the third type the
# registry-derived config must project without a code change.
FIXTURE_TYPES = os.path.join(os.path.dirname(__file__), "fixtures", "types")

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


REVIEW_REFUSED_TEMPLATE = """\
---
rfe_id: {rfe_id}
score: 6
pass: false
recommendation: split
feasibility: feasible
auto_revised: false
needs_attention: true
needs_attention_reason: {reason}
error: '{error}'
scores:
  what: 2
  why: 1
  open_to_how: 2
  not_a_task: 1
  right_sized: 0
---

## Feedback
Too big.
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

    def test_task_without_review_becomes_error_entry(self, art_dir):
        """A task file with no review must surface as an error entry, not
        vanish: the CLI's default population scans the reviews directory, so
        RHAIRFE-3201 — whose review was cleared for a reassess cycle that
        never ran — was absent from all 77 entries of its run while being
        marked processed (RHAIFIRST-582)."""
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-100.md",
            TASK_TEMPLATE.format(rfe_id="RHAIRFE-100", extra=""),
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-100-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RHAIRFE-100",
                score=9,
                pass_val="true",
                recommendation="submit",
                right_sized=2,
            ),
        )
        # Task present, review absent — the 3201 shape.
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-101.md",
            TASK_TEMPLATE.format(rfe_id="RHAIRFE-101", extra=""),
        )

        report = build_report(["RHAIRFE-100"], "20260825-151200", artifacts_dir=art_dir)

        by_id = {e["id"]: e for e in report["per_rfe"]}
        assert "RHAIRFE-101" in by_id, "task without review vanished from the report"
        entry = by_id["RHAIRFE-101"]
        assert entry["error"] == "review file not found"
        assert entry["tracker_ref"] == "RHAIRFE-101"
        assert report["results"]["errors"] == 1
        assert [e["id"] for e in report["errors"]] == ["RHAIRFE-101"]
        # input_count keeps meaning what was asked for, not what was found.
        assert report["input_count"] == 1

    def test_local_task_without_review_has_null_tracker_ref(self, art_dir):
        """The error entry keeps the tracker_ref contract: a local id that was
        never submitted has no remote reference."""
        _write(
            f"{art_dir}/rfe-tasks/RFE-002.md",
            TASK_TEMPLATE.format(rfe_id="RFE-002", extra=""),
        )

        report = build_report([], "20260825-151200", artifacts_dir=art_dir)

        by_id = {e["id"]: e for e in report["per_rfe"]}
        assert by_id["RFE-002"]["error"] == "review file not found"
        assert by_id["RFE-002"]["tracker_ref"] is None

    def test_torn_task_file_still_surfaces(self, art_dir):
        """A task file whose frontmatter was torn mid-write fails the scan —
        the id must be recovered from the filename instead of vanishing."""
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-99.md",
            "---\nrfe_id: RHAIRFE-99\ntitle: Torn\n",  # no closing ---
        )

        report = build_report([], "20260825-151200", artifacts_dir=art_dir)

        by_id = {e["id"]: e for e in report["per_rfe"]}
        assert by_id["RHAIRFE-99"]["error"] == "review file not found"
        assert by_id["RHAIRFE-99"]["tracker_ref"] == "RHAIRFE-99"

    def test_task_with_review_is_not_duplicated(self, art_dir):
        """The task-population fold must not double-report ids the review
        scan already covers."""
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-100.md",
            TASK_TEMPLATE.format(rfe_id="RHAIRFE-100", extra=""),
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-100-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RHAIRFE-100",
                score=9,
                pass_val="true",
                recommendation="submit",
                right_sized=2,
            ),
        )

        report = build_report(["RHAIRFE-100"], "20260825-151200", artifacts_dir=art_dir)

        assert [e["id"] for e in report["per_rfe"]] == ["RHAIRFE-100"]
        assert report["results"]["errors"] == 0

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


class TestRefusedSplitIsBlockedNotSplit:
    """A split split_submit refused created nothing in Jira — it is blocked work.

    submit.py records the refusal in the review frontmatter; the report surfaces
    it rather than re-deriving the leaf-count rule that lives in split_submit.
    """

    def _refused(self, art_dir, rfe_id, error, reason="Automatic splitting produced too many"):
        _write(f"{art_dir}/rfe-tasks/{rfe_id}.md", TASK_TEMPLATE.format(rfe_id=rfe_id, extra=""))
        _write(
            f"{art_dir}/rfe-reviews/{rfe_id}-review.md",
            REVIEW_REFUSED_TEMPLATE.format(rfe_id=rfe_id, reason=reason, error=error),
        )

    def test_too_many_children_is_blocked(self, art_dir):
        self._refused(art_dir, "RHAIRFE-2429", "split_refused: too many leaf children")

        report = build_report(["RHAIRFE-2429"], "2026-08-15T03:09:45Z", 5, [], [])

        entry = report["per_rfe"][0]
        assert entry["blocked_reason"] == "Automatic splitting produced too many"
        assert report["results"]["blocked"] == 1
        assert report["results"]["split"] == 0

    def test_jira_conflict_is_also_blocked(self, art_dir):
        self._refused(
            art_dir,
            "RHAIRFE-2455",
            "split_refused: jira conflict",
            reason="Parent description was modified in Jira since fetch.",
        )

        report = build_report(["RHAIRFE-2455"], "2026-08-15T03:09:45Z", 5, [], [])

        assert report["per_rfe"][0]["blocked_reason"].startswith("Parent description")
        assert report["results"]["blocked"] == 1

    def test_recommendation_is_preserved(self, art_dir):
        """`recommendation` is what the review advised; blocked_reason is what happened."""
        self._refused(art_dir, "RHAIRFE-3121", "split_refused: too many leaf children")

        report = build_report(["RHAIRFE-3121"], "2026-08-15T03:09:45Z", 5, [], [])

        assert report["per_rfe"][0]["recommendation"] == "split"

    def test_successful_split_is_untouched(self, art_dir):
        """A split that went through still counts as split and carries no reason."""
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-3079.md",
            TASK_TEMPLATE.format(rfe_id="RHAIRFE-3079", extra=""),
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-3079-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RHAIRFE-3079",
                score=8,
                pass_val="false",
                recommendation="split",
                right_sized=1,
            ),
        )

        report = build_report(["RHAIRFE-3079"], "2026-08-06T15:21:14Z", 5, [], [])

        assert "blocked_reason" not in report["per_rfe"][0]
        assert report["results"]["split"] == 1
        assert report["results"]["blocked"] == 0

    def test_crashed_split_counts_failed_not_split(self, art_dir):
        """A split_submit crash leaves recommendation: split on the review —
        counting it as a successful split reported the crash as an outcome
        (RHAIFIRST-571)."""
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-100.md",
            TASK_TEMPLATE.format(rfe_id="RHAIRFE-100", extra=""),
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-100-review.md",
            REVIEW_REFUSED_TEMPLATE.format(
                rfe_id="RHAIRFE-100",
                reason="Split submission failed partway (exit 4).",
                error="split_submit_failed: exit 4",
            ),
        )

        report = build_report(["RHAIRFE-100"], "20260831-120000", artifacts_dir=art_dir)

        assert report["results"]["failed"] == 1
        assert report["results"]["split"] == 0
        assert report["results"]["blocked"] == 0
        entry = report["per_rfe"][0]
        assert entry["failed_reason"].startswith("Split submission failed")
        assert "blocked_reason" not in entry
        # Not an error entry: the review is readable and the scores are real.
        assert report["errors"] == []

    def test_unrelated_review_error_is_not_blocked(self, art_dir):
        """Only the split_refused marker means blocked, not any error field."""
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-9000.md",
            TASK_TEMPLATE.format(rfe_id="RHAIRFE-9000", extra=""),
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-9000-review.md",
            REVIEW_REFUSED_TEMPLATE.format(
                rfe_id="RHAIRFE-9000", reason="Scorer timed out", error="assess_failed: timeout"
            ),
        )

        report = build_report(["RHAIRFE-9000"], "2026-08-15T03:09:45Z", 5, [], [])

        assert "blocked_reason" not in report["per_rfe"][0]
        assert report["results"]["blocked"] == 0
        assert report["results"]["split"] == 1

    def test_needs_attention_is_surfaced_on_rfe_entries(self, art_dir):
        """The initiative report already carried this; the RFE side did not."""
        self._refused(art_dir, "RHAIRFE-2429", "split_refused: too many leaf children")

        report = build_report(["RHAIRFE-2429"], "2026-08-15T03:09:45Z", 5, [], [])

        assert report["per_rfe"][0]["needs_attention"] is True


class TestEntryProvenanceFields:
    """tracker_ref, role and local_id — the three-mode classification per entry.

    | mode          | role         | tracker_ref  |
    |---------------|--------------|--------------|
    | intermediary  | intermediary | null         |
    | blocked leaf  | leaf         | null         |
    | submitted     | leaf         | the Jira key |
    """

    def _task(self, art_dir, rfe_id, status="Ready", parent=None, extra_fm=""):
        parent_line = f"parent_key: {parent}\n" if parent else ""
        _write(
            f"{art_dir}/rfe-tasks/{rfe_id}.md",
            f"---\nrfe_id: {rfe_id}\ntitle: T\npriority: Major\n"
            f"status: {status}\n{parent_line}{extra_fm}---\n\nBody.\n",
        )

    def _review(self, art_dir, rfe_id, extra_fm=""):
        _write(
            f"{art_dir}/rfe-reviews/{rfe_id}-review.md",
            f"---\nrfe_id: {rfe_id}\nscore: 9\npass: true\nrecommendation: submit\n"
            f"feasibility: feasible\nauto_revised: false\nneeds_attention: false\n{extra_fm}"
            "scores:\n  what: 2\n  why: 2\n  open_to_how: 2\n  not_a_task: 2\n  right_sized: 2\n"
            "---\n\nok.\n",
        )

    def test_submitted_entry_carries_tracker_ref_and_provenance(self, art_dir):
        """Post-rename: id is the Jira key, local_id preserves where it came from."""
        self._task(art_dir, "RHAIRFE-3082", status="Submitted", extra_fm="local_id: RFE-001\n")
        self._review(art_dir, "RHAIRFE-3082", extra_fm="local_id: RFE-001\n")

        report = build_report(["RHAIRFE-3082"], "2026-08-21T12:00:00Z")
        entry = report["per_rfe"][0]

        assert entry["tracker_ref"] == "RHAIRFE-3082"
        assert entry["role"] == "leaf"
        assert entry["local_id"] == "RFE-001"

    def test_unsubmitted_local_entry_is_a_leaf_without_a_ticket(self, art_dir):
        """Mode 1 shape: a Draft child whose split was refused."""
        self._task(art_dir, "RFE-001", status="Draft", parent="RHAIRFE-1000")
        self._review(art_dir, "RFE-001")
        self._task(art_dir, "RHAIRFE-1000", status="Archived")
        self._review(art_dir, "RHAIRFE-1000")

        report = build_report(["RHAIRFE-1000"], "2026-08-21T12:00:00Z")
        by_id = {e["id"]: e for e in report["per_rfe"]}

        assert by_id["RFE-001"]["tracker_ref"] is None
        assert by_id["RFE-001"]["role"] == "leaf"
        assert "local_id" not in by_id["RFE-001"]

    def test_archived_local_node_is_an_intermediary(self, art_dir):
        """Mode 2: re-split stepping stone — never was and never will be a ticket."""
        self._task(art_dir, "RHAIRFE-1000", status="Archived")
        self._review(art_dir, "RHAIRFE-1000")
        self._task(art_dir, "RFE-004", status="Archived", parent="RHAIRFE-1000")
        self._review(art_dir, "RFE-004")
        self._task(art_dir, "RFE-007", status="Ready", parent="RFE-004")
        self._review(art_dir, "RFE-007")

        report = build_report(["RHAIRFE-1000"], "2026-08-21T12:00:00Z")
        by_id = {e["id"]: e for e in report["per_rfe"]}

        assert by_id["RFE-004"]["role"] == "intermediary"
        assert by_id["RFE-004"]["tracker_ref"] is None

    def test_archived_jira_parent_is_not_an_intermediary(self, art_dir):
        """The real split parent is also Archived — but it IS a ticket.

        Classifying on Archived alone would mislabel exactly the row consumers
        care about most; the rule requires a local id as well.
        """
        self._task(art_dir, "RHAIRFE-1000", status="Archived")
        self._review(art_dir, "RHAIRFE-1000")

        report = build_report(["RHAIRFE-1000"], "2026-08-21T12:00:00Z")
        entry = report["per_rfe"][0]

        assert entry["role"] == "leaf"
        assert entry["tracker_ref"] == "RHAIRFE-1000"

    def test_empty_frontmatter_review_is_an_error_entry(self, art_dir):
        """read_frontmatter normalizes empty/non-mapping frontmatter to {} —
        without a guard that review becomes a normal entry with score 0,
        silently dragging the averages. Seen in the published corpus
        (20260727-031043/RHAIRFE-2911-review.md)."""
        self._task(art_dir, "RHAIRFE-2911", status="Ready")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-2911-review.md", "---\n---\n\n## Feedback\n")

        report = build_report(["RHAIRFE-2911"], "2026-08-21T12:00:00Z")
        entry = report["per_rfe"][0]

        assert "error" in entry
        assert entry["tracker_ref"] == "RHAIRFE-2911"
        assert report["results"]["errors"] == 1
        assert report["after_scores_avg"]["total"] == 0.0  # nothing scored, not a 0-score entry

    def test_review_without_a_score_is_an_error_entry(self, art_dir):
        """Non-empty but score-less frontmatter must not feed a phantom 0 into
        the averages. Full schema validation is deliberately NOT used here —
        the aggregator tolerates field drift across review vintages; only the
        values it consumes are checked."""
        self._task(art_dir, "RHAIRFE-77", status="Ready")
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-77-review.md",
            "---\nrfe_id: RHAIRFE-77\nrecommendation: submit\n---\n\nok.\n",
        )

        report = build_report(["RHAIRFE-77"], "2026-08-21T12:00:00Z")
        entry = report["per_rfe"][0]

        assert "error" in entry and "no usable score" in entry["error"]
        assert entry["tracker_ref"] == "RHAIRFE-77"
        assert report["after_scores_avg"]["total"] == 0.0
        assert report["results"]["errors"] == 1

    def test_malformed_nested_score_is_an_error_entry_not_a_crash(self, art_dir):
        """One corrupt review must become an error entry — not abort the whole
        report via sum() raising TypeError in avg()."""
        self._task(art_dir, "RHAIRFE-80", status="Ready")
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-80-review.md",
            "---\nrfe_id: RHAIRFE-80\nscore: 8\npass: true\nrecommendation: submit\n"
            "feasibility: feasible\nauto_revised: false\nneeds_attention: false\n"
            "scores:\n  what: bad\n  why: 2\n---\n\nok.\n",
        )
        # A healthy sibling proves the report itself survives.
        self._task(art_dir, "RHAIRFE-81", status="Ready")
        self._review(art_dir, "RHAIRFE-81")

        report = build_report(["RHAIRFE-80", "RHAIRFE-81"], "2026-08-24T12:00:00Z")
        by_id = {e["id"]: e for e in report["per_rfe"]}

        assert "unusable scores members" in by_id["RHAIRFE-80"]["error"]
        assert by_id["RHAIRFE-81"]["after_score"] == 9
        assert report["after_scores_avg"]["total"] == 9.0

    def test_malformed_before_score_is_an_error_entry(self, art_dir):
        self._task(art_dir, "RHAIRFE-82", status="Ready")
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-82-review.md",
            "---\nrfe_id: RHAIRFE-82\nscore: 8\npass: true\nrecommendation: submit\n"
            "feasibility: feasible\nauto_revised: false\nneeds_attention: false\n"
            "before_score: seven\n---\n\nok.\n",
        )

        report = build_report(["RHAIRFE-82"], "2026-08-24T12:00:00Z")

        assert "unusable before_score" in report["per_rfe"][0]["error"]
        assert report["results"]["errors"] == 1

    def test_non_dict_scores_do_not_crash_or_pollute(self, art_dir):
        self._task(art_dir, "RHAIRFE-78", status="Ready")
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-78-review.md",
            "---\nrfe_id: RHAIRFE-78\nscore: 9\npass: true\nrecommendation: submit\n"
            "feasibility: feasible\nauto_revised: false\nneeds_attention: false\n"
            "scores: what happened here\n---\n\nok.\n",
        )

        report = build_report(["RHAIRFE-78"], "2026-08-21T12:00:00Z")

        assert report["per_rfe"][0]["after_score"] == 9
        assert report["after_scores_avg"]["what"] == 0.0

    def test_missing_task_file_omits_role(self, art_dir):
        """Absent means not determined — never guessed."""
        self._review(art_dir, "RHAIRFE-1234")

        report = build_report(["RHAIRFE-1234"], "2026-08-21T12:00:00Z")
        entry = report["per_rfe"][0]

        assert "role" not in entry
        assert entry["tracker_ref"] == "RHAIRFE-1234"


class TestReportRootMetadata:
    """Root fields that let a consumer know what it is reading."""

    def _one_rfe(self, art_dir):
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

    def test_schema_version_and_type_are_emitted(self, art_dir):
        self._one_rfe(art_dir)

        report = build_report(["RHAIRFE-1234"], "2026-08-18T09:00:00Z")

        assert report["report_schema_version"] == 1
        assert report["type"] == "rfe"

    def test_stage_defaults_to_pre_submit(self, art_dir):
        """A caller that forgets must understate authority, never overstate it."""
        self._one_rfe(art_dir)

        report = build_report(["RHAIRFE-1234"], "2026-08-18T09:00:00Z")

        assert report["report_stage"] == "pre_submit"

    def test_stage_is_final_when_asked(self, art_dir):
        self._one_rfe(art_dir)

        report = build_report(["RHAIRFE-1234"], "2026-08-18T09:00:00Z", report_stage="final")

        assert report["report_stage"] == "final"

    def test_type_follows_entry_type(self, tmp_path, monkeypatch):
        for d in ["initiatives", "initiative-reviews"]:
            os.makedirs(tmp_path / "artifacts" / d)
        art = str(tmp_path / "artifacts")

        report = build_report(
            [], "2026-08-18T09:00:00Z", artifacts_dir=art, entry_type="initiative"
        )

        assert report["type"] == "initiative"

    def test_build_report_rejects_an_unknown_stage(self, art_dir):
        """argparse only guards the CLI; a programmatic caller must fail at write time too."""
        self._one_rfe(art_dir)

        with pytest.raises(ValueError, match="unsupported report stage"):
            build_report(["RHAIRFE-1234"], "2026-08-18T09:00:00Z", report_stage="submitted")

    def test_cli_rejects_an_unknown_stage(self, tmp_path):
        os.makedirs(tmp_path / "artifacts" / "rfe-reviews")
        result = subprocess.run(
            [
                sys.executable,
                os.path.join(os.path.dirname(__file__), "..", "scripts", "generate_run_report.py"),
                "--start-time",
                "20260818-090000",
                "--artifacts-dir",
                str(tmp_path / "artifacts"),
                "--report-stage",
                "definitely-not-a-stage",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "invalid choice" in result.stderr

    def test_cli_writes_the_stage_it_was_given(self, tmp_path):
        art = str(tmp_path / "artifacts")
        os.makedirs(f"{art}/rfe-tasks")
        os.makedirs(f"{art}/rfe-reviews")
        _write(
            f"{art}/rfe-tasks/RHAIRFE-1234.md",
            TASK_TEMPLATE.format(rfe_id="RHAIRFE-1234", extra=""),
        )
        _write(
            f"{art}/rfe-reviews/RHAIRFE-1234-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RHAIRFE-1234",
                score=9,
                pass_val="true",
                recommendation="submit",
                right_sized=2,
            ),
        )
        result = subprocess.run(
            [
                sys.executable,
                os.path.join(os.path.dirname(__file__), "..", "scripts", "generate_run_report.py"),
                "--start-time",
                "20260818-090000",
                "--artifacts-dir",
                art,
                "--report-stage",
                "final",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        with open(result.stdout.strip()) as f:
            written = yaml.safe_load(f)
        assert written["report_stage"] == "final"
        assert written["report_schema_version"] == 1
        assert written["type"] == "rfe"


class TestTypeConfigIsRegistryDerived:
    """TYPE_CONFIG is a projection of types/<name>/type.yaml, not a hand-kept dict."""

    def test_keys_are_the_registry_types_in_choices_order(self):
        registry = type_registry.load(extra_roots=[], env={})
        assert list(TYPE_CONFIG) == registry.choices()

    def test_shipped_entries_are_the_descriptor_projection(self):
        registry = type_registry.load(extra_roots=[], env={})
        for name, cfg in TYPE_CONFIG.items():
            desc = registry.get(name)
            assert cfg["score_fields"] == desc.score_fields
            assert cfg["reviews_dir"] == desc.dirs("bare")["reviews"]
            assert cfg["tasks_dir"] == desc.dirs("bare")["tasks"]
            assert cfg["item_key"] == desc.get("reporting.item_key")
            assert cfg["output_prefix"] == desc.get("snapshot.report_prefix")
            assert cfg["extra_entry_fields"] == desc.get("reporting.run_report.extra_entry_fields")
            assert cfg["id_field"] == desc.id_field
            assert cfg["child_parent_prefixes"] == (desc.local_prefix, *desc.key_prefixes)
            assert cfg["tracker_prefix"] == desc.write_prefix
            assert cfg["local_prefix"] == desc.local_prefix
            # The generic scan bound to the descriptor, not a per-type function.
            assert cfg["scan_tasks"].func is artifact_utils.scan_tasks
            assert cfg["scan_tasks"].keywords["desc"].name == name

    def test_scan_tasks_is_the_generic_over_the_type_tree(self, tmp_path):
        """config["scan_tasks"](artifacts_dir) returns exactly what the per-type wrappers
        return — the contract split_children_map, build_report and batch_summary rely on."""
        art = str(tmp_path / "artifacts")
        _write(f"{art}/rfe-tasks/RHAIRFE-1.md", TASK_TEMPLATE.format(rfe_id="RHAIRFE-1", extra=""))
        _write(
            f"{art}/initiatives/INIT-1.md",
            "---\ninitiative_id: INIT-1\ntitle: T\npriority: Major\nstatus: Draft\n---\n\nbody\n",
        )
        rfe_rows = TYPE_CONFIG["rfe"]["scan_tasks"](art)
        init_rows = TYPE_CONFIG["initiative"]["scan_tasks"](art)
        assert rfe_rows == artifact_utils.scan_task_files(art)
        assert init_rows == artifact_utils.scan_initiative_task_files(art)
        assert [d["rfe_id"] for _, d in rfe_rows] == ["RHAIRFE-1"]
        assert [d["initiative_id"] for _, d in init_rows] == ["INIT-1"]

    def test_a_drop_in_descriptor_projects_without_a_code_change(self):
        registry = type_registry.load(extra_roots=[FIXTURE_TYPES], env={})
        cfg = generate_run_report._type_config(registry.get("epic"))
        assert set(cfg) == set(TYPE_CONFIG["rfe"])  # same shape, so downstream code is untouched
        assert cfg["reviews_dir"] == "epic-reviews"
        assert cfg["tasks_dir"] == "epic-tasks"
        assert cfg["item_key"] == "results"
        assert cfg["output_prefix"] == ""  # no snapshot.report_prefix declared -> no prefix
        assert cfg["extra_entry_fields"] == []  # no reporting.run_report block
        assert cfg["id_field"] == "epic_id"
        # local prefix + tracker key prefixes; never conventions.parent_key_patterns
        assert cfg["child_parent_prefixes"] == ("RHAISTRAT-", "RHAI-")
        assert cfg["tracker_prefix"] == "RHAI-"
        assert cfg["local_prefix"] == "RHAISTRAT-"
        # The scan is the generic bound to this descriptor: a third type gets a scanner over
        # its own dirs.tasks without a code change.
        assert cfg["scan_tasks"].func is artifact_utils.scan_tasks
        assert cfg["scan_tasks"].keywords == {"desc": registry.get("epic")}

    def test_projection_does_not_alias_descriptor_data(self):
        registry = type_registry.load(extra_roots=[], env={})
        desc = registry.get("rfe")
        cfg = generate_run_report._type_config(desc)
        cfg["extra_entry_fields"].append("mutated")
        cfg["score_fields"].append("mutated")
        assert "mutated" not in desc.get("reporting.run_report.extra_entry_fields")
        assert "mutated" not in desc.score_fields
