#!/usr/bin/env python3
"""Tests for scripts/snapshot_fetch.py — content hashing, snapshot diffing,
ID file writing, and snapshot loading from results directories."""
import hashlib
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from snapshot_fetch import (
    compute_content_hash,
    diff_snapshots,
    load_snapshot_from_dir,
    write_id_file,
)


class TestComputeContentHash:
    def test_none_input(self):
        """None/empty ADF → hash of empty bytes."""
        expected = hashlib.sha256(b"").hexdigest()
        assert compute_content_hash(None) == expected

    def test_empty_dict(self):
        """Empty ADF doc → hash of empty bytes."""
        expected = hashlib.sha256(b"").hexdigest()
        assert compute_content_hash({}) == expected

    def test_simple_adf(self):
        """Basic ADF paragraph → deterministic hash."""
        adf = {
            "type": "doc",
            "version": 1,
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "Hello world"}
                ]}
            ]
        }
        h = compute_content_hash(adf)
        assert isinstance(h, str)
        assert len(h) == 64  # SHA256 hex

    def test_same_content_same_hash(self):
        """Identical ADF content → identical hash."""
        adf = {
            "type": "doc",
            "version": 1,
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "Test content"}
                ]}
            ]
        }
        assert compute_content_hash(adf) == compute_content_hash(adf)

    def test_different_content_different_hash(self):
        """Different ADF content → different hash."""
        adf1 = {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": "Version A"}
            ]}]
        }
        adf2 = {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": "Version B"}
            ]}]
        }
        assert compute_content_hash(adf1) != compute_content_hash(adf2)

    def test_normalization_collapses_whitespace(self):
        """Curly quotes and extra spaces normalize to the same hash."""
        adf_straight = {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": "It's a \"test\""}
            ]}]
        }
        adf_curly = {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [
                {"type": "text",
                 "text": "It\u2019s a \u201ctest\u201d"}
            ]}]
        }
        assert compute_content_hash(adf_straight) == \
            compute_content_hash(adf_curly)

    def test_whitespace_only_changes_same_hash(self):
        """Extra blank lines, indentation, and tabs produce the same hash."""
        adf_clean = {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": "Line one\nLine two\nLine three"}
            ]}]
        }
        adf_messy = {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [
                {"type": "text",
                 "text": "  Line one\n\n\n\tLine two  \n\n  Line three  "}
            ]}]
        }
        assert compute_content_hash(adf_clean) == \
            compute_content_hash(adf_messy)


