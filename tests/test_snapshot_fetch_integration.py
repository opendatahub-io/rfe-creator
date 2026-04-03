#!/usr/bin/env python3
"""Integration tests for snapshot_fetch.py — cmd_fetch exercised
end-to-end against a mock Jira HTTP server.

Covers the full pipeline: first fetch, incremental fetch with change
detection, snapshot update, and data-dir fallback.
"""
import io
import json
import os
import sys
import threading
import urllib.parse
from contextlib import redirect_stdout
from http.server import HTTPServer, BaseHTTPRequestHandler
from types import SimpleNamespace

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import snapshot_fetch
from snapshot_fetch import cmd_fetch, compute_content_hash, update_snapshot_hashes


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _read_ids(path):
    """Read ID file, return list of non-blank lines."""
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


def _text_to_adf(text):
    return {
        "type": "doc", "version": 1,
        "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": text}
        ]}],
    }


# ── Mock Jira Server ─────────────────────────────────────────────────────────

class JiraHandler(BaseHTTPRequestHandler):
    """Mock Jira that serves search results.

    server.issues: dict of {key: description} — all current issues
    """

    def do_GET(self):
        if "/search/jql" not in self.path:
            self._json(404, {"error": "not found"})
            return

        issues = []
        for key, desc in self.server.issues.items():
            adf = _text_to_adf(desc) if desc else None
            issues.append({
                "key": key,
                "fields": {"description": adf, "labels": []},
            })

        self._json(200, {"issues": issues, "isLast": True})

    def _json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_jira():
    server = HTTPServer(("127.0.0.1", 0), JiraHandler)
    server.issues = {}
    url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    yield url, server
    server.shutdown()


