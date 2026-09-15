#!/usr/bin/env python3
"""Integration tests for submit.py --type initiative using a jira-emulator server.

Runs the full execution path against a real HTTP server that tracks
issue state, changelogs, labels, and comments.
"""

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
    """Create a minimal artifacts directory for initiative tests."""
    for d in ["initiatives", "initiative-reviews", "initiative-originals"]:
        os.makedirs(tmp_path / d)
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield str(tmp_path)
    os.chdir(orig)


def _run_submit(artifacts_dir, server_url, extra_flags=None):
    """Run submit.py --type initiative (non-dry-run) against the jira-emulator."""
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


# ── Templates ────────────────────────────────────────────────────────────────

TASK_FM = """\
---
initiative_id: {initiative_id}
title: Test Initiative
priority: Major
status: Ready
---

## Problem Statement

Users need a unified model serving platform.

## Acceptance Criteria

- Platform supports multi-model inference
"""

REVIEW_FM = """\
---
initiative_id: {initiative_id}
score: 9
pass: true
recommendation: submit
feasibility: feasible
auto_revised: {auto_revised}
needs_attention: {needs_attention}
alignment: {alignment}
{extra_fields}scores:
  what: 2
  why: 2
  scope: 2
  open_to_how: 2
  right_sized: 1
---

## Assessor Feedback
Looks good.
"""

REJECT_REVIEW_FM = """\
---
initiative_id: {initiative_id}
score: 3
pass: false
recommendation: reject
feasibility: feasible
auto_revised: false
needs_attention: false
scores:
  what: 0
  why: 1
  scope: 1
  open_to_how: 1
  right_sized: 0
---

## Assessor Feedback
Does not meet rubric.
"""


def _review(
    initiative_id,
    auto_revised="false",
    needs_attention="false",
    alignment="strong",
    extra_fields="",
):
    return REVIEW_FM.format(
        initiative_id=initiative_id,
        auto_revised=auto_revised,
        needs_attention=needs_attention,
        alignment=alignment,
        extra_fields=extra_fields,
    )


# ── Tests ────────────────────────────────────────────────────────────────────


