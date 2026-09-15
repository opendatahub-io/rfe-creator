#!/usr/bin/env python3
"""Integration tests for submit.py using a jira-emulator server.

Runs the full execution path against a real HTTP server that tracks
issue state, changelogs, labels, and comments.
"""

import json
import os
import subprocess
import sys

import pytest
import yaml

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "submit.py")


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _read_frontmatter(path):
    """Read YAML frontmatter from a file."""
    with open(path) as f:
        content = f.read()
    if not content.startswith("---"):
        return {}
    end = content.index("---", 3)
    return yaml.safe_load(content[3:end])


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def art_dir(tmp_path):
    """Create a minimal artifacts directory."""
    for d in ["rfe-tasks", "rfe-reviews", "rfe-originals"]:
        os.makedirs(tmp_path / d)
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield str(tmp_path)
    os.chdir(orig)


def _run_submit(artifacts_dir, server_url, extra_flags=None):
    """Run submit.py (non-dry-run) against the jira-emulator."""
    env = {
        **os.environ,
        "JIRA_SERVER": server_url,
        "JIRA_USER": "admin",
        "JIRA_TOKEN": "admin",
    }
    cmd = [sys.executable, SCRIPT, "--artifacts-dir", artifacts_dir]
    if extra_flags:
        cmd.extend(extra_flags)
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


# ── Templates ────────────────────────────────────────────────────────────────

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
needs_attention: {needs_attention}
{extra_fields}scores:
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


def _review(rfe_id, auto_revised="false", needs_attention="false", extra_fields=""):
    return REVIEW_FM.format(
        rfe_id=rfe_id,
        auto_revised=auto_revised,
        needs_attention=needs_attention,
        extra_fields=extra_fields,
    )


# ── Tests ────────────────────────────────────────────────────────────────────


class TestCreateNewRFE:
    def test_posts_correct_fields(self, art_dir, jira):
        """New RFE → issue created in Jira with correct fields."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md", _review("RFE-001"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        # Find the created issue key from stdout
        issues = jira.search("project = RHAIRFE")
        assert len(issues) == 1
        key = issues[0]["key"]
        issue = jira.get(key)
        assert issue["fields"]["summary"] == "Test RFE"
        assert issue["fields"]["priority"]["name"] == "Major"

    def test_includes_labels(self, art_dir, jira):
        """New RFE → labels include auto-created and rubric-pass."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md", _review("RFE-001"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        issues = jira.search("project = RHAIRFE")
        issue = jira.get(issues[0]["key"])
        labels = issue["fields"]["labels"]
        assert "rfe-creator-auto-created" in labels
        assert "rfe-creator-autofix-rubric-pass" in labels

    def test_renames_files(self, art_dir, jira):
        """New RFE → RFE-001.md renamed to RHAIRFE-N.md."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md", _review("RFE-001"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        # RFE-001.md should be renamed to the Jira key
        assert not os.path.exists(f"{art_dir}/rfe-tasks/RFE-001.md")
        issues = jira.search("project = RHAIRFE")
        key = issues[0]["key"]
        assert os.path.exists(f"{art_dir}/rfe-tasks/{key}.md")
        fm = _read_frontmatter(f"{art_dir}/rfe-tasks/{key}.md")
        assert fm["rfe_id"] == key


class TestUpdateExistingRFE:
    def _setup_existing(self, art_dir, jira, original, revised):
        jira.create("RHAIRFE-1234", "Test RFE", original)
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", original)
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            f"priority: Major\nstatus: Ready\n---\n{revised}",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            _review("RHAIRFE-1234", auto_revised="true"),
        )

    def test_puts_description(self, art_dir, jira):
        """Existing RFE with changes → description updated in Jira."""
        self._setup_existing(art_dir, jira, "Original.", "Revised.")

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "Updated" in r.stdout

        # Verify description was updated
        issue = jira.get("RHAIRFE-1234")
        desc = issue["fields"]["description"]
        # Description is stored as ADF by the emulator via v3
        if isinstance(desc, dict):
            # Extract text from ADF
            texts = []
            for node in desc.get("content", []):
                for child in node.get("content", []):
                    if child.get("type") == "text":
                        texts.append(child["text"])
            desc_text = " ".join(texts)
        else:
            desc_text = desc
        assert "Revised" in desc_text or "Problem Statement" in desc_text

    def test_adds_labels_separately(self, art_dir, jira):
        """Update → labels added to the issue."""
        self._setup_existing(art_dir, jira, "Original.", "Revised.")

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        issue = jira.get("RHAIRFE-1234")
        labels = issue["fields"]["labels"]
        assert "rfe-creator-auto-revised" in labels
        assert "rfe-creator-autofix-rubric-pass" in labels

    def test_sets_status_submitted(self, art_dir, jira):
        """Existing RFE after update → frontmatter status = Submitted."""
        self._setup_existing(art_dir, jira, "Original.", "Revised.")

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        fm = _read_frontmatter(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md")
        assert fm["status"] == "Submitted"


class TestLabelOnly:
    def test_no_description_put(self, art_dir, jira):
        """Unchanged content → label added, description not changed."""
        body = "Same content.\n"
        jira.create("RHAIRFE-1234", "Test RFE", body)
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", body)
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            f"priority: Major\nstatus: Ready\n---\n{body}",
        )
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md", _review("RHAIRFE-1234"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        # Labels should be added
        issue = jira.get("RHAIRFE-1234")
        assert "rfe-creator-autofix-rubric-pass" in issue["fields"]["labels"]

        # Check changelog — should have label change but no description change
        desc_changes = []
        for h in issue.get("changelog", {}).get("histories", []):
            for item in h.get("items", []):
                if item["field"] == "description":
                    desc_changes.append(item)
        assert len(desc_changes) == 0


class TestRemoveLabels:
    def test_sends_remove_operation(self, art_dir, jira):
        """Rejected RFE with stale rubric-pass → label removed."""
        jira.create(
            "RHAIRFE-1234", "Test RFE", "Content.", labels=["rfe-creator-autofix-rubric-pass"]
        )
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            "priority: Major\nstatus: Ready\n"
            "original_labels:\n- rfe-creator-autofix-rubric-pass\n"
            "---\n\n## Problem\n\nContent.\n",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"),
        )

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "Removed labels" in r.stdout

        # Verify label was removed
        issue = jira.get("RHAIRFE-1234")
        assert "rfe-creator-autofix-rubric-pass" not in issue["fields"]["labels"]

    def test_no_api_call_on_plain_reject(self, art_dir, jira):
        """Rejected RFE without rubric-pass → issue unchanged."""
        jira.create("RHAIRFE-1234", "Test RFE", "Content.")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md", TASK_FM.format(rfe_id="RHAIRFE-1234"))
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"),
        )

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        # Issue should have no changes (empty changelog)
        issue = jira.get("RHAIRFE-1234")
        histories = issue.get("changelog", {}).get("histories", [])
        assert len(histories) == 0

    def test_does_not_update_frontmatter_status(self, art_dir, jira):
        """Remove labels must NOT set status to Submitted."""
        jira.create(
            "RHAIRFE-1234", "Test RFE", "Content.", labels=["rfe-creator-autofix-rubric-pass"]
        )
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            "priority: Major\nstatus: Ready\n"
            "original_labels:\n- rfe-creator-autofix-rubric-pass\n"
            "---\n\n## Problem\n\nContent.\n",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"),
        )

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        fm = _read_frontmatter(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md")
        assert fm["status"] == "Ready"  # NOT "Submitted"

    def test_not_in_snapshot_update(self, art_dir, jira):
        """Remove labels must NOT update the snapshot."""
        # Seed a snapshot
        snap_dir = os.path.join(art_dir, "auto-fix-runs")
        os.makedirs(snap_dir, exist_ok=True)
        snap = {
            "query_timestamp": "2026-04-01T00:00:00Z",
            "timestamp": "2026-04-01T00:00:01Z",
            "issues": {"RHAIRFE-1234": "original-hash"},
        }
        snap_path = os.path.join(snap_dir, "issue-snapshot-20260401-000000.yaml")
        with open(snap_path, "w") as f:
            yaml.dump(snap, f)

        jira.create(
            "RHAIRFE-1234", "Test RFE", "Content.", labels=["rfe-creator-autofix-rubric-pass"]
        )
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            "priority: Major\nstatus: Ready\n"
            "original_labels:\n- rfe-creator-autofix-rubric-pass\n"
            "---\n\n## Problem\n\nContent.\n",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"),
        )

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        # Snapshot hash preserved, but marked processed (pipeline completed)
        with open(snap_path) as f:
            data = yaml.safe_load(f)
        entry = data["issues"]["RHAIRFE-1234"]
        assert entry == {"hash": "original-hash", "processed": True}


FEAS_REVIEW_TEMPLATE = """\
---
rfe_id: {rfe_id}
score: 9
pass: true
recommendation: submit
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


class TestFeasibilityLabelExecutor:
    """End-to-end coverage that the executor actually fires remove_labels()
    in both the Label-only and Update branches when feasibility flips."""

    def test_label_only_path_calls_remove(self, art_dir, jira):
        """Re-submit existing RHAIRFE, no body change, verdict flipped →
        Label-only branch must remove stale feasibility label."""
        body = "## Problem\n\nSame content.\n"
        jira.create("RHAIRFE-1234", "Test RFE", body, labels=["rfe-creator-feasibility-fail"])
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", body)
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            f"priority: Major\nstatus: Ready\n"
            f"original_labels:\n- rfe-creator-feasibility-fail\n"
            f"---\n{body}",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            FEAS_REVIEW_TEMPLATE.format(rfe_id="RHAIRFE-1234", verdict="feasible"),
        )

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        issue = jira.get("RHAIRFE-1234")
        assert "rfe-creator-feasibility-pass" in issue["fields"]["labels"]
        assert "rfe-creator-feasibility-fail" not in issue["fields"]["labels"]

    def test_update_path_calls_remove(self, art_dir, jira):
        """Body change AND verdict flip → Update branch must remove stale."""
        original_body = "## Problem\n\nOriginal content.\n"
        new_body = "## Problem\n\nUpdated content.\n"
        jira.create(
            "RHAIRFE-1234", "Test RFE", original_body, labels=["rfe-creator-feasibility-fail"]
        )
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", original_body)
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            f"priority: Major\nstatus: Ready\n"
            f"original_labels:\n- rfe-creator-feasibility-fail\n"
            f"---\n{new_body}",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            FEAS_REVIEW_TEMPLATE.format(rfe_id="RHAIRFE-1234", verdict="feasible"),
        )

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        issue = jira.get("RHAIRFE-1234")
        assert "rfe-creator-feasibility-pass" in issue["fields"]["labels"]
        assert "rfe-creator-feasibility-fail" not in issue["fields"]["labels"]


