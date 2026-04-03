#!/usr/bin/env python3
"""Integration tests for submit.py using a mock Jira HTTP server.

Unlike test_submit.py (which only tests --dry-run plan building), these tests
run the full execution path against a local HTTP server that records requests
and returns plausible Jira API responses.
"""
import json
import os
import subprocess
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import pytest
import yaml

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "submit.py")

# Counter for generated Jira keys
_next_key = [2000]


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


def _text_to_adf(text):
    """Build a minimal ADF doc from plain text."""
    return {
        "type": "doc", "version": 1,
        "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": text}
        ]}],
    }


class JiraHandler(BaseHTTPRequestHandler):
    """Mock Jira REST API handler that records requests."""

    def do_GET(self):
        self._record("GET")

        # GET /issue/KEY?fields=description — conflict check
        if "/rest/api/3/issue/" in self.path and "fields=" in self.path:
            key = self.path.split("/rest/api/3/issue/")[1].split("?")[0]
            desc_md = self.server.original_descriptions.get(key)
            adf = _text_to_adf(desc_md) if desc_md is not None else None
            self._json(200, {"key": key, "fields": {"description": adf}})
            return

        self._json(404, {"error": "not found"})

    def do_POST(self):
        body = self._record("POST")

        # POST /issue — create
        if self.path == "/rest/api/3/issue":
            key = f"RHAIRFE-{_next_key[0]}"
            _next_key[0] += 1
            self._json(201, {"key": key, "id": "99999"})
            return

        # POST /issue/KEY/comment
        if "/comment" in self.path:
            self._json(201, {"id": "10001"})
            return

        self._json(404, {"error": "not found"})

    def do_PUT(self):
        self._record("PUT")

        if "/rest/api/3/issue/" in self.path:
            self.send_response(204)
            self.end_headers()
            return

        self._json(404, {"error": "not found"})

    def _record(self, method):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else None
        self.server.requests.append({
            "method": method, "path": self.path, "body": body,
        })
        return body

    def _json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def mock_jira():
    """Start a mock Jira server, yield (url, server), then shut down."""
    server = HTTPServer(("127.0.0.1", 0), JiraHandler)
    server.requests = []
    server.original_descriptions = {}
    url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    yield url, server
    server.shutdown()


@pytest.fixture(autouse=True)
def reset_key_counter():
    """Reset the mock key counter between tests."""
    _next_key[0] = 2000
    yield


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
    """Run submit.py (non-dry-run) against the mock server."""
    env = {
        **os.environ,
        "JIRA_SERVER": server_url,
        "JIRA_USER": "test@example.com",
        "JIRA_TOKEN": "test-token",
    }
    return subprocess.run(
        ["python3", SCRIPT, "--artifacts-dir", artifacts_dir],
        capture_output=True, text=True, env=env,
    )


def _filter_requests(server, method=None, path_contains=None):
    """Filter recorded requests."""
    reqs = server.requests
    if method:
        reqs = [r for r in reqs if r["method"] == method]
    if path_contains:
        reqs = [r for r in reqs if path_contains in r["path"]]
    return reqs


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
    def test_posts_correct_fields(self, art_dir, mock_jira):
        """New RFE → POST /issue with correct project, type, priority."""
        url, server = mock_jira
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               _review("RFE-001"))

        r = _run_submit(art_dir, url)
        assert r.returncode == 0, r.stderr

        creates = [r for r in _filter_requests(server, "POST", "/issue")
                   if "/comment" not in r["path"]]
        assert len(creates) == 1
        fields = creates[0]["body"]["fields"]
        assert fields["project"]["key"] == "RHAIRFE"
        assert fields["issuetype"]["name"] == "Feature Request"
        assert fields["summary"] == "Test RFE"
        assert fields["priority"]["name"] == "Major"

    def test_includes_labels(self, art_dir, mock_jira):
        """New RFE → labels include auto-created and rubric-pass."""
        url, server = mock_jira
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               _review("RFE-001"))

        r = _run_submit(art_dir, url)
        assert r.returncode == 0, r.stderr

        creates = [r for r in _filter_requests(server, "POST", "/issue")
                   if "/comment" not in r["path"]]
        labels = creates[0]["body"]["fields"]["labels"]
        assert "rfe-creator-auto-created" in labels
        assert "rfe-creator-autofix-rubric-pass" in labels

    def test_renames_files(self, art_dir, mock_jira):
        """New RFE → RFE-001.md renamed to RHAIRFE-2000.md."""
        url, server = mock_jira
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               _review("RFE-001"))

        r = _run_submit(art_dir, url)
        assert r.returncode == 0, r.stderr

        assert not os.path.exists(f"{art_dir}/rfe-tasks/RFE-001.md")
        assert os.path.exists(f"{art_dir}/rfe-tasks/RHAIRFE-2000.md")
        fm = _read_frontmatter(f"{art_dir}/rfe-tasks/RHAIRFE-2000.md")
        assert fm["rfe_id"] == "RHAIRFE-2000"


