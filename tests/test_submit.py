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
        args = SimpleNamespace(
            type=type_name, artifacts_dir=str(tmp_path), report_timestamp="20260404-170041"
        )
        submit_mod._generate_reports(args)
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