class TestConflictDetection:
    def test_conflict_prevents_update(self, art_dir, jira):
        """Jira description differs from original → skip, no PUT."""
        jira.create("RHAIRFE-1234", "Test RFE", "Edited by someone.")
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", "Original.")
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            "priority: Major\nstatus: Ready\n---\nOur revision.",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            _review("RHAIRFE-1234", auto_revised="true"),
        )

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "Skipping" in r.stdout

        # Verify description was NOT changed
        issue = jira.get("RHAIRFE-1234")
        desc = issue["fields"]["description"]
        if isinstance(desc, dict):
            texts = []
            for node in desc.get("content", []):
                for child in node.get("content", []):
                    if child.get("type") == "text":
                        texts.append(child["text"])
            desc_text = " ".join(texts)
        else:
            desc_text = desc
        assert "Edited by someone" in desc_text


class TestCommentPosting:
    def test_removed_context_comment(self, art_dir, jira):
        """RFE with removed-context YAML → comment posted."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md", _review("RFE-001"))
        rc_yaml = {
            "blocks": [
                {
                    "type": "genuine",
                    "heading": "Implementation Notes",
                    "content": "Use gRPC for the service mesh.",
                }
            ]
        }
        _write(f"{art_dir}/rfe-tasks/RFE-001-removed-context.yaml", yaml.dump(rc_yaml))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "Posted removed-context comment" in r.stdout

        # Find the created issue and check its comments
        issues = jira.search("project = RHAIRFE")
        key = issues[0]["key"]
        comments = jira.request("GET", f"/rest/api/3/issue/{key}/comment")
        assert comments["total"] >= 1

    def test_needs_attention_comment(self, art_dir, jira):
        """RFE with needs_attention → comment posted."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(
            f"{art_dir}/rfe-reviews/RFE-001-review.md",
            _review(
                "RFE-001",
                needs_attention="true",
                extra_fields="needs_attention_reason: Unclear scope\n",
            ),
        )

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "needs-attention comment" in r.stdout

        issues = jira.search("project = RHAIRFE")
        key = issues[0]["key"]
        comments = jira.request("GET", f"/rest/api/3/issue/{key}/comment")
        assert comments["total"] >= 1


