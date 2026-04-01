#!/usr/bin/env python3
"""Tests for scripts/submit.py — content-diff guard and skip logic."""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "submit.py")


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _run_submit(artifacts_dir):
    """Run submit.py --dry-run and return stdout."""
    env = {
        **os.environ,
        "JIRA_SERVER": "https://fake.atlassian.net",
        "JIRA_USER": "fake@example.com",
        "JIRA_TOKEN": "fake-token",
    }
    result = subprocess.run(
        ["python3", SCRIPT, "--dry-run", "--artifacts-dir", artifacts_dir],
        capture_output=True, text=True, env=env,
    )
    return result.stdout, result.stderr, result.returncode


TASK_FM = """\
---
rfe_id: {rfe_id}
title: Test RFE
priority: Major
status: Ready
---

## Problem Statement

Users need better logging for compliance audits.

## Acceptance Criteria

- Audit logs capture all inference requests
"""

REVIEW_FM = """\
---
rfe_id: {rfe_id}
score: 9
pass: true
recommendation: submit
feasibility: feasible
auto_revised: {auto_revised}
needs_attention: false
scores:
  what: 2
  why: 2
  open_to_how: 2
  not_a_task: 2
  right_sized: 1
---

## Assessor Feedback
Looks good.
"""


@pytest.fixture
def art_dir(tmp_path):
    """Create a minimal artifacts directory."""
    for d in ["rfe-tasks", "rfe-reviews", "rfe-originals"]:
        os.makedirs(tmp_path / d)
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield str(tmp_path)
    os.chdir(orig)


class TestContentDiffGuard:
    def test_existing_rfe_no_changes_skipped(self, art_dir):
        """Existing RFE with identical content → SKIP no changes."""
        body = "## Problem\n\nSame content.\n"
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               TASK_FM.format(rfe_id="RHAIRFE-1234") )
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", body)
        # Make task body match original (strip_metadata removes frontmatter)
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n---\n{body}")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REVIEW_FM.format(rfe_id="RHAIRFE-1234", auto_revised="false"))

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "SKIP" in stdout
        assert "no changes" in stdout

    def test_existing_rfe_with_changes_submitted(self, art_dir):
        """Existing RFE with different content → update."""
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md",
               "## Problem\n\nOriginal content.\n")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n---\n"
               f"## Problem\n\nRevised content with improvements.\n")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REVIEW_FM.format(rfe_id="RHAIRFE-1234", auto_revised="true"))

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Would update" in stdout
        assert "no changes" not in stdout

    def test_existing_rfe_no_original_file_submitted(self, art_dir):
        """Existing RFE with no original file → submit (no guard)."""
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               TASK_FM.format(rfe_id="RHAIRFE-1234"))
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REVIEW_FM.format(rfe_id="RHAIRFE-1234", auto_revised="false"))
        # No file in rfe-originals/

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Would update" in stdout

    def test_new_rfe_always_created(self, art_dir):
        """New RFE (RFE-NNN) → always create, no content-diff check."""
        _write(f"{art_dir}/rfe-tasks/RFE-001-test.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false"))

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Would create" in stdout


class TestSkipLogic:
    def test_rejected_rfe_skipped(self, art_dir):
        """RFE with recommendation=reject → SKIP rejected."""
        _write(f"{art_dir}/rfe-tasks/RFE-001-test.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false")
               .replace("recommendation: submit", "recommendation: reject"))

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "SKIP" in stdout
        assert "rejected" in stdout

    def test_archived_rfe_excluded(self, art_dir):
        """Archived RFE → not in plan at all."""
        _write(f"{art_dir}/rfe-tasks/RFE-001-test.md",
               TASK_FM.format(rfe_id="RFE-001").replace(
                   "status: Ready", "status: Archived"))

        stdout, stderr, rc = _run_submit(art_dir)
        # Should error because no submittable RFEs found
        assert rc == 1
        assert "No submittable" in stderr or "No RFE task" in stderr


class TestAutoRevisedLabel:
    def test_auto_revised_label_applied(self, art_dir):
        """auto_revised=true → rfe-creator-auto-revised label."""
        _write(f"{art_dir}/rfe-tasks/RFE-001-test.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               REVIEW_FM.format(rfe_id="RFE-001", auto_revised="true"))

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "rfe-creator-auto-revised" in stdout

    def test_no_label_when_not_revised(self, art_dir):
        """auto_revised=false → no auto-revised label."""
        _write(f"{art_dir}/rfe-tasks/RFE-001-test.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false"))

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "rfe-creator-auto-revised" not in stdout
