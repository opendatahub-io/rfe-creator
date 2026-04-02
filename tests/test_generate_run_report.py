#!/usr/bin/env python3
"""Tests for scripts/generate_run_report.py — run report generation."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from generate_run_report import build_report

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
    monkeypatch.setattr(generate_run_report, "ARTIFACTS_DIR",
                        str(tmp_path / "artifacts"))
    return str(tmp_path / "artifacts")


class TestSplitChildrenIncluded:
    def test_children_get_own_entries(self, art_dir):
        """Split children should appear as their own per_rfe entries."""
        # Parent task — was split
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               TASK_TEMPLATE.format(rfe_id="RHAIRFE-1234", extra=""))
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REVIEW_TEMPLATE.format(rfe_id="RHAIRFE-1234", score=6,
                                      pass_val="false", recommendation="split",
                                      right_sized=0))
        # Child tasks with parent_key
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_TEMPLATE.format(rfe_id="RFE-001",
                                    extra="parent_key: RHAIRFE-1234"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               REVIEW_TEMPLATE.format(rfe_id="RFE-001", score=9,
                                      pass_val="true", recommendation="submit",
                                      right_sized=2))
        _write(f"{art_dir}/rfe-tasks/RFE-002.md",
               TASK_TEMPLATE.format(rfe_id="RFE-002",
                                    extra="parent_key: RHAIRFE-1234"))
        _write(f"{art_dir}/rfe-reviews/RFE-002-review.md",
               REVIEW_TEMPLATE.format(rfe_id="RFE-002", score=8,
                                      pass_val="true", recommendation="submit",
                                      right_sized=1))

        # Only pass parent ID — children should be auto-discovered
        report = build_report(["RHAIRFE-1234"], "2026-04-01T22:50:53Z", 5,
                              [], [])

        ids_in_report = [e["id"] for e in report["per_rfe"]]
        assert "RHAIRFE-1234" in ids_in_report
        assert "RFE-001" in ids_in_report
        assert "RFE-002" in ids_in_report
        assert len(report["per_rfe"]) == 3

    def test_children_not_duplicated_if_already_passed(self, art_dir):
        """If caller already includes child IDs, don't duplicate them."""
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               TASK_TEMPLATE.format(rfe_id="RHAIRFE-1234", extra=""))
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REVIEW_TEMPLATE.format(rfe_id="RHAIRFE-1234", score=6,
                                      pass_val="false", recommendation="split",
                                      right_sized=0))
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_TEMPLATE.format(rfe_id="RFE-001",
                                    extra="parent_key: RHAIRFE-1234"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               REVIEW_TEMPLATE.format(rfe_id="RFE-001", score=9,
                                      pass_val="true", recommendation="submit",
                                      right_sized=2))

        report = build_report(["RHAIRFE-1234", "RFE-001"],
                              "2026-04-01T22:50:53Z", 5, [], [])

        ids_in_report = [e["id"] for e in report["per_rfe"]]
        assert ids_in_report.count("RFE-001") == 1
        assert len(report["per_rfe"]) == 2

    def test_input_count_reflects_original_ids(self, art_dir):
        """input_count should only count caller-supplied IDs, not children."""
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               TASK_TEMPLATE.format(rfe_id="RHAIRFE-1234", extra=""))
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REVIEW_TEMPLATE.format(rfe_id="RHAIRFE-1234", score=6,
                                      pass_val="false", recommendation="split",
                                      right_sized=0))
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_TEMPLATE.format(rfe_id="RFE-001",
                                    extra="parent_key: RHAIRFE-1234"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               REVIEW_TEMPLATE.format(rfe_id="RFE-001", score=9,
                                      pass_val="true", recommendation="submit",
                                      right_sized=2))

        report = build_report(["RHAIRFE-1234"], "2026-04-01T22:50:53Z", 5,
                              [], [])

        assert report["input_count"] == 1
        assert len(report["per_rfe"]) == 2

    def test_no_children_no_change(self, art_dir):
        """When no splits occurred, behavior is unchanged."""
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               TASK_TEMPLATE.format(rfe_id="RHAIRFE-1234", extra=""))
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REVIEW_TEMPLATE.format(rfe_id="RHAIRFE-1234", score=9,
                                      pass_val="true", recommendation="submit",
                                      right_sized=2))

        report = build_report(["RHAIRFE-1234"], "2026-04-01T22:50:53Z", 5,
                              [], [])

        assert len(report["per_rfe"]) == 1
        assert report["per_rfe"][0]["id"] == "RHAIRFE-1234"