class TestSnapshotUpdate:
    def _seed_snapshot(self, art_dir, issues):
        """Write a snapshot so submit.py can update it."""
        snap_dir = os.path.join(art_dir, "auto-fix-runs")
        os.makedirs(snap_dir, exist_ok=True)
        snap = {
            "query_timestamp": "2026-04-01T00:00:00Z",
            "timestamp": "2026-04-01T00:00:01Z",
            "issues": issues,
        }
        path = os.path.join(snap_dir, "issue-snapshot-20260401-000000.yaml")
        with open(path, "w") as f:
            yaml.dump(snap, f, default_flow_style=False, sort_keys=False)
        return path

    def test_snapshot_updated_on_create(self, art_dir, jira):
        """Create → snapshot updated with new issue hash."""
        snap_path = self._seed_snapshot(art_dir, {"RHAIRFE-9000": "existing"})
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md", _review("RFE-001"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        with open(snap_path) as f:
            data = yaml.safe_load(f)
        # Find the created key
        issues = jira.search("project = RHAIRFE")
        key = issues[0]["key"]
        assert key in data["issues"]
        entry = data["issues"][key]
        assert isinstance(entry, dict)
        assert len(entry["hash"]) == 64  # SHA256 hex
        assert entry["processed"] is True
        # Other issues in snapshot still present
        assert data["issues"]["RHAIRFE-9000"] == "existing"

    def test_snapshot_updated_on_update(self, art_dir, jira):
        """Update → snapshot updated with revised hash."""
        snap_path = self._seed_snapshot(art_dir, {"RHAIRFE-1234": "old-hash"})
        jira.create("RHAIRFE-1234", "Test RFE", "Original.")
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", "Original.")
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            "priority: Major\nstatus: Ready\n---\nRevised.",
        )
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md", _review("RHAIRFE-1234"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        with open(snap_path) as f:
            data = yaml.safe_load(f)
        assert "RHAIRFE-1234" in data["issues"]
        entry = data["issues"]["RHAIRFE-1234"]
        assert isinstance(entry, dict)
        assert len(entry["hash"]) == 64
        assert entry["hash"] != "old-hash"
        assert entry["processed"] is True

    def test_no_update_when_all_skipped(self, art_dir, jira):
        """All RFEs rejected/skipped → snapshot unchanged."""
        snap_path = self._seed_snapshot(art_dir, {"RHAIRFE-1234": "existing"})
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md", TASK_FM.format(rfe_id="RHAIRFE-1234"))
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"),
        )

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        with open(snap_path) as f:
            data = yaml.safe_load(f)
        # Rejected entries are marked processed (pipeline completed)
        entry = data["issues"]["RHAIRFE-1234"]
        assert entry == {"hash": "existing", "processed": True}

    def test_unreviewed_item_left_unprocessed(self, art_dir, jira):
        """An item without a readable review is not disposed of: no Jira
        write, no processed flag — so the next fetch re-selects it instead
        of freezing it at an unchanged hash (RHAIRFE-3201, RHAIFIRST-582)."""
        # A selected issue enters the run unprocessed (cmd_fetch only ever
        # resets or preserves the flag; submit.py alone sets it).
        snap_path = self._seed_snapshot(
            art_dir,
            {
                "RHAIRFE-1234": {"hash": "hash-a", "processed": False},
                "RHAIRFE-5678": {"hash": "hash-b", "processed": False},
            },
        )
        jira.create("RHAIRFE-1234", "Test RFE", "Original.")
        jira.create("RHAIRFE-5678", "Other RFE", "Original.")
        for key in ("RHAIRFE-1234", "RHAIRFE-5678"):
            _write(f"{art_dir}/rfe-originals/{key}.md", "Original.")
            _write(
                f"{art_dir}/rfe-tasks/{key}.md",
                f"---\nrfe_id: {key}\ntitle: Test RFE\n"
                "priority: Major\nstatus: Ready\n---\nRevised.",
            )
        # Review only for 5678 — 1234 is the interrupted, unreviewed shape.
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-5678-review.md", _review("RHAIRFE-5678"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "no readable review" in r.stdout

        with open(snap_path) as f:
            data = yaml.safe_load(f)
        # The reviewed item was disposed of normally...
        assert data["issues"]["RHAIRFE-5678"]["processed"] is True
        # ...the unreviewed one was left exactly as it was: unchanged hash,
        # still unprocessed, so the next fetch re-selects it.
        assert data["issues"]["RHAIRFE-1234"] == {"hash": "hash-a", "processed": False}

    def test_dry_run_does_not_update_snapshot(self, art_dir, jira):
        """Dry-run must not write processed flags or hashes to snapshot."""
        snap_path = self._seed_snapshot(art_dir, {"RHAIRFE-1234": "existing"})

        # Set up a passing RFE that would normally be submitted
        jira.create("RHAIRFE-1234", "Test RFE", "Original.")
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", "Original.")
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            "priority: Major\nstatus: Ready\n---\nRevised.",
        )
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md", _review("RHAIRFE-1234"))

        env = {
            **os.environ,
            "JIRA_SERVER": jira.url,
            "JIRA_USER": "admin",
            "JIRA_TOKEN": "admin",
        }
        r = subprocess.run(
            [sys.executable, SCRIPT, "--artifacts-dir", art_dir, "--dry-run"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr

        # Snapshot must be completely untouched
        with open(snap_path) as f:
            data = yaml.safe_load(f)
        assert data["issues"] == {"RHAIRFE-1234": "existing"}

    def test_dry_run_does_not_mark_processed(self, art_dir, jira):
        """Dry-run with all-skipped entries must not mark processed."""
        snap_path = self._seed_snapshot(art_dir, {"RHAIRFE-1234": "existing"})

        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md", TASK_FM.format(rfe_id="RHAIRFE-1234"))
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"),
        )

        env = {
            **os.environ,
            "JIRA_SERVER": jira.url,
            "JIRA_USER": "admin",
            "JIRA_TOKEN": "admin",
        }
        r = subprocess.run(
            [sys.executable, SCRIPT, "--artifacts-dir", art_dir, "--dry-run"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr

        # Snapshot untouched — dry-run must not mark processed
        with open(snap_path) as f:
            data = yaml.safe_load(f)
        assert data["issues"] == {"RHAIRFE-1234": "existing"}


class TestSplitQuarantine:
    """A parent whose split failed partway must not be silently re-attempted
    by the nightly (RHAIFIRST-570): submit.py labels it split-quarantine,
    which the fetch hard filters exclude until a human removes it."""

    PARENT_TASK = (
        "---\nrfe_id: RHAIRFE-1000\ntitle: Parent RFE\n"
        "priority: Major\nstatus: Archived\n---\n\nOriginal content.\n"
    )
    PARENT_REVIEW = (
        "---\nrfe_id: RHAIRFE-1000\nscore: 6\npass: false\n"
        "recommendation: split\nfeasibility: feasible\n"
        "auto_revised: false\nneeds_attention: false\n"
        "scores:\n  what: 2\n  why: 1\n  open_to_how: 2\n"
        "  not_a_task: 1\n  right_sized: 0\n---\n\nToo big.\n"
    )

    def _labels(self, jira, key):
        issue = None
        import urllib.request as _rq

        req = _rq.Request(f"{jira.url}/rest/api/3/issue/{key}?fields=labels")
        with _rq.urlopen(req) as resp:
            issue = json.loads(resp.read())
        return set(issue["fields"].get("labels", []))

    def test_generic_split_failure_quarantines_the_parent(self, art_dir, jira):
        """An archived intermediary child with no leaves: submit detects the
        split parent, but split_submit collects zero leaf children and exits
        1 — the generic, possibly-partially-applied class."""
        jira.create("RHAIRFE-1000", "Parent RFE", "Original content.")
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1000.md", "Original content.")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", self.PARENT_TASK)
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1000-review.md", self.PARENT_REVIEW)
        _write(
            f"{art_dir}/rfe-tasks/RFE-001.md",
            "---\nrfe_id: RFE-001\ntitle: Dead-end intermediary\n"
            "priority: Major\nstatus: Archived\n"
            "parent_key: RHAIRFE-1000\n---\n\nChild content.\n",
        )

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 1

        labels = self._labels(jira, "RHAIRFE-1000")
        assert "rfe-creator-split-quarantine" in labels, r.stdout + r.stderr
        assert "rfe-creator-needs-attention" in labels

    def test_refusal_is_not_quarantined(self, art_dir, jira):
        """The leaf-cap refusal created nothing in Jira: flagged for a human,
        but a later re-attempt is cheap and safe — no quarantine."""
        jira.create("RHAIRFE-1000", "Parent RFE", "Original content.")
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1000.md", "Original content.")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", self.PARENT_TASK)
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1000-review.md", self.PARENT_REVIEW)
        for n in range(1, 9):  # 8 children > MAX_LEAF_CHILDREN
            _write(
                f"{art_dir}/rfe-tasks/RFE-{n:03d}.md",
                f"---\nrfe_id: RFE-{n:03d}\ntitle: Child {n}\n"
                "priority: Major\nstatus: Ready\n"
                "parent_key: RHAIRFE-1000\n---\n\nChild content.\n",
            )

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        labels = self._labels(jira, "RHAIRFE-1000")
        assert "rfe-creator-split-quarantine" not in labels
        assert "rfe-creator-needs-attention" in labels


class TestSplitLoopPolicy:
    """RHAIFIRST-571: per-parent failures continue, systemic ones abort
    without fan-out, and the breaker stops unclassified streaks."""

    PARENT_TASK_TPL = (
        "---\nrfe_id: {key}\ntitle: Parent {key}\n"
        "priority: Major\nstatus: Archived\n---\n\nContent {key}.\n"
    )
    PARENT_REVIEW_TPL = (
        "---\nrfe_id: {key}\nscore: 6\npass: false\n"
        "recommendation: split\nfeasibility: feasible\n"
        "auto_revised: false\nneeds_attention: false\n"
        "scores:\n  what: 2\n  why: 1\n  open_to_how: 2\n"
        "  not_a_task: 1\n  right_sized: 0\n---\n\nToo big.\n"
    )

    def _parent(self, jira, art_dir, key, broken=False):
        jira.create(key, f"Parent {key}", f"Content {key}.")
        _write(f"{art_dir}/rfe-originals/{key}.md", f"Content {key}.")
        _write(f"{art_dir}/rfe-tasks/{key}.md", self.PARENT_TASK_TPL.format(key=key))
        _write(f"{art_dir}/rfe-reviews/{key}-review.md", self.PARENT_REVIEW_TPL.format(key=key))
        n = key.split("-")[1]
        if broken:
            # Archived dead-end intermediary: split_submit collects zero
            # leaves and exits 4 (per-parent).
            _write(
                f"{art_dir}/rfe-tasks/RFE-{n}90.md",
                f"---\nrfe_id: RFE-{n}90\ntitle: Dead end\n"
                f"priority: Major\nstatus: Archived\n"
                f"parent_key: {key}\n---\n\nDead end.\n",
            )
        else:
            _write(
                f"{art_dir}/rfe-tasks/RFE-{n}01.md",
                f"---\nrfe_id: RFE-{n}01\ntitle: Child of {key}\n"
                f"priority: Major\nstatus: Ready\n"
                f"parent_key: {key}\n---\n\nChild content.\n",
            )

    def test_per_parent_failure_continues_to_the_next_parent(self, art_dir, jira):
        """Parent A fails per-parent (exit 4); parent B must still submit."""
        self._parent(jira, art_dir, "RHAIRFE-1000", broken=True)
        self._parent(jira, art_dir, "RHAIRFE-2000", broken=False)

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 1, r.stdout + r.stderr

        # B's child was created and linked despite A's failure.
        import urllib.request as _rq

        req = _rq.Request(f"{jira.url}/rest/api/3/issue/RHAIRFE-2000?fields=issuelinks,labels")
        with _rq.urlopen(req) as resp:
            parent_b = json.loads(resp.read())
        links = [
            link
            for link in parent_b["fields"].get("issuelinks", [])
            if link.get("type", {}).get("name") == "Work item split"
        ]
        assert len(links) == 1, r.stdout
        # A was quarantined, B was not.
        req = _rq.Request(f"{jira.url}/rest/api/3/issue/RHAIRFE-1000?fields=labels")
        with _rq.urlopen(req) as resp:
            labels_a = set(json.loads(resp.read())["fields"]["labels"])
        assert "rfe-creator-split-quarantine" in labels_a
        assert "rfe-creator-split-quarantine" not in set(parent_b["fields"]["labels"])

    def _stub_script(self, tmp_path, exit_code):
        """A fake split_submit that logs its invocation and exits."""
        log = tmp_path / "invocations.log"
        stub = tmp_path / "stub_split_submit.py"
        stub.write_text(
            "import sys\n"
            f"open({str(log)!r}, 'a').write(sys.argv[1] + chr(10))\n"
            f"sys.exit({exit_code})\n"
        )
        return str(stub), log

    def test_systemic_exit_aborts_without_touching_remaining_parents(
        self, art_dir, jira, tmp_path, monkeypatch
    ):
        for key in ("RHAIRFE-1000", "RHAIRFE-2000", "RHAIRFE-3000"):
            self._parent(jira, art_dir, key, broken=False)
        stub, log = self._stub_script(tmp_path, 5)
        monkeypatch.setenv("RFE_SPLIT_SUBMIT_SCRIPT", stub)

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 1
        assert "systemic Jira failure" in r.stderr
        assert len(log.read_text().splitlines()) == 1, "remaining parents were attempted"
        import urllib.request as _rq

        # The FAILING parent is quarantined: the classify wrapper spans the
        # phases, so exit 5 can land after real Jira writes (review finding).
        req = _rq.Request(f"{jira.url}/rest/api/3/issue/RHAIRFE-1000?fields=labels")
        with _rq.urlopen(req) as resp:
            labels = set(json.loads(resp.read())["fields"]["labels"])
        assert "rfe-creator-split-quarantine" in labels
        fm = _read_frontmatter(f"{art_dir}/rfe-reviews/RHAIRFE-1000-review.md")
        assert fm["error"] == "split_submit_failed: exit 5"
        # The never-attempted parents get a LOCAL record only — no Jira flags
        # (avoiding the fan-out is the point of the abort), but the report
        # must not count them as successful splits.
        for key in ("RHAIRFE-2000", "RHAIRFE-3000"):
            fm = _read_frontmatter(f"{art_dir}/rfe-reviews/{key}-review.md")
            assert fm["error"].startswith("split_not_attempted:")
            req = _rq.Request(f"{jira.url}/rest/api/3/issue/{key}?fields=labels")
            with _rq.urlopen(req) as resp:
                labels = set(json.loads(resp.read())["fields"]["labels"])
            assert "rfe-creator-split-quarantine" not in labels
            assert "rfe-creator-needs-attention" not in labels

    def test_abort_report_counts_skipped_parents_failed_not_split(
        self, art_dir, jira, tmp_path, monkeypatch
    ):
        """The final report after an abort must not claim successful splits
        for parents that were never attempted (review finding: reproduced
        results.split == N with zero splits in Jira)."""
        for key in ("RHAIRFE-1000", "RHAIRFE-2000", "RHAIRFE-3000"):
            self._parent(jira, art_dir, key, broken=False)
        stub, log = self._stub_script(tmp_path, 5)
        monkeypatch.setenv("RFE_SPLIT_SUBMIT_SCRIPT", stub)

        r = _run_submit(
            art_dir,
            jira.url,
            extra_flags=["--generate-report", "--report-timestamp", "20260831-180000"],
        )
        assert r.returncode == 1

        report_path = f"{art_dir}/auto-fix-runs/20260831-180000.yaml"
        assert os.path.exists(report_path), "abort took the report with it"
        with open(report_path) as f:
            report = yaml.safe_load(f)
        assert report["results"]["split"] == 0
        assert report["results"]["failed"] == 3
        by_id = {e["id"]: e for e in report["per_rfe"]}
        assert by_id["RHAIRFE-1000"]["failed_reason"].startswith(
            "Split submission failed"
        ) or "exit 5" in str(by_id["RHAIRFE-1000"].get("failed_reason", ""))
        assert "split_not_attempted" in by_id["RHAIRFE-2000"]["failed_reason"]

    def test_breaker_requires_consecutive_failures(self, art_dir, jira, tmp_path, monkeypatch):
        """A classified outcome between two unclassified failures proves the
        classifier is alive — the streak resets (review finding)."""
        for key in ("RHAIRFE-1000", "RHAIRFE-2000", "RHAIRFE-3000"):
            self._parent(jira, art_dir, key, broken=False)
        log = tmp_path / "invocations.log"
        stub = tmp_path / "stub_split_submit.py"
        # The middle outcome is a CLASSIFIED refusal (2), not a success:
        # this is what discriminates "reset on any classified outcome" from
        # "reset on success only" — a healthy refusal between two signal
        # deaths proves the classifier is alive.
        stub.write_text(
            "import sys\n"
            f"open({str(log)!r}, 'a').write(sys.argv[1] + chr(10))\n"
            "sys.exit(2 if sys.argv[1] == 'RHAIRFE-2000' else 7)\n"
        )
        monkeypatch.setenv("RFE_SPLIT_SUBMIT_SCRIPT", str(stub))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 1
        assert "consecutive unclassified" not in r.stderr, "breaker tripped on a broken streak"
        assert len(log.read_text().splitlines()) == 3

    def test_seam_is_inert_outside_pytest(self, art_dir, jira, tmp_path):
        """RFE_SPLIT_SUBMIT_SCRIPT must not swap the script in production
        (review finding): without PYTEST_CURRENT_TEST the real script runs."""
        self._parent(jira, art_dir, "RHAIRFE-1000", broken=True)
        stub, log = self._stub_script(tmp_path, 0)

        env = {
            **os.environ,
            "JIRA_SERVER": jira.url,
            "JIRA_USER": "admin",
            "JIRA_TOKEN": "admin",
            "RFE_SPLIT_SUBMIT_SCRIPT": str(stub),
        }
        env.pop("PYTEST_CURRENT_TEST", None)
        r = subprocess.run(
            [sys.executable, SCRIPT, "--artifacts-dir", art_dir],
            capture_output=True,
            text=True,
            env=env,
        )
        assert not log.exists(), "seam activated outside pytest"
        # The real script ran and hit the dead-end shape (exit 4 recorded).
        assert r.returncode == 1

    def test_breaker_trips_after_two_consecutive_unclassified_failures(
        self, art_dir, jira, tmp_path, monkeypatch
    ):
        for key in ("RHAIRFE-1000", "RHAIRFE-2000", "RHAIRFE-3000"):
            self._parent(jira, art_dir, key, broken=False)
        stub, log = self._stub_script(tmp_path, 7)
        monkeypatch.setenv("RFE_SPLIT_SUBMIT_SCRIPT", stub)

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 1
        assert "2 consecutive unclassified split failures" in r.stderr
        assert len(log.read_text().splitlines()) == 2, "breaker did not stop the loop"

    def test_dead_jira_fails_preflight_with_one_message_and_no_fanout(
        self, art_dir, tmp_path, monkeypatch
    ):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Deny(BaseHTTPRequestHandler):
            def _deny(self):
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"{}")

            do_GET = _deny  # noqa: N815
            do_POST = _deny  # noqa: N815
            do_PUT = _deny  # noqa: N815

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Deny)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}"
        stub, log = self._stub_script(tmp_path, 0)
        monkeypatch.setenv("RFE_SPLIT_SUBMIT_SCRIPT", stub)
        try:
            _write(
                f"{art_dir}/rfe-tasks/RHAIRFE-1000.md",
                self.PARENT_TASK_TPL.format(key="RHAIRFE-1000"),
            )
            _write(f"{art_dir}/rfe-originals/RHAIRFE-1000.md", "Content RHAIRFE-1000.")
            _write(
                f"{art_dir}/rfe-reviews/RHAIRFE-1000-review.md",
                self.PARENT_REVIEW_TPL.format(key="RHAIRFE-1000"),
            )
            _write(
                f"{art_dir}/rfe-tasks/RFE-100001.md",
                "---\nrfe_id: RFE-100001\ntitle: Child\n"
                "priority: Major\nstatus: Ready\n"
                "parent_key: RHAIRFE-1000\n---\n\nChild content.\n",
            )
            r = _run_submit(art_dir, url)
        finally:
            server.shutdown()

        assert r.returncode == 1
        assert "Jira preflight failed" in r.stderr
        assert not log.exists(), "split_submit was invoked despite a dead preflight"


