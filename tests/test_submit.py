#!/usr/bin/env python3
"""Tests for scripts/submit.py — content-diff guard and skip logic."""

import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import type_registry  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "submit.py")


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _clean_env(**extra):
    """The developer's registry seams and the headless/CI markers stay out of subprocess runs
    (an RFE_CREATOR_EXTRA_TYPES root would change the --type choices; a runner's ``CI`` would
    gate it)."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("RFE_CREATOR_") and k not in type_registry.HEADLESS_MARKER_VARS
    }
    env.update(extra)
    return env


FAKE_CREDS = {
    "JIRA_SERVER": "https://fake.atlassian.net",
    "JIRA_USER": "fake@example.com",
    "JIRA_TOKEN": "fake-token",
}
NO_CREDS = {"JIRA_SERVER": "", "JIRA_USER": "", "JIRA_TOKEN": ""}


def _run_submit(artifacts_dir, extra_flags=None):
    """Run submit.py --dry-run and return stdout."""
    env = _clean_env(**FAKE_CREDS)
    cmd = ["python3", SCRIPT, "--dry-run", "--artifacts-dir", artifacts_dir]
    if extra_flags:
        cmd.extend(extra_flags)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
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
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md", TASK_FM.format(rfe_id="RHAIRFE-1234"))
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", body)
        # Make task body match original (strip_metadata removes frontmatter)
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            f"priority: Major\nstatus: Ready\n---\n{body}",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REVIEW_FM.format(rfe_id="RHAIRFE-1234", auto_revised="false"),
        )

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Label only" in stdout
        assert "rfe-creator-autofix-rubric-pass" in stdout

    def test_existing_rfe_with_changes_submitted(self, art_dir):
        """Existing RFE with different content → update."""
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", "## Problem\n\nOriginal content.\n")
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            "priority: Major\nstatus: Ready\n---\n"
            "## Problem\n\nRevised content with improvements.\n",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REVIEW_FM.format(rfe_id="RHAIRFE-1234", auto_revised="true"),
        )

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Would update" in stdout
        assert "no changes" not in stdout

    def test_existing_rfe_no_original_file_submitted(self, art_dir):
        """Existing RFE with no original file → submit (no guard)."""
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md", TASK_FM.format(rfe_id="RHAIRFE-1234"))
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REVIEW_FM.format(rfe_id="RHAIRFE-1234", auto_revised="false"),
        )
        # No file in rfe-originals/

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Would update" in stdout

    def test_new_rfe_always_created(self, art_dir):
        """New RFE (RFE-NNN) → always create, no content-diff check."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false"),
        )

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Would create" in stdout


class TestSkipLogic:
    def test_rejected_rfe_skipped(self, art_dir):
        """RFE with recommendation=reject → SKIP rejected."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false").replace(
                "recommendation: submit", "recommendation: reject"
            ),
        )

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "SKIP" in stdout
        assert "rejected" in stdout

    def test_archived_rfe_excluded(self, art_dir):
        """Archived RFE → not in plan at all."""
        _write(
            f"{art_dir}/rfe-tasks/RFE-001.md",
            TASK_FM.format(rfe_id="RFE-001").replace("status: Ready", "status: Archived"),
        )

        stdout, stderr, rc = _run_submit(art_dir)
        # Should error because no submittable RFEs found
        assert rc == 1
        assert "No submittable" in stderr or "No RFE task" in stderr

    def test_children_of_local_parent_submitted(self, art_dir):
        """Children of local RFE-NNN parent → included in Phase 2 as creates."""
        # Archived local parent
        _write(
            f"{art_dir}/rfe-tasks/RFE-001.md",
            TASK_FM.format(rfe_id="RFE-001").replace("status: Ready", "status: Archived"),
        )
        # Children with local parent_key
        for i, child_id in enumerate(["RFE-002", "RFE-003"], start=2):
            _write(
                f"{art_dir}/rfe-tasks/{child_id}.md",
                TASK_FM.format(rfe_id=child_id).replace(
                    "status: Ready", "status: Ready\nparent_key: RFE-001"
                ),
            )
            _write(
                f"{art_dir}/rfe-reviews/{child_id}-review.md",
                REVIEW_FM.format(rfe_id=child_id, auto_revised="false"),
            )

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Would create" in stdout
        assert "RFE-002" in stdout
        assert "RFE-003" in stdout

    def test_children_of_jira_parent_excluded(self, art_dir):
        """Children of RHAIRFE parent → excluded from Phase 2 (Phase 1 handles)."""
        # Child with Jira parent_key
        _write(
            f"{art_dir}/rfe-tasks/RFE-002.md",
            TASK_FM.format(rfe_id="RFE-002").replace(
                "status: Ready", "status: Ready\nparent_key: RHAIRFE-1234"
            ),
        )
        _write(
            f"{art_dir}/rfe-reviews/RFE-002-review.md",
            REVIEW_FM.format(rfe_id="RFE-002", auto_revised="false"),
        )

        stdout, stderr, rc = _run_submit(art_dir)
        assert rc == 1
        assert "No submittable" in stderr or "No RFE task" in stderr

    def test_grandchildren_of_jira_parent_excluded(self, art_dir):
        """Grandchildren via local intermediary → excluded from Phase 2.

        Tests Phase 2 filtering only: the RHAIRFE parent task file is
        omitted so Phase 1 has no split parents to run.  The ancestor
        chain in frontmatter (RFE-011 → RFE-010 → RHAIRFE-1234) is
        enough for _has_jira_ancestor to exclude the grandchildren.
        A standalone RFE-020 is included so Phase 2 has work to do.
        """
        # Archived local intermediary whose parent_key traces to Jira
        _write(
            f"{art_dir}/rfe-tasks/RFE-010.md",
            TASK_FM.format(rfe_id="RFE-010").replace(
                "status: Ready", "status: Archived\nparent_key: RHAIRFE-1234"
            ),
        )
        # Grandchildren — parent_key points to local intermediary
        for child_id in ["RFE-011", "RFE-012"]:
            _write(
                f"{art_dir}/rfe-tasks/{child_id}.md",
                TASK_FM.format(rfe_id=child_id).replace(
                    "status: Ready", "status: Ready\nparent_key: RFE-010"
                ),
            )
            _write(
                f"{art_dir}/rfe-reviews/{child_id}-review.md",
                REVIEW_FM.format(rfe_id=child_id, auto_revised="false"),
            )
        # Standalone RFE so Phase 2 runs (rc == 0)
        _write(f"{art_dir}/rfe-tasks/RFE-020.md", TASK_FM.format(rfe_id="RFE-020"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-020-review.md",
            REVIEW_FM.format(rfe_id="RFE-020", auto_revised="false"),
        )

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        # Standalone RFE submitted normally
        assert "RFE-020" in stdout and "Would create" in stdout
        # Grandchildren excluded from Phase 2 plan
        plan_section = stdout.split("Submission plan:")[1]
        assert "RFE-011" not in plan_section
        assert "RFE-012" not in plan_section


class TestAutoRevisedLabel:
    def test_auto_revised_label_applied(self, art_dir):
        """auto_revised=true → rfe-creator-auto-revised label."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_FM.format(rfe_id="RFE-001", auto_revised="true"),
        )

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "rfe-creator-auto-revised" in stdout

    def test_saved_review_state_is_reapplied_before_submit_reads(self, art_dir):
        """AISDLC-33: a review agent that rewrote its review after the pipeline's last
        reconcile lost auto_revised; the kept state file is re-applied at submit start-up,
        so the label is derived from the restored flag, and the file is removed."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false"),
        )
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review-state.json",
            '{"before_score": 6, "before_scores": {"what": 2, "why": 0, "open_to_how": 2,'
            ' "not_a_task": 2, "right_sized": 0}, "auto_revised": true, "revision_history": ""}',
        )

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Re-applied saved review state for 1 item(s): RFE-001" in stdout
        assert "rfe-creator-auto-revised" in stdout
        assert not os.path.exists(f"{art_dir}/rfe-reviews/RFE-001-review-state.json")

    def test_guard_skips_an_unreadable_review_and_names_it_only(self, art_dir):
        """A review whose frontmatter does not parse is skipped by check_revised.py (per-id
        isolation, exit 0); the guard warns with the id only — the child's per-id line
        carries the exception class, and its message quotes the offending source line,
        email included — and the submit goes on."""
        body = "## Problem\n\nSame content.\n"
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", body)
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            f"priority: Major\nstatus: Ready\n---\n{body}",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            "---\nrfe_id: RHAIRFE-1234\nauto_revised: true\n"
            "score: [unclosed owner: jane.doe@example.com\n---\nbody\n",
        )

        stdout, stderr, rc = _run_submit(art_dir)
        guard = [ln for ln in stderr.splitlines() if "auto_revised content guard" in ln]
        assert guard == [
            "Warning: auto_revised content guard skipped 1 item(s) it could not read or"
            " update: RHAIRFE-1234"
        ]
        assert "jane.doe@example.com" not in stderr
        assert "Traceback" not in stderr

    def test_guard_lowers_the_ids_after_an_unreadable_review(self, art_dir):
        """Mixed batch: the unreadable review sorts first; the id after it is still lowered
        and does not get the label (the skip must not abort the pass)."""
        body = "## Problem\n\nSame content.\n"
        for rid in ("RHAIRFE-1000", "RHAIRFE-1234"):
            _write(f"{art_dir}/rfe-originals/{rid}.md", body)
            _write(
                f"{art_dir}/rfe-tasks/{rid}.md",
                f"---\nrfe_id: {rid}\ntitle: Test RFE\npriority: Major\nstatus: Ready\n---\n{body}",
            )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1000-review.md",
            "---\nrfe_id: RHAIRFE-1000\nauto_revised: true\n"
            "score: [unclosed owner: jane.doe@example.com\n---\nbody\n",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REVIEW_FM.format(rfe_id="RHAIRFE-1234", auto_revised="true"),
        )

        stdout, stderr, rc = _run_submit(art_dir)
        assert (
            "Lowered auto_revised on 1 item(s) whose text equals the original: RHAIRFE-1234"
            in stdout
        )
        assert (
            "Warning: auto_revised content guard skipped 1 item(s) it could not read or"
            " update: RHAIRFE-1000"
        ) in stderr
        assert "rfe-creator-auto-revised" not in stdout
        assert "jane.doe@example.com" not in stderr

    def test_saved_state_is_reapplied_before_the_content_guard(self, art_dir):
        """The order of the two start-up passes is load-bearing: the state reconcile may
        re-raise auto_revised from a kept state file, and the content guard must run after
        it so an unchanged task still ends up unflagged. Swapping the calls would re-raise
        the flag after the guard and apply the label."""
        body = "## Problem\n\nSame content.\n"
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", body)
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            f"priority: Major\nstatus: Ready\n---\n{body}",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REVIEW_FM.format(rfe_id="RHAIRFE-1234", auto_revised="false"),
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review-state.json",
            '{"before_score": 6, "before_scores": {"what": 2, "why": 0, "open_to_how": 2,'
            ' "not_a_task": 2, "right_sized": 0}, "auto_revised": true, "revision_history": ""}',
        )

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Re-applied saved review state for 1 item(s): RHAIRFE-1234" in stdout
        assert (
            "Lowered auto_revised on 1 item(s) whose text equals the original: RHAIRFE-1234"
            in stdout
        )
        assert "rfe-creator-auto-revised" not in stdout
        review = open(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md").read()
        assert "auto_revised: false" in review

    def test_set_flag_on_unchanged_text_is_lowered_before_labels(self, art_dir):
        """2026-09-21 stage dry run (RHAIRFE-3444): the revise agent's last write restored
        auto_revised after FIXUP had lowered it on an unchanged task, and the dry-run submit
        would have applied the auto-revised label. The content guard at start-up lowers a set
        flag whose task body equals its original; the label follows the corrected flag."""
        body = "## Problem\n\nSame content.\n"
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", body)
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            f"priority: Major\nstatus: Ready\n---\n{body}",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REVIEW_FM.format(rfe_id="RHAIRFE-1234", auto_revised="true"),
        )

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert (
            "Lowered auto_revised on 1 item(s) whose text equals the original: RHAIRFE-1234"
            in stdout
        )
        assert "rfe-creator-auto-revised" not in stdout
        assert "Label only" in stdout

    def test_set_flag_on_changed_text_keeps_the_label(self, art_dir):
        """The guard is lower-only: a genuine revision keeps its flag and label."""
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", "## Problem\n\nOriginal content.\n")
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            "priority: Major\nstatus: Ready\n---\n"
            "## Problem\n\nRevised content with improvements.\n",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REVIEW_FM.format(rfe_id="RHAIRFE-1234", auto_revised="true"),
        )

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Lowered auto_revised" not in stdout
        assert "rfe-creator-auto-revised" in stdout

    def test_no_label_when_not_revised(self, art_dir):
        """auto_revised=false → no auto-revised label."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false"),
        )

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "rfe-creator-auto-revised" not in stdout