class TestDiffSnapshots:
    def test_first_run_all_new(self):
        """No previous snapshot → all issues are new."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa", "labels": []},
            "RHAIRFE-2": {"content_hash": "bbb", "labels": []},
        }
        changed, new = diff_snapshots(current, None)
        assert changed == []
        assert new == ["RHAIRFE-1", "RHAIRFE-2"]

    def test_unchanged_issues_excluded(self):
        """Issues with same hash → not in changed or new."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa", "labels": []},
            "RHAIRFE-2": {"content_hash": "bbb", "labels": []},
        }
        previous = {"issues": {"RHAIRFE-1": "aaa", "RHAIRFE-2": "bbb"}}
        changed, new = diff_snapshots(current, previous)
        assert changed == []
        assert new == []

    def test_changed_issue_detected(self):
        """Issue with different hash → in changed list."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa-new", "labels": []},
            "RHAIRFE-2": {"content_hash": "bbb", "labels": []},
        }
        previous = {"issues": {"RHAIRFE-1": "aaa", "RHAIRFE-2": "bbb"}}
        changed, new = diff_snapshots(current, previous)
        assert changed == ["RHAIRFE-1"]
        assert new == []

    def test_new_issue_detected(self):
        """Issue not in previous snapshot → in new list."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa", "labels": []},
            "RHAIRFE-3": {"content_hash": "ccc", "labels": []},
        }
        previous = {"issues": {"RHAIRFE-1": "aaa"}}
        changed, new = diff_snapshots(current, previous)
        assert changed == []
        assert new == ["RHAIRFE-3"]

    def test_preserves_jira_order(self):
        """Output preserves insertion order from current dict."""
        current = {
            "RHAIRFE-3": {"content_hash": "ccc", "labels": []},
            "RHAIRFE-1": {"content_hash": "aaa-new", "labels": []},
            "RHAIRFE-2": {"content_hash": "bbb", "labels": []},
        }
        previous = {"issues": {"RHAIRFE-1": "aaa", "RHAIRFE-2": "bbb"}}
        changed, new = diff_snapshots(current, previous)
        assert new == ["RHAIRFE-3"]
        assert changed == ["RHAIRFE-1"]

    def test_mixed_changed_new_unchanged(self):
        """Mix of changed, new, and unchanged issues."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa-new", "labels": []},
            "RHAIRFE-2": {"content_hash": "bbb", "labels": []},
            "RHAIRFE-3": {"content_hash": "ccc", "labels": []},
            "RHAIRFE-4": {"content_hash": "ddd", "labels": []},
        }
        previous = {
            "issues": {
                "RHAIRFE-1": "aaa",
                "RHAIRFE-2": "bbb",
                "RHAIRFE-3": "ccc-old",
            }
        }
        changed, new = diff_snapshots(current, previous)
        assert changed == ["RHAIRFE-1", "RHAIRFE-3"]
        assert new == ["RHAIRFE-4"]


def _make_results_dir(tmp_path, runs):
    """Create a directory mimicking the results directory structure.

    runs: list of dicts with keys: name, snapshot (dict or None),
          latest (bool).
    Returns the repo path.
    """
    repo = str(tmp_path / "data-repo")
    os.makedirs(repo)

    latest_name = None
    for run in runs:
        name = run["name"]
        snap_dir = os.path.join(repo, name, "auto-fix-runs")
        os.makedirs(snap_dir, exist_ok=True)

        if run.get("snapshot"):
            snap_path = os.path.join(snap_dir,
                                     f"issue-snapshot-{name}.yaml")
            with open(snap_path, "w") as f:
                yaml.dump(run["snapshot"], f)

        if run.get("latest"):
            latest_name = name

    if latest_name:
        os.symlink(latest_name, os.path.join(repo, "latest"))

    return repo


class TestLoadSnapshotFromDir:
    def test_follows_latest_symlink(self, tmp_path):
        """Finds snapshot via latest symlink."""
        snapshot = {"issues": {"RHAIRFE-1": "aaa"}}
        repo = _make_results_dir(tmp_path, [
            {"name": "20260401-120000", "snapshot": snapshot,
             "latest": True},
        ])

        data = load_snapshot_from_dir(repo)
        assert data is not None
        assert data["issues"] == {"RHAIRFE-1": "aaa"}

    def test_walks_backwards_for_snapshot(self, tmp_path):
        """Latest run has no snapshot → walks to older run."""
        old_snapshot = {"issues": {"RHAIRFE-1": "aaa"}}
        repo = _make_results_dir(tmp_path, [
            {"name": "20260401-120000", "snapshot": old_snapshot},
            {"name": "20260402-120000", "snapshot": None,
             "latest": True},
        ])

        data = load_snapshot_from_dir(repo)
        assert data is not None
        assert data["issues"] == {"RHAIRFE-1": "aaa"}

    def test_no_symlink_uses_newest_dir(self, tmp_path):
        """No latest symlink → uses newest directory by name."""
        snap_old = {"issues": {"RHAIRFE-1": "aaa"}}
        snap_new = {"issues": {"RHAIRFE-1": "bbb", "RHAIRFE-2": "ccc"}}
        repo = _make_results_dir(tmp_path, [
            {"name": "20260401-120000", "snapshot": snap_old},
            {"name": "20260402-120000", "snapshot": snap_new},
        ])

        data = load_snapshot_from_dir(repo)
        assert data is not None
        assert data["issues"] == {"RHAIRFE-1": "bbb",
                                  "RHAIRFE-2": "ccc"}

    def test_empty_repo_returns_none(self, tmp_path):
        """No run directories → returns None."""
        repo = str(tmp_path / "empty-repo")
        os.makedirs(repo)

        data = load_snapshot_from_dir(repo)
        assert data is None

    def test_missing_path_returns_none(self, tmp_path):
        """Non-existent path → returns None."""
        data = load_snapshot_from_dir(str(tmp_path / "no-such-dir"))
        assert data is None

    def test_skips_test_data_dir(self, tmp_path):
        """test-data/ with a valid snapshot is ignored."""
        repo = str(tmp_path / "data-repo")
        td_dir = os.path.join(repo, "test-data", "auto-fix-runs")
        os.makedirs(td_dir)
        with open(os.path.join(td_dir,
                  "issue-snapshot-20260401-120000.yaml"), "w") as f:
            yaml.dump({"issues": {"RHAIRFE-1": "aaa"}}, f)

        data = load_snapshot_from_dir(repo)
        assert data is None

    def test_latest_symlink_with_relative_prefix(self, tmp_path):
        """latest symlink with ./ prefix still prioritises target."""
        snap_old = {"issues": {"RHAIRFE-1": "old"}}
        snap_new = {"issues": {"RHAIRFE-1": "new"}}
        repo = _make_results_dir(tmp_path, [
            {"name": "20260401-120000", "snapshot": snap_old,
             "latest": True},
            {"name": "20260402-120000", "snapshot": snap_new},
        ])
        # Re-create symlink with ./ prefix
        os.remove(os.path.join(repo, "latest"))
        os.symlink("./20260401-120000", os.path.join(repo, "latest"))

        data = load_snapshot_from_dir(repo)
        assert data is not None
        # Should prioritise the symlink target (older), not newest
        assert data["issues"] == {"RHAIRFE-1": "old"}


class TestWriteIdFile:
    def test_writes_ids_one_per_line(self, tmp_path):
        path = str(tmp_path / "ids.txt")
        write_id_file(path, ["RHAIRFE-1", "RHAIRFE-2", "RHAIRFE-3"])
        with open(path) as f:
            lines = f.read().splitlines()
        assert lines == ["RHAIRFE-1", "RHAIRFE-2", "RHAIRFE-3"]

    def test_creates_parent_dirs(self, tmp_path):
        path = str(tmp_path / "sub" / "dir" / "ids.txt")
        write_id_file(path, ["RHAIRFE-1"])
        assert os.path.exists(path)

    def test_empty_list_creates_empty_file(self, tmp_path):
        path = str(tmp_path / "empty.txt")
        write_id_file(path, [])
        with open(path) as f:
            assert f.read() == ""
