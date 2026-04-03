#!/usr/bin/env python3
"""Integration tests for snapshot_fetch.py — cmd_fetch exercised
end-to-end against a jira-emulator server.

Covers the full pipeline: first fetch, incremental fetch with change
detection, snapshot update, and data-dir fallback.
"""
import io
import os
import subprocess
import sys
import threading
from contextlib import redirect_stdout
from types import SimpleNamespace

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

CLONE_SCRIPT = os.path.join(os.path.dirname(__file__), "..",
                            "scripts", "clone_results_repo.py")

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


# ── Fixtures ─────────────────────────────────────────────────────────────────

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
    monkeypatch.setenv("JIRA_USER", "admin")
    monkeypatch.setenv("JIRA_TOKEN", "admin")


# ── cmd_fetch: First Run ─────────────────────────────────────────────────────

class TestCmdFetchFirstRun:
    def test_all_issues_are_new(self, work_dirs, jira, monkeypatch, tmp_path):
        """First run (no snapshot) → all issues NEW, correct stdout."""
        jira.create("RHAIRFE-1", "Issue one", "Description one.")
        jira.create("RHAIRFE-2", "Issue two", "Description two.")
        _jira_env(monkeypatch, jira.url)
        args = _fetch_args(tmp_path)

        stdout = _run_fetch(args)

        assert "TOTAL=2" in stdout
        assert "CHANGED=0" in stdout
        assert "NEW=2" in stdout
        assert set(_read_ids(args.ids_file)) == {"RHAIRFE-1", "RHAIRFE-2"}
        assert _read_ids(args.changed_file) == []

    def test_snapshot_written(self, work_dirs, jira, monkeypatch, tmp_path):
        """Fetch writes snapshot directly to artifacts with hashes."""
        jira.create("RHAIRFE-1", "Issue one", "Content.")
        _jira_env(monkeypatch, jira.url)
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
    def test_unchanged_included_not_changed(self, work_dirs, jira,
                                               monkeypatch, tmp_path):
        """Issue with same hash → in output IDs but not in changed file."""
        jira.create("RHAIRFE-1", "Issue one", "Same content.")
        same_hash = compute_content_hash(_text_to_adf("Same content."))
        _seed_snapshot(work_dirs, {"RHAIRFE-1": same_hash})
        _jira_env(monkeypatch, jira.url)

        args = _fetch_args(tmp_path)
        stdout = _run_fetch(args)

        assert "TOTAL=1" in stdout
        assert "CHANGED=0" in stdout
        assert "UNCHANGED=1" in stdout
        assert _read_ids(args.ids_file) == ["RHAIRFE-1"]
        assert _read_ids(args.changed_file) == []

    def test_changed_detected(self, work_dirs, jira, monkeypatch, tmp_path):
        """Issue with different hash → in changed file."""
        jira.create("RHAIRFE-1", "Issue one", "New content.")
        _seed_snapshot(work_dirs, {"RHAIRFE-1": "old-hash"})
        _jira_env(monkeypatch, jira.url)

        args = _fetch_args(tmp_path)
        stdout = _run_fetch(args)

        assert "TOTAL=1" in stdout
        assert "CHANGED=1" in stdout
        assert "NEW=0" in stdout
        assert _read_ids(args.ids_file) == ["RHAIRFE-1"]
        assert _read_ids(args.changed_file) == ["RHAIRFE-1"]

    def test_new_detected(self, work_dirs, jira, monkeypatch, tmp_path):
        """Issue not in previous snapshot → new. Unchanged fills remaining."""
        jira.create("RHAIRFE-1", "Issue one", "Existing.")
        jira.create("RHAIRFE-2", "Issue two", "Brand new.")
        existing_hash = compute_content_hash(_text_to_adf("Existing."))
        _seed_snapshot(work_dirs, {"RHAIRFE-1": existing_hash})
        _jira_env(monkeypatch, jira.url)

        args = _fetch_args(tmp_path)
        stdout = _run_fetch(args)

        assert "TOTAL=2" in stdout
        assert "NEW=1" in stdout
        assert "UNCHANGED=1" in stdout
        ids = _read_ids(args.ids_file)
        assert "RHAIRFE-2" in ids
        assert "RHAIRFE-1" in ids

    def test_limit_caps_output(self, work_dirs, jira, monkeypatch, tmp_path):
        """--limit caps the number of output IDs."""
        jira.create("RHAIRFE-1", "One", "One.")
        jira.create("RHAIRFE-2", "Two", "Two.")
        jira.create("RHAIRFE-3", "Three", "Three.")
        _seed_snapshot(work_dirs, {})
        _jira_env(monkeypatch, jira.url)

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

    def test_falls_back_to_data_dir(self, work_dirs, jira, monkeypatch,
                                    tmp_path):
        """No local snapshot → uses data-dir snapshot for diffing."""
        jira.create("RHAIRFE-1", "Issue one", "Changed content.")
        data_dir = self._make_data_dir(tmp_path,
                                       {"RHAIRFE-1": "old-hash"})
        _jira_env(monkeypatch, jira.url)

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

    def test_steady_state_skips_unchanged(self, work_dirs, jira,
                                          monkeypatch, tmp_path):
        """Run 1 processes everything. Run 2 includes all but none changed."""
        jira.create("RHAIRFE-1", "Issue one", "Description one.")
        jira.create("RHAIRFE-2", "Issue two", "Description two.")
        _jira_env(monkeypatch, jira.url)

        # Run 1: first fetch — all new
        stdout1 = _run_fetch(_fetch_args(tmp_path))
        assert "TOTAL=2" in stdout1
        assert "NEW=2" in stdout1

        # Run 2: nothing changed — all unchanged, none in changed file
        args2 = _fetch_args(tmp_path)
        stdout2 = _run_fetch(args2)
        assert "TOTAL=2" in stdout2
        assert "CHANGED=0" in stdout2
        assert "UNCHANGED=2" in stdout2
        assert _read_ids(args2.changed_file) == []

    def test_user_edits_after_submit(self, work_dirs, jira,
                                     monkeypatch, tmp_path):
        """Run 1: fetch + submit. Run 2: user edits → re-process.
        Run 3: nothing changed → skip."""
        jira.create("RHAIRFE-1", "Issue one", "Original description.")
        jira.create("RHAIRFE-2", "Issue two", "Another description.")
        _jira_env(monkeypatch, jira.url)

        # Run 1: fetch all
        _run_fetch(_fetch_args(tmp_path))

        # Run 1: submit revises RHAIRFE-1, updates snapshot
        revised_hash = compute_content_hash(
            _text_to_adf("Auto-revised description."))
        update_snapshot_hashes(
            {"RHAIRFE-1": revised_hash}, work_dirs.snapshot_dir)
        # Simulate Jira now has the revised description
        jira.request( "PUT", "/rest/api/3/issue/RHAIRFE-1",
                      {"fields": {"description": "Auto-revised description."}})

        # Between runs: user edits RHAIRFE-1 in Jira
        jira.request( "PUT", "/rest/api/3/issue/RHAIRFE-1",
                      {"fields": {"description":
                                  "User rewrote this after our fix."}})

        # Run 2: detects user's edit, RHAIRFE-2 unchanged fills capacity
        args2 = _fetch_args(tmp_path)
        stdout2 = _run_fetch(args2)
        assert "TOTAL=2" in stdout2
        assert "CHANGED=1" in stdout2
        assert _read_ids(args2.changed_file) == ["RHAIRFE-1"]

        # Run 3: nothing changed — all unchanged
        args3 = _fetch_args(tmp_path)
        stdout3 = _run_fetch(args3)
        assert "CHANGED=0" in stdout3
        assert "UNCHANGED=2" in stdout3

    def test_submit_without_user_edit(self, work_dirs, jira,
                                      monkeypatch, tmp_path):
        """Run 1: fetch + submit. Run 2: no user edits → skip our own
        changes."""
        jira.create("RHAIRFE-1", "Issue one", "Original.")
        _jira_env(monkeypatch, jira.url)

        # Run 1: fetch
        _run_fetch(_fetch_args(tmp_path))

        # Run 1: submit revises RHAIRFE-1, updates snapshot
        revised_hash = compute_content_hash(
            _text_to_adf("We revised this."))
        update_snapshot_hashes(
            {"RHAIRFE-1": revised_hash}, work_dirs.snapshot_dir)
        # Simulate Jira now has our revision
        jira.request( "PUT", "/rest/api/3/issue/RHAIRFE-1",
                      {"fields": {"description": "We revised this."}})

        # Run 2: our own change is in the snapshot — unchanged
        args2 = _fetch_args(tmp_path)
        stdout2 = _run_fetch(args2)
        assert "TOTAL=1" in stdout2
        assert "CHANGED=0" in stdout2
        assert _read_ids(args2.changed_file) == []

    def test_new_issue_created_by_submit(self, work_dirs, jira,
                                         monkeypatch, tmp_path):
        """Run 1: fetch + create new issue. Run 2: new issue not
        re-flagged. Run 3: user edits the new issue → detected."""
        jira.create("RHAIRFE-1", "Existing", "Existing.")
        _jira_env(monkeypatch, jira.url)

        # Run 1: fetch
        _run_fetch(_fetch_args(tmp_path))

        # Run 1: submit creates RHAIRFE-2, updates snapshot
        jira.create("RHAIRFE-2", "We created this", "We created this.")
        new_hash = compute_content_hash(
            _text_to_adf("We created this."))
        update_snapshot_hashes(
            {"RHAIRFE-2": new_hash}, work_dirs.snapshot_dir)

        # Run 2: our new issue is already in the snapshot — unchanged
        args2 = _fetch_args(tmp_path)
        stdout2 = _run_fetch(args2)
        assert "CHANGED=0" in stdout2
        assert _read_ids(args2.changed_file) == []

        # Between runs: user edits the new issue
        jira.request( "PUT", "/rest/api/3/issue/RHAIRFE-2",
                      {"fields": {"description":
                                  "User improved our new issue."}})

        # Run 3: detects the edit; unchanged fills remaining capacity
        args3 = _fetch_args(tmp_path)
        stdout3 = _run_fetch(args3)
        assert "TOTAL=2" in stdout3
        assert "CHANGED=1" in stdout3
        assert set(_read_ids(args3.ids_file)) == {"RHAIRFE-1", "RHAIRFE-2"}

    def test_issue_leaves_scope(self, work_dirs, jira,
                                monkeypatch, tmp_path):
        """Issue closed between runs → silently dropped, not flagged."""
        jira.create("RHAIRFE-1", "Open issue", "Open issue.")
        jira.create("RHAIRFE-2", "Another", "Another open issue.")
        _jira_env(monkeypatch, jira.url)

        # Run 1
        stdout1 = _run_fetch(_fetch_args(tmp_path))
        assert "TOTAL=2" in stdout1

        # Between runs: RHAIRFE-2 gets closed (transitions to Done)
        transitions = jira.request(
            "GET", "/rest/api/3/issue/RHAIRFE-2/transitions")
        done_t = next(
            (t for t in transitions["transitions"]
             if t["to"]["statusCategory"]["key"] == "done"), None)
        if done_t:
            jira.request( "POST",
                          "/rest/api/3/issue/RHAIRFE-2/transitions",
                          {"transition": {"id": done_t["id"]}})

        # Run 2: RHAIRFE-2 gone, RHAIRFE-1 unchanged but included
        args2 = _fetch_args(tmp_path)
        stdout2 = _run_fetch(args2)
        assert "TOTAL=1" in stdout2
        assert "CHANGED=0" in stdout2
        assert _read_ids(args2.changed_file) == []

    def test_mixed_activity_across_runs(self, work_dirs, jira,
                                        monkeypatch, tmp_path):
        """Multiple runs with a mix of edits, new issues, and closures."""
        jira.create("RHAIRFE-1", "Issue one", "Issue one.")
        jira.create("RHAIRFE-2", "Issue two", "Issue two.")
        _jira_env(monkeypatch, jira.url)

        # Run 1: first fetch — all new
        stdout1 = _run_fetch(_fetch_args(tmp_path))
        assert "TOTAL=2" in stdout1
        assert "NEW=2" in stdout1

        # Run 1: submit revises RHAIRFE-1
        revised_hash = compute_content_hash(
            _text_to_adf("Revised issue one."))
        update_snapshot_hashes(
            {"RHAIRFE-1": revised_hash}, work_dirs.snapshot_dir)
        jira.request( "PUT", "/rest/api/3/issue/RHAIRFE-1",
                      {"fields": {"description": "Revised issue one."}})

        # Between runs: user edits RHAIRFE-2, new RHAIRFE-3 filed
        jira.request( "PUT", "/rest/api/3/issue/RHAIRFE-2",
                      {"fields": {"description":
                                  "User rewrote issue two."}})
        jira.create("RHAIRFE-3", "Issue three", "Brand new issue three.")

        # Run 2: RHAIRFE-2 changed, RHAIRFE-3 new, RHAIRFE-1 unchanged
        args2 = _fetch_args(tmp_path)
        stdout2 = _run_fetch(args2)
        assert "TOTAL=3" in stdout2
        assert "CHANGED=1" in stdout2
        assert "NEW=1" in stdout2
        assert "UNCHANGED=1" in stdout2
        changed2 = _read_ids(args2.changed_file)
        assert "RHAIRFE-2" in changed2
        assert "RHAIRFE-1" not in changed2

        # Between runs: RHAIRFE-2 closed, nothing else changes
        transitions = jira.request(
            "GET", "/rest/api/3/issue/RHAIRFE-2/transitions")
        done_t = next(
            (t for t in transitions["transitions"]
             if t["to"]["statusCategory"]["key"] == "done"), None)
        if done_t:
            jira.request( "POST",
                          "/rest/api/3/issue/RHAIRFE-2/transitions",
                          {"transition": {"id": done_t["id"]}})

        # Run 3: RHAIRFE-2 gone, rest unchanged
        args3 = _fetch_args(tmp_path)
        stdout3 = _run_fetch(args3)
        assert "CHANGED=0" in stdout3
        assert _read_ids(args3.changed_file) == []