class TestRemoveLabels:
    """Tests for stale label removal on rejected RFEs."""

    def _task_with_labels(self, rfe_id, labels):
        """Task frontmatter with original_labels set."""
        labels_yaml = "\n".join(f"- {label}" for label in labels) if labels else "[]"
        return (
            f"---\nrfe_id: {rfe_id}\ntitle: Test RFE\n"
            f"priority: Major\nstatus: Ready\n"
            f"original_labels:\n{labels_yaml}\n---\n\n"
            f"## Problem\n\nContent here.\n"
        )

    def test_rejected_with_rubric_pass_removes_label(self, art_dir):
        """Rejected RFE that had rubric-pass → Remove labels action."""
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            self._task_with_labels("RHAIRFE-1234", ["rfe-creator-autofix-rubric-pass"]),
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"),
        )

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Remove labels" in stdout
        assert "rfe-creator-autofix-rubric-pass" in stdout
        assert "Would remove labels" in stdout

    def test_rejected_without_rubric_pass_skips(self, art_dir):
        """Rejected RFE without rubric-pass → plain SKIP."""
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md", TASK_FM.format(rfe_id="RHAIRFE-1234"))
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"),
        )

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "SKIP" in stdout
        assert "rejected" in stdout
        assert "Remove labels" not in stdout

    def test_rejected_new_rfe_skips(self, art_dir):
        """Rejected new RFE (RFE-NNN) → SKIP, no label removal."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md", REJECT_REVIEW_FM.format(rfe_id="RFE-001")
        )

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "SKIP" in stdout
        assert "Remove labels" not in stdout

    def test_autorevise_reject_removes_rubric_pass(self, art_dir):
        """autorevise_reject with rubric-pass → Remove labels."""
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            self._task_with_labels("RHAIRFE-1234", ["rfe-creator-autofix-rubric-pass"]),
        )
        review = REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234").replace(
            "recommendation: reject", "recommendation: autorevise_reject"
        )
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md", review)

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Remove labels" in stdout
        assert "Would remove labels" in stdout


class TestFeasibilityLabelHelper:
    """Direct unit tests for feasibility_label_changes()."""

    @pytest.fixture(autouse=True)
    def _import_helper(self):
        from submit import (
            FEASIBILITY_LABELS,
            feasibility_label_changes,
        )

        self.LABELS = FEASIBILITY_LABELS
        self.fn = feasibility_label_changes

    @pytest.mark.parametrize(
        "verdict,expected_label",
        [
            ("feasible", "rfe-creator-feasibility-pass"),
            ("infeasible", "rfe-creator-feasibility-fail"),
            ("indeterminate", "rfe-creator-feasibility-unknown"),
        ],
    )
    def test_each_verdict_no_existing_labels(self, verdict, expected_label):
        add, remove = self.fn(verdict, is_reject=False, original_labels=None)
        assert add == expected_label
        assert remove == []

    def test_each_verdict_with_matching_label_already_present(self):
        for verdict, label in self.LABELS.items():
            add, remove = self.fn(verdict, is_reject=False, original_labels=[label])
            assert add == label, f"{verdict}: matching label is added (Jira no-ops)"
            assert remove == []

    def test_flip_removes_only_present_stale(self):
        # original has fail; new verdict feasible
        add, remove = self.fn(
            "feasible", is_reject=False, original_labels=["rfe-creator-feasibility-fail"]
        )
        assert add == "rfe-creator-feasibility-pass"
        assert remove == ["rfe-creator-feasibility-fail"]

    def test_reject_with_no_feasibility_labels(self):
        add, remove = self.fn(None, is_reject=True, original_labels=["unrelated-label"])
        assert add is None
        assert remove == []

    def test_reject_with_one_feasibility_label(self):
        add, remove = self.fn(
            None, is_reject=True, original_labels=["rfe-creator-feasibility-pass", "other"]
        )
        assert add is None
        assert remove == ["rfe-creator-feasibility-pass"]

    def test_missing_verdict(self):
        for verdict in (None, "", "yes", "TBD"):
            add, remove = self.fn(verdict, is_reject=False, original_labels=None)
            assert add is None
            assert remove == []

    def test_original_labels_none_treated_as_empty(self):
        add, remove = self.fn("feasible", is_reject=False, original_labels=None)
        assert add == "rfe-creator-feasibility-pass"
        assert remove == []


FEAS_TASK_FM = """\
---
rfe_id: {rfe_id}
title: Test RFE
priority: Major
status: Ready
{extra}---

## Problem Statement

Users need better logging for compliance audits.

## Acceptance Criteria

- Audit logs capture all inference requests
"""


def _feas_review(rfe_id, verdict, recommendation="submit"):
    return f"""\
---
rfe_id: {rfe_id}
score: 9
pass: true
recommendation: {recommendation}
feasibility: {verdict}
auto_revised: false
needs_attention: false
scores:
  what: 2
  why: 2
  open_to_how: 2
  not_a_task: 2
  right_sized: 1
---

## Assessor Feedback
ok.
"""


def _error_review(rfe_id, error, verdict="feasible", passed=False):
    """A review carrying an ``error``: the registry error stub shape when ``passed`` is False
    (``*_failed`` / ``*_stalled`` inherit ``feasibility: feasible`` without any feasibility
    review having run), or a real passing review a stall marker landed on."""
    return f"""\
---
rfe_id: {rfe_id}
score: {9 if passed else 0}
pass: {"true" if passed else "false"}
recommendation: {"submit" if passed else "revise"}
feasibility: {verdict}
auto_revised: false
needs_attention: true
needs_attention_reason: "Agent failed: {error}"
error: "{error}"
scores:
  what: 2
  why: 2
  open_to_how: 2
  not_a_task: 2
  right_sized: 1
---

## Assessor Feedback
n/a.
"""


class TestFeasibilityLabelOnSubmit:
    """End-to-end (dry-run) tests for feasibility label wiring."""

    def _task(self, rfe_id, original_labels=None):
        if original_labels is None:
            extra = ""
        else:
            labels_yaml = "\n".join(f"- {label}" for label in original_labels)
            extra = f"original_labels:\n{labels_yaml}\n"
        return FEAS_TASK_FM.format(rfe_id=rfe_id, extra=extra)

    @pytest.mark.parametrize(
        "verdict,label",
        [
            ("feasible", "rfe-creator-feasibility-pass"),
            ("infeasible", "rfe-creator-feasibility-fail"),
            ("indeterminate", "rfe-creator-feasibility-unknown"),
        ],
    )
    def test_feasibility_label_on_create(self, art_dir, verdict, label):
        """Each verdict applies the matching label on a new RFE."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", self._task("RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md", _feas_review("RFE-001", verdict))
        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert label in stdout

    def test_feasibility_label_flip_on_update(self, art_dir):
        """Existing RHAIRFE with stale label → flip adds new, removes stale."""
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            self._task("RHAIRFE-1234", original_labels=["rfe-creator-feasibility-fail"]),
        )
        # Original snapshot identical to current body for "no content change"
        # path exercising remove + add via Label only.
        _write(
            f"{art_dir}/rfe-originals/RHAIRFE-1234.md",
            self._task("RHAIRFE-1234", original_labels=["rfe-creator-feasibility-fail"]),
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            _feas_review("RHAIRFE-1234", "feasible"),
        )
        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "rfe-creator-feasibility-pass" in stdout
        assert "rfe-creator-feasibility-fail" in stdout
        assert "Would remove labels" in stdout

    def test_reject_strips_present_feasibility_label(self, art_dir):
        """Rejected RFE with stale feasibility label → Remove labels."""
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            self._task("RHAIRFE-1234", original_labels=["rfe-creator-feasibility-pass"]),
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            _feas_review("RHAIRFE-1234", "feasible", recommendation="reject"),
        )
        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Remove labels" in stdout
        assert "rfe-creator-feasibility-pass" in stdout

    def test_reject_without_feasibility_label_stays_skip(self, art_dir):
        """Rejected RFE with no feasibility labels → SKIP, no remove ops.

        Locks the conditional-removal behavior so a future "blind self-healing"
        refactor can't silently shift this case to Remove labels.
        """
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md", self._task("RHAIRFE-1234"))
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            _feas_review("RHAIRFE-1234", "feasible", recommendation="reject"),
        )
        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "SKIP" in stdout
        assert "Remove labels" not in stdout
        assert "rfe-creator-feasibility" not in stdout

    def test_invalid_review_still_proceeds(self, art_dir):
        """A PRESENT but schema-invalid review keeps the old warn-and-proceed
        behavior: check_resume reads reviews with a laxer bar, so leaving the
        item unprocessed would re-select it forever without ever re-reviewing
        it — the skip is deliberately for ABSENT reviews only."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", self._task("RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            "---\nrfe_id: RFE-001\nscore: not-a-number\n---\nBroken.",
        )
        stdout, stderr, rc = _run_submit(art_dir)
        assert rc == 0
        assert "cannot read review" in stderr
        assert "Would create" in stdout
        assert "no readable review" not in stdout

    def test_no_review_skips_the_item_entirely(self, art_dir):
        """Missing review file → the item is not submitted at all. It never
        finished the pipeline, so submitting would push content no review
        approved, and disposing of it would freeze it out of future runs
        (RHAIRFE-3201, RHAIFIRST-582)."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", self._task("RFE-001"))
        # No review file written
        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        assert "no readable review" in stdout
        assert "Would create" not in stdout
        assert "rfe-creator-feasibility" not in stdout

    @pytest.mark.parametrize("error", ["assess_stalled", "review_failed", "revise_stalled"])
    def test_error_bearing_review_has_no_feasibility_verdict(self, art_dir, error):
        """A review carrying an ``error`` is an error stub (or a real review a stall marker
        landed on): its ``feasibility: feasible`` is the stub shape, not a verdict, so no
        feasibility label is added. The needs-attention label and the rubric logic are
        unchanged, and the same review without the error still earns the label."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", self._task("RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md", _error_review("RFE-001", error))
        stdout, stderr, rc = _run_submit(art_dir)
        assert rc == 0, stderr
        assert "Would create" in stdout
        assert "rfe-creator-feasibility" not in stdout
        assert "rfe-creator-needs-attention" in stdout
        assert "rfe-rubric-pass" not in stdout

        review = _error_review("RFE-001", error).replace(f'error: "{error}"\n', "")
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md", review)
        stdout, stderr, rc = _run_submit(art_dir)
        assert rc == 0, stderr
        assert "rfe-creator-feasibility-pass" in stdout  # the verdict counts without the error

    def test_error_bearing_review_leaves_existing_feasibility_labels_alone(self, art_dir):
        """No verdict means no flip either: an existing item's stale feasibility label is
        neither replaced nor removed (the Label-only path, identical original body)."""
        task = self._task("RHAIRFE-1234", original_labels=["rfe-creator-feasibility-fail"])
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md", task)
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", task)
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            _error_review("RHAIRFE-1234", "assess_stalled"),
        )
        stdout, stderr, rc = _run_submit(art_dir)
        assert rc == 0, stderr
        assert "RHAIRFE-1234: Would add labels: rfe-creator-needs-attention\n" in stdout
        assert "Would remove labels" not in stdout
        assert "rfe-creator-feasibility" not in stdout


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
            _write(f"{art_dir}/rfe-tasks/RFE-{i:03d}.md", self.CHILD_TASK_TPL.format(num=i))

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
        _write(f"{art_dir}/rfe-tasks/RFE-099.md", TASK_FM.format(rfe_id="RFE-099"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-099-review.md",
            REVIEW_FM.format(rfe_id="RFE-099", auto_revised="false"),
        )

        stdout, stderr, rc = _run_submit(art_dir)
        assert rc == 0
        assert "Split refused" in stdout
        assert "Would create" in stdout  # RFE-099 still processed


class TestSnapshotUpdate:
    """Tests for snapshot update after submission."""

    def test_dry_run_does_not_update_snapshot(self, art_dir):
        """Dry-run does not update snapshot."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false"),
        )

        stdout, _, rc = _run_submit(art_dir)
        assert rc == 0
        # Dry run should NOT create any snapshot files
        snap_dir = os.path.join(art_dir, "auto-fix-runs")
        assert not os.path.exists(snap_dir)