class TestSplitConflictDetection:
    """Integration test: split_submit.py detects parent conflict."""

    SPLIT_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "split_submit.py")

    PARENT_TASK = (
        "---\nrfe_id: RHAIRFE-1000\ntitle: Parent RFE\n"
        "priority: Major\nstatus: Archived\n---\n\nOriginal content.\n"
    )
    CHILD_TASK = (
        "---\nrfe_id: RFE-001\ntitle: Child RFE\n"
        "priority: Major\nstatus: Ready\n"
        "parent_key: RHAIRFE-1000\n---\n\nChild content.\n"
    )

    def test_conflict_exits_code_3(self, art_dir, jira):
        """Parent modified in Jira since fetch → exit code 3."""
        # Jira has different content than our original
        jira.create("RHAIRFE-1000", "Parent RFE", "Edited by someone.")
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1000.md", "Original content.")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", self.PARENT_TASK)
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", self.CHILD_TASK)

        env = {
            **os.environ,
            "JIRA_SERVER": jira.url,
            "JIRA_USER": "admin",
            "JIRA_TOKEN": "admin",
        }
        r = subprocess.run(
            [sys.executable, self.SPLIT_SCRIPT, "RHAIRFE-1000", "--artifacts-dir", art_dir],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 3
        assert "modified in Jira since fetch" in r.stderr

    def test_no_conflict_proceeds(self, art_dir, jira):
        """Parent unchanged in Jira → no conflict exit."""
        body = "Original content."
        jira.create("RHAIRFE-1000", "Parent RFE", body)
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1000.md", body)
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", self.PARENT_TASK)
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", self.CHILD_TASK)

        env = {
            **os.environ,
            "JIRA_SERVER": jira.url,
            "JIRA_USER": "admin",
            "JIRA_TOKEN": "admin",
        }
        r = subprocess.run(
            [sys.executable, self.SPLIT_SCRIPT, "RHAIRFE-1000", "--artifacts-dir", art_dir],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode != 3

    def test_submit_handles_conflict_refusal(self, art_dir, jira):
        """submit.py handles split conflict (exit 3) gracefully."""
        # Parent in Jira has different content than our original
        jira.create("RHAIRFE-1000", "Parent RFE", "Edited by someone.")
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1000.md", "Original content.")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", self.PARENT_TASK)
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", self.CHILD_TASK)
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1000-review.md", _review("RHAIRFE-1000"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0  # continues after refusal
        assert "Jira conflict" in r.stdout

        # Check review frontmatter was updated
        fm = _read_frontmatter(f"{art_dir}/rfe-reviews/RHAIRFE-1000-review.md")
        assert fm["needs_attention"] is True
        assert "modified in Jira" in fm["needs_attention_reason"]
        assert fm["error"] == "split_refused: jira conflict"

        # Check needs-attention label was added
        issue = jira.get("RHAIRFE-1000")
        assert "rfe-creator-needs-attention" in issue["fields"]["labels"]


class TestSplitFieldInheritance:
    """Integration test: children inherit parent's components and labels."""

    SPLIT_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "split_submit.py")

    PARENT_TASK = (
        "---\nrfe_id: RHAIRFE-1000\ntitle: Parent RFE\n"
        "priority: Major\nstatus: Archived\n---\n\nParent content.\n"
    )
    CHILD_TASK_TPL = (
        "---\nrfe_id: RFE-{num:03d}\ntitle: Child RFE {num}\n"
        "priority: Major\nstatus: Ready\n"
        "parent_key: RHAIRFE-1000\n---\n\nChild {num} content.\n"
    )

    def test_children_inherit_components_and_labels(self, art_dir, jira):
        """Split children get parent's components and non-automation labels."""
        jira.create(
            "RHAIRFE-1000",
            "Parent RFE",
            "Parent content.",
            labels=["3.5-candidate", "rfe-creator-auto-revised"],
            components=["AI Safety"],
        )
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1000.md", "Parent content.")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", self.PARENT_TASK)
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", self.CHILD_TASK_TPL.format(num=1))
        _write(f"{art_dir}/rfe-tasks/RFE-002.md", self.CHILD_TASK_TPL.format(num=2))

        env = {
            **os.environ,
            "JIRA_SERVER": jira.url,
            "JIRA_USER": "admin",
            "JIRA_TOKEN": "admin",
        }
        r = subprocess.run(
            [sys.executable, self.SPLIT_SCRIPT, "RHAIRFE-1000", "--artifacts-dir", art_dir],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr

        # Find the created children
        issues = jira.search("project = RHAIRFE", fields="key,labels,components")
        children = [i for i in issues if i["key"] != "RHAIRFE-1000"]
        assert len(children) == 2

        for child in children:
            child_detail = jira.get(child["key"])
            labels = child_detail["fields"]["labels"]
            components = [c["name"] for c in child_detail["fields"].get("components", [])]

            # Should inherit non-automation label
            assert "3.5-candidate" in labels
            # Should NOT inherit automation labels
            assert "rfe-creator-auto-revised" not in labels
            # Should have its own automation labels
            assert "rfe-creator-auto-created" in labels
            assert "rfe-creator-split-result" in labels
            # Should inherit component
            assert "AI Safety" in components

    def test_children_inherit_jira_parent(self, art_dir, jira):
        """Split children inherit the Jira parent link (e.g. RHAISTRAT)."""
        # Create RHAISTRAT parent, then RHAIRFE with epic_link to set parent
        jira.request(
            "POST",
            "/api/admin/import",
            {
                "issues": [
                    {
                        "key": "RHAISTRAT-100",
                        "summary": "Strategy Feature",
                        "project": "RHAISTRAT",
                        "issue_type": "Feature",
                        "description": "Strategy content.",
                    },
                    {
                        "key": "RHAIRFE-2000",
                        "summary": "Parent RFE",
                        "project": "RHAIRFE",
                        "issue_type": "Feature Request",
                        "description": "Parent content.",
                        "epic_link": "RHAISTRAT-100",
                    },
                ]
            },
        )

        _write(f"{art_dir}/rfe-originals/RHAIRFE-2000.md", "Parent content.")
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-2000.md",
            "---\nrfe_id: RHAIRFE-2000\ntitle: Parent RFE\n"
            "priority: Major\nstatus: Archived\n---\n\nParent content.\n",
        )
        _write(
            f"{art_dir}/rfe-tasks/RFE-001.md",
            "---\nrfe_id: RFE-001\ntitle: Child RFE 1\n"
            "priority: Major\nstatus: Ready\n"
            "parent_key: RHAIRFE-2000\n---\n\nChild 1 content.\n",
        )
        _write(
            f"{art_dir}/rfe-tasks/RFE-002.md",
            "---\nrfe_id: RFE-002\ntitle: Child RFE 2\n"
            "priority: Major\nstatus: Ready\n"
            "parent_key: RHAIRFE-2000\n---\n\nChild 2 content.\n",
        )

        env = {
            **os.environ,
            "JIRA_SERVER": jira.url,
            "JIRA_USER": "admin",
            "JIRA_TOKEN": "admin",
        }
        r = subprocess.run(
            [sys.executable, self.SPLIT_SCRIPT, "RHAIRFE-2000", "--artifacts-dir", art_dir],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr

        # Find the created children
        issues = jira.search("project = RHAIRFE", fields="key,parent")
        children = [i for i in issues if i["key"] not in ("RHAIRFE-2000",)]
        assert len(children) == 2

        for child in children:
            child_detail = jira.get(child["key"])
            parent = child_detail["fields"].get("parent")
            assert parent is not None, f"{child['key']} should inherit parent RHAISTRAT-100"
            assert parent["key"] == "RHAISTRAT-100"

    def test_no_parent_when_parent_has_none(self, art_dir, jira):
        """Children don't get a parent field if the split parent has none."""
        jira.create("RHAIRFE-3000", "Parent No Strat", "Content.")
        _write(f"{art_dir}/rfe-originals/RHAIRFE-3000.md", "Content.")
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-3000.md",
            "---\nrfe_id: RHAIRFE-3000\ntitle: Parent No Strat\n"
            "priority: Major\nstatus: Archived\n---\n\nContent.\n",
        )
        _write(
            f"{art_dir}/rfe-tasks/RFE-001.md",
            "---\nrfe_id: RFE-001\ntitle: Child RFE 1\n"
            "priority: Major\nstatus: Ready\n"
            "parent_key: RHAIRFE-3000\n---\n\nChild content.\n",
        )

        env = {
            **os.environ,
            "JIRA_SERVER": jira.url,
            "JIRA_USER": "admin",
            "JIRA_TOKEN": "admin",
        }
        r = subprocess.run(
            [sys.executable, self.SPLIT_SCRIPT, "RHAIRFE-3000", "--artifacts-dir", art_dir],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr

        issues = jira.search("project = RHAIRFE", fields="key,parent")
        children = [i for i in issues if i["key"] != "RHAIRFE-3000"]
        assert len(children) == 1

        child_detail = jira.get(children[0]["key"])
        parent = child_detail["fields"].get("parent")
        assert parent is None, "Child should not have a parent field"


class TestReplayIdempotency:
    """Submit replay must be safe: no duplicate creates, no re-updates."""

    def test_replay_new_rfe_no_duplicate(self, art_dir, jira):
        """Create RFE, replay → no duplicate issue in Jira."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md", _review("RFE-001"))

        # First run: creates the issue + renames file
        r1 = _run_submit(art_dir, jira.url)
        assert r1.returncode == 0, r1.stderr
        assert "Created" in r1.stdout

        issues_after_first = jira.search("project = RHAIRFE")
        assert len(issues_after_first) == 1
        key = issues_after_first[0]["key"]

        # File should be renamed and marked Submitted
        assert os.path.exists(f"{art_dir}/rfe-tasks/{key}.md")
        assert not os.path.exists(f"{art_dir}/rfe-tasks/RFE-001.md")
        fm = _read_frontmatter(f"{art_dir}/rfe-tasks/{key}.md")
        assert fm["status"] == "Submitted"

        # Second run (replay): should skip, no new issue
        r2 = _run_submit(art_dir, jira.url)
        assert r2.returncode == 0, r2.stderr
        assert "Created" not in r2.stdout

        issues_after_second = jira.search("project = RHAIRFE")
        assert len(issues_after_second) == 1  # No duplicate

    def test_replay_existing_rfe_no_resubmit(self, art_dir, jira):
        """Update existing RFE, replay → second run skips it."""
        jira.create("RHAIRFE-1234", "Test RFE", "Original.")
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", "Original.")
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            "priority: Major\nstatus: Ready\n---\nRevised content.",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            _review("RHAIRFE-1234", auto_revised="true"),
        )

        # First run: updates the issue
        r1 = _run_submit(art_dir, jira.url)
        assert r1.returncode == 0, r1.stderr
        assert "Updated" in r1.stdout

        fm = _read_frontmatter(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md")
        assert fm["status"] == "Submitted"

        # Second run (replay): should skip
        r2 = _run_submit(art_dir, jira.url)
        assert r2.returncode == 0, r2.stderr
        assert "Updated" not in r2.stdout
        assert "Created" not in r2.stdout

    def test_replay_after_partial_failure(self, art_dir, jira):
        """First RFE succeeds, second fails → replay only submits second."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md", _review("RFE-001"))
        _write(
            f"{art_dir}/rfe-tasks/RFE-002.md",
            TASK_FM.format(rfe_id="RFE-002").replace("title: Test RFE", "title: Second RFE"),
        )
        _write(f"{art_dir}/rfe-reviews/RFE-002-review.md", _review("RFE-002"))

        # First run: both succeed
        r1 = _run_submit(art_dir, jira.url)
        assert r1.returncode == 0, r1.stderr

        issues = jira.search("project = RHAIRFE")
        assert len(issues) == 2

        # Both renamed and marked Submitted
        assert not os.path.exists(f"{art_dir}/rfe-tasks/RFE-001.md")
        assert not os.path.exists(f"{art_dir}/rfe-tasks/RFE-002.md")

        # Replay: nothing to do
        r2 = _run_submit(art_dir, jira.url)
        assert r2.returncode == 0, r2.stderr
        assert "Created" not in r2.stdout

        # Still only 2 issues
        issues_final = jira.search("project = RHAIRFE")
        assert len(issues_final) == 2

    def test_replay_label_only_skipped(self, art_dir, jira):
        """Label-only action, replay → second run skips it."""
        body = "Same content.\n"
        jira.create("RHAIRFE-1234", "Test RFE", body)
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", body)
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            f"priority: Major\nstatus: Ready\n---\n{body}",
        )
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md", _review("RHAIRFE-1234"))

        # First run: label-only
        r1 = _run_submit(art_dir, jira.url)
        assert r1.returncode == 0, r1.stderr
        assert "Labels" in r1.stdout

        fm = _read_frontmatter(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md")
        assert fm["status"] == "Submitted"

        # Second run (replay): should skip
        r2 = _run_submit(art_dir, jira.url)
        assert r2.returncode == 0, r2.stderr
        assert "Labels" not in r2.stdout
        assert "Updated" not in r2.stdout


class TestSplitChildSnapshot:
    """Split children's content hashes must be recorded in the snapshot."""

    PARENT_TASK = (
        "---\nrfe_id: RHAIRFE-1000\ntitle: Parent RFE\n"
        "priority: Major\nstatus: Archived\n---\n\nParent content.\n"
    )
    CHILD_TASK_TPL = (
        "---\nrfe_id: RFE-{num:03d}\ntitle: Child RFE {num}\n"
        "priority: Major\nstatus: Ready\n"
        "parent_key: RHAIRFE-1000\n---\n\nChild {num} content.\n"
    )

    def _seed_snapshot(self, art_dir, issues):
        snap_dir = os.path.join(art_dir, "auto-fix-runs")
        os.makedirs(snap_dir, exist_ok=True)
        snap = {
            "query_timestamp": "2026-04-01T00:00:00Z",
            "timestamp": "2026-04-01T00:00:01Z",
            "issues": issues,
        }
        path = os.path.join(snap_dir, "issue-snapshot-20260401-000000.yaml")
        with open(path, "w") as f:
            yaml.dump(snap, f, default_flow_style=False, sort_keys=False)
        return path

    def _setup_split(self, art_dir, jira, num_children=2):
        """Set up a split parent with children and a seeded snapshot."""
        jira.create("RHAIRFE-1000", "Parent RFE", "Parent content.")
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1000.md", "Parent content.")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", self.PARENT_TASK)
        for i in range(1, num_children + 1):
            _write(f"{art_dir}/rfe-tasks/RFE-{i:03d}.md", self.CHILD_TASK_TPL.format(num=i))
        return self._seed_snapshot(art_dir, {"RHAIRFE-1000": "parent-hash"})

    def test_split_child_hashes_in_snapshot(self, art_dir, jira):
        """Split children's hashes appear in the snapshot after submit."""
        snap_path = self._setup_split(art_dir, jira)

        # Add a regular RFE so Phase 2 also runs
        _write(f"{art_dir}/rfe-tasks/RFE-099.md", TASK_FM.format(rfe_id="RFE-099"))
        _write(f"{art_dir}/rfe-reviews/RFE-099-review.md", _review("RFE-099"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "split-child hashes" in r.stdout

        with open(snap_path) as f:
            data = yaml.safe_load(f)

        # Find the child keys (RHAIRFE-NNNN created by split_submit.py)
        all_issues = jira.search("project = RHAIRFE")
        child_keys = sorted([i["key"] for i in all_issues if i["key"] != "RHAIRFE-1000"])
        # At least the 2 split children + 1 regular RFE
        assert len(child_keys) >= 3

        # Each split child must have a valid hash in the snapshot
        split_child_entries = {
            k: v
            for k, v in data["issues"].items()
            if k != "RHAIRFE-1000" and isinstance(v, dict) and len(v.get("hash", "")) == 64
        }
        assert len(split_child_entries) >= 2
        for key, entry in split_child_entries.items():
            assert entry["processed"] is True

    def test_splits_only_early_return(self, art_dir, jira):
        """Splits-only run (no regular RFEs) completes and records hashes."""
        snap_path = self._setup_split(art_dir, jira)

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "split-child hashes" in r.stdout
        assert "Done. Index rebuilt" in r.stdout

        with open(snap_path) as f:
            data = yaml.safe_load(f)

        # Split children must have valid hashes
        child_entries = {
            k: v for k, v in data["issues"].items() if k != "RHAIRFE-1000" and isinstance(v, dict)
        }
        assert len(child_entries) >= 2
        for key, entry in child_entries.items():
            assert len(entry["hash"]) == 64, f"{key} hash should be SHA256"
            assert entry["processed"] is True

    def test_dry_run_skips_split_child_hashes(self, art_dir, jira):
        """Dry-run must not update the snapshot with split-child hashes."""
        snap_path = self._setup_split(art_dir, jira)

        env = {
            **os.environ,
            "JIRA_SERVER": jira.url,
            "JIRA_USER": "admin",
            "JIRA_TOKEN": "admin",
        }
        r = subprocess.run(
            [sys.executable, SCRIPT, "--artifacts-dir", art_dir, "--dry-run"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr

        # Snapshot must be completely untouched
        with open(snap_path) as f:
            data = yaml.safe_load(f)
        assert data["issues"] == {"RHAIRFE-1000": "parent-hash"}


class TestApprovedTransition:
    def test_existing_rfe_transitions_to_approved(self, art_dir, jira):
        """--auto-approve + passing review → existing RFE moved to Approved."""
        body = "Original."
        jira.create("RHAIRFE-1234", "Test RFE", body)
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", body)
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            "priority: Major\nstatus: Ready\n---\nRevised.",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            _review("RHAIRFE-1234", auto_revised="true"),
        )

        r = _run_submit(art_dir, jira.url, ["--auto-approve"])
        assert r.returncode == 0, r.stderr
        assert "Transitioned to Approved" in r.stdout

        issue = jira.get("RHAIRFE-1234")
        assert issue["fields"]["status"]["name"] == "Approved"

        comments = jira.request("GET", "/rest/api/3/issue/RHAIRFE-1234/comment")
        bodies = [c["body"] for c in comments["comments"]]
        assert any("automatically transitioned to Approved" in json.dumps(b) for b in bodies)

    def test_new_rfe_transitions_to_approved(self, art_dir, jira):
        """--auto-approve + new RFE with passing review → Approved."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md", _review("RFE-001"))

        r = _run_submit(art_dir, jira.url, ["--auto-approve"])
        assert r.returncode == 0, r.stderr
        assert "Transitioned to Approved" in r.stdout

        issues = jira.search("project = RHAIRFE")
        assert len(issues) == 1
        issue = jira.get(issues[0]["key"])
        assert issue["fields"]["status"]["name"] == "Approved"

    def test_failing_review_not_transitioned(self, art_dir, jira):
        """--auto-approve + failing review → status unchanged."""
        body = "Original."
        jira.create("RHAIRFE-1234", "Test RFE", body)
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

        r = _run_submit(art_dir, jira.url, ["--auto-approve"])
        assert r.returncode == 0, r.stderr
        assert "Transitioned to Approved" not in r.stdout

        issue = jira.get("RHAIRFE-1234")
        assert issue["fields"]["status"]["name"] == "New"

    def test_already_approved_skips_transition(self, art_dir, jira):
        """--auto-approve + already Approved → skips transition."""
        body = "Original."
        jira.create("RHAIRFE-1234", "Test RFE", body)
        # Pre-transition to Approved
        transitions = jira.request("GET", "/rest/api/3/issue/RHAIRFE-1234/transitions")
        approve_id = next(
            t["id"] for t in transitions["transitions"] if t["to"]["name"] == "Approved"
        )
        jira.request(
            "POST", "/rest/api/3/issue/RHAIRFE-1234/transitions", {"transition": {"id": approve_id}}
        )

        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", body)
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            "priority: Major\nstatus: Ready\n---\nRevised.",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            _review("RHAIRFE-1234", auto_revised="true"),
        )

        r = _run_submit(art_dir, jira.url, ["--auto-approve"])
        assert r.returncode == 0, r.stderr
        assert "Already Approved, skipping transition" in r.stdout
        assert "Transitioned to Approved" not in r.stdout

    def test_no_flag_no_transition(self, art_dir, jira):
        """Without --auto-approve → no transition even with passing review."""
        body = "Original."
        jira.create("RHAIRFE-1234", "Test RFE", body)
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", body)
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
            "priority: Major\nstatus: Ready\n---\nRevised.",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            _review("RHAIRFE-1234", auto_revised="true"),
        )

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "Transitioned to Approved" not in r.stdout

        issue = jira.get("RHAIRFE-1234")
        assert issue["fields"]["status"]["name"] == "New"


# ── Initiative-specific fixtures and helpers ──────────────────────────────────


@pytest.fixture
def init_art_dir(tmp_path):
    """Create a minimal artifacts directory for initiative submissions."""
    for d in ["initiatives", "initiative-reviews", "initiative-originals"]:
        os.makedirs(tmp_path / d)
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield str(tmp_path)
    os.chdir(orig)


def _run_initiative_submit(artifacts_dir, server_url, extra_flags=None):
    """Run submit.py --type initiative against the jira-emulator."""
    env = {
        **os.environ,
        "JIRA_SERVER": server_url,
        "JIRA_USER": "admin",
        "JIRA_TOKEN": "admin",
    }
    cmd = [sys.executable, SCRIPT, "--type", "initiative", "--artifacts-dir", artifacts_dir]
    if extra_flags:
        cmd.extend(extra_flags)
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


INITIATIVE_TASK_FM = """\
---
initiative_id: {init_id}
title: Test Initiative
priority: Major
status: Ready
---

## Engineering Objective

Consolidate inference backends under a unified API.

## Success Criteria

- Single API surface for all backends
"""

INITIATIVE_REVIEW_FM = """\
---
initiative_id: {init_id}
score: 9
pass: true
recommendation: submit
feasibility: feasible
alignment: {alignment}
auto_revised: false
needs_attention: false
scores:
  what: 2
  why: 2
  scope: 2
  open_to_how: 2
  right_sized: 1
---

## Assessor Feedback
Looks good.
"""


# ── Initiative Alignment Label Tests ──────────────────────────────────────────


class TestAlignmentLabels:
    """Initiative-specific: alignment field maps to alignment labels."""

    def test_strong_alignment_label_applied(self, init_art_dir, jira):
        """alignment=strong → initiative-alignment-strong label."""
        _write(
            f"{init_art_dir}/initiatives/INIT-001.md",
            INITIATIVE_TASK_FM.format(init_id="INIT-001"),
        )
        _write(
            f"{init_art_dir}/initiative-reviews/INIT-001-review.md",
            INITIATIVE_REVIEW_FM.format(init_id="INIT-001", alignment="strong"),
        )

        r = _run_initiative_submit(init_art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        issues = jira.search("project = RHOAIENG")
        assert len(issues) == 1
        issue = jira.get(issues[0]["key"])
        assert "initiative-alignment-strong" in issue["fields"]["labels"]
        assert "initiative-alignment-partial" not in issue["fields"]["labels"]
        assert "initiative-alignment-weak" not in issue["fields"]["labels"]

    def test_partial_alignment_label_applied(self, init_art_dir, jira):
        """alignment=partial → initiative-alignment-partial label."""
        _write(
            f"{init_art_dir}/initiatives/INIT-001.md",
            INITIATIVE_TASK_FM.format(init_id="INIT-001"),
        )
        _write(
            f"{init_art_dir}/initiative-reviews/INIT-001-review.md",
            INITIATIVE_REVIEW_FM.format(init_id="INIT-001", alignment="partial"),
        )

        r = _run_initiative_submit(init_art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        issues = jira.search("project = RHOAIENG")
        assert len(issues) == 1
        issue = jira.get(issues[0]["key"])
        assert "initiative-alignment-partial" in issue["fields"]["labels"]
        assert "initiative-alignment-strong" not in issue["fields"]["labels"]

    def test_weak_alignment_label_applied(self, init_art_dir, jira):
        """alignment=weak → initiative-alignment-weak label."""
        _write(
            f"{init_art_dir}/initiatives/INIT-001.md",
            INITIATIVE_TASK_FM.format(init_id="INIT-001"),
        )
        _write(
            f"{init_art_dir}/initiative-reviews/INIT-001-review.md",
            INITIATIVE_REVIEW_FM.format(init_id="INIT-001", alignment="weak"),
        )

        r = _run_initiative_submit(init_art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        issues = jira.search("project = RHOAIENG")
        assert len(issues) == 1
        issue = jira.get(issues[0]["key"])
        assert "initiative-alignment-weak" in issue["fields"]["labels"]
        assert "initiative-alignment-strong" not in issue["fields"]["labels"]

    def test_no_alignment_no_label(self, init_art_dir, jira):
        """No alignment field → no alignment label applied."""
        review_no_alignment = """\
---
initiative_id: INIT-001
score: 9
pass: true
recommendation: submit
feasibility: feasible
auto_revised: false
needs_attention: false
scores:
  what: 2
  why: 2
  scope: 2
  open_to_how: 2
  right_sized: 1
---

## Assessor Feedback
Looks good.
"""
        _write(
            f"{init_art_dir}/initiatives/INIT-001.md",
            INITIATIVE_TASK_FM.format(init_id="INIT-001"),
        )
        _write(
            f"{init_art_dir}/initiative-reviews/INIT-001-review.md",
            review_no_alignment,
        )

        r = _run_initiative_submit(init_art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        issues = jira.search("project = RHOAIENG")
        assert len(issues) == 1
        issue = jira.get(issues[0]["key"])
        labels = issue["fields"]["labels"]
        assert not any("alignment" in label for label in labels)


class TestLocalParentKey:
    """A local parent id must never be sent to Jira as a parent field.

    Auto-fix can split an RFE that has not been submitted yet, in which case
    the children carry `parent_key: RFE-NNN`. That parent does not exist in
    Jira, so forwarding it makes the create fail with a 400 and aborts submit.

    The emulator silently drops an unknown parent instead of rejecting it, so
    it cannot reproduce that 400 — the assertion that pins the fix is the
    skip message, not the return code.
    """

    CHILD_TASK_TPL = (
        "---\nrfe_id: RFE-{num:03d}\ntitle: Child RFE {num}\n"
        "priority: Major\nstatus: Ready\nparent_key: RFE-001\n---\n\nChild {num} content.\n"
    )

    def test_children_of_local_parent_created_without_parent(self, art_dir, jira):
        """Local RFE-001 parent → children created standalone, submit succeeds."""
        _write(
            f"{art_dir}/rfe-tasks/RFE-001.md",
            TASK_FM.format(rfe_id="RFE-001").replace("status: Ready", "status: Archived"),
        )
        for num in (2, 3):
            child_id = f"RFE-{num:03d}"
            _write(f"{art_dir}/rfe-tasks/{child_id}.md", self.CHILD_TASK_TPL.format(num=num))
            _write(f"{art_dir}/rfe-reviews/{child_id}-review.md", _review(child_id))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        issues = jira.search("project = RHAIRFE")
        assert len(issues) == 2
        for issue in issues:
            assert jira.get(issue["key"])["fields"].get("parent") is None
        assert "is not in Jira" in r.stdout


# ── PR-3c-iii: pre-update binding verification and the overridden-project suite ──────────────

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FETCH_SCRIPT = os.path.join(REPO_ROOT, "scripts", "fetch_issue.py")


def _env_for(jira, **extra):
    """A subprocess environment against the emulator: the developer's RFE_CREATOR_* seam and
    the headless markers stay out, ``extra`` (an override, a marker) goes in."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("RFE_CREATOR_") and k not in ("CI", "GITHUB_ACTIONS")
    }
    env.update(JIRA_SERVER=jira.url, JIRA_USER="admin", JIRA_TOKEN="admin")
    env.update(extra)
    return env


def _run_submit_with(env, artifacts_dir, *flags):
    cmd = [sys.executable, SCRIPT, "--artifacts-dir", artifacts_dir, *flags]
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def _description_text(issue):
    desc = issue["fields"]["description"]
    if isinstance(desc, dict):
        texts = []
        for node in desc.get("content", []):
            for child in node.get("content", []):
                if child.get("type") == "text":
                    texts.append(child["text"])
        return " ".join(texts)
    return desc or ""


def _histories(jira, key):
    return jira.get(key).get("changelog", {}).get("histories", [])


class TestPreUpdateBindingVerification:
    """Before an existing issue is written, the fetch submit.py performs for conflict detection
    also requests project and issuetype and the pair is verified against the resolved binding:
    an issue behind an owned key whose (project, issue type) is another pair is a plan SKIP —
    no PUT, no label change, not marked processed — reported like every other skip."""

    def _existing(self, art_dir, review=None, original_labels=""):
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", "Original.")
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\npriority: Major\nstatus: Ready\n"
            f"type: rfe\ntracker_ref: RHAIRFE-1234\n{original_labels}---\nRevised.",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            review or _review("RHAIRFE-1234", auto_revised="true"),
        )

    def test_an_epic_behind_an_rfe_key_is_skipped_not_updated(self, art_dir, jira):
        jira.create("RHAIRFE-1234", "Test RFE", "Original.", issue_type="Epic")
        self._existing(art_dir)
        snap_dir = os.path.join(art_dir, "auto-fix-runs")
        os.makedirs(snap_dir, exist_ok=True)
        snap_path = os.path.join(snap_dir, "issue-snapshot-20260401-000000.yaml")
        with open(snap_path, "w") as f:
            yaml.dump(
                {
                    "query_timestamp": "2026-04-01T00:00:00Z",
                    "timestamp": "2026-04-01T00:00:01Z",
                    "issues": {"RHAIRFE-1234": {"hash": "hash-a", "processed": False}},
                },
                f,
                default_flow_style=False,
                sort_keys=False,
            )

        r = _run_submit_with(_env_for(jira), art_dir)
        assert r.returncode == 0, r.stderr
        assert "Traceback" not in r.stderr
        assert (
            "Reason: binding mismatch — RHAIRFE-1234 is (RHAIRFE, Epic) in Jira but type rfe "
            "binds (RHAIRFE, Feature Request)"
        ) in r.stdout
        assert "RHAIRFE-1234: Skipping — binding mismatch" in r.stdout
        assert "Updated" not in r.stdout
        assert "Original" in _description_text(jira.get("RHAIRFE-1234"))
        assert _histories(jira, "RHAIRFE-1234") == []
        assert _read_frontmatter(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md")["status"] == "Ready"
        with open(snap_path) as f:
            data = yaml.safe_load(f)
        assert data["issues"]["RHAIRFE-1234"] == {"hash": "hash-a", "processed": False}

    def test_the_reject_paths_label_removal_is_verified_too(self, art_dir, jira):
        jira.create(
            "RHAIRFE-1234",
            "Test RFE",
            "Original.",
            labels=["rfe-creator-autofix-rubric-pass"],
            issue_type="Epic",
        )
        self._existing(
            art_dir,
            review=REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"),
            original_labels="original_labels:\n- rfe-creator-autofix-rubric-pass\n",
        )
        r = _run_submit_with(_env_for(jira), art_dir)
        assert r.returncode == 0, r.stderr
        assert "binding mismatch" in r.stdout and "Removed labels" not in r.stdout
        assert "rfe-creator-autofix-rubric-pass" in jira.get("RHAIRFE-1234")["fields"]["labels"]
        assert _histories(jira, "RHAIRFE-1234") == []

    def test_a_matching_pair_updates_exactly_as_before(self, art_dir, jira):
        jira.create("RHAIRFE-1234", "Test RFE", "Original.")
        self._existing(art_dir)
        r = _run_submit_with(_env_for(jira), art_dir)
        assert r.returncode == 0, r.stderr
        assert (
            "TYPE RESOLVED" not in r.stderr and "Traceback" not in r.stderr
        )  # legacy default: silent
        assert "RHAIRFE-1234: Updated" in r.stdout
        assert "Revised" in _description_text(jira.get("RHAIRFE-1234"))


class TestTheRemoteKeyIsTheTrackerRef:
    """One remote key per task (design §5): the frontmatter tracker_ref when present, else the
    id. rfe_id RHAIRFE-1 with tracker_ref RHAIRFE-2 is fetched, verified and written as
    RHAIRFE-2 — check_conflicts.py names the same key — while the local paths keep RHAIRFE-1."""

    def test_fetched_and_written_as_the_tracker_ref(self, art_dir, jira):
        jira.create("RHAIRFE-2", "Test RFE", "Original.")
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1.md", "Original.")
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1.md",
            "---\nrfe_id: RHAIRFE-1\ntitle: Test RFE\npriority: Major\nstatus: Ready\n"
            "type: rfe\ntracker_ref: RHAIRFE-2\n---\nRevised.",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1-review.md",
            _review("RHAIRFE-1", auto_revised="true"),
        )
        r = _run_submit_with(_env_for(jira), art_dir, "--auto-approve")
        assert r.returncode == 0, r.stderr
        assert "Traceback" not in r.stderr
        assert "RHAIRFE-1: Updated" in r.stdout
        assert "RHAIRFE-1: Transitioned to Approved" in r.stdout
        issue = jira.get("RHAIRFE-2")
        assert "Revised" in _description_text(issue)
        assert "rfe-creator-auto-revised" in issue["fields"]["labels"]
        assert issue["fields"]["status"]["name"] == "Approved"
        assert [i["key"] for i in jira.search("project = RHAIRFE")] == ["RHAIRFE-2"]
        assert os.path.exists(f"{art_dir}/rfe-tasks/RHAIRFE-1.md")
        assert _read_frontmatter(f"{art_dir}/rfe-tasks/RHAIRFE-1.md")["status"] == "Submitted"


class TestOverriddenProjectBinding:
    """RFE_CREATOR_BINDING_RFE_PROJECT=KONFLUX (design §3.2.1): the rfe type writes to the
    KONFLUX project — the effective binding's project, issue type and write prefix — after
    resolve printed (or, on the legacy default rung, did not print) and assert_registered_binding
    passed. The emulator creates the KONFLUX project from the first imported key."""

    OVERRIDE = {"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"}

    def _seed(self, jira, key="KONFLUX-1", body="Original."):
        jira.create(key, "Test RFE", body)  # a Feature Request; the project comes from the key

    def test_a_fetched_task_is_updated_in_the_overridden_project(self, art_dir, jira):
        self._seed(jira)
        env = _env_for(jira, **self.OVERRIDE)
        fetched = subprocess.run(
            [sys.executable, FETCH_SCRIPT, "KONFLUX-1", "--fetch-all", art_dir],
            capture_output=True,
            text=True,
            env=env,
            cwd=REPO_ROOT,  # fetch_issue.py runs scripts/frontmatter.py relative to its cwd
        )
        assert fetched.returncode == 0, fetched.stderr
        task_path = f"{art_dir}/rfe-tasks/KONFLUX-1.md"
        fm = _read_frontmatter(task_path)
        assert (fm["rfe_id"], fm["type"], fm["tracker_ref"]) == ("KONFLUX-1", "rfe", "KONFLUX-1")
        with open(task_path, encoding="utf-8") as f:
            text = f.read()
        _write(task_path, text.replace("Original.", "Revised."))
        _write(
            f"{art_dir}/rfe-reviews/KONFLUX-1-review.md",
            _review("KONFLUX-1", auto_revised="true"),
        )

        r = _run_submit_with(env, art_dir)
        assert r.returncode == 0, r.stderr
        assert (
            "TYPE RESOLVED" not in r.stderr and "Traceback" not in r.stderr
        )  # legacy default: silent
        assert "KONFLUX-1: Updated" in r.stdout
        issue = jira.get("KONFLUX-1")
        assert "Revised" in _description_text(issue)
        assert "rfe-creator-auto-revised" in issue["fields"]["labels"]
        assert "rfe-creator-autofix-rubric-pass" in issue["fields"]["labels"]
        assert _read_frontmatter(task_path)["status"] == "Submitted"
        assert jira.search("project = RHAIRFE") == []

    def test_a_local_draft_is_created_in_the_overridden_project(self, art_dir, jira):
        self._seed(jira)
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md", _review("RFE-001"))

        r = _run_submit_with(_env_for(jira, **self.OVERRIDE), art_dir)
        assert r.returncode == 0, r.stderr
        assert (
            "TYPE RESOLVED" not in r.stderr and "Traceback" not in r.stderr
        )  # legacy default: silent
        created = [
            i
            for i in jira.search("project = KONFLUX", fields="key,summary")
            if i["key"] != "KONFLUX-1"
        ]
        assert len(created) == 1
        key = created[0]["key"]
        assert key.startswith("KONFLUX-")
        assert f"RFE-001: Created {key}" in r.stdout
        issue = jira.get(key)
        assert issue["fields"]["issuetype"]["name"] == "Feature Request"
        assert issue["fields"]["project"]["key"] == "KONFLUX"
        assert issue["fields"]["summary"] == "Test RFE"
        assert "rfe-creator-auto-created" in issue["fields"]["labels"]
        assert jira.search("project = RHAIRFE") == []
        # Renamed to the KONFLUX key, with the self-describing fields.
        assert not os.path.exists(f"{art_dir}/rfe-tasks/RFE-001.md")
        fm = _read_frontmatter(f"{art_dir}/rfe-tasks/{key}.md")
        assert (fm["rfe_id"], fm["local_id"], fm["tracker_ref"]) == (key, "RFE-001", key)
        assert os.path.exists(f"{art_dir}/rfe-reviews/{key}-review.md")

    def test_a_dry_run_plans_the_create_under_the_overridden_project(self, art_dir, jira):
        self._seed(jira)
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md", _review("RFE-001"))

        r = _run_submit_with(_env_for(jira, **self.OVERRIDE), art_dir, "--dry-run")
        assert r.returncode == 0, r.stderr
        assert r.stderr == ""
        assert "RFE-001: Would create KONFLUX Feature Request: Test RFE" in r.stdout
        assert "RHAIRFE" not in r.stdout
        # The plan is binding-derived through and through; nothing was created anywhere.
        assert [i["key"] for i in jira.search("project = KONFLUX")] == ["KONFLUX-1"]
        assert jira.search("project = RHAIRFE") == []
        assert os.path.exists(f"{art_dir}/rfe-tasks/RFE-001.md")
        # With --type the D3 line names the override.
        r = _run_submit_with(_env_for(jira, **self.OVERRIDE), art_dir, "--dry-run", "--type", "rfe")
        assert r.returncode == 0, r.stderr
        assert r.stderr == "TYPE RESOLVED: rfe (--type; binding override project=KONFLUX)\n"

    def test_an_rfe_override_selecting_the_initiative_pair_is_refused_before_any_write(
        self, art_dir, jira
    ):
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md", _review("RFE-001"))
        env = _env_for(
            jira,
            RFE_CREATOR_BINDING_RFE_PROJECT="RHOAIENG",
            RFE_CREATOR_BINDING_RFE_ISSUE_TYPE="Initiative",
        )
        r = _run_submit_with(env, art_dir)
        assert r.returncode == 1
        assert r.stdout == ""
        assert r.stderr.startswith(
            "Error: rfe: effective binding ('jira', 'RHOAIENG', 'Initiative') (source: env) is "
            "the binding registered for type 'initiative'; a tracker binding must be owned by "
            "exactly one type"
        )
        assert r.stderr.count("\n") == 1 and "Traceback" not in r.stderr
        assert jira.search("project = RHOAIENG") == []
        assert jira.search("project = RHAIRFE") == []
        assert os.path.exists(f"{art_dir}/rfe-tasks/RFE-001.md")

    def test_a_tracker_ref_of_another_type_is_a_hard_error_before_any_write(self, art_dir, jira):
        jira.create(
            "RHAIRFE-1234", "Test RFE", "Original.", labels=["rfe-creator-autofix-rubric-pass"]
        )
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", "Original.")
        _write(
            f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
            "---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\npriority: Major\nstatus: Ready\n"
            "type: rfe\ntracker_ref: RHOAIENG-7\n"
            "original_labels:\n- rfe-creator-autofix-rubric-pass\n---\nRevised.",
        )
        _write(
            f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
            _review("RHAIRFE-1234", auto_revised="true"),
        )
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md", _review("RFE-001"))

        r = _run_submit_with(_env_for(jira), art_dir)
        assert r.returncode == 1
        assert r.stdout == ""
        assert r.stderr == (
            "Error: RHAIRFE-1234 carries tracker_ref 'RHOAIENG-7', a key type initiative owns, "
            "not the resolved type rfe (key prefixes: RHAIRFE-); an artifact bound to another "
            "type is never updated or created here — fix the artifact before re-running; "
            "nothing submitted\n"
        )
        # Nothing was updated and nothing was created.
        assert "Original" in _description_text(jira.get("RHAIRFE-1234"))
        assert _histories(jira, "RHAIRFE-1234") == []
        assert [i["key"] for i in jira.search("project = RHAIRFE")] == ["RHAIRFE-1234"]
        assert os.path.exists(f"{art_dir}/rfe-tasks/RFE-001.md")
        assert _read_frontmatter(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md")["status"] == "Ready"

    def test_the_shorthand_creates_nothing(self, art_dir, jira):
        # JIRA_PROJECT=KONFLUX reaches the writers' binding through resolve but not the artifact
        # layer (SCHEMAS, the rename guard), so a run under it would create the issue in KONFLUX
        # and then fail the rename, orphaning it on every retry. Refused up front instead: one
        # line naming the typed variables, exit 1, nothing created anywhere, the draft untouched.
        self._seed(jira)
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md", _review("RFE-001"))
        r = _run_submit_with(_env_for(jira, JIRA_PROJECT="KONFLUX"), art_dir)
        assert r.returncode == 1
        assert r.stdout == ""
        assert r.stderr == (
            "Error: JIRA_PROJECT / JIRA_ISSUE_TYPE shorthand is not honoured by the artifact "
            "layer; set RFE_CREATOR_BINDING_RFE_PROJECT / _ISSUE_TYPE instead\n"
        )
        assert [i["key"] for i in jira.search("project = KONFLUX")] == ["KONFLUX-1"]
        assert jira.search("project = RHAIRFE") == []
        assert os.path.exists(f"{art_dir}/rfe-tasks/RFE-001.md")
        assert _read_frontmatter(f"{art_dir}/rfe-tasks/RFE-001.md")["status"] == "Ready"

    def test_a_pre_override_item_is_updated_in_place(self, art_dir, jira):
        # RHAIRFE-7, a Feature Request created before the override, carries the descriptor read
        # prefix: its (RHAIRFE, Feature Request) pair is accepted and it is updated in place —
        # while a KONFLUX Epic in the same run is still skipped.
        self._seed(jira)
        jira.create("RHAIRFE-7", "Test RFE", "Original.")
        jira.create("KONFLUX-2", "An epic", "Original.", issue_type="Epic")
        for key in ("RHAIRFE-7", "KONFLUX-2"):
            _write(f"{art_dir}/rfe-originals/{key}.md", "Original.")
            _write(
                f"{art_dir}/rfe-tasks/{key}.md",
                f"---\nrfe_id: {key}\ntitle: Test RFE\npriority: Major\nstatus: Ready\n"
                f"type: rfe\ntracker_ref: {key}\n---\nRevised.",
            )
            _write(f"{art_dir}/rfe-reviews/{key}-review.md", _review(key, auto_revised="true"))
        r = _run_submit_with(_env_for(jira, **self.OVERRIDE), art_dir)
        assert r.returncode == 0, r.stderr
        assert "Traceback" not in r.stderr
        assert "RHAIRFE-7: Updated" in r.stdout
        assert "Revised" in _description_text(jira.get("RHAIRFE-7"))
        assert "rfe-creator-auto-revised" in jira.get("RHAIRFE-7")["fields"]["labels"]
        assert _read_frontmatter(f"{art_dir}/rfe-tasks/RHAIRFE-7.md")["status"] == "Submitted"
        assert (
            "Reason: binding mismatch — KONFLUX-2 is (KONFLUX, Epic) in Jira but type rfe binds "
            "(KONFLUX, Feature Request)"
        ) in r.stdout
        assert "Original" in _description_text(jira.get("KONFLUX-2"))
        assert _histories(jira, "KONFLUX-2") == []

    def test_a_foreign_issue_behind_an_overridden_key_is_skipped(self, art_dir, jira):
        # The pre-update verification compares with the EFFECTIVE pair: KONFLUX-2 is an Epic.
        self._seed(jira)
        jira.create("KONFLUX-2", "An epic", "Original.", issue_type="Epic")
        _write(f"{art_dir}/rfe-originals/KONFLUX-2.md", "Original.")
        _write(
            f"{art_dir}/rfe-tasks/KONFLUX-2.md",
            "---\nrfe_id: KONFLUX-2\ntitle: An epic\npriority: Major\nstatus: Ready\n"
            "type: rfe\ntracker_ref: KONFLUX-2\n---\nRevised.",
        )
        _write(f"{art_dir}/rfe-reviews/KONFLUX-2-review.md", _review("KONFLUX-2"))
        r = _run_submit_with(_env_for(jira, **self.OVERRIDE), art_dir)
        assert r.returncode == 0, r.stderr
        assert (
            "Reason: binding mismatch — KONFLUX-2 is (KONFLUX, Epic) in Jira but type rfe binds "
            "(KONFLUX, Feature Request)"
        ) in r.stdout
        assert "Original" in _description_text(jira.get("KONFLUX-2"))
        assert _histories(jira, "KONFLUX-2") == []