# ── End-to-End: Clone → Fetch with --data-dir ──────────────────────────────

def _init_git_repo(path):
    """Init a git repo for use as a data source."""
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", path], check=True, capture_output=True)
    subprocess.run(["git", "-C", path, "config", "user.email", "test@test"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", path, "config", "user.name", "test"],
                   check=True, capture_output=True)


def _git_add_commit(repo, relpath, content, msg):
    """Write a file, git add, git commit."""
    full = os.path.join(repo, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    subprocess.run(["git", "-C", repo, "add", relpath],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", msg],
                   check=True, capture_output=True)


class TestCloneThenFetch:
    """End-to-end: clone a data repo, then use it as --data-dir for fetch.

    Verifies that clone_results_repo.py sparse checkout produces a layout
    that snapshot_fetch.py can read, and that incremental diffing works
    across the full pipeline.
    """

    def test_clone_provides_baseline_for_incremental_fetch(
            self, jira, monkeypatch, tmp_path):
        """Clone data repo → fetch with --data-dir → unchanged skipped."""
        _jira_env(monkeypatch, jira.url)

        # Build a source git repo with a previous snapshot
        src = str(tmp_path / "source")
        _init_git_repo(src)

        existing_hash = compute_content_hash(
            _text_to_adf("Already processed."))
        snap = yaml.dump({
            "query_timestamp": "2026-04-01T00:00:00Z",
            "timestamp": "2026-04-01T00:00:01Z",
            "issues": {
                "RHAIRFE-1": existing_hash,
                "RHAIRFE-2": "old-hash-will-differ",
            },
        })
        _git_add_commit(src,
                        "20260401-120000/auto-fix-runs/"
                        "issue-snapshot-20260401-120000.yaml",
                        snap, "add snapshot")
        # Add the latest symlink
        os.symlink("20260401-120000", os.path.join(src, "latest"))
        subprocess.run(["git", "-C", src, "add", "latest"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", src, "commit", "-m", "add latest"],
                       check=True, capture_output=True)

        # Clone it with clone_results_repo.py
        clone_dest = str(tmp_path / "cloned")
        r = subprocess.run(
            [sys.executable, CLONE_SCRIPT, src, clone_dest],
            capture_output=True, text=True,
            env={**os.environ, "DATA_REPO_TOKEN": ""},
        )
        assert r.returncode == 0, r.stderr

        # Jira has the same issues — RHAIRFE-1 unchanged, RHAIRFE-2 changed
        jira.create("RHAIRFE-1", "Already processed", "Already processed.")
        jira.create("RHAIRFE-2", "Edited", "User edited this since last run.")
        jira.create("RHAIRFE-3", "Brand new", "Brand new issue.")

        # Fetch with --data-dir pointing at clone
        snap_dir = str(tmp_path / "artifacts" / "auto-fix-runs")
        os.makedirs(snap_dir)
        monkeypatch.setattr(snapshot_fetch, "SNAPSHOT_DIR", snap_dir)
        args = _fetch_args(tmp_path, data_dir=clone_dest)
        stdout = _run_fetch(args)

        # RHAIRFE-1: unchanged → included but not changed
        # RHAIRFE-2: hash differs → changed
        # RHAIRFE-3: new
        assert "TOTAL=3" in stdout
        assert "CHANGED=1" in stdout
        assert "NEW=1" in stdout
        assert "UNCHANGED=1" in stdout
        ids = _read_ids(args.ids_file)
        assert "RHAIRFE-1" in ids
        assert "RHAIRFE-2" in ids
        assert "RHAIRFE-3" in ids
        changed = _read_ids(args.changed_file)
        assert "RHAIRFE-2" in changed
        assert "RHAIRFE-1" not in changed