class TestGenerateReportFlag:
    """Tests for --generate-report / --report-timestamp validation."""

    def test_generate_report_without_timestamp_fails(self):
        """--generate-report without --report-timestamp → error exit."""
        env = _clean_env(**NO_CREDS)
        result = subprocess.run(
            [sys.executable, SCRIPT, "--dry-run", "--generate-report"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "--report-timestamp is required" in result.stderr

    def test_generate_report_with_timestamp_accepted(self, art_dir):
        """--generate-report with --report-timestamp → no validation error."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false"),
        )

        env = _clean_env(**FAKE_CREDS)
        result = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--dry-run",
                "--generate-report",
                "--report-timestamp",
                "20260404-170041",
                "--artifacts-dir",
                art_dir,
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "--report-timestamp is required" not in result.stderr


class TestApprovedTransition:
    def test_passing_review_prints_would_transition(self, art_dir):
        """--auto-approve + passing review → prints would-transition."""
        body = "Original.\n"
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", body)
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            "priority: Major\nstatus: Ready\n---\nRevised.",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REVIEW_FM.format(rfe_id="RHAIRFE-1234", auto_revised="true"),
        )

        stdout, stderr, rc = _run_submit(art_dir, ["--auto-approve"])
        assert rc == 0, stderr
        assert "Would transition to Approved" in stdout

    def test_failing_review_no_transition(self, art_dir):
        """--auto-approve + failing review → no transition message."""
        body = "Original.\n"
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", body)
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            "priority: Major\nstatus: Ready\n---\nRevised.",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"),
        )

        stdout, stderr, rc = _run_submit(art_dir, ["--auto-approve"])
        assert rc == 0, stderr
        assert "Would transition to Approved" not in stdout

    def test_new_rfe_prints_would_transition(self, art_dir):
        """--auto-approve + new RFE with passing review → would-transition."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false"),
        )

        stdout, stderr, rc = _run_submit(art_dir, ["--auto-approve"])
        assert rc == 0, stderr
        assert "Would transition to Approved" in stdout

    def test_indeterminate_feasibility_no_transition(self, art_dir):
        """Passing review but feasibility=indeterminate → no auto-approve.

        An inconclusive feasibility read is not a basis for transitioning a
        ticket to Approved — the gate requires an explicit `feasible`.
        """
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false").replace(
                "feasibility: feasible", "feasibility: indeterminate"
            ),
        )

        stdout, stderr, rc = _run_submit(art_dir, ["--auto-approve"])
        assert rc == 0, stderr
        assert "Would transition to Approved" not in stdout

    def test_needs_attention_still_transitions(self, art_dir):
        """needs_attention does not block auto-approve — it is advisory.

        The flag drives the needs-attention label; the transition gate is
        pass + feasible only.
        """
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false").replace(
                "needs_attention: false",
                'needs_attention: true\nneeds_attention_reason: "Needs a human look."',
            ),
        )

        stdout, stderr, rc = _run_submit(art_dir, ["--auto-approve"])
        assert rc == 0, stderr
        assert "Would transition to Approved" in stdout

    def test_error_bearing_review_no_transition(self, art_dir):
        """A passing, feasible review that carries an ``error`` (a stall marker landed on a
        real review, or a stub whose fields were edited) has no verdict: no auto-approve. The
        same review without the error transitions, so the gate is the error alone."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        review = _error_review("RFE-001", "review_stalled", passed=True)
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md", review)
        stdout, stderr, rc = _run_submit(art_dir, ["--auto-approve"])
        assert rc == 0, stderr
        assert "Would create" in stdout
        assert "Would transition to Approved" not in stdout
        assert "rfe-creator-feasibility" not in stdout

        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            review.replace('error: "review_stalled"\n', ""),
        )
        stdout, stderr, rc = _run_submit(art_dir, ["--auto-approve"])
        assert rc == 0, stderr
        assert "Would transition to Approved" in stdout
        assert "rfe-creator-feasibility-pass" in stdout

    def test_no_flag_no_transition(self, art_dir):
        """Without --auto-approve → no transition even if review passes."""
        body = "Original.\n"
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", body)
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            "priority: Major\nstatus: Ready\n---\nRevised.",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REVIEW_FM.format(rfe_id="RHAIRFE-1234", auto_revised="true"),
        )

        stdout, stderr, rc = _run_submit(art_dir)
        assert rc == 0, stderr
        assert "Would transition to Approved" not in stdout


class TestSplitFailureIsRecorded:
    """A split that crashes must not take the run report down with it.

    split_submit writes to Jira across three phases, so by the time an unclassified failure
    surfaces, earlier parents in the same loop may already be fully split in Jira. Aborting before
    the report was regenerated left those successes recorded nowhere — observed 2026-07-21/22,
    where 11 of 18 parents were genuinely split and the job logged "No changes to commit".
    """

    PARENT_TASK = (
        "---\nrfe_id: RHAIRFE-1000\ntitle: Parent RFE\n"
        "priority: Major\nstatus: Archived\n---\n\nParent content.\n"
    )
    CHILD_TASK = (
        "---\nrfe_id: RFE-001\ntitle: Child RFE\n"
        "priority: Major\nstatus: Ready\nparent_key: RHAIRFE-1000\n---\n\nChild content.\n"
    )
    REVIEW_TPL = (
        "---\nrfe_id: {rid}\nscore: 9\npass: true\n"
        "recommendation: submit\nfeasibility: feasible\n"
        "auto_revised: false\nneeds_attention: false\n"
        "scores:\n  what: 2\n  why: 2\n  open_to_how: 2\n"
        "  not_a_task: 2\n  right_sized: 1\n---\n\nLooks good.\n"
    )

    def _split_only_batch(self, art_dir):
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", self.PARENT_TASK)
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1000-review.md",
            self.REVIEW_TPL.format(rid="RHAIRFE-1000"),
        )
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", self.CHILD_TASK)
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md", self.REVIEW_TPL.format(rid="RFE-001"))

    def _report_paths(self, art_dir, ts="20260818-120000"):
        return (
            f"{art_dir}/auto-fix-runs/{ts}.yaml",
            f"{art_dir}/auto-fix-runs/{ts}-report.html",
        )

    def test_split_only_batch_still_writes_a_report(self, art_dir):
        """The early return used to skip report generation entirely on this shape.

        Uses the leaf-cap refusal to reach it: exit 2 is decided before split_submit makes any
        Jira call, so this exercises the split-only path without a live instance. Afterwards
        nothing is regular-submittable (the children all have a Jira ancestor), which is exactly
        the branch that used to `return` before the report block.
        """
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", self.PARENT_TASK)
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1000-review.md",
            self.REVIEW_TPL.format(rid="RHAIRFE-1000"),
        )
        for i in range(1, 8):  # 7 leaves > MAX_LEAF_CHILDREN
            _write(
                f"{art_dir}/rfe-tasks/RFE-{i:03d}.md",
                "---\nrfe_id: RFE-%03d\ntitle: Child %d\npriority: Major\n"
                "status: Ready\nparent_key: RHAIRFE-1000\n---\n\nChild.\n" % (i, i),
            )
        yaml_path, _ = self._report_paths(art_dir)

        stdout, stderr, rc = _run_submit(
            art_dir, ["--generate-report", "--report-timestamp", "20260818-120000"]
        )

        assert rc == 0, stdout + stderr
        assert "Split refused" in stdout
        assert os.path.exists(yaml_path), (
            "a batch whose every input was a split produced no run report"
        )

    def test_corrupt_review_file_does_not_take_the_run_down(self, art_dir):
        """Recording is best-effort on the local half too.

        If the review file cannot be updated (corrupt frontmatter here; read-only or
        schema-invalid in the wild), the refusal branch must warn and carry on to the report,
        not replace the diagnosis with a traceback.
        """
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", self.PARENT_TASK)
        # A review file whose frontmatter cannot be parsed, let alone updated.
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1000-review.md", "---\n: not yaml [\n---\n")
        for i in range(1, 8):  # 7 leaves > MAX_LEAF_CHILDREN -> refusal branch
            _write(
                f"{art_dir}/rfe-tasks/RFE-{i:03d}.md",
                "---\nrfe_id: RFE-%03d\ntitle: Child %d\npriority: Major\n"
                "status: Ready\nparent_key: RHAIRFE-1000\n---\n\nChild.\n" % (i, i),
            )

        stdout, stderr, rc = _run_submit(
            art_dir, ["--generate-report", "--report-timestamp", "20260818-120000"]
        )

        assert rc == 0, stdout + stderr
        # The handled path, not a crash. (stderr may still QUOTE a traceback: submit.py wraps
        # the HTML generator's own, pre-existing crash on corrupt reviews in a warning.)
        assert "could not record the failure" in stderr
        assert os.path.exists(f"{art_dir}/auto-fix-runs/20260818-120000.yaml")

    def test_generic_failure_records_and_still_reports(self, art_dir, monkeypatch, tmp_path):
        """Exit code 1 from split_submit: record it, write the report, then exit 1."""
        self._split_only_batch(art_dir)
        yaml_path, _ = self._report_paths(art_dir)

        # Stand in for split_submit.py with something that fails the way a Jira 404 does.
        stub = tmp_path / "split_submit.py"
        stub.write_text("import sys\nsys.stderr.write('boom\\n')\nsys.exit(1)\n")
        scripts_copy = tmp_path / "scripts"
        scripts_copy.mkdir()
        for name in os.listdir(os.path.dirname(SCRIPT)):
            if name.endswith(".py") and name != "split_submit.py":
                src = os.path.join(os.path.dirname(SCRIPT), name)
                if os.path.isfile(src):
                    with open(src) as f:
                        (scripts_copy / name).write_text(f.read())
        (scripts_copy / "split_submit.py").write_text(stub.read_text())
        # type_registry.DEFAULT_ROOT is <scripts dir>/../types: the relocated copy needs the
        # type root beside it or every registry-adopted import (generate_run_report) dies.
        shutil.copytree(os.path.join(os.path.dirname(SCRIPT), "..", "types"), tmp_path / "types")

        env = _clean_env(**FAKE_CREDS)
        result = subprocess.run(
            [
                "python3",
                str(scripts_copy / "submit.py"),
                "--dry-run",
                "--artifacts-dir",
                art_dir,
                "--generate-report",
                "--report-timestamp",
                "20260818-120000",
            ],
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode == 1, result.stdout + result.stderr
        assert "Traceback" not in result.stderr, result.stderr

        import yaml

        with open(f"{art_dir}/rfe-reviews/RHAIRFE-1000-review.md") as f:
            content = f.read()
        fm = yaml.safe_load(content[3 : content.index("---", 3)])
        assert fm["error"] == "split_submit_failed: exit 1"
        assert fm["needs_attention"] is True
        assert "partially updated" in fm["needs_attention_reason"]

        assert os.path.exists(yaml_path), (
            "the failure took the run report with it — the successes before it are unrecorded"
        )


class TestStallEscalatedSplitParentIsSkipped:
    """Phase 1 does not split-submit a parent the wave stall guard gave up on.

    A split agent escalated as ``split_not_attempted: wave stalled ...`` (docs/wave-stall-guard.md)
    may have been slow rather than dead: it can still archive the parent and mint children after
    the marker was written and the parent left the split ids file, so no SPLIT_ASSESS /
    SPLIT_REVIEW wave ever saw those children. Selecting the parent by ``status: Archived`` plus
    children alone would split-submit never-reviewed children. Such a parent is skipped with one
    line, invoked nowhere, and marked processed nowhere (it is in no plan).
    """

    PARENT_TASK = (
        "---\nrfe_id: RHAIRFE-1000\ntitle: Parent RFE\n"
        "priority: Major\nstatus: Archived\n---\n\nParent content.\n"
    )
    CHILD_TASK = (
        "---\nrfe_id: RFE-001\ntitle: Child RFE\n"
        "priority: Major\nstatus: Ready\nparent_key: RHAIRFE-1000\n---\n\nChild content.\n"
    )
    STALLED = (
        "split_not_attempted: wave stalled in SPLIT: no agent reached a terminal state for"
        " 1800s (window 1800s); the subagent produced no output"
    )

    def _parent_review(self, error=None):
        fm = (
            "rfe_id: RHAIRFE-1000\nscore: 6\npass: false\nrecommendation: split\n"
            "feasibility: feasible\nauto_revised: false\nneeds_attention: false\n"
        )
        if error:
            fm += f'needs_attention: true\nneeds_attention_reason: "Agent failed: {error}"\n'
            fm += f'error: "{error}"\n'
        fm += "scores:\n  what: 2\n  why: 2\n  open_to_how: 1\n  not_a_task: 1\n  right_sized: 0\n"
        return f"---\n{fm}---\n\nToo big.\n"

    def _batch(self, art_dir, review, with_regular=True):
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", self.PARENT_TASK)
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1000-review.md", review)
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", self.CHILD_TASK)  # never reviewed
        if with_regular:
            _write(f"{art_dir}/rfe-tasks/RFE-099.md", TASK_FM.format(rfe_id="RFE-099"))
            _write(
                f"{art_dir}/rfe-reviews/RFE-099-review.md",
                REVIEW_FM.format(rfe_id="RFE-099", auto_revised="false"),
            )

    def _run(self, art_dir, tmp_path, extra_flags=None):
        """submit.py --dry-run with split_submit.py replaced by a stub that logs its argv (the
        RFE_SPLIT_SUBMIT_SCRIPT seam, honored under pytest only). Returns (stdout, stderr, rc,
        the stub's invocations)."""
        log = tmp_path / "split-stub.log"
        stub = tmp_path / "split_submit_stub.py"
        stub.write_text(
            "import os, sys\n"
            "with open(os.environ['SPLIT_STUB_LOG'], 'a') as f:\n"
            "    f.write(' '.join(sys.argv[1:]) + '\\n')\n"
            "sys.exit(0)\n"
        )
        env = _clean_env(**FAKE_CREDS, RFE_SPLIT_SUBMIT_SCRIPT=str(stub), SPLIT_STUB_LOG=str(log))
        cmd = ["python3", SCRIPT, "--dry-run", "--artifacts-dir", art_dir] + (extra_flags or [])
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        calls = log.read_text().splitlines() if log.exists() else []
        return result.stdout, result.stderr, result.returncode, calls

    def _plan_ids(self, stdout):
        lines = stdout.splitlines()
        start = next(i for i, ln in enumerate(lines) if ln.startswith("Submission plan:"))
        rows = []
        for ln in lines[start + 3 :]:
            if not ln.strip():
                break
            if not ln.startswith(" "):
                rows.append(ln.split()[0])
        return rows

    def test_split_not_attempted_parent_is_skipped(self, art_dir, tmp_path):
        self._batch(art_dir, self._parent_review(self.STALLED))
        stdout, stderr, rc, calls = self._run(art_dir, tmp_path)
        assert rc == 0, stderr
        assert calls == []  # split_submit never invoked
        assert (
            f"  RHAIRFE-1000: SKIP split-submit - review error {self.STALLED}; children were"
            " not reviewed, left for an operator\n"
        ) in stdout
        assert "Phase 1: Submitting" not in stdout
        # In no plan: the parent is neither disposed of nor marked processed (mark_processed_ids
        # is built from plan entries only), and the unreviewed child (a Jira ancestor) is not
        # a regular item either. The regular RFE is submitted as usual.
        assert self._plan_ids(stdout) == ["RFE-099"]
        assert "Would create RHAIRFE Feature Request: Test RFE" in stdout
        assert "RHAIRFE-1000: Skipping" not in stdout
        assert "RFE-001" not in stdout

    def test_stalled_stub_parent_is_skipped(self, art_dir, tmp_path):
        self._batch(art_dir, self._parent_review("split_stalled"))
        stdout, stderr, rc, calls = self._run(art_dir, tmp_path)
        assert rc == 0, stderr
        assert calls == []
        assert (
            "  RHAIRFE-1000: SKIP split-submit - review error split_stalled; children were not"
            " reviewed, left for an operator\n"
        ) in stdout
        assert self._plan_ids(stdout) == ["RFE-099"]

    def test_parent_without_error_is_split_submitted_as_before(self, art_dir, tmp_path):
        self._batch(art_dir, self._parent_review())
        stdout, stderr, rc, calls = self._run(art_dir, tmp_path)
        assert rc == 0, stderr
        assert calls == [f"RHAIRFE-1000 --artifacts-dir {art_dir} --dry-run"]
        assert "Phase 1: Submitting 1 split parent(s)" in stdout
        assert "SKIP split-submit" not in stdout
        assert self._plan_ids(stdout) == ["RFE-099"]

    def test_split_submit_failed_parent_is_split_submitted_as_before(self, art_dir, tmp_path):
        """A previous run's split_submit_failed: is a different class (quarantined in Jira,
        re-attempt is the operator's call after removing the label): not this skip."""
        self._batch(art_dir, self._parent_review("split_submit_failed: exit 4"))
        stdout, stderr, rc, calls = self._run(art_dir, tmp_path)
        assert rc == 0, stderr
        assert calls == [f"RHAIRFE-1000 --artifacts-dir {art_dir} --dry-run"]
        assert "SKIP split-submit" not in stdout

    @pytest.mark.parametrize(
        "error",
        [
            "split_not_attempted: Jira preflight failed",
            "split_not_attempted: split phase aborted before this parent",
        ],
    )
    def test_this_scripts_own_not_attempted_markers_are_still_attempted(
        self, art_dir, tmp_path, error
    ):
        """submit.py's own not-attempted markers mean "not attempted yet": the manual submit
        jobs re-run over the same artifacts after a preflight failure and must still try the
        split. Only the stall guard's ``wave stalled`` reason is the skip."""
        self._batch(art_dir, self._parent_review(error))
        stdout, stderr, rc, calls = self._run(art_dir, tmp_path)
        assert rc == 0, stderr
        assert calls == [f"RHAIRFE-1000 --artifacts-dir {art_dir} --dry-run"]
        assert "SKIP split-submit" not in stdout

    def test_unreadable_parent_review_is_treated_as_no_error(self, art_dir, tmp_path):
        self._batch(art_dir, "---\n: not yaml [\n---\n")
        stdout, stderr, rc, calls = self._run(art_dir, tmp_path)
        assert rc == 0, stderr
        assert calls == [f"RHAIRFE-1000 --artifacts-dir {art_dir} --dry-run"]
        assert "SKIP split-submit" not in stdout

    def test_skipped_split_only_batch_still_finishes_and_reports(self, art_dir, tmp_path):
        """A batch whose only input is a skipped parent is not "No submittable RFEs" (exit 1,
        no report): it finishes like any split-only batch and writes the run report, where the
        parent keeps counting as failed, not split."""
        self._batch(art_dir, self._parent_review(self.STALLED), with_regular=False)
        stdout, stderr, rc, calls = self._run(
            art_dir, tmp_path, ["--generate-report", "--report-timestamp", "20260818-120000"]
        )
        assert rc == 0, stdout + stderr
        assert calls == []
        assert "SKIP split-submit" in stdout
        assert "No submittable" not in stderr
        assert os.path.exists(f"{art_dir}/auto-fix-runs/20260818-120000.yaml")


class TestRecordSplitFailureResilience:
    """The quarantine label is the load-bearing write — it must land even
    when the advisory needs-attention comment fails (CodeRabbit on #169)."""

    def test_quarantine_label_survives_comment_failure(self, monkeypatch, tmp_path):
        from types import SimpleNamespace

        import submit as submit_mod

        added = []
        monkeypatch.setattr(
            submit_mod, "add_labels", lambda srv, u, t, key, labels: added.extend(labels)
        )

        def boom(*a, **k):
            raise RuntimeError("comment rejected")

        monkeypatch.setattr(submit_mod, "_post_needs_attention_comment", boom)

        args = SimpleNamespace(artifacts_dir=str(tmp_path), dry_run=False)
        cfg = submit_mod.TYPE_CONFIGS["rfe"]
        submit_mod._record_split_failure(
            "https://x",
            "u",
            "t",
            "RHAIRFE-1",
            "reason",
            "split_submit_failed: exit 4",
            args,
            cfg,
            {"original_labels": []},
            quarantine=True,
        )

        assert "rfe-creator-split-quarantine" in added
        assert "rfe-creator-needs-attention" in added


class TestTypeConfigsProjection:
    """TYPE_CONFIGS projects the type descriptors (types/<type>/type.yaml) at import.

    The rfe entry must equal, key for key and in key order, the literal table submit.py carried
    before it read the registry: every label, comment and Jira payload this script composes
    derives from these values, so the table is an artifact contract and this golden keeps a
    descriptor edit that reaches Jira a visible test change. (The initiative twin lives in
    tests/test_initiative_submit.py.)
    """

    RFE = {
        "project": "RHAIRFE",
        "issue_type": "Feature Request",
        "type_label": "RFE",
        "id_field": "rfe_id",
        "local_prefix": "RFE-",
        "jira_prefix": "RHAIRFE-",
        "tasks_dir": "rfe-tasks",
        "reviews_dir": "rfe-reviews",
        "originals_dir": "rfe-originals",
        "task_schema": "rfe-task",
        "review_schema": "rfe-review",
        "snapshot_prefix": "",
        "split_type_arg": None,
        "label_prefix": "rfe-creator",
        "rubric_pass_label": "rfe-creator-autofix-rubric-pass",
        "feasibility_labels": {
            "feasible": "rfe-creator-feasibility-pass",
            "infeasible": "rfe-creator-feasibility-fail",
            "indeterminate": "rfe-creator-feasibility-unknown",
        },
        "alignment_labels": None,
        "removed_context_preamble": (
            "*[RFE Creator]* The following technical implementation "
            "details were removed from the RFE description during review. "
            "This content is better suited for a RHAISTRAT and is "
            "preserved here for reference:"
        ),
        "comment_prefix": "[RFE Creator]",
        "has_index": True,
    }

    def test_rfe_projection_is_the_literal_table(self):
        import submit as submit_mod

        cfg = submit_mod.TYPE_CONFIGS["rfe"]
        assert cfg == self.RFE
        assert list(cfg) == list(self.RFE)
        assert list(cfg["feasibility_labels"]) == list(self.RFE["feasibility_labels"])
        assert {k: type(v) for k, v in cfg.items()} == {k: type(v) for k, v in self.RFE.items()}

    def test_every_registered_type_has_a_config_in_registry_order(self):
        import submit as submit_mod

        assert list(submit_mod.TYPE_CONFIGS) == submit_mod._TYPES.names()
        assert list(submit_mod.TYPE_CONFIGS) == ["rfe", "initiative"]
        for cfg in submit_mod.TYPE_CONFIGS.values():
            assert list(cfg) == list(self.RFE)

    def test_module_alias_is_the_rfe_feasibility_map(self):
        import submit as submit_mod

        assert submit_mod.FEASIBILITY_LABELS is submit_mod.TYPE_CONFIGS["rfe"]["feasibility_labels"]

    def test_projected_maps_do_not_alias_descriptor_data(self):
        """A caller editing a projected label map must not corrupt the registry."""
        import submit as submit_mod

        desc = submit_mod._TYPES.get("rfe")
        feas = submit_mod.TYPE_CONFIGS["rfe"]["feasibility_labels"]
        assert feas == desc.get("conventions.labels.feasibility")
        assert feas is not desc.get("conventions.labels.feasibility")

    def test_rfe_grandfathered_sentinels(self):
        """'' means "snapshot_fetch's default prefix" and None means "spawn split_submit.py
        without --type" — the rfe conventions the pre-registry table encoded, never the
        descriptor's own snapshot.prefix."""
        import submit as submit_mod

        desc = submit_mod._TYPES.get("rfe")
        assert desc.get("snapshot.prefix") == "issue-snapshot-"
        assert submit_mod.TYPE_CONFIGS["rfe"]["snapshot_prefix"] == ""
        assert submit_mod.TYPE_CONFIGS["rfe"]["split_type_arg"] is None

    def test_type_choices_are_the_registry_choices(self):
        """--help still offers exactly {rfe,initiative}; an unregistered type is refused."""
        result = subprocess.run(
            [sys.executable, SCRIPT, "--help"], capture_output=True, text=True, env=_clean_env()
        )
        assert result.returncode == 0, result.stderr
        assert "--type {rfe,initiative}" in result.stdout

        result = subprocess.run(
            [sys.executable, SCRIPT, "--type", "epic", "--dry-run"],
            capture_output=True,
            text=True,
            env=_clean_env(),
        )
        assert result.returncode == 2
        assert "invalid choice: 'epic'" in result.stderr


class TestApproveStateFromTheBinding:
    """identity.jira.state_map.approved is optional in the schema: a registered type without it
    still submits, and only --auto-approve is refused — as a usage error, before any scan or
    Jira call. Both shipped types declare it (tests/test_type_registry_pins.py pins the value the
    emulator workflow seeds)."""

    def _run_memo(self, root, tmp_path, *flags):
        return subprocess.run(
            [sys.executable, SCRIPT, "--type", "memo", "--dry-run", *flags]
            + ["--artifacts-dir", str(tmp_path / "artifacts")],
            capture_output=True,
            text=True,
            env=_clean_env(RFE_CREATOR_EXTRA_TYPES=root, **FAKE_CREDS),
        )

    def test_a_type_without_the_approved_state_submits(self, drop_in_root, tmp_path):
        root = drop_in_root.memo(drop=("identity.jira.state_map.approved",))
        result = self._run_memo(root, tmp_path)
        assert result.returncode == 1, result.stderr
        assert "No Memo task files found." in result.stderr  # startup passed, the scan ran

    def test_auto_approve_is_refused_for_a_type_without_the_approved_state(
        self, drop_in_root, tmp_path
    ):
        root = drop_in_root.memo(drop=("identity.jira.state_map.approved",))
        result = self._run_memo(root, tmp_path, "--auto-approve")
        assert result.returncode == 2, result.stderr
        assert (
            "error: --auto-approve: type 'memo' declares no identity.jira.state_map.approved"
        ) in result.stderr
        assert "No Memo task files found" not in result.stderr
        assert result.stdout == ""

    def test_auto_approve_uses_the_type_s_own_state(self, drop_in_root, tmp_path):
        root = drop_in_root.memo()
        result = self._run_memo(root, tmp_path, "--auto-approve")
        assert result.returncode == 1, result.stderr
        assert "No Memo task files found." in result.stderr


class TestReportCommandArgv:
    """_generate_reports spawns generate_run_report.py and generate_review_pdf.py.

    Both scripts default to rfe, so the rfe invocation carries no --type (grandfathered
    argv, byte-identical to the pre-registry command) and every other type is named.
    """

    def _capture(self, monkeypatch, type_name, tmp_path):
        import submit as submit_mod

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(submit_mod.subprocess, "run", fake_run)
        # The resolved type is passed explicitly (PR-3c: --type defaults to None and the
        # ladder decides), so args carries no type at all here.
        args = SimpleNamespace(artifacts_dir=str(tmp_path), report_timestamp="20260404-170041")
        submit_mod._generate_reports(args, type_name)
        return calls

    def test_rfe_report_commands_carry_no_type_flag(self, monkeypatch, tmp_path):
        yaml_cmd, html_cmd = self._capture(monkeypatch, "rfe", tmp_path)
        assert yaml_cmd[1].endswith("generate_run_report.py")
        assert yaml_cmd[2:] == [
            "--start-time",
            "20260404-170041",
            "--artifacts-dir",
            str(tmp_path),
            "--report-stage",
            "final",
        ]
        assert html_cmd[1].endswith("generate_review_pdf.py")
        assert html_cmd[2:] == [
            "--revised-only",
            "--artifacts-dir",
            str(tmp_path),
            "--output",
            os.path.join(str(tmp_path), "auto-fix-runs", "20260404-170041-report.html"),
        ]

    def test_other_types_are_named_explicitly(self, monkeypatch, tmp_path):
        yaml_cmd, html_cmd = self._capture(monkeypatch, "initiative", tmp_path)
        assert yaml_cmd[2:] == [
            "--start-time",
            "20260404-170041",
            "--artifacts-dir",
            str(tmp_path),
            "--report-stage",
            "final",
            "--type",
            "initiative",
        ]
        assert html_cmd[2:] == [
            "--revised-only",
            "--artifacts-dir",
            str(tmp_path),
            "--output",
            os.path.join(
                str(tmp_path), "auto-fix-runs", "initiative-run-20260404-170041-report.html"
            ),
            "--type",
            "initiative",
        ]


# ── PR-3c-iii: effective binding, is_existing, ownership, pre-update verification ────────────


def _run_submit_env(artifacts_dir, env_extra, extra_flags=None):
    """Run submit.py --dry-run with ``env_extra`` layered over the clean environment."""
    env = _clean_env(**FAKE_CREDS, **env_extra)
    cmd = ["python3", SCRIPT, "--dry-run", "--artifacts-dir", artifacts_dir]
    if extra_flags:
        cmd.extend(extra_flags)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return result.stdout, result.stderr, result.returncode


KONFLUX = {"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"}
INITIATIVE_PAIR = {
    "RFE_CREATOR_BINDING_RFE_PROJECT": "RHOAIENG",
    "RFE_CREATOR_BINDING_RFE_ISSUE_TYPE": "Initiative",
}


def _rfe_binding(env=None):
    import submit as submit_mod

    return submit_mod._TYPES.get("rfe").binding(env or {})


class TestIsExistingRule:
    """The design §5 ``is_existing`` rule, one helper for every site in submit.py: the
    frontmatter ``tracker_ref`` decides when present (owned by the resolved type's effective
    binding -> existing; owned by another type -> hard error), else key-prefix-union membership
    on the effective binding (the pre-migration fallback)."""

    def _existing(self, data, binding=None, type_name="rfe"):
        import submit as submit_mod

        return submit_mod._is_existing(data, "rfe_id", type_name, binding or _rfe_binding(), env={})

    def test_tracker_ref_owned_by_the_resolved_type_is_existing(self):
        assert self._existing({"rfe_id": "RHAIRFE-1", "tracker_ref": "RHAIRFE-1"}) is True

    def test_tracker_ref_is_read_never_re_derived_from_the_id(self):
        # The id is a local draft id, the reference says the item is in Jira: the reference wins.
        assert self._existing({"rfe_id": "RFE-001", "tracker_ref": "RHAIRFE-9"}) is True

    def test_tracker_ref_owned_by_another_type_is_a_hard_error(self):
        import submit as submit_mod

        data = {"rfe_id": "RHAIRFE-1", "type": "rfe", "tracker_ref": "RHOAIENG-7"}
        with pytest.raises(submit_mod.TrackerRefError) as exc:
            self._existing(data)
        message = str(exc.value)
        assert message == (
            "RHAIRFE-1 carries tracker_ref 'RHOAIENG-7', a key type initiative owns, not the "
            "resolved type rfe (key prefixes: RHAIRFE-); an artifact bound to another type is "
            "never updated or created here — fix the artifact before re-running; nothing submitted"
        )

    def test_tracker_ref_owned_by_no_type_is_a_hard_error_too(self):
        import submit as submit_mod

        with pytest.raises(submit_mod.TrackerRefError) as exc:
            self._existing({"rfe_id": "RHAIRFE-1", "tracker_ref": "FOO-1"})
        assert "a key no registered type owns, not the resolved type rfe" in str(exc.value)

    @pytest.mark.parametrize("absent", [None, "", "   "], ids=["null", "empty", "blank"])
    def test_absent_tracker_ref_falls_back_to_the_key_prefix_union(self, absent):
        assert self._existing({"rfe_id": "RHAIRFE-1", "tracker_ref": absent}) is True
        assert self._existing({"rfe_id": "RFE-001", "tracker_ref": absent}) is False

    def test_no_tracker_ref_key_at_all_is_the_pre_migration_shape(self):
        assert self._existing({"rfe_id": "RHAIRFE-1"}) is True
        assert self._existing({"rfe_id": "RFE-001"}) is False
        assert self._existing({"rfe_id": "RHOAIENG-1"}) is False  # not this type's key

    def test_the_fallback_is_the_effective_union_under_a_project_override(self):
        binding = _rfe_binding(KONFLUX)
        assert binding["key_prefixes"] == ["KONFLUX-", "RHAIRFE-"]
        assert self._existing({"rfe_id": "KONFLUX-1"}, binding) is True  # the write prefix
        assert self._existing({"rfe_id": "RHAIRFE-1"}, binding) is True  # kept as a read prefix
        assert self._existing({"rfe_id": "RFE-001"}, binding) is False
        assert self._existing({"rfe_id": "KONFLUX-1", "tracker_ref": "KONFLUX-1"}, binding) is True
        assert self._existing({"rfe_id": "RHAIRFE-1", "tracker_ref": "RHAIRFE-1"}, binding) is True

    def test_owned_key_is_the_prefix_union_predicate(self):
        import submit as submit_mod

        binding = _rfe_binding(KONFLUX)
        assert submit_mod._owned_key("KONFLUX-12", binding)
        assert submit_mod._owned_key("RHAIRFE-12", binding)
        assert not submit_mod._owned_key("RHOAIENG-12", binding)
        assert not submit_mod._owned_key("", binding)
        assert not submit_mod._owned_key(None, binding)


class TestEffectiveBindingInTheConfig:
    """main() reads project, issue type and the write prefix from the resolved type's EFFECTIVE
    binding; the module-level TYPE_CONFIGS stays the descriptor projection other tests pin."""

    def test_no_override_is_the_descriptor_projection(self):
        import submit as submit_mod

        for name in ("rfe", "initiative"):
            binding = submit_mod._TYPES.get(name).binding({})
            cfg = submit_mod._effective_config(name, binding)
            assert cfg == submit_mod.TYPE_CONFIGS[name]
            assert list(cfg) == list(submit_mod.TYPE_CONFIGS[name])

    def test_project_override_reaches_project_and_write_prefix_only(self):
        import submit as submit_mod

        cfg = submit_mod._effective_config("rfe", _rfe_binding(KONFLUX))
        assert (cfg["project"], cfg["issue_type"], cfg["jira_prefix"]) == (
            "KONFLUX",
            "Feature Request",
            "KONFLUX-",
        )
        untouched = {k: v for k, v in cfg.items() if k not in ("project", "jira_prefix")}
        expected = {
            k: v
            for k, v in submit_mod.TYPE_CONFIGS["rfe"].items()
            if k not in ("project", "jira_prefix")
        }
        assert untouched == expected
        # The module table is a descriptor projection and is never mutated.
        assert submit_mod.TYPE_CONFIGS["rfe"]["project"] == "RHAIRFE"
        assert submit_mod.TYPE_CONFIGS["rfe"]["jira_prefix"] == "RHAIRFE-"

    def test_issue_type_override_reaches_issue_type(self):
        import submit as submit_mod

        cfg = submit_mod._effective_config(
            "rfe", _rfe_binding({"RFE_CREATOR_BINDING_RFE_ISSUE_TYPE": "Epic"})
        )
        assert (cfg["project"], cfg["issue_type"], cfg["jira_prefix"]) == (
            "RHAIRFE",
            "Epic",
            "RHAIRFE-",
        )

    def test_dry_run_sentinel_is_binding_derived(self):
        import submit as submit_mod

        assert submit_mod._dry_run_key(submit_mod.TYPE_CONFIGS["rfe"]["jira_prefix"]) == (
            "RHAIRFE-DRY"
        )
        cfg = submit_mod._effective_config("rfe", _rfe_binding(KONFLUX))
        assert submit_mod._dry_run_key(cfg["jira_prefix"]) == "KONFLUX-DRY"


class TestBindingSkipReason:
    """The pre-update verification's verdict: None on a match, else the plan's skip reason."""

    def _reason(self, fields, env=None):
        import submit as submit_mod

        return submit_mod._binding_skip_reason("RHAIRFE-1", fields, "rfe", _rfe_binding(env))

    def test_match_is_none(self):
        fields = {"project": {"key": "RHAIRFE"}, "issuetype": {"name": "Feature Request"}}
        assert self._reason(fields) is None

    def test_mismatch_names_both_pairs(self):
        fields = {"project": {"key": "RHAIRFE"}, "issuetype": {"name": "Epic"}}
        assert self._reason(fields) == (
            "binding mismatch — RHAIRFE-1 is (RHAIRFE, Epic) in Jira but type rfe binds "
            "(RHAIRFE, Feature Request)"
        )

    def test_missing_witnesses_fail_closed(self):
        fields = {"issuetype": {"name": "Feature Request"}}
        assert self._reason(fields) == (
            "binding unverifiable — the fetched issue has no project field; type rfe binds "
            "(RHAIRFE, Feature Request)"
        )
        assert self._reason({}) == (
            "binding unverifiable — the fetched issue has no project or issuetype field; type "
            "rfe binds (RHAIRFE, Feature Request)"
        )
        assert self._reason({"project": {}, "issuetype": None}).startswith(
            "binding unverifiable — the fetched issue has no project or issuetype field"
        )

    def test_the_expected_pair_is_the_effective_binding(self):
        fields = {"project": {"key": "KONFLUX"}, "issuetype": {"name": "Feature Request"}}
        assert self._reason(fields) is not None
        assert self._reason(fields, KONFLUX) is None

    def test_a_descriptor_prefixed_key_accepts_the_descriptor_pair_under_an_override(self):
        import submit as submit_mod

        binding = _rfe_binding(KONFLUX)
        rfe_pair = {"project": {"key": "RHAIRFE"}, "issuetype": {"name": "Feature Request"}}
        rfe_epic = {"project": {"key": "RHAIRFE"}, "issuetype": {"name": "Epic"}}
        konflux_epic = {"project": {"key": "KONFLUX"}, "issuetype": {"name": "Epic"}}
        # RHAIRFE-1 carries the descriptor read prefix: (RHAIRFE, Feature Request) is accepted.
        assert submit_mod._binding_skip_reason("RHAIRFE-1", rfe_pair, "rfe", binding) is None
        assert submit_mod._binding_skip_reason("RHAIRFE-1", rfe_epic, "rfe", binding) == (
            "binding mismatch — RHAIRFE-1 is (RHAIRFE, Epic) in Jira but type rfe binds "
            "(KONFLUX, Feature Request) or, for a pre-override key, (RHAIRFE, Feature Request)"
        )
        # KONFLUX-1 carries the effective write prefix only: a mismatching type is refused.
        assert submit_mod._binding_skip_reason("KONFLUX-1", konflux_epic, "rfe", binding) == (
            "binding mismatch — KONFLUX-1 is (KONFLUX, Epic) in Jira but type rfe binds "
            "(KONFLUX, Feature Request)"
        )
        assert submit_mod._binding_skip_reason("KONFLUX-1", rfe_pair, "rfe", binding) is not None

    def test_the_message_names_the_remote_key(self):
        import submit as submit_mod

        fields = {"project": {"key": "RHAIRFE"}, "issuetype": {"name": "Epic"}}
        reason = submit_mod._binding_skip_reason(
            "RHAIRFE-1", fields, "rfe", _rfe_binding(), jira_key="RHAIRFE-2"
        )
        assert reason.startswith("binding mismatch — RHAIRFE-2 is (RHAIRFE, Epic) in Jira")


class TestResolveLineAndOwnership:
    """D3: the resolve line goes to stderr only when a non-default rung decided; §3.2.1 g: an
    override that selects another type's pair is refused before any file or Jira access."""

    def test_legacy_default_is_silent(self, art_dir):
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false"),
        )
        stdout, stderr, rc = _run_submit(art_dir)
        assert rc == 0
        assert stderr == ""
        assert "Would create RHAIRFE Feature Request: Test RFE" in stdout

    def test_explicit_type_prints_the_resolve_line_on_stderr(self, art_dir):
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false"),
        )
        stdout, stderr, rc = _run_submit(art_dir, ["--type", "rfe"])
        assert rc == 0
        assert stderr == "TYPE RESOLVED: rfe (--type)\n"
        assert "Would create RHAIRFE Feature Request: Test RFE" in stdout

    def test_override_under_the_legacy_default_binds_the_plan_and_stays_silent(self, art_dir):
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false"),
        )
        stdout, stderr, rc = _run_submit_env(art_dir, KONFLUX)
        assert rc == 0, stderr
        assert stderr == ""  # legacy default rung: silent, override or not (D3)
        assert "Would create KONFLUX Feature Request: Test RFE" in stdout
        assert "RHAIRFE" not in stdout

    def test_explicit_type_with_an_override_prints_the_override(self, art_dir):
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false"),
        )
        stdout, stderr, rc = _run_submit_env(art_dir, KONFLUX, ["--type", "rfe"])
        assert rc == 0, stderr
        assert stderr == "TYPE RESOLVED: rfe (--type; binding override project=KONFLUX)\n"

    def test_an_override_selecting_another_types_pair_is_refused_before_the_scan(self, art_dir):
        # No task file at all: the refusal comes first, so the scan's own error never prints.
        stdout, stderr, rc = _run_submit_env(art_dir, INITIATIVE_PAIR)
        assert rc == 1
        assert stdout == ""
        assert stderr.startswith(
            "Error: rfe: effective binding ('jira', 'RHOAIENG', 'Initiative') (source: env) is "
            "the binding registered for type 'initiative'; a tracker binding must be owned by "
            "exactly one type"
        )
        assert stderr.count("\n") == 1 and "Traceback" not in stderr
        assert "No RFE task files found" not in stderr

    def test_type_default_is_none_and_the_rendered_default_is_unchanged(self):
        import ast as _ast

        with open(SCRIPT, encoding="utf-8") as fh:
            tree = _ast.parse(fh.read())
        defaults = [
            kw.value.value
            for node in _ast.walk(tree)
            if isinstance(node, _ast.Call)
            and getattr(node.func, "attr", None) == "add_argument"
            and node.args
            and isinstance(node.args[0], _ast.Constant)
            and node.args[0].value == "--type"
            for kw in node.keywords
            if kw.arg == "default"
        ]
        assert defaults == [None]
        result = subprocess.run(
            [sys.executable, SCRIPT, "--help"], capture_output=True, text=True, env=_clean_env()
        )
        assert "Item type to submit (default: rfe)" in result.stdout