class TestCreateNewInitiative:
    def test_posts_correct_fields(self, art_dir, jira):
        """New Initiative → issue created in Jira with correct fields."""
        _write(f"{art_dir}/initiatives/INIT-001.md", TASK_FM.format(initiative_id="INIT-001"))
        _write(f"{art_dir}/initiative-reviews/INIT-001-review.md", _review("INIT-001"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        issues = jira.search("project = RHOAIENG")
        assert len(issues) == 1
        key = issues[0]["key"]
        issue = jira.get(key)
        assert issue["fields"]["summary"] == "Test Initiative"
        assert issue["fields"]["priority"]["name"] == "Major"

    def test_includes_labels(self, art_dir, jira):
        """New Initiative → labels include auto-created, alignment, feasibility."""
        _write(f"{art_dir}/initiatives/INIT-001.md", TASK_FM.format(initiative_id="INIT-001"))
        _write(
            f"{art_dir}/initiative-reviews/INIT-001-review.md",
            _review("INIT-001", alignment="strong"),
        )

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        issues = jira.search("project = RHOAIENG")
        issue = jira.get(issues[0]["key"])
        labels = issue["fields"]["labels"]
        assert "initiative-auto-created" in labels
        assert "initiative-alignment-strong" in labels
        assert "initiative-feasibility-pass" in labels
        assert "initiative-autofix-rubric-pass" in labels
        assert "rfe-creator-autofix-rubric-pass" not in labels

    def test_renames_files(self, art_dir, jira):
        """New Initiative → INIT-001.md renamed to RHOAIENG-N.md."""
        _write(f"{art_dir}/initiatives/INIT-001.md", TASK_FM.format(initiative_id="INIT-001"))
        _write(f"{art_dir}/initiative-reviews/INIT-001-review.md", _review("INIT-001"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        assert not os.path.exists(f"{art_dir}/initiatives/INIT-001.md")
        issues = jira.search("project = RHOAIENG")
        key = issues[0]["key"]
        assert os.path.exists(f"{art_dir}/initiatives/{key}.md")
        fm = _read_frontmatter(f"{art_dir}/initiatives/{key}.md")
        assert fm["initiative_id"] == key


class TestUpdateExistingInitiative:
    def _setup_existing(self, art_dir, jira, original, revised, alignment="strong"):
        jira.create(
            "RHOAIENG-1234",
            "Test Initiative",
            original,
            issue_type="Initiative",
        )
        _write(f"{art_dir}/initiative-originals/RHOAIENG-1234.md", original)
        _write(
            f"{art_dir}/initiatives/RHOAIENG-1234.md",
            f"---\ninitiative_id: RHOAIENG-1234\ntitle: Test Initiative\n"
            f"priority: Major\nstatus: Ready\n---\n{revised}",
        )
        _write(
            f"{art_dir}/initiative-reviews/RHOAIENG-1234-review.md",
            _review("RHOAIENG-1234", auto_revised="true", alignment=alignment),
        )

    def test_puts_description(self, art_dir, jira):
        """Existing Initiative with changes → description updated in Jira."""
        self._setup_existing(art_dir, jira, "Original.", "Revised.")

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "Updated" in r.stdout

    def test_adds_labels(self, art_dir, jira):
        """Update → labels added to the issue."""
        self._setup_existing(art_dir, jira, "Original.", "Revised.")

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        issue = jira.get("RHOAIENG-1234")
        labels = issue["fields"]["labels"]
        assert "initiative-auto-revised" in labels
        assert "initiative-alignment-strong" in labels

    def test_sets_status_submitted(self, art_dir, jira):
        """Existing Initiative after update → frontmatter status = Submitted."""
        self._setup_existing(art_dir, jira, "Original.", "Revised.")

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        fm = _read_frontmatter(f"{art_dir}/initiatives/RHOAIENG-1234.md")
        assert fm["status"] == "Submitted"


class TestLabelOnly:
    def test_no_description_put(self, art_dir, jira):
        """Unchanged content → label added, description not changed."""
        body = "Same content.\n"
        jira.create("RHOAIENG-1234", "Test Initiative", body, issue_type="Initiative")
        _write(f"{art_dir}/initiative-originals/RHOAIENG-1234.md", body)
        _write(
            f"{art_dir}/initiatives/RHOAIENG-1234.md",
            f"---\ninitiative_id: RHOAIENG-1234\ntitle: Test Initiative\n"
            f"priority: Major\nstatus: Ready\n---\n{body}",
        )
        _write(
            f"{art_dir}/initiative-reviews/RHOAIENG-1234-review.md",
            _review("RHOAIENG-1234"),
        )

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        issue = jira.get("RHOAIENG-1234")
        labels = issue["fields"]["labels"]
        assert "initiative-alignment-strong" in labels
        assert "initiative-feasibility-pass" in labels

        desc_changes = []
        for h in issue.get("changelog", {}).get("histories", []):
            for item in h.get("items", []):
                if item["field"] == "description":
                    desc_changes.append(item)
        assert len(desc_changes) == 0


class TestConflictDetection:
    def test_conflict_prevents_update(self, art_dir, jira):
        """Jira description differs from original → skip, no PUT."""
        jira.create(
            "RHOAIENG-1234", "Test Initiative", "Edited by someone.", issue_type="Initiative"
        )
        _write(f"{art_dir}/initiative-originals/RHOAIENG-1234.md", "Original.")
        _write(
            f"{art_dir}/initiatives/RHOAIENG-1234.md",
            "---\ninitiative_id: RHOAIENG-1234\ntitle: Test Initiative\n"
            "priority: Major\nstatus: Ready\n---\nOur revision.",
        )
        _write(
            f"{art_dir}/initiative-reviews/RHOAIENG-1234-review.md",
            _review("RHOAIENG-1234", auto_revised="true"),
        )

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "Skipping" in r.stdout


class TestCommentPosting:
    def test_removed_context_comment(self, art_dir, jira):
        """Initiative with removed-context YAML → comment posted."""
        _write(f"{art_dir}/initiatives/INIT-001.md", TASK_FM.format(initiative_id="INIT-001"))
        _write(f"{art_dir}/initiative-reviews/INIT-001-review.md", _review("INIT-001"))
        rc_yaml = {
            "blocks": [
                {
                    "type": "genuine",
                    "heading": "Implementation Notes",
                    "content": "Use gRPC for the service mesh.",
                }
            ]
        }
        _write(f"{art_dir}/initiatives/INIT-001-removed-context.yaml", yaml.dump(rc_yaml))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "Posted removed-context comment" in r.stdout

        issues = jira.search("project = RHOAIENG")
        key = issues[0]["key"]
        comments = jira.request("GET", f"/rest/api/3/issue/{key}/comment")
        assert comments["total"] >= 1

    def test_needs_attention_comment(self, art_dir, jira):
        """Initiative with needs_attention → comment posted."""
        _write(f"{art_dir}/initiatives/INIT-001.md", TASK_FM.format(initiative_id="INIT-001"))
        _write(
            f"{art_dir}/initiative-reviews/INIT-001-review.md",
            _review(
                "INIT-001",
                needs_attention="true",
                extra_fields="needs_attention_reason: Unclear scope\n",
            ),
        )

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "needs-attention comment" in r.stdout

        issues = jira.search("project = RHOAIENG")
        key = issues[0]["key"]
        comments = jira.request("GET", f"/rest/api/3/issue/{key}/comment")
        assert comments["total"] >= 1


class TestSnapshotUpdate:
    def _seed_snapshot(self, art_dir, issues):
        """Write an initiative snapshot so submit.py can update it."""
        snap_dir = os.path.join(art_dir, "auto-fix-runs")
        os.makedirs(snap_dir, exist_ok=True)
        snap = {
            "query_timestamp": "2026-04-01T00:00:00Z",
            "timestamp": "2026-04-01T00:00:01Z",
            "issues": issues,
        }
        path = os.path.join(snap_dir, "initiative-snapshot-20260401-000000.yaml")
        with open(path, "w") as f:
            yaml.dump(snap, f, default_flow_style=False, sort_keys=False)
        return path

    def test_snapshot_updated_on_create(self, art_dir, jira):
        """Create → snapshot updated with new issue hash."""
        snap_path = self._seed_snapshot(art_dir, {"RHOAIENG-9000": "existing"})
        _write(f"{art_dir}/initiatives/INIT-001.md", TASK_FM.format(initiative_id="INIT-001"))
        _write(f"{art_dir}/initiative-reviews/INIT-001-review.md", _review("INIT-001"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        with open(snap_path) as f:
            data = yaml.safe_load(f)
        issues = jira.search("project = RHOAIENG")
        key = issues[0]["key"]
        assert key in data["issues"]
        entry = data["issues"][key]
        assert isinstance(entry, dict)
        assert len(entry["hash"]) == 64
        assert entry["processed"] is True

    def test_snapshot_updated_on_update(self, art_dir, jira):
        """Update → snapshot updated with revised hash."""
        snap_path = self._seed_snapshot(art_dir, {"RHOAIENG-1234": "old-hash"})
        jira.create("RHOAIENG-1234", "Test Initiative", "Original.", issue_type="Initiative")
        _write(f"{art_dir}/initiative-originals/RHOAIENG-1234.md", "Original.")
        _write(
            f"{art_dir}/initiatives/RHOAIENG-1234.md",
            "---\ninitiative_id: RHOAIENG-1234\ntitle: Test Initiative\n"
            "priority: Major\nstatus: Ready\n---\nRevised.",
        )
        _write(
            f"{art_dir}/initiative-reviews/RHOAIENG-1234-review.md",
            _review("RHOAIENG-1234"),
        )

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        with open(snap_path) as f:
            data = yaml.safe_load(f)
        assert "RHOAIENG-1234" in data["issues"]
        entry = data["issues"]["RHOAIENG-1234"]
        assert isinstance(entry, dict)
        assert len(entry["hash"]) == 64
        assert entry["hash"] != "old-hash"
        assert entry["processed"] is True

    def test_dry_run_does_not_update_snapshot(self, art_dir, jira):
        """Dry-run must not write processed flags or hashes to snapshot."""
        snap_path = self._seed_snapshot(art_dir, {"RHOAIENG-1234": "existing"})

        jira.create("RHOAIENG-1234", "Test Initiative", "Original.", issue_type="Initiative")
        _write(f"{art_dir}/initiative-originals/RHOAIENG-1234.md", "Original.")
        _write(
            f"{art_dir}/initiatives/RHOAIENG-1234.md",
            "---\ninitiative_id: RHOAIENG-1234\ntitle: Test Initiative\n"
            "priority: Major\nstatus: Ready\n---\nRevised.",
        )
        _write(
            f"{art_dir}/initiative-reviews/RHOAIENG-1234-review.md",
            _review("RHOAIENG-1234"),
        )

        env = {
            **os.environ,
            "JIRA_SERVER": jira.url,
            "JIRA_USER": "admin",
            "JIRA_TOKEN": "admin",
        }
        r = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--type",
                "initiative",
                "--artifacts-dir",
                art_dir,
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr

        with open(snap_path) as f:
            data = yaml.safe_load(f)
        assert data["issues"] == {"RHOAIENG-1234": "existing"}


class TestReplayIdempotency:
    def test_replay_create_no_duplicate(self, art_dir, jira):
        """Replay after create → second run skips (status=Submitted)."""
        _write(f"{art_dir}/initiatives/INIT-001.md", TASK_FM.format(initiative_id="INIT-001"))
        _write(f"{art_dir}/initiative-reviews/INIT-001-review.md", _review("INIT-001"))

        r1 = _run_submit(art_dir, jira.url)
        assert r1.returncode == 0, r1.stderr

        # File was renamed — re-run should find it as Submitted
        _run_submit(art_dir, jira.url)
        # Should succeed (no error) — either "No submittable" or processes the submitted one
        issues = jira.search("project = RHOAIENG")
        assert len(issues) == 1  # No duplicate created

    def test_replay_update_skipped(self, art_dir, jira):
        """Replay after update → second run sees Submitted status."""
        jira.create("RHOAIENG-1234", "Test Initiative", "Original.", issue_type="Initiative")
        _write(f"{art_dir}/initiative-originals/RHOAIENG-1234.md", "Original.")
        _write(
            f"{art_dir}/initiatives/RHOAIENG-1234.md",
            "---\ninitiative_id: RHOAIENG-1234\ntitle: Test Initiative\n"
            "priority: Major\nstatus: Ready\n---\nRevised.",
        )
        _write(
            f"{art_dir}/initiative-reviews/RHOAIENG-1234-review.md",
            _review("RHOAIENG-1234"),
        )

        r1 = _run_submit(art_dir, jira.url)
        assert r1.returncode == 0, r1.stderr

        fm = _read_frontmatter(f"{art_dir}/initiatives/RHOAIENG-1234.md")
        assert fm["status"] == "Submitted"

        r2 = _run_submit(art_dir, jira.url)
        # Should exit cleanly — status is Submitted so it's excluded
        assert r2.returncode == 0 or "No submittable" in r2.stderr


class TestAutoApproveIntegration:
    def test_passing_review_transitions_to_approved(self, art_dir, jira):
        """--auto-approve + passing review → Jira status transitions to Approved."""
        jira.create("RHOAIENG-1234", "Test Initiative", "Original.", issue_type="Initiative")
        _write(f"{art_dir}/initiative-originals/RHOAIENG-1234.md", "Original.")
        _write(
            f"{art_dir}/initiatives/RHOAIENG-1234.md",
            "---\ninitiative_id: RHOAIENG-1234\ntitle: Test Initiative\n"
            "priority: Major\nstatus: Ready\n---\nRevised.",
        )
        _write(
            f"{art_dir}/initiative-reviews/RHOAIENG-1234-review.md",
            _review("RHOAIENG-1234", auto_revised="true"),
        )

        r = _run_submit(art_dir, jira.url, ["--auto-approve"])
        assert r.returncode == 0, r.stderr
        assert "Transitioned to Approved" in r.stdout

        issue = jira.get("RHOAIENG-1234")
        assert issue["fields"]["status"]["name"] == "Approved"

        comments = jira.request("GET", "/rest/api/3/issue/RHOAIENG-1234/comment")
        comment_bodies = []
        for c in comments.get("comments", []):
            body = c.get("body", {})
            if isinstance(body, dict):
                for node in body.get("content", []):
                    for child in node.get("content", []):
                        if child.get("type") == "text":
                            comment_bodies.append(child["text"])
        combined = " ".join(comment_bodies)
        assert "automatically transitioned to Approved" in combined

    def test_failing_review_no_transition(self, art_dir, jira):
        """--auto-approve + failing review → no transition."""
        jira.create("RHOAIENG-1234", "Test Initiative", "Original.", issue_type="Initiative")
        _write(f"{art_dir}/initiative-originals/RHOAIENG-1234.md", "Original.")
        _write(
            f"{art_dir}/initiatives/RHOAIENG-1234.md",
            "---\ninitiative_id: RHOAIENG-1234\ntitle: Test Initiative\n"
            "priority: Major\nstatus: Ready\n---\nRevised.",
        )
        _write(
            f"{art_dir}/initiative-reviews/RHOAIENG-1234-review.md",
            REJECT_REVIEW_FM.format(initiative_id="RHOAIENG-1234"),
        )

        r = _run_submit(art_dir, jira.url, ["--auto-approve"])
        assert r.returncode == 0, r.stderr
        assert "Transitioned to Approved" not in r.stdout

        issue = jira.get("RHOAIENG-1234")
        assert issue["fields"]["status"]["name"] != "Approved"


class TestInfeasibleSubmit:
    def test_infeasible_initiative_submitted_with_fail_label(self, art_dir, jira):
        """Infeasible initiative → submitted with feasibility-fail label."""
        jira.create("RHOAIENG-1234", "Test Initiative", "Original.", issue_type="Initiative")
        _write(f"{art_dir}/initiative-originals/RHOAIENG-1234.md", "Original.")
        _write(
            f"{art_dir}/initiatives/RHOAIENG-1234.md",
            "---\ninitiative_id: RHOAIENG-1234\ntitle: Test Initiative\n"
            "priority: Major\nstatus: Ready\n---\nRevised.",
        )
        review = _review("RHOAIENG-1234").replace(
            "feasibility: feasible", "feasibility: infeasible"
        )
        _write(f"{art_dir}/initiative-reviews/RHOAIENG-1234-review.md", review)

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "Updated" in r.stdout

        issue = jira.get("RHOAIENG-1234")
        assert "initiative-feasibility-fail" in issue["fields"]["labels"]


class TestAlignmentLabelIntegration:
    @pytest.mark.parametrize(
        "alignment,expected_label",
        [
            ("strong", "initiative-alignment-strong"),
            ("partial", "initiative-alignment-partial"),
            ("weak", "initiative-alignment-weak"),
        ],
    )
    def test_alignment_label_applied_to_jira(self, art_dir, jira, alignment, expected_label):
        """Each alignment verdict → matching label on the Jira issue."""
        _write(f"{art_dir}/initiatives/INIT-001.md", TASK_FM.format(initiative_id="INIT-001"))
        _write(
            f"{art_dir}/initiative-reviews/INIT-001-review.md",
            _review("INIT-001", alignment=alignment),
        )

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        issues = jira.search("project = RHOAIENG")
        issue = jira.get(issues[0]["key"])
        assert expected_label in issue["fields"]["labels"]


class TestParentKeyForwarding:
    """An Initiative's parent is a RHAISTRAT Outcome, not a RHOAIENG key.

    The local-id guard in submit.py therefore excludes `INIT-`, rather than
    requiring the type's own jira_prefix, which would drop a valid parent.
    """

    TASK_WITH_PARENT = (
        "---\ninitiative_id: INIT-001\ntitle: Test Initiative\n"
        "priority: Major\nstatus: Ready\nparent_key: RHAISTRAT-1234\n---\n\nBody.\n"
    )
    TASK_WITH_LOCAL_PARENT = (
        "---\ninitiative_id: INIT-002\ntitle: Test Initiative\n"
        "priority: Major\nstatus: Ready\nparent_key: INIT-001\n---\n\nBody.\n"
    )

    def test_strategic_outcome_parent_is_forwarded(self, art_dir, jira):
        """RHAISTRAT parent → sent to Jira as the parent field."""
        jira.create("RHAISTRAT-1234", "Outcome", "Outcome body.")
        _write(f"{art_dir}/initiatives/INIT-001.md", self.TASK_WITH_PARENT)
        _write(f"{art_dir}/initiative-reviews/INIT-001-review.md", _review("INIT-001"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert "is not in Jira" not in r.stdout

        issues = jira.search("project = RHOAIENG")
        assert len(issues) == 1
        parent = jira.get(issues[0]["key"])["fields"].get("parent")
        assert parent and parent["key"] == "RHAISTRAT-1234"

    def test_local_parent_is_not_forwarded(self, art_dir, jira):
        """INIT- parent → dropped, Initiative created standalone."""
        _write(f"{art_dir}/initiatives/INIT-002.md", self.TASK_WITH_LOCAL_PARENT)
        _write(f"{art_dir}/initiative-reviews/INIT-002-review.md", _review("INIT-002"))

        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr

        issues = jira.search("project = RHOAIENG")
        assert len(issues) == 1
        assert jira.get(issues[0]["key"])["fields"].get("parent") is None
        assert "is not in Jira" in r.stdout


class TestResolveLine:
    """--type initiative is rung 1 of the design §5 ladder: exactly one D3 line on stderr, and
    the run itself is unchanged (the initiative binding is the descriptor's — no override)."""

    def test_explicit_type_prints_exactly_one_line_and_updates(self, art_dir, jira):
        jira.create("RHOAIENG-1234", "Test Initiative", "Original.", issue_type="Initiative")
        _write(f"{art_dir}/initiative-originals/RHOAIENG-1234.md", "Original.")
        _write(
            f"{art_dir}/initiatives/RHOAIENG-1234.md",
            "---\ninitiative_id: RHOAIENG-1234\ntitle: Test Initiative\n"
            "priority: Major\nstatus: Ready\ntype: initiative\ntracker_ref: RHOAIENG-1234\n"
            "---\nRevised.",
        )
        _write(
            f"{art_dir}/initiative-reviews/RHOAIENG-1234-review.md",
            _review("RHOAIENG-1234", auto_revised="true"),
        )
        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert r.stderr.startswith("TYPE RESOLVED: initiative (--type)\n")
        assert r.stderr.count("TYPE RESOLVED") == 1 and "Traceback" not in r.stderr
        assert "RHOAIENG-1234: Updated" in r.stdout
        assert _read_frontmatter(f"{art_dir}/initiatives/RHOAIENG-1234.md")["status"] == "Submitted"

    def test_a_feature_request_behind_an_initiative_key_is_skipped(self, art_dir, jira):
        jira.create("RHOAIENG-1234", "Test Initiative", "Original.")  # a Feature Request
        _write(f"{art_dir}/initiative-originals/RHOAIENG-1234.md", "Original.")
        _write(
            f"{art_dir}/initiatives/RHOAIENG-1234.md",
            "---\ninitiative_id: RHOAIENG-1234\ntitle: Test Initiative\n"
            "priority: Major\nstatus: Ready\n---\nRevised.",
        )
        _write(
            f"{art_dir}/initiative-reviews/RHOAIENG-1234-review.md",
            _review("RHOAIENG-1234", auto_revised="true"),
        )
        r = _run_submit(art_dir, jira.url)
        assert r.returncode == 0, r.stderr
        assert (
            "Reason: binding mismatch — RHOAIENG-1234 is (RHOAIENG, Feature Request) in Jira "
            "but type initiative binds (RHOAIENG, Initiative)"
        ) in r.stdout
        assert "Updated" not in r.stdout
        assert _read_frontmatter(f"{art_dir}/initiatives/RHOAIENG-1234.md")["status"] == "Ready"