class TestUpdateExistingRFE:
    def _setup_existing(self, art_dir, server, original, revised):
        server.original_descriptions["RHAIRFE-1234"] = original
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", original)
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n---\n{revised}")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               _review("RHAIRFE-1234", auto_revised="true"))

    def test_puts_description(self, art_dir, mock_jira):
        """Existing RFE with changes → PUT with description."""
        url, server = mock_jira
        self._setup_existing(art_dir, server, "Original.", "Revised.")

        r = _run_submit(art_dir, url)
        assert r.returncode == 0, r.stderr
        assert "Updated" in r.stdout

        puts = _filter_requests(server, "PUT", "/issue/RHAIRFE-1234")
        field_puts = [p for p in puts if p["body"] and "fields" in p["body"]]
        assert len(field_puts) == 1
        assert "description" in field_puts[0]["body"]["fields"]

    def test_adds_labels_separately(self, art_dir, mock_jira):
        """Update → labels added via separate PUT with update.labels."""
        url, server = mock_jira
        self._setup_existing(art_dir, server, "Original.", "Revised.")

        r = _run_submit(art_dir, url)
        assert r.returncode == 0, r.stderr

        puts = _filter_requests(server, "PUT", "/issue/RHAIRFE-1234")
        label_puts = [p for p in puts if p["body"]
                      and "update" in p["body"]
                      and "labels" in p["body"].get("update", {})]
        assert len(label_puts) == 1
        added = [op["add"] for op in label_puts[0]["body"]["update"]["labels"]
                 if "add" in op]
        assert "rfe-creator-auto-revised" in added
        assert "rfe-creator-autofix-rubric-pass" in added

    def test_sets_status_submitted(self, art_dir, mock_jira):
        """Existing RFE after update → frontmatter status = Submitted."""
        url, server = mock_jira
        self._setup_existing(art_dir, server, "Original.", "Revised.")

        r = _run_submit(art_dir, url)
        assert r.returncode == 0, r.stderr

        fm = _read_frontmatter(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md")
        assert fm["status"] == "Submitted"


class TestLabelOnly:
    def test_no_description_put(self, art_dir, mock_jira):
        """Unchanged content → label PUT only, no fields PUT."""
        url, server = mock_jira
        body = "Same content.\n"
        server.original_descriptions["RHAIRFE-1234"] = body
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", body)
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n---\n{body}")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               _review("RHAIRFE-1234"))

        r = _run_submit(art_dir, url)
        assert r.returncode == 0, r.stderr

        puts = _filter_requests(server, "PUT", "/issue/RHAIRFE-1234")
        field_puts = [p for p in puts if p["body"] and "fields" in p["body"]]
        label_puts = [p for p in puts if p["body"] and "update" in p["body"]]
        assert len(field_puts) == 0
        assert len(label_puts) == 1


class TestRemoveLabels:
    def test_sends_remove_operation(self, art_dir, mock_jira):
        """Rejected RFE with stale rubric-pass → PUT with remove op."""
        url, server = mock_jira
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n"
               f"original_labels:\n- rfe-creator-autofix-rubric-pass\n"
               f"---\n\n## Problem\n\nContent.\n")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"))

        r = _run_submit(art_dir, url)
        assert r.returncode == 0, r.stderr
        assert "Removed labels" in r.stdout

        puts = _filter_requests(server, "PUT", "/issue/RHAIRFE-1234")
        assert len(puts) == 1
        label_ops = puts[0]["body"]["update"]["labels"]
        removed = [op["remove"] for op in label_ops if "remove" in op]
        assert "rfe-creator-autofix-rubric-pass" in removed

    def test_no_api_call_on_plain_reject(self, art_dir, mock_jira):
        """Rejected RFE without rubric-pass → no PUT at all."""
        url, server = mock_jira
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               TASK_FM.format(rfe_id="RHAIRFE-1234"))
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"))

        r = _run_submit(art_dir, url)
        assert r.returncode == 0, r.stderr

        non_get = [r for r in server.requests if r["method"] != "GET"]
        assert len(non_get) == 0

    def test_does_not_update_frontmatter_status(self, art_dir, mock_jira):
        """Remove labels must NOT set status to Submitted."""
        url, server = mock_jira
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n"
               f"original_labels:\n- rfe-creator-autofix-rubric-pass\n"
               f"---\n\n## Problem\n\nContent.\n")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"))

        r = _run_submit(art_dir, url)
        assert r.returncode == 0, r.stderr

        fm = _read_frontmatter(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md")
        assert fm["status"] == "Ready"  # NOT "Submitted"

    def test_not_in_snapshot_update(self, art_dir, mock_jira):
        """Remove labels must NOT update the snapshot."""
        url, server = mock_jira
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

        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n"
               f"original_labels:\n- rfe-creator-autofix-rubric-pass\n"
               f"---\n\n## Problem\n\nContent.\n")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"))

        r = _run_submit(art_dir, url)
        assert r.returncode == 0, r.stderr

        # Snapshot should be unchanged (no submitted hashes)
        with open(snap_path) as f:
            data = yaml.safe_load(f)
        assert data["issues"]["RHAIRFE-1234"] == "original-hash"