class TestForeignTrackerRefIsAHardError:
    """A task whose frontmatter tracker_ref is a key another type owns ends the run — exit 1,
    one line, before the plan (and so before any Jira write)."""

    def test_exit_1_before_the_plan(self, art_dir):
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\npriority: Major\nstatus: Ready\n"
            "type: rfe\ntracker_ref: RHOAIENG-7\n---\n\nBody.\n",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REVIEW_FM.format(rfe_id="RHAIRFE-1234", auto_revised="false"),
        )
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false"),
        )
        stdout, stderr, rc = _run_submit(art_dir)
        assert rc == 1
        assert stdout == ""
        assert stderr == (
            "Error: RHAIRFE-1234 carries tracker_ref 'RHOAIENG-7', a key type initiative owns, "
            "not the resolved type rfe (key prefixes: RHAIRFE-); an artifact bound to another "
            "type is never updated or created here — fix the artifact before re-running; "
            "nothing submitted\n"
        )

    def test_every_scanned_task_is_checked_whatever_its_status(self, art_dir):
        # A Submitted task from an earlier run is not in the plan, but a foreign reference on
        # it is the same corrupt workspace: the run does not start.
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\npriority: Major\nstatus: Submitted\n"
            "type: rfe\ntracker_ref: RHOAIENG-7\n---\n\nBody.\n",
        )
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false"),
        )
        stdout, stderr, rc = _run_submit(art_dir)
        assert rc == 1
        assert stdout == ""
        assert stderr.startswith("Error: RHAIRFE-1234 carries tracker_ref 'RHOAIENG-7'")

    def test_an_owned_tracker_ref_is_an_update_as_before(self, art_dir):
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\npriority: Major\nstatus: Ready\n"
            "type: rfe\ntracker_ref: RHAIRFE-1234\n---\n\nBody.\n",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REVIEW_FM.format(rfe_id="RHAIRFE-1234", auto_revised="false"),
        )
        stdout, stderr, rc = _run_submit(art_dir)
        assert rc == 0, stderr
        assert "Would update" in stdout


