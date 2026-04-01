#!/usr/bin/env python3
"""Tests for scripts/collect_recommendations.py — recommendation grouping."""
import os
import subprocess

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                      "collect_recommendations.py")


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


REVIEW_TEMPLATE = """\
---
rfe_id: {rfe_id}
score: {score}
pass: {pass_val}
recommendation: {recommendation}
feasibility: feasible
auto_revised: {auto_revised}
needs_attention: false
scores:
  what: 2
  why: 2
  open_to_how: 2
  not_a_task: 2
  right_sized: 0
---

## Feedback
Looks good.
"""

ERROR_REVIEW = """\
---
rfe_id: {rfe_id}
score: 0
pass: false
recommendation: revise
feasibility: feasible
auto_revised: false
needs_attention: true
error: "{error}"
scores:
  what: 0
  why: 0
  open_to_how: 0
  not_a_task: 0
  right_sized: 0
---

## Feedback
Error occurred.
"""


def _run(args):
    result = subprocess.run(
        ["python3", SCRIPT] + args,
        capture_output=True, text=True,
    )
    return result.stdout.strip(), result.stderr, result.returncode


def _parse_output(stdout):
    """Parse KEY=val1,val2 output into dict."""
    result = {}
    for line in stdout.splitlines():
        if "=" in line:
            key, val = line.split("=", 1)
            result[key] = [v for v in val.split(",") if v]
    return result


@pytest.fixture
def art_dir(tmp_path):
    os.makedirs(tmp_path / "artifacts" / "rfe-reviews")
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield str(tmp_path)
    os.chdir(orig)


class TestCollectDefault:
    def test_groups_by_recommendation(self, art_dir):
        _write(f"{art_dir}/artifacts/rfe-reviews/RFE-001-review.md",
               REVIEW_TEMPLATE.format(rfe_id="RFE-001", score=9,
                                      pass_val="true", recommendation="submit",
                                      auto_revised="false"))
        _write(f"{art_dir}/artifacts/rfe-reviews/RFE-002-review.md",
               REVIEW_TEMPLATE.format(rfe_id="RFE-002", score=3,
                                      pass_val="false", recommendation="reject",
                                      auto_revised="false"))
        _write(f"{art_dir}/artifacts/rfe-reviews/RFE-003-review.md",
               REVIEW_TEMPLATE.format(rfe_id="RFE-003", score=7,
                                      pass_val="true", recommendation="split",
                                      auto_revised="false"))
        out, _, rc = _run(["RFE-001", "RFE-002", "RFE-003"])
        assert rc == 0
        groups = _parse_output(out)
        assert "RFE-001" in groups["SUBMIT"]
        assert "RFE-002" in groups["REJECT"]
        assert "RFE-003" in groups["SPLIT"]

    def test_autorevise_reject_maps_to_reject(self, art_dir):
        """autorevise_reject should be grouped as REJECT, not ERRORS."""
        _write(f"{art_dir}/artifacts/rfe-reviews/RFE-001-review.md",
               REVIEW_TEMPLATE.format(rfe_id="RFE-001", score=4,
                                      pass_val="false",
                                      recommendation="autorevise_reject",
                                      auto_revised="true"))
        out, _, rc = _run(["RFE-001"])
        assert rc == 0
        groups = _parse_output(out)
        assert "RFE-001" in groups["REJECT"]
        assert groups["ERRORS"] == []

    def test_missing_review_file_goes_to_errors(self, art_dir):
        out, _, rc = _run(["RFE-MISSING"])
        assert rc == 0
        groups = _parse_output(out)
        assert "RFE-MISSING" in groups["ERRORS"]

    def test_error_field_goes_to_errors(self, art_dir):
        _write(f"{art_dir}/artifacts/rfe-reviews/RFE-001-review.md",
               ERROR_REVIEW.format(rfe_id="RFE-001", error="fetch_failed"))
        out, _, rc = _run(["RFE-001"])
        assert rc == 0
        groups = _parse_output(out)
        assert "RFE-001" in groups["ERRORS"]


class TestCollectReassess:
    def test_revised_and_failing_needs_reassess(self, art_dir):
        _write(f"{art_dir}/artifacts/rfe-reviews/RFE-001-review.md",
               REVIEW_TEMPLATE.format(rfe_id="RFE-001", score=5,
                                      pass_val="false", recommendation="revise",
                                      auto_revised="true"))
        out, _, rc = _run(["--reassess", "RFE-001"])
        assert rc == 0
        groups = _parse_output(out)
        assert "RFE-001" in groups["REASSESS"]

    def test_passing_goes_to_done(self, art_dir):
        _write(f"{art_dir}/artifacts/rfe-reviews/RFE-001-review.md",
               REVIEW_TEMPLATE.format(rfe_id="RFE-001", score=9,
                                      pass_val="true", recommendation="submit",
                                      auto_revised="true"))
        out, _, rc = _run(["--reassess", "RFE-001"])
        assert rc == 0
        groups = _parse_output(out)
        assert "RFE-001" in groups["DONE"]

    def test_not_revised_goes_to_done(self, art_dir):
        _write(f"{art_dir}/artifacts/rfe-reviews/RFE-001-review.md",
               REVIEW_TEMPLATE.format(rfe_id="RFE-001", score=5,
                                      pass_val="false", recommendation="revise",
                                      auto_revised="false"))
        out, _, rc = _run(["--reassess", "RFE-001"])
        assert rc == 0
        groups = _parse_output(out)
        assert "RFE-001" in groups["DONE"]
