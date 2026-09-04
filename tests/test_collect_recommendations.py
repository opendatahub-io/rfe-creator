#!/usr/bin/env python3
"""Tests for scripts/collect_recommendations.py — recommendation grouping."""

import os
import subprocess
import sys

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "collect_recommendations.py")


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

CORRUPT_REVIEW = """\
---
rfe_id: {rfe_id}
score: 6
pass: false
recommendation: revise
feasibility: indeterminate
needs_attention: true
needs_attention_reason: Blocked: needs architecture input
---

## Feedback
The unquoted colon above makes this block unparseable.
"""


def _run(args):
    # sys.executable, not a bare "python3": the child then runs under the same
    # interpreter (and dependencies) as the test session. A literal "python3"
    # resolves to whatever is first on PATH, which under `uv run pytest` is an
    # ephemeral interpreter without PyYAML installed, so the subprocess crashes.
    result = subprocess.run(
        [sys.executable, SCRIPT] + args,
        capture_output=True,
        text=True,
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
        _write(
            f"{art_dir}/artifacts/rfe-reviews/RFE-001-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RFE-001",
                score=9,
                pass_val="true",
                recommendation="submit",
                auto_revised="false",
            ),
        )
        _write(
            f"{art_dir}/artifacts/rfe-reviews/RFE-002-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RFE-002",
                score=3,
                pass_val="false",
                recommendation="reject",
                auto_revised="false",
            ),
        )
        _write(
            f"{art_dir}/artifacts/rfe-reviews/RFE-003-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RFE-003",
                score=7,
                pass_val="true",
                recommendation="split",
                auto_revised="false",
            ),
        )
        out, _, rc = _run(["RFE-001", "RFE-002", "RFE-003"])
        assert rc == 0
        groups = _parse_output(out)
        assert "RFE-001" in groups["SUBMIT"]
        assert "RFE-002" in groups["REJECT"]
        assert "RFE-003" in groups["SPLIT"]

    def test_autorevise_reject_maps_to_reject(self, art_dir):
        """autorevise_reject should be grouped as REJECT, not ERRORS."""
        _write(
            f"{art_dir}/artifacts/rfe-reviews/RFE-001-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RFE-001",
                score=4,
                pass_val="false",
                recommendation="autorevise_reject",
                auto_revised="true",
            ),
        )
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
        _write(
            f"{art_dir}/artifacts/rfe-reviews/RFE-001-review.md",
            ERROR_REVIEW.format(rfe_id="RFE-001", error="fetch_failed"),
        )
        out, _, rc = _run(["RFE-001"])
        assert rc == 0
        groups = _parse_output(out)
        assert "RFE-001" in groups["ERRORS"]


class TestCollectReassess:
    def test_revised_and_failing_needs_reassess(self, art_dir):
        _write(
            f"{art_dir}/artifacts/rfe-reviews/RFE-001-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RFE-001",
                score=5,
                pass_val="false",
                recommendation="revise",
                auto_revised="true",
            ),
        )
        out, _, rc = _run(["--reassess", "RFE-001"])
        assert rc == 0
        groups = _parse_output(out)
        assert "RFE-001" in groups["REASSESS"]

    def test_passing_goes_to_done(self, art_dir):
        _write(
            f"{art_dir}/artifacts/rfe-reviews/RFE-001-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RFE-001",
                score=9,
                pass_val="true",
                recommendation="submit",
                auto_revised="true",
            ),
        )
        out, _, rc = _run(["--reassess", "RFE-001"])
        assert rc == 0
        groups = _parse_output(out)
        assert "RFE-001" in groups["DONE"]

    def test_not_revised_goes_to_done(self, art_dir):
        _write(
            f"{art_dir}/artifacts/rfe-reviews/RFE-001-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RFE-001",
                score=5,
                pass_val="false",
                recommendation="revise",
                auto_revised="false",
            ),
        )
        out, _, rc = _run(["--reassess", "RFE-001"])
        assert rc == 0
        groups = _parse_output(out)
        assert "RFE-001" in groups["DONE"]