class TestPreUpdateVerification:
    """Before an existing issue is written (description, labels, transition) the fetch submit.py
    performs for conflict detection also carries the (project, issuetype) witnesses and they are
    verified against the effective binding; a mismatch is a plan SKIP — no write, left
    unprocessed — never a traceback. Paths without a fetch of their own (an update with no
    original to compare with; the reject path's label removal) get the smallest fetch."""

    JIRA_STATUS = {"status": {"name": "New"}}
    RFE_PAIR = {"project": {"key": "RHAIRFE"}, "issuetype": {"name": "Feature Request"}}
    EPIC_PAIR = {"project": {"key": "RHAIRFE"}, "issuetype": {"name": "Epic"}}

    def _run_main(self, monkeypatch, art_dir, conflict=None, issue=None):
        """Run submit.main() in-process (non-dry-run, fake credentials) with the Jira reads
        stubbed and every Jira write recorded instead of sent. Returns (rc, writes, calls)."""
        import submit as submit_mod

        for key in list(os.environ):
            if key.startswith("RFE_CREATOR_") or key in type_registry.HEADLESS_MARKER_VARS:
                monkeypatch.delenv(key, raising=False)
        for key, value in FAKE_CREDS.items():
            monkeypatch.setenv(key, value)
        calls, writes = [], []

        def fake_conflict(server, user, token, key, original_path, extra_fields=None):
            calls.append(("conflict", key, list(extra_fields or [])))
            if conflict is None:
                raise AssertionError("check_description_conflict must not be reached")
            if isinstance(conflict, Exception):
                raise conflict
            return conflict

        def fake_get_issue(server, user, token, key, fields=None):
            calls.append(("get_issue", key, list(fields or [])))
            if issue is None:
                raise AssertionError("get_issue must not be reached")
            if isinstance(issue, Exception):
                raise issue
            return {"key": key, "fields": issue}

        def recorder(name):
            def fake(*args, **kwargs):
                writes.append((name, args[3] if len(args) > 3 else None))
                return "RHAIRFE-9999" if name == "create_issue" else True

            return fake

        monkeypatch.setattr(submit_mod, "check_description_conflict", fake_conflict)
        monkeypatch.setattr(submit_mod, "get_issue", fake_get_issue)
        for name in (
            "update_issue",
            "swap_labels",
            "remove_labels",
            "add_labels",
            "add_comment",
            "transition_issue",
            "create_issue",
        ):
            monkeypatch.setattr(submit_mod, name, recorder(name))
        monkeypatch.setattr(
            submit_mod,
            "update_snapshot_hashes",
            lambda hashes, snap_dir, **kw: calls.append(("snapshot", dict(hashes), dict(kw))),
        )
        monkeypatch.setattr(sys, "argv", ["submit.py", "--artifacts-dir", art_dir])
        with pytest.raises(SystemExit) as exc:
            submit_mod.main()
        return exc.value.code, writes, calls

    def _existing(self, art_dir, original=True, review=None):
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\npriority: Major\nstatus: Ready\n"
            "type: rfe\ntracker_ref: RHAIRFE-1234\n---\n\nRevised.\n",
        )
        if original:
            _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", "Original.\n")
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            review or REVIEW_FM.format(rfe_id="RHAIRFE-1234", auto_revised="true"),
        )

    def test_the_conflict_fetch_carries_the_witnesses_and_a_match_updates(
        self, monkeypatch, art_dir, capsys
    ):
        self._existing(art_dir)
        rc, writes, calls = self._run_main(
            monkeypatch, art_dir, conflict=(False, {**self.JIRA_STATUS, **self.RFE_PAIR})
        )
        assert rc == 0
        assert calls[0] == ("conflict", "RHAIRFE-1234", ["status", "project", "issuetype"])
        assert [name for name, _ in writes] == ["update_issue", "swap_labels"]
        assert ("get_issue", "RHAIRFE-1234", ["project", "issuetype"]) not in calls
        assert "RHAIRFE-1234: Updated" in capsys.readouterr().out

    def test_a_mismatch_is_a_skip_with_no_write_and_left_unprocessed(
        self, monkeypatch, art_dir, capsys
    ):
        self._existing(art_dir)
        rc, writes, calls = self._run_main(
            monkeypatch, art_dir, conflict=(False, {**self.JIRA_STATUS, **self.EPIC_PAIR})
        )
        out = capsys.readouterr()
        assert rc == 0
        assert writes == []
        assert "Traceback" not in out.err
        assert (
            "Reason: binding mismatch — RHAIRFE-1234 is (RHAIRFE, Epic) in Jira but type rfe "
            "binds (RHAIRFE, Feature Request)"
        ) in out.out
        assert "RHAIRFE-1234: Skipping — binding mismatch" in out.out
        # Not disposed of: no hash, no processed flag (the snapshot is not touched at all).
        assert not [c for c in calls if c[0] == "snapshot"]
        fm_text = open(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md", encoding="utf-8").read()
        assert "status: Ready" in fm_text

    def test_a_mismatch_wins_over_a_conflict(self, monkeypatch, art_dir, capsys):
        self._existing(art_dir)
        rc, writes, _ = self._run_main(
            monkeypatch, art_dir, conflict=(True, {**self.JIRA_STATUS, **self.EPIC_PAIR})
        )
        out = capsys.readouterr().out
        assert rc == 0 and writes == []
        assert "binding mismatch" in out and "Jira conflict" not in out

    def test_no_original_gets_the_smallest_fetch(self, monkeypatch, art_dir, capsys):
        self._existing(art_dir, original=False)
        rc, writes, calls = self._run_main(
            monkeypatch, art_dir, conflict=(False, None), issue=self.EPIC_PAIR
        )
        assert rc == 0
        assert ("get_issue", "RHAIRFE-1234", ["project", "issuetype"]) in calls
        assert writes == []
        assert "binding mismatch" in capsys.readouterr().out

    def test_no_original_and_a_match_updates_with_no_status_short_circuit(
        self, monkeypatch, art_dir, capsys
    ):
        # The witness fetch requests project and issuetype only: jira_status stays None on
        # this path exactly as before, so the approve short-circuit is unchanged.
        self._existing(art_dir, original=False)
        rc, writes, calls = self._run_main(
            monkeypatch, art_dir, conflict=(False, None), issue=self.RFE_PAIR
        )
        assert rc == 0
        assert [name for name, _ in writes] == ["update_issue", "swap_labels"]
        assert ("get_issue", "RHAIRFE-1234", ["project", "issuetype"]) in calls

    def test_missing_witnesses_fail_closed(self, monkeypatch, art_dir, capsys):
        self._existing(art_dir)
        rc, writes, _ = self._run_main(monkeypatch, art_dir, conflict=(False, {**self.JIRA_STATUS}))
        assert rc == 0 and writes == []
        assert (
            "binding unverifiable — the fetched issue has no project or issuetype field"
        ) in capsys.readouterr().out

    def test_the_reject_paths_label_removal_is_verified_too(self, monkeypatch, art_dir, capsys):
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\npriority: Major\nstatus: Ready\n"
            "original_labels:\n- rfe-creator-autofix-rubric-pass\n---\n\nBody.\n",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"),
        )
        rc, writes, calls = self._run_main(monkeypatch, art_dir, issue=self.EPIC_PAIR)
        out = capsys.readouterr().out
        assert rc == 0
        assert ("get_issue", "RHAIRFE-1234", ["project", "issuetype"]) in calls
        assert writes == []
        assert "Remove labels" not in out and "binding mismatch" in out

    def test_the_reject_path_removes_labels_after_a_match(self, monkeypatch, art_dir, capsys):
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\npriority: Major\nstatus: Ready\n"
            "original_labels:\n- rfe-creator-autofix-rubric-pass\n---\n\nBody.\n",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"),
        )
        rc, writes, _ = self._run_main(monkeypatch, art_dir, issue=self.RFE_PAIR)
        assert rc == 0
        assert writes == [("remove_labels", "RHAIRFE-1234")]
        assert "Removed labels: rfe-creator-autofix-rubric-pass" in capsys.readouterr().out

    def test_a_plain_reject_makes_no_fetch(self, monkeypatch, art_dir):
        # Nothing to remove -> nothing written -> nothing to verify: no request at all.
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md", TASK_FM.format(rfe_id="RHAIRFE-1234"))
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"),
        )
        rc, writes, calls = self._run_main(monkeypatch, art_dir)
        assert rc == 0 and writes == []
        assert not [c for c in calls if c[0] in ("get_issue", "conflict")]

    def test_a_failed_witness_fetch_skips_the_item_and_writes_nothing(
        self, monkeypatch, art_dir, capsys
    ):
        # No original: the witness fetch is the only read. A transport failure there means the
        # binding cannot be verified, so — like split_submit's parent check — the item is
        # skipped, left unprocessed for the next run, and nothing is written.
        self._existing(art_dir, original=False)
        rc, writes, _ = self._run_main(
            monkeypatch, art_dir, conflict=(False, None), issue=RuntimeError("HTTP 503")
        )
        out = capsys.readouterr()
        assert rc == 0
        assert writes == []
        assert "could not verify RHAIRFE-1234 against the rfe binding" in out.out
        assert "RuntimeError: HTTP 503" in out.out
        assert "Warning: conflict check failed" not in out.err

    def test_a_failed_conflict_check_skips_the_item_and_writes_nothing(
        self, monkeypatch, art_dir, capsys
    ):
        # With an original present the conflict check IS the witness fetch; if it raises, the
        # same fail-closed skip applies (main used to warn and update anyway).
        self._existing(art_dir, original=True)
        rc, writes, _ = self._run_main(
            monkeypatch, art_dir, conflict=RuntimeError("HTTP Error 404: Not Found")
        )
        out = capsys.readouterr()
        assert rc == 0
        assert writes == []
        assert "could not verify RHAIRFE-1234 against the rfe binding" in out.out
        assert "Warning: conflict check failed" not in out.err

    def test_the_remote_key_is_the_tracker_ref(self, monkeypatch, art_dir, capsys):
        # rfe_id RHAIRFE-1 with tracker_ref RHAIRFE-2: ONE remote key — the fetch, the update
        # and the labels name RHAIRFE-2; the local task / original / review paths keep RHAIRFE-1.
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1.md",
            "---\nrfe_id: RHAIRFE-1\ntitle: Test RFE\npriority: Major\nstatus: Ready\n"
            "type: rfe\ntracker_ref: RHAIRFE-2\n---\n\nRevised.\n",
        )
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1.md", "Original.\n")
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1-review.md",
            REVIEW_FM.format(rfe_id="RHAIRFE-1", auto_revised="true"),
        )
        rc, writes, calls = self._run_main(
            monkeypatch, art_dir, conflict=(False, {**self.JIRA_STATUS, **self.RFE_PAIR})
        )
        assert rc == 0
        assert calls[0] == ("conflict", "RHAIRFE-2", ["status", "project", "issuetype"])
        assert writes == [("update_issue", "RHAIRFE-2"), ("swap_labels", "RHAIRFE-2")]
        assert "RHAIRFE-1: Updated" in capsys.readouterr().out
        with open(f"{art_dir}/rfe-tasks/RHAIRFE-1.md", encoding="utf-8") as f:
            assert "status: Submitted" in f.read()

    def test_the_reject_path_removes_labels_from_the_tracker_ref(self, monkeypatch, art_dir):
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1.md",
            "---\nrfe_id: RHAIRFE-1\ntitle: Test RFE\npriority: Major\nstatus: Ready\n"
            "type: rfe\ntracker_ref: RHAIRFE-2\n"
            "original_labels:\n- rfe-creator-autofix-rubric-pass\n---\n\nBody.\n",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1-review.md",
            REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1"),
        )
        rc, writes, calls = self._run_main(monkeypatch, art_dir, issue=self.RFE_PAIR)
        assert rc == 0
        assert ("get_issue", "RHAIRFE-2", ["project", "issuetype"]) in calls
        assert writes == [("remove_labels", "RHAIRFE-2")]

    def test_a_pre_override_key_accepts_the_descriptor_pair(self, monkeypatch, art_dir, capsys):
        # RHAIRFE-1234 (RHAIRFE, Feature Request) under RFE_CREATOR_BINDING_RFE_PROJECT=KONFLUX:
        # a legitimate item created before the override — updated in place, not skipped.
        # (monkeypatch first, so the raw assignment inside the wrapper is undone at teardown.)
        monkeypatch.setenv("RFE_CREATOR_BINDING_RFE_PROJECT", "KONFLUX")
        self._existing(art_dir)
        import submit as submit_mod

        original_main = submit_mod.main

        def main_with_override():
            os.environ["RFE_CREATOR_BINDING_RFE_PROJECT"] = "KONFLUX"
            return original_main()

        monkeypatch.setattr(submit_mod, "main", main_with_override)
        rc, writes, _ = self._run_main(
            monkeypatch, art_dir, conflict=(False, {**self.JIRA_STATUS, **self.RFE_PAIR})
        )
        assert rc == 0
        assert [name for name, _ in writes] == ["update_issue", "swap_labels"]
        assert "RHAIRFE-1234: Updated" in capsys.readouterr().out

    def test_the_expected_pair_is_the_effective_binding(self, monkeypatch, art_dir, capsys):
        monkeypatch.setenv("RFE_CREATOR_BINDING_RFE_ISSUE_TYPE", "Epic")
        self._existing(art_dir)
        # _run_main clears RFE_CREATOR_*; set the override after that via the env it builds.
        import submit as submit_mod

        original_main = submit_mod.main

        def main_with_override():
            os.environ["RFE_CREATOR_BINDING_RFE_ISSUE_TYPE"] = "Epic"
            return original_main()

        monkeypatch.setattr(submit_mod, "main", main_with_override)
        rc, writes, _ = self._run_main(
            monkeypatch, art_dir, conflict=(False, {**self.JIRA_STATUS, **self.EPIC_PAIR})
        )
        assert rc == 0
        assert [name for name, _ in writes] == ["update_issue", "swap_labels"]
        assert "RHAIRFE-1234: Updated" in capsys.readouterr().out


