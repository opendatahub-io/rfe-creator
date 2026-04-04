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

REJECT_REVIEW_FM = """\
---
rfe_id: {rfe_id}
score: 3
pass: false
recommendation: reject
feasibility: feasible
auto_revised: false
needs_attention: false
scores:
  what: 0
  why: 1
  open_to_how: 1
  not_a_task: 1
  right_sized: 0
---

## Assessor Feedback
Does not meet rubric.
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
    def test_existing_rfe_no_changes_label_only(self, art_dir):
        """Existing RFE with identical content and passing review → Label only."""
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
        assert "Label only" in stdout
        assert "rfe-creator-autofix-rubric-pass" in stdout

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
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false"))

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Would create" in stdout


class TestSkipLogic:
    def test_rejected_rfe_skipped(self, art_dir):
        """RFE with recommendation=reject → SKIP rejected."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
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
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001").replace(
                   "status: Ready", "status: Archived"))

        stdout, stderr, rc = _run_submit(art_dir)
        # Should error because no submittable RFEs found
        assert rc == 1
        assert "No submittable" in stderr or "No RFE task" in stderr


class TestAutoRevisedLabel:
    def test_auto_revised_label_applied(self, art_dir):
        """auto_revised=true → rfe-creator-auto-revised label."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               REVIEW_FM.format(rfe_id="RFE-001", auto_revised="true"))

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "rfe-creator-auto-revised" in stdout

    def test_no_label_when_not_revised(self, art_dir):
        """auto_revised=false → no auto-revised label."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false"))

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "rfe-creator-auto-revised" not in stdout


class TestRemoveLabels:
    """Tests for stale label removal on rejected RFEs."""

    def _task_with_labels(self, rfe_id, labels):
        """Task frontmatter with original_labels set."""
        labels_yaml = "\n".join(f"- {l}" for l in labels) if labels else "[]"
        return (f"---\nrfe_id: {rfe_id}\ntitle: Test RFE\n"
                f"priority: Major\nstatus: Ready\n"
                f"original_labels:\n{labels_yaml}\n---\n\n"
                f"## Problem\n\nContent here.\n")

    def test_rejected_with_rubric_pass_removes_label(self, art_dir):
        """Rejected RFE that had rubric-pass → Remove labels action."""
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               self._task_with_labels("RHAIRFE-1234",
                                      ["rfe-creator-autofix-rubric-pass"]))
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"))

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Remove labels" in stdout
        assert "rfe-creator-autofix-rubric-pass" in stdout
        assert "Would remove labels" in stdout

    def test_rejected_without_rubric_pass_skips(self, art_dir):
        """Rejected RFE without rubric-pass → plain SKIP."""
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               TASK_FM.format(rfe_id="RHAIRFE-1234"))
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"))

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "SKIP" in stdout
        assert "rejected" in stdout
        assert "Remove labels" not in stdout

    def test_rejected_new_rfe_skips(self, art_dir):
        """Rejected new RFE (RFE-NNN) → SKIP, no label removal."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               REJECT_REVIEW_FM.format(rfe_id="RFE-001"))

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "SKIP" in stdout
        assert "Remove labels" not in stdout

    def test_autorevise_reject_removes_rubric_pass(self, art_dir):
        """autorevise_reject with rubric-pass → Remove labels."""
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               self._task_with_labels("RHAIRFE-1234",
                                      ["rfe-creator-autofix-rubric-pass"]))
        review = REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234").replace(
            "recommendation: reject", "recommendation: autorevise_reject")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md", review)

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Remove labels" in stdout
        assert "Would remove labels" in stdout


class TestSplitRefusal:
    """Tests for submit.py handling split_submit.py exit code 2."""

    PARENT_TASK = (
        "---\nrfe_id: RHAIRFE-1000\ntitle: Parent RFE\n"
        "priority: Major\nstatus: Archived\n---\n\nParent content.\n"
    )

    CHILD_TASK_TPL = (
        "---\nrfe_id: RFE-{num:03d}\ntitle: Child RFE {num}\n"
        "priority: Major\nstatus: Ready\n"
        "parent_key: RHAIRFE-1000\n---\n\nChild {num} content.\n"
    )

    REVIEW = (
        "---\nrfe_id: RHAIRFE-1000\nscore: 9\npass: true\n"
        "recommendation: submit\nfeasibility: feasible\n"
        "auto_revised: false\nneeds_attention: false\n"
        "scores:\n  what: 2\n  why: 2\n  open_to_how: 2\n"
        "  not_a_task: 2\n  right_sized: 1\n---\n\nLooks good.\n"
    )

    def _setup_oversized_split(self, art_dir, num_children=7):
        """Create a parent with too many children to trigger refusal."""
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", self.PARENT_TASK)
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1000-review.md", self.REVIEW)
        for i in range(1, num_children + 1):
            _write(f"{art_dir}/rfe-tasks/RFE-{i:03d}.md",
                   self.CHILD_TASK_TPL.format(num=i))

    def test_refusal_sets_frontmatter_fields(self, art_dir):
        """Exit code 2 → needs_attention + reason + error in review."""
        self._setup_oversized_split(art_dir)

        stdout, stderr, rc = _run_submit(art_dir)
        assert rc == 0  # submit.py continues after refusal

        assert "Split refused" in stdout

        # Check review frontmatter was updated
        import yaml
        review_path = f"{art_dir}/rfe-reviews/RHAIRFE-1000-review.md"
        with open(review_path) as f:
            content = f.read()
        end = content.index("---", 3)
        fm = yaml.safe_load(content[3:end])
        assert fm["needs_attention"] is True
        assert "too many child RFEs" in fm["needs_attention_reason"]
        assert fm["error"] == "split_refused: too many leaf children"

    def test_refusal_prints_needs_attention(self, art_dir):
        """Exit code 2 → dry-run prints needs-attention comment."""
        self._setup_oversized_split(art_dir)

        stdout, stderr, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Would post needs-attention comment" in stdout

    def test_refusal_continues_processing(self, art_dir):
        """Refused split doesn't abort — other RFEs still submitted."""
        self._setup_oversized_split(art_dir)

        # Add a regular (non-split) RFE that should still be processed
        _write(f"{art_dir}/rfe-tasks/RFE-099.md",
               TASK_FM.format(rfe_id="RFE-099"))
        _write(f"{art_dir}/rfe-reviews/RFE-099-review.md",
               REVIEW_FM.format(rfe_id="RFE-099", auto_revised="false"))

        stdout, stderr, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Split refused" in stdout
        assert "Would create" in stdout  # RFE-099 still processed


class TestSnapshotUpdate:
    """Tests for snapshot update after submission."""

    def test_dry_run_does_not_update_snapshot(self, art_dir):
        """Dry-run does not update snapshot."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false"))

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        # Dry run should NOT create any snapshot files
        snap_dir = os.path.join(art_dir, "auto-fix-runs")
        assert not os.path.exists(snap_dir)