class TestCollectInitiative:
    """Tests for --type initiative, which reads from initiative-reviews/ directory."""

    @pytest.fixture(autouse=True)
    def _init_dir(self, tmp_path):
        os.makedirs(tmp_path / "artifacts" / "initiative-reviews")
        orig = os.getcwd()
        os.chdir(tmp_path)
        self._art_dir = str(tmp_path)
        yield
        os.chdir(orig)

    def test_groups_by_recommendation(self):
        _write(
            f"{self._art_dir}/artifacts/initiative-reviews/INIT-001-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="INIT-001",
                score=9,
                pass_val="true",
                recommendation="submit",
                auto_revised="false",
            ),
        )
        _write(
            f"{self._art_dir}/artifacts/initiative-reviews/INIT-002-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="INIT-002",
                score=3,
                pass_val="false",
                recommendation="revise",
                auto_revised="false",
            ),
        )
        out, _, rc = _run(["--type", "initiative", "INIT-001", "INIT-002"])
        assert rc == 0
        groups = _parse_output(out)
        assert "INIT-001" in groups["SUBMIT"]
        assert "INIT-002" in groups["REVISE"]

    def test_missing_initiative_review_goes_to_errors(self):
        out, _, rc = _run(["--type", "initiative", "INIT-MISSING"])
        assert rc == 0
        groups = _parse_output(out)
        assert "INIT-MISSING" in groups["ERRORS"]

    def test_reassess_initiative(self):
        _write(
            f"{self._art_dir}/artifacts/initiative-reviews/INIT-001-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="INIT-001",
                score=5,
                pass_val="false",
                recommendation="revise",
                auto_revised="true",
            ),
        )
        out, _, rc = _run(["--type", "initiative", "--reassess", "INIT-001"])
        assert rc == 0
        groups = _parse_output(out)
        assert "INIT-001" in groups["REASSESS"]


class TestCollectErrors:
    def test_unparseable_frontmatter_goes_to_errors(self, art_dir):
        """A corrupt review is what --errors exists to find, so it must not crash."""
        _write(
            f"{art_dir}/artifacts/rfe-reviews/RFE-001-review.md",
            CORRUPT_REVIEW.format(rfe_id="RFE-001"),
        )
        out, _, rc = _run(["--errors", "RFE-001"])
        assert rc == 0
        assert "RFE-001" in _parse_output(out)["ERRORS"]


class TestCorruptReviewDoesNotCrashAnyMode:
    """A single malformed review must not take down a batch in any mode.

    read_frontmatter raises ValidationError on a bad block, so every collection
    mode — not just --errors — has to tolerate it.
    """

    def test_default_mode_buckets_corrupt_as_error(self, art_dir):
        _write(
            f"{art_dir}/artifacts/rfe-reviews/RFE-001-review.md",
            CORRUPT_REVIEW.format(rfe_id="RFE-001"),
        )
        _write(
            f"{art_dir}/artifacts/rfe-reviews/RFE-002-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="RFE-002",
                score=9,
                pass_val="true",
                recommendation="submit",
                auto_revised="false",
            ),
        )
        out, _, rc = _run(["RFE-001", "RFE-002"])
        assert rc == 0
        groups = _parse_output(out)
        assert "RFE-001" in groups["ERRORS"]
        assert "RFE-002" in groups["SUBMIT"]  # the healthy review is unaffected

    def test_reassess_mode_does_not_crash_on_corrupt(self, art_dir):
        _write(
            f"{art_dir}/artifacts/rfe-reviews/RFE-001-review.md",
            CORRUPT_REVIEW.format(rfe_id="RFE-001"),
        )
        out, _, rc = _run(["--reassess", "RFE-001"])
        assert rc == 0
        # Not reassessable — bucketed as done rather than crashing.
        assert "RFE-001" in _parse_output(out)["DONE"]