class TestShorthandIsRefused:
    """The bare JIRA_PROJECT / JIRA_ISSUE_TYPE shorthand reaches the writers' binding through
    resolve but not the artifact layer (SCHEMAS, the rename guard), so a run under it would
    create the issue and then fail the rename: refused with one Error: line, exit 1, before any
    scan — the typed RFE_CREATOR_BINDING_* variables are the deployment knob."""

    LINE = (
        "Error: JIRA_PROJECT / JIRA_ISSUE_TYPE shorthand is not honoured by the artifact layer; "
        "set RFE_CREATOR_BINDING_RFE_PROJECT / _ISSUE_TYPE instead\n"
    )

    def _draft(self, art_dir):
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            REVIEW_FM.format(rfe_id="RFE-001", auto_revised="false"),
        )

    @pytest.mark.parametrize(
        "env", [{"JIRA_PROJECT": "KONFLUX"}, {"JIRA_ISSUE_TYPE": "Story"}], ids=["project", "type"]
    )
    def test_refused_before_the_scan(self, art_dir, env):
        self._draft(art_dir)
        stdout, stderr, rc = _run_submit_env(art_dir, env)
        assert (rc, stdout, stderr) == (1, "", self.LINE)

    def test_with_an_explicit_type_the_resolve_line_precedes_the_refusal(self, art_dir):
        self._draft(art_dir)
        stdout, stderr, rc = _run_submit_env(
            art_dir, {"JIRA_PROJECT": "KONFLUX"}, ["--type", "rfe"]
        )
        assert rc == 1 and stdout == ""
        line = "TYPE RESOLVED: rfe (--type; binding override project=KONFLUX)\n"
        assert stderr == line + self.LINE

    def test_the_typed_variable_is_honoured_instead(self, art_dir):
        self._draft(art_dir)
        stdout, stderr, rc = _run_submit_env(
            art_dir, {"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"}
        )
        assert rc == 0, stderr
        assert stderr == ""
        assert "RFE-001: Would create KONFLUX Feature Request" in stdout

    def test_a_malformed_override_for_another_type_is_one_line_never_a_traceback(self, art_dir):
        # artifact_utils degrades the initiative type's effective values to the descriptor at
        # import; the writer's own resolve / ownership step then reports the value once.
        self._draft(art_dir)
        stdout, stderr, rc = _run_submit_env(
            art_dir, {"RFE_CREATOR_BINDING_INITIATIVE_PROJECT": "lower"}
        )
        assert (rc, stdout) == (1, "")
        assert stderr == (
            "Error: RFE_CREATOR_BINDING_INITIATIVE_PROJECT='lower': expected an upper-case "
            "tracker project key\n"
        )