class TestConflictDetection:
    def test_conflict_prevents_update(self, art_dir, mock_jira):
        """Jira description differs from original → skip, no PUT."""
        url, server = mock_jira
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", "Original.")
        server.original_descriptions["RHAIRFE-1234"] = "Edited by someone."
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n---\nOur revision.")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               _review("RHAIRFE-1234", auto_revised="true"))

        r = _run_submit(art_dir, url)
        assert r.returncode == 0, r.stderr
        assert "Skipping" in r.stdout

        puts = _filter_requests(server, "PUT")
        assert len(puts) == 0


class TestCommentPosting:
    def test_removed_context_comment(self, art_dir, mock_jira):
        """RFE with removed-context YAML → POST comment."""
        url, server = mock_jira
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

        r = _run_submit(art_dir, url)
        assert r.returncode == 0, r.stderr
        assert "Posted removed-context comment" in r.stdout

        comments = _filter_requests(server, "POST", "/comment")
        assert len(comments) >= 1
        assert "body" in comments[0]["body"]

    def test_needs_attention_comment(self, art_dir, mock_jira):
        """RFE with needs_attention → POST comment with reason."""
        url, server = mock_jira
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               _review("RFE-001", needs_attention="true",
                       extra_fields="needs_attention_reason: Unclear scope\n"))

        r = _run_submit(art_dir, url)
        assert r.returncode == 0, r.stderr
        assert "needs-attention comment" in r.stdout

        comments = _filter_requests(server, "POST", "/comment")
        assert len(comments) >= 1


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

    def test_snapshot_updated_on_create(self, art_dir, mock_jira):
        """Create → snapshot updated with new issue hash."""
        url, server = mock_jira
        snap_path = self._seed_snapshot(art_dir, {"RHAIRFE-1": "existing"})
        _write(f"{art_dir}/rfe-tasks/RFE-001.md",
               TASK_FM.format(rfe_id="RFE-001"))
        _write(f"{art_dir}/rfe-reviews/RFE-001-review.md",
               _review("RFE-001"))

        r = _run_submit(art_dir, url)
        assert r.returncode == 0, r.stderr

        with open(snap_path) as f:
            data = yaml.safe_load(f)
        assert "RHAIRFE-2000" in data["issues"]
        assert isinstance(data["issues"]["RHAIRFE-2000"], str)
        assert len(data["issues"]["RHAIRFE-2000"]) == 64  # SHA256 hex
        # Original issue still present
        assert data["issues"]["RHAIRFE-1"] == "existing"

    def test_snapshot_updated_on_update(self, art_dir, mock_jira):
        """Update → snapshot updated with revised hash."""
        url, server = mock_jira
        snap_path = self._seed_snapshot(art_dir,
                                        {"RHAIRFE-1234": "old-hash"})
        server.original_descriptions["RHAIRFE-1234"] = "Original."
        _write(f"{art_dir}/rfe-originals/RHAIRFE-1234.md", "Original.")
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               f"---\nrfe_id: RHAIRFE-1234\ntitle: Test RFE\n"
               f"priority: Major\nstatus: Ready\n---\nRevised.")
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               _review("RHAIRFE-1234"))

        r = _run_submit(art_dir, url)
        assert r.returncode == 0, r.stderr

        with open(snap_path) as f:
            data = yaml.safe_load(f)
        assert "RHAIRFE-1234" in data["issues"]
        assert len(data["issues"]["RHAIRFE-1234"]) == 64
        assert data["issues"]["RHAIRFE-1234"] != "old-hash"

    def test_no_update_when_all_skipped(self, art_dir, mock_jira):
        """All RFEs rejected/skipped → snapshot unchanged."""
        url, server = mock_jira
        snap_path = self._seed_snapshot(art_dir,
                                        {"RHAIRFE-1234": "existing"})
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1234.md",
               TASK_FM.format(rfe_id="RHAIRFE-1234"))
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1234-review.md",
               REJECT_REVIEW_FM.format(rfe_id="RHAIRFE-1234"))

        r = _run_submit(art_dir, url)
        assert r.returncode == 0, r.stderr

        with open(snap_path) as f:
            data = yaml.safe_load(f)
        # Snapshot untouched — still just the original issue
        assert data["issues"] == {"RHAIRFE-1234": "existing"}