@pytest.fixture
def work_dirs(tmp_path, monkeypatch):
    """Redirect snapshot_fetch module-level paths to temp directories."""
    snap_dir = str(tmp_path / "artifacts" / "auto-fix-runs")
    os.makedirs(snap_dir)
    monkeypatch.setattr(snapshot_fetch, "SNAPSHOT_DIR", snap_dir)
    return SimpleNamespace(
        snapshot_dir=snap_dir,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fetch_args(tmp_path, jql="project = RHAIRFE", limit=None, data_dir=None):
    return SimpleNamespace(
        jql=jql, limit=limit, data_dir=data_dir,
        ids_file=str(tmp_path / "ids.txt"),
        changed_file=str(tmp_path / "changed.txt"),
    )


def _run_fetch(args):
    """Run cmd_fetch, capturing stdout. Returns stdout string."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_fetch(args)
    return buf.getvalue()


def _seed_snapshot(work_dirs, issues, query_ts="2026-04-01T00:00:00Z"):
    """Write a previous snapshot."""
    snap = {
        "query_timestamp": query_ts,
        "timestamp": "2026-04-01T00:00:01Z",
        "issues": issues,
    }
    path = os.path.join(work_dirs.snapshot_dir,
                        "issue-snapshot-20260401-000000.yaml")
    with open(path, "w") as f:
        yaml.dump(snap, f, default_flow_style=False, sort_keys=False)


def _jira_env(monkeypatch, url):
    monkeypatch.setenv("JIRA_SERVER", url)
    monkeypatch.setenv("JIRA_USER", "test@example.com")
    monkeypatch.setenv("JIRA_TOKEN", "test-token")


# ── cmd_fetch: First Run ─────────────────────────────────────────────────────

class TestCmdFetchFirstRun:
    def test_all_issues_are_new(self, work_dirs, mock_jira, monkeypatch,
                                tmp_path):
        """First run (no snapshot) → all issues NEW, correct stdout."""
        url, server = mock_jira
        server.issues = {
            "RHAIRFE-1": "Description one.",
            "RHAIRFE-2": "Description two.",
        }
        _jira_env(monkeypatch, url)
        args = _fetch_args(tmp_path)

        stdout = _run_fetch(args)

        assert "TOTAL=2" in stdout
        assert "CHANGED=0" in stdout
        assert "NEW=2" in stdout
        assert set(_read_ids(args.ids_file)) == {"RHAIRFE-1", "RHAIRFE-2"}
        assert _read_ids(args.changed_file) == []

    def test_snapshot_written(self, work_dirs, mock_jira, monkeypatch,
                              tmp_path):
        """Fetch writes snapshot directly to artifacts with hashes."""
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Content."}
        _jira_env(monkeypatch, url)
        args = _fetch_args(tmp_path)

        _run_fetch(args)

        snaps = [f for f in os.listdir(work_dirs.snapshot_dir)
                 if f.startswith("issue-snapshot-")]
        assert len(snaps) == 1
        with open(os.path.join(work_dirs.snapshot_dir, snaps[0])) as f:
            snap = yaml.safe_load(f)
        assert "RHAIRFE-1" in snap["issues"]
        assert "query_timestamp" in snap
        assert len(snap["issues"]["RHAIRFE-1"]) == 64


# ── cmd_fetch: Incremental Run ───────────────────────────────────────────────

class TestCmdFetchIncremental:
    def test_unchanged_excluded(self, work_dirs, mock_jira, monkeypatch,
                                tmp_path):
        """Issue with same hash → not in output IDs."""
        url, server = mock_jira
        same_hash = compute_content_hash(_text_to_adf("Same content."))
        _seed_snapshot(work_dirs, {"RHAIRFE-1": same_hash})
        server.issues = {"RHAIRFE-1": "Same content."}
        _jira_env(monkeypatch, url)

        stdout = _run_fetch(_fetch_args(tmp_path))

        assert "TOTAL=0" in stdout

    def test_changed_detected(self, work_dirs, mock_jira, monkeypatch,
                              tmp_path):
        """Issue with different hash → in changed file."""
        url, server = mock_jira
        _seed_snapshot(work_dirs, {"RHAIRFE-1": "old-hash"})
        server.issues = {"RHAIRFE-1": "New content."}
        _jira_env(monkeypatch, url)

        args = _fetch_args(tmp_path)
        stdout = _run_fetch(args)

        assert "TOTAL=1" in stdout
        assert "CHANGED=1" in stdout
        assert "NEW=0" in stdout
        assert _read_ids(args.ids_file) == ["RHAIRFE-1"]
        assert _read_ids(args.changed_file) == ["RHAIRFE-1"]

    def test_new_detected(self, work_dirs, mock_jira, monkeypatch, tmp_path):
        """Issue not in previous snapshot → new."""
        url, server = mock_jira
        existing_hash = compute_content_hash(_text_to_adf("Existing."))
        _seed_snapshot(work_dirs, {"RHAIRFE-1": existing_hash})
        server.issues = {
            "RHAIRFE-1": "Existing.",
            "RHAIRFE-2": "Brand new.",
        }
        _jira_env(monkeypatch, url)

        args = _fetch_args(tmp_path)
        stdout = _run_fetch(args)

        assert "TOTAL=1" in stdout
        assert "NEW=1" in stdout
        assert _read_ids(args.ids_file) == ["RHAIRFE-2"]

    def test_limit_caps_output(self, work_dirs, mock_jira, monkeypatch,
                               tmp_path):
        """--limit caps the number of output IDs."""
        url, server = mock_jira
        _seed_snapshot(work_dirs, {})
        server.issues = {
            "RHAIRFE-1": "One.",
            "RHAIRFE-2": "Two.",
            "RHAIRFE-3": "Three.",
        }
        _jira_env(monkeypatch, url)

        args = _fetch_args(tmp_path, limit=2)
        stdout = _run_fetch(args)

        assert "TOTAL=2" in stdout
        assert len(_read_ids(args.ids_file)) == 2


# ── cmd_fetch with --data-dir ────────────────────────────────────────────────

class TestCmdFetchWithDataDir:
    def _make_data_dir(self, tmp_path, snapshot_issues):
        """Create a data-dir structure with one run."""
        data_dir = str(tmp_path / "data-repo")
        run_dir = os.path.join(data_dir, "20260401-120000",
                               "auto-fix-runs")
        os.makedirs(run_dir)

        snap = {
            "query_timestamp": "2026-04-01T00:00:00Z",
            "timestamp": "2026-04-01T00:00:01Z",
            "issues": snapshot_issues,
        }
        snap_path = os.path.join(run_dir,
                                 "issue-snapshot-20260401-120000.yaml")
        with open(snap_path, "w") as f:
            yaml.dump(snap, f)

        os.symlink("20260401-120000", os.path.join(data_dir, "latest"))
        return data_dir

    def test_falls_back_to_data_dir(self, work_dirs, mock_jira, monkeypatch,
                                    tmp_path):
        """No local snapshot → uses data-dir snapshot for diffing."""
        url, server = mock_jira
        data_dir = self._make_data_dir(tmp_path,
                                       {"RHAIRFE-1": "old-hash"})
        server.issues = {"RHAIRFE-1": "Changed content."}
        _jira_env(monkeypatch, url)

        args = _fetch_args(tmp_path, data_dir=data_dir)
        stdout = _run_fetch(args)

        assert "CHANGED=1" in stdout
        assert _read_ids(args.ids_file) == ["RHAIRFE-1"]


# ── Multi-Run Pipeline ──────────────────────────────────────────────────────

class TestMultiRunPipeline:
    """Simulate realistic multi-run CI cycles.

    Each test exercises the full flow: fetch → (simulate submit) → fetch,
    modeling the snapshot as a cache that skips already-processed issues
    unless a user changes the description in Jira.
    """

    def test_steady_state_skips_unchanged(self, work_dirs, mock_jira,
                                          monkeypatch, tmp_path):
        """Run 1 processes everything. Run 2 sees nothing to do."""
        url, server = mock_jira
        server.issues = {
            "RHAIRFE-1": "Description one.",
            "RHAIRFE-2": "Description two.",
        }
        _jira_env(monkeypatch, url)

        # Run 1: first fetch — all new
        stdout1 = _run_fetch(_fetch_args(tmp_path))
        assert "TOTAL=2" in stdout1
        assert "NEW=2" in stdout1

        # Run 2: nothing changed — nothing to process
        stdout2 = _run_fetch(_fetch_args(tmp_path))
        assert "TOTAL=0" in stdout2

    def test_user_edits_after_submit(self, work_dirs, mock_jira,
                                     monkeypatch, tmp_path):
        """Run 1: fetch + submit. Run 2: user edits → re-process.
        Run 3: nothing changed → skip."""
        url, server = mock_jira
        server.issues = {
            "RHAIRFE-1": "Original description.",
            "RHAIRFE-2": "Another description.",
        }
        _jira_env(monkeypatch, url)

        # Run 1: fetch all
        _run_fetch(_fetch_args(tmp_path))

        # Run 1: submit revises RHAIRFE-1, updates snapshot
        revised_hash = compute_content_hash(
            _text_to_adf("Auto-revised description."))
        update_snapshot_hashes(
            {"RHAIRFE-1": revised_hash}, work_dirs.snapshot_dir)
        server.issues["RHAIRFE-1"] = "Auto-revised description."

        # Between runs: user edits RHAIRFE-1 in Jira
        server.issues["RHAIRFE-1"] = "User rewrote this after our fix."

        # Run 2: detects user's edit, skips RHAIRFE-2
        args2 = _fetch_args(tmp_path)
        stdout2 = _run_fetch(args2)
        assert "TOTAL=1" in stdout2
        assert "CHANGED=1" in stdout2
        assert _read_ids(args2.ids_file) == ["RHAIRFE-1"]
        assert _read_ids(args2.changed_file) == ["RHAIRFE-1"]

        # Run 3: nothing changed — nothing to process
        stdout3 = _run_fetch(_fetch_args(tmp_path))
        assert "TOTAL=0" in stdout3

    def test_submit_without_user_edit(self, work_dirs, mock_jira,
                                      monkeypatch, tmp_path):
        """Run 1: fetch + submit. Run 2: no user edits → skip our own
        changes."""
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Original."}
        _jira_env(monkeypatch, url)

        # Run 1: fetch
        _run_fetch(_fetch_args(tmp_path))

        # Run 1: submit revises RHAIRFE-1, updates snapshot
        revised_hash = compute_content_hash(
            _text_to_adf("We revised this."))
        update_snapshot_hashes(
            {"RHAIRFE-1": revised_hash}, work_dirs.snapshot_dir)
        server.issues["RHAIRFE-1"] = "We revised this."

        # Run 2: our own change is in the snapshot — skip
        stdout2 = _run_fetch(_fetch_args(tmp_path))
        assert "TOTAL=0" in stdout2

    def test_new_issue_created_by_submit(self, work_dirs, mock_jira,
                                         monkeypatch, tmp_path):
        """Run 1: fetch + create new issue. Run 2: new issue not
        re-flagged. Run 3: user edits the new issue → detected."""
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Existing."}
        _jira_env(monkeypatch, url)

        # Run 1: fetch
        _run_fetch(_fetch_args(tmp_path))

        # Run 1: submit creates RHAIRFE-2, updates snapshot
        new_hash = compute_content_hash(
            _text_to_adf("We created this."))
        update_snapshot_hashes(
            {"RHAIRFE-2": new_hash}, work_dirs.snapshot_dir)
        server.issues["RHAIRFE-2"] = "We created this."

        # Run 2: our new issue is already in the snapshot — skip
        stdout2 = _run_fetch(_fetch_args(tmp_path))
        assert "TOTAL=0" in stdout2

        # Between runs: user edits the new issue
        server.issues["RHAIRFE-2"] = "User improved our new issue."

        # Run 3: detects the edit
        args3 = _fetch_args(tmp_path)
        stdout3 = _run_fetch(args3)
        assert "TOTAL=1" in stdout3
        assert "CHANGED=1" in stdout3
        assert _read_ids(args3.ids_file) == ["RHAIRFE-2"]

    def test_issue_leaves_scope(self, work_dirs, mock_jira,
                                monkeypatch, tmp_path):
        """Issue closed between runs → silently dropped, not flagged."""
        url, server = mock_jira
        server.issues = {
            "RHAIRFE-1": "Open issue.",
            "RHAIRFE-2": "Another open issue.",
        }
        _jira_env(monkeypatch, url)

        # Run 1
        stdout1 = _run_fetch(_fetch_args(tmp_path))
        assert "TOTAL=2" in stdout1

        # Between runs: RHAIRFE-2 gets closed (no longer in JQL results)
        del server.issues["RHAIRFE-2"]

        # Run 2: RHAIRFE-2 gone, RHAIRFE-1 unchanged — nothing to do
        stdout2 = _run_fetch(_fetch_args(tmp_path))
        assert "TOTAL=0" in stdout2

    def test_mixed_activity_across_runs(self, work_dirs, mock_jira,
                                        monkeypatch, tmp_path):
        """Multiple runs with a mix of edits, new issues, and closures."""
        url, server = mock_jira
        server.issues = {
            "RHAIRFE-1": "Issue one.",
            "RHAIRFE-2": "Issue two.",
        }
        _jira_env(monkeypatch, url)

        # Run 1: first fetch — all new
        stdout1 = _run_fetch(_fetch_args(tmp_path))
        assert "TOTAL=2" in stdout1
        assert "NEW=2" in stdout1

        # Run 1: submit revises RHAIRFE-1
        revised_hash = compute_content_hash(
            _text_to_adf("Revised issue one."))
        update_snapshot_hashes(
            {"RHAIRFE-1": revised_hash}, work_dirs.snapshot_dir)
        server.issues["RHAIRFE-1"] = "Revised issue one."

        # Between runs: user edits RHAIRFE-2, new RHAIRFE-3 filed
        server.issues["RHAIRFE-2"] = "User rewrote issue two."
        server.issues["RHAIRFE-3"] = "Brand new issue three."

        # Run 2: RHAIRFE-1 skip (our change), RHAIRFE-2 changed,
        #         RHAIRFE-3 new
        args2 = _fetch_args(tmp_path)
        stdout2 = _run_fetch(args2)
        assert "TOTAL=2" in stdout2
        assert "CHANGED=1" in stdout2
        assert "NEW=1" in stdout2
        ids2 = _read_ids(args2.ids_file)
        assert "RHAIRFE-2" in ids2
        assert "RHAIRFE-3" in ids2
        assert "RHAIRFE-1" not in ids2

        # Between runs: RHAIRFE-2 closed, nothing else changes
        del server.issues["RHAIRFE-2"]

        # Run 3: RHAIRFE-2 gone, rest unchanged — nothing to do
        stdout3 = _run_fetch(_fetch_args(tmp_path))
        assert "TOTAL=0" in stdout3