class TestResolveLineIsPrintedOncePerRun:
    """D3 across the split spawn: submit.py --type <t> prints its line and marks the child's
    environment (RFE_CREATOR_RESOLVED_BY_PARENT), so the split_submit.py it spawns per parent
    prints none — one TYPE RESOLVED line per run, not 1+N."""

    def _tree(self, art_dir):
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1000.md",
            "---\nrfe_id: RHAIRFE-1000\ntitle: Parent\npriority: Major\nstatus: Archived\n---\n"
            "\nParent body.\n",
        )
        for i in (1, 2):
            _write(
                f"{art_dir}/rfe-tasks/RFE-00{i}.md",
                f"---\nrfe_id: RFE-00{i}\ntitle: Child {i}\npriority: Major\nstatus: Ready\n"
                f"parent_key: RHAIRFE-1000\n---\n\nChild {i} body.\n",
            )

    def _run(self, art_dir, *flags):
        # No credentials: the dry run needs none and split_submit --dry-run skips recovery.
        env = _clean_env(JIRA_SERVER="", JIRA_USER="", JIRA_TOKEN="")
        return subprocess.run(
            [sys.executable, SCRIPT, "--dry-run", "--artifacts-dir", art_dir, *flags],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_the_child_skips_its_line(self, art_dir):
        self._tree(art_dir)
        explicit = self._run(art_dir, "--type", "rfe")
        assert explicit.returncode == 0, explicit.stderr
        assert "Phase 1: Submitting 1 split parent(s)" in explicit.stdout
        assert "Would create RHAIRFE ticket for child 1/2" in explicit.stdout
        assert explicit.stderr == "TYPE RESOLVED: rfe (--type)\n"
        silent = self._run(art_dir)
        assert silent.returncode == 0, silent.stderr
        assert silent.stderr == ""
        assert silent.stdout == explicit.stdout

    def test_the_marker_is_set_on_the_child_environment(self, art_dir, tmp_path):
        self._tree(art_dir)
        log = tmp_path / "split-stub.log"
        stub = tmp_path / "split_submit_stub.py"
        stub.write_text(
            "import os, sys\n"
            "with open(os.environ['SPLIT_STUB_LOG'], 'a') as f:\n"
            "    f.write(os.environ.get('RFE_CREATOR_RESOLVED_BY_PARENT', '<unset>') + '\\n')\n"
            "sys.exit(0)\n"
        )
        env = _clean_env(
            JIRA_SERVER="",
            JIRA_USER="",
            JIRA_TOKEN="",
            RFE_SPLIT_SUBMIT_SCRIPT=str(stub),
            SPLIT_STUB_LOG=str(log),
        )
        result = subprocess.run(
            [sys.executable, SCRIPT, "--dry-run", "--artifacts-dir", art_dir],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert log.read_text().splitlines() == ["1"]


class TestAnOverriddenParentIsSplittable:
    """Under RFE_CREATOR_BINDING_RFE_PROJECT=KONFLUX a fetched KONFLUX-1 parent and its children
    (parent_key: KONFLUX-1) pass the task schema — the parent_key grammar follows the effective
    write prefix — so submit Phase 1 selects the parent and the split_submit it spawns finds the
    children. Without the override neither file is an rfe artifact."""

    def _tree(self, art_dir):
        _write(
            f"{art_dir}/rfe-tasks/KONFLUX-1.md",
            "---\nrfe_id: KONFLUX-1\ntitle: Parent\npriority: Major\nstatus: Archived\n"
            "type: rfe\ntracker_ref: KONFLUX-1\n---\n\nParent body.\n",
        )
        _write(
            f"{art_dir}/rfe-tasks/RFE-001.md",
            "---\nrfe_id: RFE-001\ntitle: Child 1\npriority: Major\nstatus: Ready\n"
            "parent_key: KONFLUX-1\n---\n\nChild body.\n",
        )

    def _run(self, art_dir, **extra):
        env = _clean_env(JIRA_SERVER="", JIRA_USER="", JIRA_TOKEN="", **extra)
        return subprocess.run(
            [sys.executable, SCRIPT, "--dry-run", "--artifacts-dir", art_dir],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_submit_phase_1_and_split_submit_find_the_child(self, art_dir):
        self._tree(art_dir)
        r = self._run(art_dir, RFE_CREATOR_BINDING_RFE_PROJECT="KONFLUX")
        assert r.returncode == 0, r.stderr
        assert r.stderr == ""
        assert "Phase 1: Submitting 1 split parent(s)" in r.stdout
        assert "Split submission: KONFLUX-1 -> 1 children" in r.stdout
        assert "Would create KONFLUX ticket for child 1/1: Child 1" in r.stdout
        assert "Would link to KONFLUX-1 via 'Work item split'" in r.stdout
        plain = self._run(art_dir)
        assert plain.returncode == 1
        assert "parent_key: 'KONFLUX-1' does not match" in plain.stderr
        assert "Error: No RFE task files found." in plain.stderr
