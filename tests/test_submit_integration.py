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


def _run_submit(artifacts_dir, server_url):
    """Run submit.py (non-dry-run) against the jira-emulator."""
    env = {
        **os.environ,
        "JIRA_SERVER": server_url,
        "JIRA_USER": "admin",
        "JIRA_TOKEN": "admin",
    }
    return subprocess.run(
        [sys.executable, SCRIPT, "--artifacts-dir", artifacts_dir],
        capture_output=True, text=True, env=env,
    )


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


def _review(rfe_id, auto_revised="false", needs_attention="false",
            extra_fields=""):
    return REVIEW_FM.format(rfe_id=rfe_id, auto_revised=auto_revised,
                            needs_attention=needs_attention,
                            extra_fields=extra_fields)


# ── Tests ────────────────────────────────────────────────────────────────────

class TestCreateNewRFE:
    def test_posts_correct_fields(self, art_dir, jira):
        """New RFE → issue created in Jira with correct fields."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               _review("RFE-001"))

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
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               _review("RFE-001"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        issues = jira.search("project = RHAIRFE")
        issue = jira.get(issues[0]["key"])
        labels = issue["fields"]["labels"]
        assert "rfe-creator-auto-created" in labels
        assert "rfe-creator-autofix-rubric-pass" in labels

    def test_renames_files(self, art_dir, jira):
        """New RFE → RFE-001.md renamed to RHAIRFE-N.md."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               _review("RFE-001"))

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
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n---\n{revised}")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               _review("RHAIRFE-1234", auto_revised="true"))

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
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n---\n{body}")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               _review("RHAIRFE-1234"))

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
        jira.create("RHAIRFE-1234", "Test RFE", "Content.",
                    labels=["rfe-creator-autofix-rubric-pass"])
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n"
               f"original_labels:\n- rfe-creator-autofix-rubric-pass\n"
               f"---\n\n## Problem\n\nContent.\n")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "Removed labels" in r.stdout

        # Verify label was removed
        issue = jira.get("RHAIRFE-1234")
        assert "rfe-creator-autofix-rubric-pass" not in \
            issue["fields"]["labels"]

    def test_no_api_call_on_plain_reject(self, art_dir, jira):
        """Rejected RFE without rubric-pass → issue unchanged."""
        jira.create("RHAIRFE-1234", "Test RFE", "Content.")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               TASK_FM.format(rfe_id="RHAIRFE-1234"))
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        # Issue should have no changes (empty changelog)
        issue = jira.get("RHAIRFE-1234")
        histories = issue.get("changelog", {}).get("histories", [])
        assert len(histories) == 0

    def test_does_not_update_frontmatter_status(self, art_dir, jira):
        """Remove labels must NOT set status to Submitted."""
        jira.create("RHAIRFE-1234", "Test RFE", "Content.",
                    labels=["rfe-creator-autofix-rubric-pass"])
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n"
               f"original_labels:\n- rfe-creator-autofix-rubric-pass\n"
               f"---\n\n## Problem\n\nContent.\n")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        fm = _read_frontmatter(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md")
        assert fm["status"] == "Ready"  # NOT "Submitted"

    def test_not_in_snapshot_update(self, art_dir, jira):
        """Remove labels must NOT update the snapshot."""
        # Seed a snapshot
        snap_dir = os.path.join(art_dir, "auto-fix-runs")
        os.makedirs(snap_dir, exist_ok=True)
        snap = {"query_timestamp": "2026-04-01T00:00:00Z",
                "timestamp": "2026-04-01T00:00:01Z",
                "issues": {"RHAIRFE-1234": "original-hash"}}
        snap_path = os.path.join(snap_dir,
                                 "issue-snapshot-20260401-000000.yaml")
        with open(snap_path, "w") as f:
            yaml.dump(snap, f)

        jira.create("RHAIRFE-1234", "Test RFE", "Content.",
                    labels=["rfe-creator-autofix-rubric-pass"])
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n"
               f"original_labels:\n- rfe-creator-autofix-rubric-pass\n"
               f"---\n\n## Problem\n\nContent.\n")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        # Snapshot hash preserved, but marked processed (pipeline completed)
        with open(snap_path) as f:
            data = yaml.safe_load(f)
        entry = data["issues"]["RHAIRFE-1234"]
        assert entry == {"hash": "original-hash", "processed": True}


class TestConflictDetection:
    def test_conflict_prevents_update(self, art_dir, jira):
        """Jira description differs from original → skip, no PUT."""
        jira.create("RHAIRFE-1234", "Test RFE", "Edited by someone.")
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", "Original.")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n---\nOur revision.")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               _review("RHAIRFE-1234", auto_revised="true"))

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
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               _review("RFE-001"))
        rc_yaml = {"blocks": [{
            "type": "genuine",
            "heading": "Implementation Notes",
            "content": "Use gRPC for the service mesh.",
        }]}
        _write(f"{art_dir}/rfe-tasks/RFE-001-removed-context.yaml",
               yaml.dump(rc_yaml))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "Posted removed-context comment" in r.stdout

        # Find the created issue and check its comments
        issues = jira.search("project = RHAIRFE")
        key = issues[0]["key"]
        comments = jira.request(
            "GET", f"/rest/api/3/issue/{key}/comment")
        assert comments["total"] >= 1

    def test_needs_attention_comment(self, art_dir, jira):
        """RFE with needs_attention → comment posted."""
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               _review("RFE-001", needs_attention="true",
                       extra_fields="needs_attention_reason: Unclear scope\n"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "needs-attention comment" in r.stdout

        issues = jira.search("project = RHAIRFE")
        key = issues[0]["key"]
        comments = jira.request(
            "GET", f"/rest/api/3/issue/{key}/comment")
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
        path = os.path.join(snap_dir,
                            "issue-snapshot-20260401-000000.yaml")
        with open(path, "w") as f:
            yaml.dump(snap, f, default_flow_style=False, sort_keys=False)
        return path

    def test_snapshot_updated_on_create(self, art_dir, jira):
        """Create → snapshot updated with new issue hash."""
        snap_path = self._seed_snapshot(art_dir, {"RHAIRFE-9000": "existing"})
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               _review("RFE-001"))

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
        snap_path = self._seed_snapshot(art_dir,
                                        {"RHAIRFE-1234": "old-hash"})
        jira.create("RHAIRFE-1234", "Test RFE", "Original.")
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", "Original.")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n---\nRevised.")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               _review("RHAIRFE-1234"))

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
        snap_path = self._seed_snapshot(art_dir,
                                        {"RHAIRFE-1234": "existing"})
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               TASK_FM.format(rfe_id="RHAIRFE-1234"))
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        with open(snap_path) as f:
            data = yaml.safe_load(f)
        # Rejected entries are marked processed (pipeline completed)
        entry = data["issues"]["RHAIRFE-1234"]
        assert entry == {"hash": "existing", "processed": True}

    def test_dry_run_does_not_update_snapshot(self, art_dir, jira):
        """Dry-run must not write processed flags or hashes to snapshot."""
        snap_path = self._seed_snapshot(art_dir, {"RHAIRFE-1234": "existing"})

        # Set up a passing RFE that would normally be submitted
        jira.create("RHAIRFE-1234", "Test RFE", "Original.")
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", "Original.")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n---\nRevised.")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               _review("RHAIRFE-1234"))

        env = {
            **os.environ,
            "JIRA_SERVER": jira.url,
            "JIRA_USER": "admin",
            "JIRA_TOKEN": "admin",
        }
        r = subprocess.run(
            [sys.executable, SCRIPT, "--artifacts-dir", art_dir,
             "--dry-run"],
            capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0, r.stderr

        # Snapshot must be completely untouched
        with open(snap_path) as f:
            data = yaml.safe_load(f)
        assert data["issues"] == {"RHAIRFE-1234": "existing"}

    def test_dry_run_does_not_mark_processed(self, art_dir, jira):
        """Dry-run with all-skipped entries must not mark processed."""
        snap_path = self._seed_snapshot(art_dir, {"RHAIRFE-1234": "existing"})

        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               TASK_FM.format(rfe_id="RHAIRFE-1234"))
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"))

        env = {
            **os.environ,
            "JIRA_SERVER": jira.url,
            "JIRA_USER": "admin",
            "JIRA_TOKEN": "admin",
        }
        r = subprocess.run(
            [sys.executable, SCRIPT, "--artifacts-dir", art_dir,
             "--dry-run"],
            capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0, r.stderr

        # Snapshot untouched — dry-run must not mark processed
        with open(snap_path) as f:
            data = yaml.safe_load(f)
        assert data["issues"] == {"RHAIRFE-1234": "existing"}


class TestSplitConflictDetection:
    """Integration test: split_submit.py detects parent conflict."""

    SPLIT_SCRIPT = os.path.join(os.path.dirname(__file__), "..",
                                "scripts", "split_submit.py")

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
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1000.md",
               "Original content.")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", self.PARENT_TASK)
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", self.CHILD_TASK)

        env = {
            **os.environ,
            "JIRA_SERVER": jira.url,
            "JIRA_USER": "admin",
            "JIRA_TOKEN": "admin",
        }
        r = subprocess.run(
            [sys.executable, self.SPLIT_SCRIPT, "RHAIRFE-1000",
             "--artifacts-dir", art_dir],
            capture_output=True, text=True, env=env,
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
            [sys.executable, self.SPLIT_SCRIPT, "RHAIRFE-1000",
             "--artifacts-dir", art_dir],
            capture_output=True, text=True, env=env,
        )
        assert r.returncode != 3

    def test_submit_handles_conflict_refusal(self, art_dir, jira):
        """submit.py handles split conflict (exit 3) gracefully."""
        # Parent in Jira has different content than our original
        jira.create("RHAIRFE-1000", "Parent RFE", "Edited by someone.")
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1000.md",
               "Original content.")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", self.PARENT_TASK)
        _write(f"{art_dir}/rfe-tasks/RFE-001.md", self.CHILD_TASK)
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1000-review.md",
               _review("RHAIRFE-1000"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0  # continues after refusal
        assert "Jira conflict" in r.stdout

        # Check review frontmatter was updated
        fm = _read_frontmatter(
            f"{art_dir}/rfe-reviews/RHAIRFE-1000-review.md")
        assert fm["needs_attention"] is True
        assert "modified in Jira" in fm["needs_attention_reason"]
        assert fm["error"] == "split_refused: jira conflict"

        # Check needs-attention label was added
        issue = jira.get("RHAIRFE-1000")
        assert "rfe-creator-needs-attention" in issue["fields"]["labels"]


class TestSplitFieldInheritance:
    """Integration test: children inherit parent's components and labels."""

    SPLIT_SCRIPT = os.path.join(os.path.dirname(__file__), "..",
                                "scripts", "split_submit.py")

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
        jira.create("RHAIRFE-1000", "Parent RFE", "Parent content.",
                    labels=["3.5-candidate", "rfe-creator-auto-revised"],
                    components=["AI Safety"])
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1000.md",
               "Parent content.")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", self.PARENT_TASK)
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               self.CHILD_TASK_TPL.format(num=1))
        _write(f"{art_dir}/rfe-tasks/RFE-002.md",
               self.CHILD_TASK_TPL.format(num=2))

        env = {
            **os.environ,
            "JIRA_SERVER": jira.url,
            "JIRA_USER": "admin",
            "JIRA_TOKEN": "admin",
        }
        r = subprocess.run(
            [sys.executable, self.SPLIT_SCRIPT, "RHAIRFE-1000",
             "--artifacts-dir", art_dir],
            capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0, r.stderr

        # Find the created children
        issues = jira.search("project = RHAIRFE",
                             fields="key,labels,components")
        children = [i for i in issues
                    if i["key"] != "RHAIRFE-1000"]
        assert len(children) == 2

        for child in children:
            child_detail = jira.get(child["key"])
            labels = child_detail["fields"]["labels"]
            components = [c["name"] for c in
                          child_detail["fields"].get("components", [])]

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
        jira.request("POST", "/api/admin/import", {"issues": [
            {"key": "RHAISTRAT-100", "summary": "Strategy Feature",
             "project": "RHAISTRAT", "issue_type": "Feature",
             "description": "Strategy content."},
            {"key": "RHAIRFE-2000", "summary": "Parent RFE",
             "project": "RHAIRFE", "issue_type": "Feature Request",
             "description": "Parent content.",
             "epic_link": "RHAISTRAT-100"},
        ]})

        _write(f"{art_dir}/rfe-originals/RHAIRFE-2000.md",
               "Parent content.")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-2000.md",
               "---\nrfe_id: RHAIRFE-2000\ntitle: Parent RFE\n"
               "priority: Major\nstatus: Archived\n---\n\nParent content.\n")
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               "---\nrfe_id: RFE-001\ntitle: Child RFE 1\n"
               "priority: Major\nstatus: Ready\n"
               "parent_key: RHAIRFE-2000\n---\n\nChild 1 content.\n")
        _write(f"{art_dir}/rfe-tasks/RFE-002.md",
               "---\nrfe_id: RFE-002\ntitle: Child RFE 2\n"
               "priority: Major\nstatus: Ready\n"
               "parent_key: RHAIRFE-2000\n---\n\nChild 2 content.\n")

        env = {
            **os.environ,
            "JIRA_SERVER": jira.url,
            "JIRA_USER": "admin",
            "JIRA_TOKEN": "admin",
        }
        r = subprocess.run(
            [sys.executable, self.SPLIT_SCRIPT, "RHAIRFE-2000",
             "--artifacts-dir", art_dir],
            capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0, r.stderr

        # Find the created children
        issues = jira.search("project = RHAIRFE",
                             fields="key,parent")
        children = [i for i in issues
                    if i["key"] not in ("RHAIRFE-2000",)]
        assert len(children) == 2

        for child in children:
            child_detail = jira.get(child["key"])
            parent = child_detail["fields"].get("parent")
            assert parent is not None, (
                f"{child['key']} should inherit parent RHAISTRAT-100")
            assert parent["key"] == "RHAISTRAT-100"

    def test_no_parent_when_parent_has_none(self, art_dir, jira):
        """Children don't get a parent field if the split parent has none."""
        jira.create("RHAIRFE-3000", "Parent No Strat", "Content.")
        _write(f"{art_dir}/rfe-originals/RHAIRFE-3000.md", "Content.")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-3000.md",
               "---\nrfe_id: RHAIRFE-3000\ntitle: Parent No Strat\n"
               "priority: Major\nstatus: Archived\n---\n\nContent.\n")
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               "---\nrfe_id: RFE-001\ntitle: Child RFE 1\n"
               "priority: Major\nstatus: Ready\n"
               "parent_key: RHAIRFE-3000\n---\n\nChild content.\n")

        env = {
            **os.environ,
            "JIRA_SERVER": jira.url,
            "JIRA_USER": "admin",
            "JIRA_TOKEN": "admin",
        }
        r = subprocess.run(
            [sys.executable, self.SPLIT_SCRIPT, "RHAIRFE-3000",
             "--artifacts-dir", art_dir],
            capture_output=True, text=True, env=env,
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
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               _review("RFE-001"))

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
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n---\nRevised content.")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               _review("RHAIRFE-1234", auto_revised="true"))

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
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               _review("RFE-001"))
        _write(f"{art_dir}/rfe-tasks/RFE-002.md",
               TASK_FM.format(rfe_id="RFE-002").replace(
                   "title: Test RFE", "title: Second RFE"))
        _write(f"{art_dir}/rfe-reviews/RFE-002-review.md",
               _review("RFE-002"))

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
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n---\n{body}")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               _review("RHAIRFE-1234"))

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
        path = os.path.join(snap_dir,
                            "issue-snapshot-20260401-000000.yaml")
        with open(path, "w") as f:
            yaml.dump(snap, f, default_flow_style=False, sort_keys=False)
        return path

    def _setup_split(self, art_dir, jira, num_children=2):
        """Set up a split parent with children and a seeded snapshot."""
        jira.create("RHAIRFE-1000", "Parent RFE", "Parent content.")
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1000.md",
               "Parent content.")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", self.PARENT_TASK)
        for i in range(1, num_children + 1):
            _write(f"{art_dir}/rfe-tasks/RFE-{i:03d}.md",
                   self.CHILD_TASK_TPL.format(num=i))
        return self._seed_snapshot(art_dir, {"RHAIRFE-1000": "parent-hash"})

    def test_split_child_hashes_in_snapshot(self, art_dir, jira):
        """Split children's hashes appear in the snapshot after submit."""
        snap_path = self._setup_split(art_dir, jira)

        # Add a regular RFE so Phase 2 also runs
        _write(f"{art_dir}/rfe-tasks/RFE-099.md",
               TASK_FM.format(rfe_id="RFE-099"))
        _write(f"{art_dir}/rfe-reviews/RFE-099-review.md",
               _review("RFE-099"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "split-child hashes" in r.stdout

        with open(snap_path) as f:
            data = yaml.safe_load(f)

        # Find the child keys (RHAIRFE-NNNN created by split_submit.py)
        all_issues = jira.search("project = RHAIRFE")
        child_keys = sorted([i["key"] for i in all_issues
                             if i["key"] != "RHAIRFE-1000"])
        # At least the 2 split children + 1 regular RFE
        assert len(child_keys) >= 3

        # Each split child must have a valid hash in the snapshot
        split_child_entries = {
            k: v for k, v in data["issues"].items()
            if k != "RHAIRFE-1000" and isinstance(v, dict)
            and len(v.get("hash", "")) == 64
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
            k: v for k, v in data["issues"].items()
            if k != "RHAIRFE-1000" and isinstance(v, dict)
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
            [sys.executable, SCRIPT, "--artifacts-dir", art_dir,
             "--dry-run"],
            capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0, r.stderr

        # Snapshot must be completely untouched
        with open(snap_path) as f:
            data = yaml.safe_load(f)
        assert data["issues"] == {"RHAIRFE-1000": "parent-hash"}
