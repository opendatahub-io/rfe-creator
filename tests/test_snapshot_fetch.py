#!/usr/bin/env python3
"""Tests for scripts/snapshot_fetch.py — content hashing, snapshot diffing,
ID file writing, and snapshot loading from results directories."""

import hashlib
import inspect
import os
import subprocess
import sys
import textwrap

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import snapshot_fetch
import type_registry
from snapshot_fetch import (
    SNAPSHOT_CONFIG,
    cmd_fetch,
    compute_content_hash,
    diff_snapshots,
    find_previous_snapshot,
    jql_binding_clauses,
    jql_binding_conflict,
    load_snapshot_from_dir,
    read_id_file,
    update_snapshot_hashes,
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
                {"type": "paragraph", "content": [{"type": "text", "text": "Hello world"}]}
            ],
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
                {"type": "paragraph", "content": [{"type": "text", "text": "Test content"}]}
            ],
        }
        assert compute_content_hash(adf) == compute_content_hash(adf)

    def test_different_content_different_hash(self):
        """Different ADF content → different hash."""
        adf1 = {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Version A"}]}],
        }
        adf2 = {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Version B"}]}],
        }
        assert compute_content_hash(adf1) != compute_content_hash(adf2)

    def test_normalization_collapses_whitespace(self):
        """Curly quotes and extra spaces normalize to the same hash."""
        adf_straight = {
            "type": "doc",
            "version": 1,
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": 'It\'s a "test"'}]}
            ],
        }
        adf_curly = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "It\u2019s a \u201ctest\u201d"}],
                }
            ],
        }
        assert compute_content_hash(adf_straight) == compute_content_hash(adf_curly)

    def test_whitespace_only_changes_same_hash(self):
        """Extra blank lines, indentation, and tabs produce the same hash."""
        adf_clean = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Line one\nLine two\nLine three"}],
                }
            ],
        }
        adf_messy = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "  Line one\n\n\n\tLine two  \n\n  Line three  "}
                    ],
                }
            ],
        }
        assert compute_content_hash(adf_clean) == compute_content_hash(adf_messy)


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

    # ── New dict format with processed flag ──

    def test_processed_true_same_hash_unchanged(self):
        """Processed + same hash → not in changed or new (unchanged)."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa", "labels": []},
        }
        previous = {
            "issues": {
                "RHAIRFE-1": {"hash": "aaa", "processed": True},
            }
        }
        changed, new = diff_snapshots(current, previous)
        assert changed == []
        assert new == []

    def test_processed_true_different_hash_changed(self):
        """Processed + different hash → in changed list."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa-new", "labels": []},
        }
        previous = {
            "issues": {
                "RHAIRFE-1": {"hash": "aaa", "processed": True},
            }
        }
        changed, new = diff_snapshots(current, previous)
        assert changed == ["RHAIRFE-1"]
        assert new == []

    def test_processed_false_treated_as_new(self):
        """Unprocessed entry → treated as new regardless of hash."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa", "labels": []},
        }
        previous = {
            "issues": {
                "RHAIRFE-1": {"hash": "aaa", "processed": False},
            }
        }
        changed, new = diff_snapshots(current, previous)
        assert changed == []
        assert new == ["RHAIRFE-1"]

    def test_processed_false_different_hash_still_new(self):
        """Unprocessed + different hash → still treated as new, not changed."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa-new", "labels": []},
        }
        previous = {
            "issues": {
                "RHAIRFE-1": {"hash": "aaa", "processed": False},
            }
        }
        changed, new = diff_snapshots(current, previous)
        assert changed == []
        assert new == ["RHAIRFE-1"]

    def test_mixed_old_and_new_format(self):
        """Mix of old string format and new dict format in same snapshot."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa", "labels": []},
            "RHAIRFE-2": {"content_hash": "bbb", "labels": []},
            "RHAIRFE-3": {"content_hash": "ccc", "labels": []},
        }
        previous = {
            "issues": {
                "RHAIRFE-1": "aaa",  # old format, implicitly processed
                "RHAIRFE-2": {"hash": "bbb", "processed": True},  # new, processed
                "RHAIRFE-3": {"hash": "ccc", "processed": False},  # new, unprocessed
            }
        }
        changed, new = diff_snapshots(current, previous)
        assert changed == []
        assert new == ["RHAIRFE-3"]

    def test_dict_missing_processed_defaults_true(self):
        """Dict entry without processed key → defaults to True."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa", "labels": []},
        }
        previous = {
            "issues": {
                "RHAIRFE-1": {"hash": "aaa"},  # no processed key
            }
        }
        changed, new = diff_snapshots(current, previous)
        assert changed == []
        assert new == []


class TestUpdateSnapshotHashes:
    def _seed(self, tmp_path, issues):
        snap_dir = str(tmp_path / "snapshots")
        os.makedirs(snap_dir)
        snap = {
            "query_timestamp": "2026-04-01T00:00:00Z",
            "timestamp": "2026-04-01T00:00:01Z",
            "issues": issues,
        }
        path = os.path.join(snap_dir, "issue-snapshot-20260401-000000.yaml")
        with open(path, "w") as f:
            yaml.dump(snap, f, default_flow_style=False, sort_keys=False)
        return snap_dir, path

    def test_submitted_hashes_written_as_dict(self, tmp_path):
        """Submitted hashes written in new dict format with processed=True."""
        snap_dir, path = self._seed(tmp_path, {"K1": "old-hash"})
        result = update_snapshot_hashes({"K1": "new-hash"}, snap_dir)
        assert result is not None
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["issues"]["K1"] == {"hash": "new-hash", "processed": True}

    def test_mark_processed_preserves_hash(self, tmp_path):
        """mark_processed sets processed=True without changing hash."""
        snap_dir, path = self._seed(
            tmp_path,
            {
                "K1": {"hash": "aaa", "processed": False},
            },
        )
        result = update_snapshot_hashes({}, snap_dir, mark_processed=["K1"])
        assert result is not None
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["issues"]["K1"] == {"hash": "aaa", "processed": True}

    def test_mark_processed_old_format(self, tmp_path):
        """mark_processed on old string format → converts to dict."""
        snap_dir, path = self._seed(tmp_path, {"K1": "aaa"})
        result = update_snapshot_hashes({}, snap_dir, mark_processed=["K1"])
        assert result is not None
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["issues"]["K1"] == {"hash": "aaa", "processed": True}

    def test_mark_processed_skips_missing_key(self, tmp_path):
        """mark_processed with key not in snapshot → no error, no change."""
        snap_dir, path = self._seed(tmp_path, {"K1": "aaa"})
        result = update_snapshot_hashes({}, snap_dir, mark_processed=["MISSING"])
        assert result is not None
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["issues"]["K1"] == "aaa"  # untouched

    def test_submitted_and_mark_processed_together(self, tmp_path):
        """Both hashes and mark_processed in single call."""
        snap_dir, path = self._seed(
            tmp_path,
            {
                "K1": {"hash": "old", "processed": False},
                "K2": {"hash": "bbb", "processed": False},
            },
        )
        result = update_snapshot_hashes({"K1": "new-hash"}, snap_dir, mark_processed=["K2"])
        assert result is not None
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["issues"]["K1"] == {"hash": "new-hash", "processed": True}
        assert data["issues"]["K2"] == {"hash": "bbb", "processed": True}

    def test_empty_hashes_and_no_mark_processed(self, tmp_path):
        """Empty hashes + no mark_processed → snapshot still written."""
        snap_dir, path = self._seed(tmp_path, {"K1": "aaa"})
        result = update_snapshot_hashes({}, snap_dir)
        assert result is not None
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["issues"]["K1"] == "aaa"  # untouched


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
            snap_path = os.path.join(snap_dir, f"issue-snapshot-{name}.yaml")
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
        repo = _make_results_dir(
            tmp_path,
            [
                {"name": "20260401-120000", "snapshot": snapshot, "latest": True},
            ],
        )

        data = load_snapshot_from_dir(repo)
        assert data is not None
        assert data["issues"] == {"RHAIRFE-1": "aaa"}

    def test_walks_backwards_for_snapshot(self, tmp_path):
        """Latest run has no snapshot → walks to older run."""
        old_snapshot = {"issues": {"RHAIRFE-1": "aaa"}}
        repo = _make_results_dir(
            tmp_path,
            [
                {"name": "20260401-120000", "snapshot": old_snapshot},
                {"name": "20260402-120000", "snapshot": None, "latest": True},
            ],
        )

        data = load_snapshot_from_dir(repo)
        assert data is not None
        assert data["issues"] == {"RHAIRFE-1": "aaa"}

    def test_no_symlink_uses_newest_dir(self, tmp_path):
        """No latest symlink → uses newest directory by name."""
        snap_old = {"issues": {"RHAIRFE-1": "aaa"}}
        snap_new = {"issues": {"RHAIRFE-1": "bbb", "RHAIRFE-2": "ccc"}}
        repo = _make_results_dir(
            tmp_path,
            [
                {"name": "20260401-120000", "snapshot": snap_old},
                {"name": "20260402-120000", "snapshot": snap_new},
            ],
        )

        data = load_snapshot_from_dir(repo)
        assert data is not None
        assert data["issues"] == {"RHAIRFE-1": "bbb", "RHAIRFE-2": "ccc"}

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
        with open(os.path.join(td_dir, "issue-snapshot-20260401-120000.yaml"), "w") as f:
            yaml.dump({"issues": {"RHAIRFE-1": "aaa"}}, f)

        data = load_snapshot_from_dir(repo)
        assert data is None

    def test_latest_symlink_with_relative_prefix(self, tmp_path):
        """latest symlink with ./ prefix still prioritises target."""
        snap_old = {"issues": {"RHAIRFE-1": "old"}}
        snap_new = {"issues": {"RHAIRFE-1": "new"}}
        repo = _make_results_dir(
            tmp_path,
            [
                {"name": "20260401-120000", "snapshot": snap_old, "latest": True},
                {"name": "20260402-120000", "snapshot": snap_new},
            ],
        )
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


class TestReprocess:
    def test_reprocess_without_jql_copies_all_to_changed(self, tmp_path):
        """--reprocess without --jql reuses prior IDs, all marked changed."""
        ids_file = str(tmp_path / "all-ids.txt")
        changed_file = str(tmp_path / "changed-ids.txt")
        write_id_file(ids_file, ["RHAIRFE-1", "RHAIRFE-2", "RHAIRFE-3"])

        import argparse

        args = argparse.Namespace(
            reprocess=True,
            jql=None,
            random=None,
            type="rfe",
            ids_file=ids_file,
            changed_file=changed_file,
        )
        cmd_fetch(args)

        assert read_id_file(changed_file) == ["RHAIRFE-1", "RHAIRFE-2", "RHAIRFE-3"]

    def test_reprocess_without_jql_fails_without_prior_ids(self, tmp_path):
        """--reprocess with no prior IDs file exits with error."""
        ids_file = str(tmp_path / "missing.txt")
        changed_file = str(tmp_path / "changed-ids.txt")

        import argparse

        args = argparse.Namespace(
            reprocess=True,
            jql=None,
            random=None,
            type="rfe",
            ids_file=ids_file,
            changed_file=changed_file,
        )
        with pytest.raises(SystemExit) as exc_info:
            cmd_fetch(args)
        assert exc_info.value.code == 1

    def test_reprocess_without_jql_preserves_ids_file(self, tmp_path):
        """--reprocess does not modify the original IDs file."""
        ids_file = str(tmp_path / "all-ids.txt")
        changed_file = str(tmp_path / "changed-ids.txt")
        write_id_file(ids_file, ["RHAIRFE-1", "RHAIRFE-2"])

        import argparse

        args = argparse.Namespace(
            reprocess=True,
            jql=None,
            random=None,
            type="rfe",
            ids_file=ids_file,
            changed_file=changed_file,
        )
        cmd_fetch(args)

        assert read_id_file(ids_file) == ["RHAIRFE-1", "RHAIRFE-2"]


class TestRandom:
    """Tests for --random N with --reprocess --jql (random sampling from JQL)."""

    def _make_current(self, keys):
        """Build a fake fetch_all_issues return dict."""
        return {k: {"content_hash": f"hash-{k}"} for k in keys}

    def _run_fetch(self, tmp_path, current, random_n, monkeypatch):
        """Run cmd_fetch with mocked Jira fetch, return (ids, changed)."""
        import argparse

        ids_file = str(tmp_path / "all-ids.txt")
        changed_file = str(tmp_path / "changed-ids.txt")
        snap_dir = str(tmp_path / "snapshots")

        monkeypatch.setattr("snapshot_fetch.require_env", lambda: ("http://x", "u", "t"))
        monkeypatch.setattr("snapshot_fetch.fetch_all_issues", lambda *a, **kw: current)
        monkeypatch.setattr("snapshot_fetch.find_previous_snapshot", lambda **kw: (None, None))
        monkeypatch.setattr("snapshot_fetch.SNAPSHOT_DIR", snap_dir)

        args = argparse.Namespace(
            reprocess=True,
            jql="project = RHAIRFE",
            random=random_n,
            limit=None,
            data_dir=None,
            type="rfe",
            ids_file=ids_file,
            changed_file=changed_file,
        )
        cmd_fetch(args)
        return read_id_file(ids_file), read_id_file(changed_file)

    def test_random_samples_n_ids(self, tmp_path, monkeypatch):
        """--random N picks N random IDs from JQL results."""
        keys = [f"RHAIRFE-{i}" for i in range(1, 11)]
        current = self._make_current(keys)

        ids, changed = self._run_fetch(tmp_path, current, 3, monkeypatch)

        assert len(ids) == 3
        assert all(k in keys for k in ids)
        # --reprocess marks all as changed
        assert changed == ids

    def test_random_exceeding_count_uses_all(self, tmp_path, monkeypatch):
        """--random N >= fetched issues uses all with a warning."""
        keys = ["RHAIRFE-1", "RHAIRFE-2"]
        current = self._make_current(keys)

        ids, changed = self._run_fetch(tmp_path, current, 10, monkeypatch)

        assert sorted(ids) == sorted(keys)

    def test_random_results_are_sorted(self, tmp_path, monkeypatch):
        """--random output is sorted for deterministic downstream."""
        keys = [f"RHAIRFE-{i}" for i in range(1, 21)]
        current = self._make_current(keys)

        ids, _ = self._run_fetch(tmp_path, current, 5, monkeypatch)

        assert ids == sorted(ids)


class TestUnchangedSkippedFromSelection:
    """Verifies unchanged-processed RFEs are excluded from selection by
    default and only included with --reprocess."""

    def _setup_snapshot(self, tmp_path, prev_issues):
        """Write a previous snapshot file under tmp_path's snapshot dir."""
        snap_dir = tmp_path / "auto-fix-runs"
        snap_dir.mkdir(parents=True, exist_ok=True)
        snap_path = snap_dir / "issue-snapshot-20260501-000000.yaml"
        with open(snap_path, "w") as f:
            yaml.dump(
                {
                    "query_timestamp": "2026-05-01T00:00:00Z",
                    "timestamp": "2026-05-01T00:00:01Z",
                    "issues": prev_issues,
                },
                f,
            )
        return str(snap_dir)

    def _run_fetch(self, tmp_path, current, prev_issues, monkeypatch, reprocess=False, limit=None):
        import argparse

        snap_dir = self._setup_snapshot(tmp_path, prev_issues)
        ids_file = str(tmp_path / "all-ids.txt")
        changed_file = str(tmp_path / "changed-ids.txt")

        monkeypatch.setattr("snapshot_fetch.require_env", lambda: ("http://x", "u", "t"))
        monkeypatch.setattr("snapshot_fetch.fetch_all_issues", lambda *a, **kw: current)
        monkeypatch.setattr(
            "snapshot_fetch.find_previous_snapshot",
            lambda **kw: (
                str(tmp_path / "auto-fix-runs" / "issue-snapshot-20260501-000000.yaml"),
                {"issues": prev_issues},
            ),
        )
        monkeypatch.setattr("snapshot_fetch.SNAPSHOT_DIR", snap_dir)

        args = argparse.Namespace(
            reprocess=reprocess,
            jql="project = RHAIRFE",
            random=None,
            limit=limit,
            data_dir=None,
            type="rfe",
            ids_file=ids_file,
            changed_file=changed_file,
        )
        cmd_fetch(args)
        return read_id_file(ids_file), read_id_file(changed_file)

    def test_unchanged_processed_excluded_by_default(self, tmp_path, monkeypatch):
        """Unchanged-processed RFEs are NOT in the selection."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa"},  # unchanged
            "RHAIRFE-2": {"content_hash": "bbb"},  # unchanged
            "RHAIRFE-3": {"content_hash": "ccc-new"},  # changed
        }
        prev_issues = {
            "RHAIRFE-1": {"hash": "aaa", "processed": True},
            "RHAIRFE-2": {"hash": "bbb", "processed": True},
            "RHAIRFE-3": {"hash": "ccc", "processed": True},
        }
        ids, _ = self._run_fetch(tmp_path, current, prev_issues, monkeypatch)
        assert ids == ["RHAIRFE-3"]

    def test_unchanged_unprocessed_still_included(self, tmp_path, monkeypatch):
        """processed: false → treated as new → selected even when unchanged."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa"},
            "RHAIRFE-2": {"content_hash": "bbb"},
        }
        prev_issues = {
            "RHAIRFE-1": {"hash": "aaa", "processed": True},
            "RHAIRFE-2": {"hash": "bbb", "processed": False},
        }
        ids, _ = self._run_fetch(tmp_path, current, prev_issues, monkeypatch)
        assert ids == ["RHAIRFE-2"]

    def test_reprocess_includes_unchanged_processed(self, tmp_path, monkeypatch):
        """--reprocess restores the fill-with-unchanged behavior."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa"},
            "RHAIRFE-2": {"content_hash": "bbb"},
            "RHAIRFE-3": {"content_hash": "ccc-new"},
        }
        prev_issues = {
            "RHAIRFE-1": {"hash": "aaa", "processed": True},
            "RHAIRFE-2": {"hash": "bbb", "processed": True},
            "RHAIRFE-3": {"hash": "ccc", "processed": True},
        }
        ids, changed = self._run_fetch(tmp_path, current, prev_issues, monkeypatch, reprocess=True)
        assert sorted(ids) == ["RHAIRFE-1", "RHAIRFE-2", "RHAIRFE-3"]
        # --reprocess marks all as changed
        assert sorted(changed) == sorted(ids)

    def test_limit_caps_changed_plus_new_only(self, tmp_path, monkeypatch):
        """--limit applies to changed+new; unchanged are not used as filler."""
        current = {
            f"RHAIRFE-{i}": {"content_hash": f"new-{i}"} for i in range(1, 6)
        }  # all 5 changed
        current["RHAIRFE-6"] = {"content_hash": "uchanged-hash"}  # unchanged
        prev_issues = {f"RHAIRFE-{i}": {"hash": f"old-{i}", "processed": True} for i in range(1, 6)}
        prev_issues["RHAIRFE-6"] = {"hash": "uchanged-hash", "processed": True}

        ids, _ = self._run_fetch(tmp_path, current, prev_issues, monkeypatch, limit=10)
        # All 5 changed selected, RHAIRFE-6 (unchanged-processed) excluded
        assert sorted(ids) == [f"RHAIRFE-{i}" for i in range(1, 6)]
        assert "RHAIRFE-6" not in ids

    def test_no_changes_empty_selection(self, tmp_path, monkeypatch):
        """All unchanged-processed → empty selection (no work to do)."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa"},
            "RHAIRFE-2": {"content_hash": "bbb"},
        }
        prev_issues = {
            "RHAIRFE-1": {"hash": "aaa", "processed": True},
            "RHAIRFE-2": {"hash": "bbb", "processed": True},
        }
        ids, _ = self._run_fetch(tmp_path, current, prev_issues, monkeypatch)
        assert ids == []


class TestSnapshotConfig:
    """Verify SNAPSHOT_CONFIG is defined for both types."""

    def test_rfe_config(self):
        assert SNAPSHOT_CONFIG["rfe"]["ignore_label"] == "rfe-creator-ignore"
        assert SNAPSHOT_CONFIG["rfe"]["snapshot_prefix"] == "issue-snapshot-"

    def test_initiative_config(self):
        assert SNAPSHOT_CONFIG["initiative"]["ignore_label"] == "initiative-ignore"
        assert SNAPSHOT_CONFIG["initiative"]["snapshot_prefix"] == "initiative-snapshot-"


class TestFindPreviousSnapshotPrefix:
    """Verify prefix parameter scopes find_previous_snapshot correctly."""

    def _seed(self, tmp_path, prefix, issues):
        snap_dir = str(tmp_path / "snapshots")
        os.makedirs(snap_dir, exist_ok=True)
        snap = {
            "query_timestamp": "2026-04-01T00:00:00Z",
            "timestamp": "2026-04-01T00:00:01Z",
            "issues": issues,
        }
        path = os.path.join(snap_dir, f"{prefix}20260401-000000.yaml")
        with open(path, "w") as f:
            yaml.dump(snap, f, default_flow_style=False, sort_keys=False)
        return snap_dir

    def test_default_prefix_finds_rfe_snapshot(self, tmp_path):
        snap_dir = self._seed(tmp_path, "issue-snapshot-", {"K1": "aaa"})
        path, data = find_previous_snapshot(snapshot_dir=snap_dir)
        assert data is not None
        assert "K1" in data["issues"]

    def test_initiative_prefix_finds_initiative_snapshot(self, tmp_path):
        snap_dir = self._seed(tmp_path, "initiative-snapshot-", {"K1": "bbb"})
        path, data = find_previous_snapshot(snapshot_dir=snap_dir, prefix="initiative-snapshot-")
        assert data is not None
        assert data["issues"]["K1"] == "bbb"

    def test_prefix_isolates_types(self, tmp_path):
        """RFE prefix doesn't find initiative snapshots and vice versa."""
        snap_dir = self._seed(tmp_path, "issue-snapshot-", {"RFE": "aaa"})
        # Also create an initiative snapshot in the same dir
        snap = {
            "query_timestamp": "2026-04-01T00:00:00Z",
            "timestamp": "2026-04-01T00:00:01Z",
            "issues": {"INIT": "bbb"},
        }
        path = os.path.join(snap_dir, "initiative-snapshot-20260401-000000.yaml")
        with open(path, "w") as f:
            yaml.dump(snap, f)

        # Default prefix finds only the RFE snapshot
        _, rfe_data = find_previous_snapshot(snapshot_dir=snap_dir)
        assert "RFE" in rfe_data["issues"]
        assert "INIT" not in rfe_data["issues"]

        # Initiative prefix finds only the initiative snapshot
        _, init_data = find_previous_snapshot(snapshot_dir=snap_dir, prefix="initiative-snapshot-")
        assert "INIT" in init_data["issues"]
        assert "RFE" not in init_data["issues"]

    def test_no_matching_prefix_returns_none(self, tmp_path):
        snap_dir = self._seed(tmp_path, "issue-snapshot-", {"K1": "aaa"})
        path, data = find_previous_snapshot(snapshot_dir=snap_dir, prefix="initiative-snapshot-")
        assert path is None
        assert data is None


class TestUpdateSnapshotHashesPrefix:
    """Verify prefix parameter scopes update_snapshot_hashes correctly."""

    def _seed(self, tmp_path, prefix, issues):
        snap_dir = str(tmp_path / "snapshots")
        os.makedirs(snap_dir, exist_ok=True)
        snap = {
            "query_timestamp": "2026-04-01T00:00:00Z",
            "timestamp": "2026-04-01T00:00:01Z",
            "issues": issues,
        }
        path = os.path.join(snap_dir, f"{prefix}20260401-000000.yaml")
        with open(path, "w") as f:
            yaml.dump(snap, f, default_flow_style=False, sort_keys=False)
        return snap_dir, path

    def test_initiative_prefix_updates_initiative_snapshot(self, tmp_path):
        snap_dir, path = self._seed(tmp_path, "initiative-snapshot-", {"K1": "old"})
        result = update_snapshot_hashes({"K1": "new"}, snap_dir, prefix="initiative-snapshot-")
        assert result is not None
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["issues"]["K1"] == {"hash": "new", "processed": True}

    def test_initiative_prefix_ignores_rfe_snapshot(self, tmp_path):
        """update with initiative prefix doesn't touch RFE snapshots."""
        snap_dir, rfe_path = self._seed(tmp_path, "issue-snapshot-", {"K1": "rfe"})
        result = update_snapshot_hashes({"K1": "new"}, snap_dir, prefix="initiative-snapshot-")
        assert result is None
        with open(rfe_path) as f:
            data = yaml.safe_load(f)
        assert data["issues"]["K1"] == "rfe"  # untouched


class TestCmdFetchInitiativeType:
    """Verify --type initiative uses the right config."""

    def _run_fetch(self, tmp_path, current, monkeypatch, issue_type="initiative"):
        import argparse

        snap_dir = str(tmp_path / "auto-fix-runs")
        os.makedirs(snap_dir, exist_ok=True)
        ids_file = str(tmp_path / "all-ids.txt")
        changed_file = str(tmp_path / "changed-ids.txt")

        captured_jql = {}

        def mock_fetch_all(server, user, token, jql):
            captured_jql["jql"] = jql
            return current

        monkeypatch.setattr("snapshot_fetch.require_env", lambda: ("http://x", "u", "t"))
        monkeypatch.setattr("snapshot_fetch.fetch_all_issues", mock_fetch_all)
        monkeypatch.setattr("snapshot_fetch.find_previous_snapshot", lambda **kw: (None, None))
        monkeypatch.setattr("snapshot_fetch.SNAPSHOT_DIR", snap_dir)

        args = argparse.Namespace(
            reprocess=False,
            jql=f"project = {REG.get(issue_type).binding(env={})['project']}",
            random=None,
            limit=None,
            data_dir=None,
            type=issue_type,
            ids_file=ids_file,
            changed_file=changed_file,
        )
        cmd_fetch(args)
        return captured_jql, ids_file, snap_dir

    def test_initiative_uses_initiative_ignore_label(self, tmp_path, monkeypatch):
        current = {"RHOAIENG-1": {"content_hash": "aaa", "labels": []}}
        captured, _, _ = self._run_fetch(tmp_path, current, monkeypatch)
        assert "initiative-ignore" in captured["jql"]
        assert "rfe-creator-ignore" not in captured["jql"]

    def test_initiative_writes_initiative_snapshot_file(self, tmp_path, monkeypatch):
        current = {"RHOAIENG-1": {"content_hash": "aaa", "labels": []}}
        _, _, snap_dir = self._run_fetch(tmp_path, current, monkeypatch)
        snap_files = os.listdir(snap_dir)
        initiative_snaps = [f for f in snap_files if f.startswith("initiative-snapshot-")]
        rfe_snaps = [f for f in snap_files if f.startswith("issue-snapshot-")]
        assert len(initiative_snaps) == 1
        assert len(rfe_snaps) == 0

    def test_rfe_uses_rfe_ignore_label(self, tmp_path, monkeypatch):
        current = {"RHAIRFE-1": {"content_hash": "aaa", "labels": []}}
        captured, _, _ = self._run_fetch(tmp_path, current, monkeypatch, issue_type="rfe")
        assert "rfe-creator-ignore" in captured["jql"]
        assert "initiative-ignore" not in captured["jql"]

    def test_rfe_writes_issue_snapshot_file(self, tmp_path, monkeypatch):
        current = {"RHAIRFE-1": {"content_hash": "aaa", "labels": []}}
        _, _, snap_dir = self._run_fetch(tmp_path, current, monkeypatch, issue_type="rfe")
        snap_files = os.listdir(snap_dir)
        rfe_snaps = [f for f in snap_files if f.startswith("issue-snapshot-")]
        initiative_snaps = [f for f in snap_files if f.startswith("initiative-snapshot-")]
        assert len(rfe_snaps) == 1
        assert len(initiative_snaps) == 0


# ── Registry-derived SNAPSHOT_CONFIG (work-item-types PR-2c) ─────────────────

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "snapshot_fetch.py")
# Hermetic: the developer's RFE_CREATOR_EXTRA_TYPES / RFE_CREATOR_BINDING_* never leak in.
REG = type_registry.load(extra_roots=[], env={})


def _dropin_root(tmp_path):
    """A minimal third type under a drop-in root (registry env seam, dev/test only)."""
    root = tmp_path / "types"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "type.yaml").write_text(
        textwrap.dedent(
            """\
            schema_version: 1
            type: docs
            identity:
              tracker: jira
              jira: {project: DOCS, issue_type: Task, key_prefixes: ["DOCS-"]}
              local_prefix: "DOC-"
              id_field: doc_id
            dirs: {tasks: artifacts/doc-tasks, originals: artifacts/doc-originals}
            conventions:
              labels: {ignore: docs-ignore, split_quarantine: docs-split-quarantine}
            snapshot: {prefix: docs-snapshot-, report_prefix: docs-run-}
            reporting: {item_key: per_doc}
            """
        )
    )
    return str(root)


def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("RFE_CREATOR_")}
    env.update(extra)
    return env


class TestSnapshotConfigDerivesFromTheRegistry:
    def test_values_are_the_pre_registry_literals(self):
        # Behaviour-neutral migration: same keys, same order, same values as the literal table
        # this replaced — the hard-filter JQL wrapper and the snapshot file names are built
        # from these, and both are production contracts (results-repo file names).
        assert SNAPSHOT_CONFIG == {
            "rfe": {
                "ignore_label": "rfe-creator-ignore",
                "quarantine_label": "rfe-creator-split-quarantine",
                "snapshot_prefix": "issue-snapshot-",
            },
            "initiative": {
                "ignore_label": "initiative-ignore",
                "quarantine_label": "initiative-split-quarantine",
                "snapshot_prefix": "initiative-snapshot-",
            },
        }
        assert list(SNAPSHOT_CONFIG) == ["rfe", "initiative"]
        for config in SNAPSHOT_CONFIG.values():
            assert list(config) == ["ignore_label", "quarantine_label", "snapshot_prefix"]

    @pytest.mark.parametrize("type_name", REG.names())
    def test_projection_from_the_descriptor(self, type_name):
        desc = REG.get(type_name)
        sc = SNAPSHOT_CONFIG[type_name]
        assert sc["ignore_label"] == desc.labels["ignore"]
        assert sc["quarantine_label"] == desc.labels["split_quarantine"]
        assert sc["snapshot_prefix"] == desc.get("snapshot.prefix")

    def test_default_prefix_kwargs_are_the_rfe_prefix_bound_at_import(self):
        # submit.py relies on these defaults for rfe (its own snapshot_prefix is "" as a
        # sentinel for them); a third type must always pass prefix=. The default is the rfe
        # descriptor's snapshot.prefix, bound once when the module is imported.
        rfe_prefix = REG.get("rfe").get("snapshot.prefix")
        assert rfe_prefix == "issue-snapshot-"
        for fn in (find_previous_snapshot, load_snapshot_from_dir, update_snapshot_hashes):
            assert inspect.signature(fn).parameters["prefix"].default == rfe_prefix, fn.__name__

    def test_type_choices_are_the_registry_choices(self):
        result = subprocess.run(
            [sys.executable, SCRIPT, "fetch", "--help"],
            capture_output=True,
            text=True,
            env=_clean_env(),
        )
        assert result.returncode == 0
        assert "--type {rfe,initiative}" in result.stdout

    def test_a_drop_in_type_is_offered_and_configured(self, tmp_path):
        root = _dropin_root(tmp_path)
        env = _clean_env(RFE_CREATOR_EXTRA_TYPES=root, RFE_CREATOR_EXTRA_TYPES_ALLOWLIST=root)
        result = subprocess.run(
            [sys.executable, SCRIPT, "fetch", "--help"], capture_output=True, text=True, env=env
        )
        assert "--type {rfe,docs,initiative}" in result.stdout
        # The one fetch path that needs no Jira — --reprocess without a JQL reuses the prior
        # IDs — still indexes SNAPSHOT_CONFIG by type, so it proves the drop-in is configured.
        ids_file = tmp_path / "ids.txt"
        ids_file.write_text("DOCS-1\nDOCS-2\n")
        changed_file = tmp_path / "changed.txt"
        result = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "fetch",
                "--type",
                "docs",
                "--reprocess",
                "--ids-file",
                str(ids_file),
                "--changed-file",
                str(changed_file),
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "TOTAL=2\nCHANGED=2\nNEW=0\nUNCHANGED=0\n"
        assert changed_file.read_text() == "DOCS-1\nDOCS-2\n"


class TestJqlBindingCheck:
    """PR-3c: the positive ``project`` / ``issuetype`` clauses of --jql — ``= X`` and every member
    of ``in (X, Y)``; a clause negated with ``NOT`` is not read — are compared with the resolved
    type's EFFECTIVE binding (design §3.2.1) before any snapshot is read and before anything is
    fetched or written; a JQL that names neither is not checked."""

    RFE = REG.get("rfe").binding(env={})
    INITIATIVE = REG.get("initiative").binding(env={})

    @pytest.mark.parametrize(
        "jql, expected",
        [
            (
                'project = RHAIRFE AND issuetype = "Feature Request"',
                {"project": ["RHAIRFE"], "issuetype": ["Feature Request"]},
            ),
            (
                "project = RHAIRFE AND issuetype = 'Feature Request'",
                {"project": ["RHAIRFE"], "issuetype": ["Feature Request"]},
            ),
            (
                "project = RHOAIENG AND issuetype = Initiative",
                {"project": ["RHOAIENG"], "issuetype": ["Initiative"]},
            ),
            (
                "PROJECT=RHAIRFE and IssueType = Epic",
                {"project": ["RHAIRFE"], "issuetype": ["Epic"]},
            ),
            ("(project = RHAIRFE) AND labels = foo", {"project": ["RHAIRFE"], "issuetype": []}),
            (
                "project = RHAIRFE OR project = RHOAIENG",
                {"project": ["RHAIRFE", "RHOAIENG"], "issuetype": []},
            ),
            # Memberships: every member is a positive assertion, quotes stripped, in order.
            (
                "project in (RHAIRFE, RHOAIENG) AND issuetype in ('Feature Request', Epic)",
                {"project": ["RHAIRFE", "RHOAIENG"], "issuetype": ["Feature Request", "Epic"]},
            ),
            ('issuetype in ("Feature Request")', {"project": [], "issuetype": ["Feature Request"]}),
            ("project IN(RHAIRFE)", {"project": ["RHAIRFE"], "issuetype": []}),
            ("type in (Epic, 10700)", {"project": [], "issuetype": ["Epic", "10700"]}),
            (
                'project in ("Red Hat AI RFE project", RHAIRFE)',
                {"project": ["Red Hat AI RFE project", "RHAIRFE"], "issuetype": []},
            ),
            # Negations assert nothing: `not in`, `!=`, `NOT <clause>` and `NOT ( ... )` groups
            # (balanced, nested, quoted parentheses respected).
            ("project not in (RHOAIENG)", {"project": [], "issuetype": []}),
            ("project != RHOAIENG AND issuetype != Epic", {"project": [], "issuetype": []}),
            (
                "project = RHAIRFE AND NOT issuetype = Epic",
                {"project": ["RHAIRFE"], "issuetype": []},
            ),
            (
                "project = RHAIRFE AND NOT (issuetype = Epic)",
                {"project": ["RHAIRFE"], "issuetype": []},
            ),
            (
                "not(issuetype = Epic) and project=RHAIRFE",
                {"project": ["RHAIRFE"], "issuetype": []},
            ),
            (
                "NOT (project = RHOAIENG AND (type = Epic OR type in (Bug, Task))) AND "
                "project = RHAIRFE",
                {"project": ["RHAIRFE"], "issuetype": []},
            ),
            (
                'NOT (summary ~ "a ) b" AND project = RHOAIENG) AND project = RHAIRFE',
                {"project": ["RHAIRFE"], "issuetype": []},
            ),
            ("NOT (project = RHOAIENG AND type = Epic", {"project": [], "issuetype": []}),
            # `type` is Jira's documented alias of `issuetype` and is listed under it.
            (
                "project = RHOAIENG AND type = Epic",
                {"project": ["RHOAIENG"], "issuetype": ["Epic"]},
            ),
            ("TYPE = 'Feature Request'", {"project": [], "issuetype": ["Feature Request"]}),
            ("myproject = X AND type = Epic", {"project": [], "issuetype": ["Epic"]}),
            # Word boundaries: a custom field named "... Type", `subtype`, `myproject`.
            (
                '"Request Type" = foo AND subtype = bar AND myproject = X',
                {"project": [], "issuetype": []},
            ),
            ("labels = foo AND statusCategory != Done", {"project": [], "issuetype": []}),
            ("", {"project": [], "issuetype": []}),
            (None, {"project": [], "issuetype": []}),
        ],
    )
    def test_binding_clauses(self, jql, expected):
        assert jql_binding_clauses(jql) == expected

    def test_production_jql_passes_silently_for_both_shipped_types(self):
        for name in REG.names():
            desc = REG.get(name)
            jql = desc.get("conventions.query_default")
            assert jql_binding_conflict(jql, desc.binding(env={}), name) is None, name
        # The CI form quotes the issue type with single quotes.
        assert (
            jql_binding_conflict(
                "project = RHAIRFE AND issuetype = 'Feature Request'", self.RFE, "rfe"
            )
            is None
        )
        assert jql_binding_conflict("project = RHAIRFE", self.RFE, "rfe") is None
        assert jql_binding_conflict("project = RHOAIENG", self.INITIATIVE, "initiative") is None

    @pytest.mark.parametrize(
        "jql, message",
        [
            (
                "project = RHOAIENG",
                "ERROR: --jql names project RHOAIENG but the rfe binding is RHAIRFE",
            ),
            (
                "project = RHAIRFE AND issuetype = Initiative",
                "ERROR: --jql names issuetype Initiative but the rfe binding is Feature Request",
            ),
            (
                'project = RHAIRFE AND issuetype = "Epic"',
                "ERROR: --jql names issuetype Epic but the rfe binding is Feature Request",
            ),
            (
                "project = RHAIRFE OR project = RHOAIENG",
                "ERROR: --jql names project RHOAIENG but the rfe binding is RHAIRFE",
            ),
            (
                "project = RHOAIENG AND issuetype = Initiative",
                # both conflict: project is reported first
                "ERROR: --jql names project RHOAIENG but the rfe binding is RHAIRFE",
            ),
            # Memberships: a member other than the binding is the same conflict, whether it is
            # the only member or one of several (the first offending member is reported).
            (
                "project in (RHOAIENG)",
                "ERROR: --jql names project RHOAIENG but the rfe binding is RHAIRFE",
            ),
            (
                "project in (RHAIRFE, RHOAIENG)",
                "ERROR: --jql names project RHOAIENG but the rfe binding is RHAIRFE",
            ),
            (
                "project = RHAIRFE AND issuetype in (Initiative, Epic)",
                "ERROR: --jql names issuetype Initiative but the rfe binding is Feature Request",
            ),
            (
                'project = RHAIRFE AND type in ("Feature Request", Epic)',
                "ERROR: --jql names issuetype Epic but the rfe binding is Feature Request",
            ),
        ],
    )
    def test_conflicts_under_rfe(self, jql, message):
        assert jql_binding_conflict(jql, self.RFE, "rfe") == message

    @pytest.mark.parametrize(
        "jql",
        [
            'summary ~ "not (a" AND project = RHOAIENG',
            "summary ~ 'NOT (x' AND project = RHOAIENG",
            'text ~ "foo not (bar) baz" AND issuetype = Epic',
        ],
    )
    def test_a_quoted_not_paren_is_text_not_a_negated_group(self, jql):
        """``not (`` inside a quoted literal must not open a group and swallow the clauses
        after it (the guard would otherwise pass a conflicting JQL through)."""
        assert jql_binding_conflict(jql, self.RFE, "rfe") is not None

    @pytest.mark.parametrize(
        "jql",
        [
            'issuetype in ("Feature Request")',
            "project in (RHAIRFE) AND issuetype in ('Feature Request')",
            "project not in (RHOAIENG)",
            "project = RHAIRFE AND NOT issuetype = Epic",
            "project = RHAIRFE AND NOT (issuetype = Epic)",
            "project = RHAIRFE AND issuetype not in (Epic, Initiative)",
            "NOT (project = RHOAIENG AND issuetype = Initiative) AND project = RHAIRFE",
            # The member skip rules are the equality ones: ids and project names go to Jira.
            'project in (10001, "Red Hat AI RFE project") AND issuetype in (10700)',
        ],
    )
    def test_memberships_and_negations_that_pass_under_rfe(self, jql):
        assert jql_binding_conflict(jql, self.RFE, "rfe") is None

    def test_conflicts_under_initiative(self):
        assert jql_binding_conflict("project = RHAIRFE", self.INITIATIVE, "initiative") == (
            "ERROR: --jql names project RHAIRFE but the initiative binding is RHOAIENG"
        )
        assert jql_binding_conflict(
            "project = RHOAIENG AND issuetype = 'Feature Request'", self.INITIATIVE, "initiative"
        ) == (
            "ERROR: --jql names issuetype Feature Request but the initiative binding is Initiative"
        )
        # The `type` alias is the same clause, reported as issuetype.
        assert (
            jql_binding_conflict(
                "project = RHOAIENG AND type = Epic", self.INITIATIVE, "initiative"
            )
            == "ERROR: --jql names issuetype Epic but the initiative binding is Initiative"
        )
        assert (
            jql_binding_conflict(
                "project = RHOAIENG AND type = Initiative", self.INITIATIVE, "initiative"
            )
            is None
        )

    def test_project_names_and_ids_are_left_to_jira(self):
        # Jira accepts a project's NAME as an alias of its key in `project = ...`; the binding
        # carries the key only, so a value that is not key-shaped cannot be compared offline
        # and is neither accepted nor refused — Jira resolves it. The key form is still checked,
        # in either case, and a numeric id is skipped as before.
        assert (
            jql_binding_conflict(
                "project = \"Red Hat AI RFE project\" AND issuetype = 'Feature Request'",
                self.RFE,
                "rfe",
            )
            is None
        )
        assert jql_binding_conflict('project = "RHAI RFEs"', self.RFE, "rfe") is None
        assert jql_binding_conflict("project = 10001", self.RFE, "rfe") is None
        assert jql_binding_conflict("project = RHOAIENG", self.RFE, "rfe") == (
            "ERROR: --jql names project RHOAIENG but the rfe binding is RHAIRFE"
        )
        assert jql_binding_conflict("project = rhoaieng", self.RFE, "rfe") == (
            "ERROR: --jql names project rhoaieng but the rfe binding is RHAIRFE"
        )
        # An issue type has no key form: its name is compared as before.
        assert jql_binding_conflict(
            'project = "Red Hat AI RFE project" AND type = Epic', self.RFE, "rfe"
        ) == ("ERROR: --jql names issuetype Epic but the rfe binding is Feature Request")
        # Key-shaped is the registry's own project-key grammar (the override validator's).
        assert snapshot_fetch._JQL_PROJECT_KEY_RE.pattern == type_registry._PROJECT_KEY_RE.pattern

    def test_memberships_are_checked_and_the_other_operators_are_not(self):
        # `in (...)` is a positive assertion like `=`; `!=`, `not in`, `~` and a `NOT`-negated
        # clause assert nothing the binding could contradict.
        assert jql_binding_conflict(
            "project in (RHOAIENG) AND issuetype in (Initiative, Epic)", self.RFE, "rfe"
        ) == ("ERROR: --jql names project RHOAIENG but the rfe binding is RHAIRFE")
        assert jql_binding_conflict(
            "project in (RHOAIENG) AND issuetype in (Epic)", self.INITIATIVE, "initiative"
        ) == ("ERROR: --jql names issuetype Epic but the initiative binding is Initiative")
        assert jql_binding_conflict("project != RHAIRFE AND issuetype ~ x", self.RFE, "rfe") is None
        assert (
            jql_binding_conflict(
                "project not in (RHAIRFE) AND NOT type = 'Feature Request'", self.RFE, "rfe"
            )
            is None
        )
        assert jql_binding_conflict("labels = foo", self.RFE, "rfe") is None

    def test_values_compare_case_insensitively_and_ids_are_skipped(self):
        assert (
            jql_binding_conflict(
                'project = rhairfe AND issuetype = "feature request"', self.RFE, "rfe"
            )
            is None
        )
        assert (
            jql_binding_conflict("project = 10001 AND issuetype = 10700", self.RFE, "rfe") is None
        )

    def test_effective_binding_is_what_is_checked(self):
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"}
        binding = REG.get("rfe").binding(env)
        assert (
            jql_binding_conflict(
                "project = KONFLUX AND issuetype = 'Feature Request'", binding, "rfe"
            )
            is None
        )
        assert jql_binding_conflict("project = RHAIRFE", binding, "rfe") == (
            "ERROR: --jql names project RHAIRFE but the rfe binding is KONFLUX"
        )

    @staticmethod
    def _args(tmp_path, jql, type_name):
        import argparse

        return argparse.Namespace(
            reprocess=False,
            jql=jql,
            random=None,
            limit=None,
            data_dir=str(tmp_path / "data"),
            type=type_name,
            ids_file=str(tmp_path / "ids.txt"),
            changed_file=str(tmp_path / "changed.txt"),
        )

    @pytest.mark.parametrize(
        "type_name, jql, message",
        [
            (
                "rfe",
                "project = RHOAIENG",
                "ERROR: --jql names project RHOAIENG but the rfe binding is RHAIRFE",
            ),
            (
                "initiative",
                'project = RHOAIENG AND issuetype = "Feature Request"',
                "ERROR: --jql names issuetype Feature Request but the initiative binding is "
                "Initiative",
            ),
        ],
    )
    def test_cmd_fetch_conflict_exits_1_before_any_read_or_fetch(
        self, tmp_path, monkeypatch, capsys, type_name, jql, message
    ):
        def boom(*a, **kw):
            raise AssertionError("must not be reached on a JQL/binding conflict")

        # Credentials are not even read: the conflict is decided first.
        for name in (
            "require_env",
            "fetch_all_issues",
            "find_previous_snapshot",
            "load_snapshot_from_dir",
        ):
            monkeypatch.setattr(f"snapshot_fetch.{name}", boom)
        snap_dir = str(tmp_path / "auto-fix-runs")
        monkeypatch.setattr("snapshot_fetch.SNAPSHOT_DIR", snap_dir)
        args = self._args(tmp_path, jql, type_name)
        with pytest.raises(SystemExit) as exc:
            cmd_fetch(args)
        assert exc.value.code == 1
        out, err = capsys.readouterr()
        assert out == ""
        assert err == message + "\n"
        assert not os.path.exists(snap_dir)
        assert not os.path.exists(args.ids_file) and not os.path.exists(args.changed_file)

    def test_passing_jql_keeps_the_output_byte_identical(self, tmp_path, monkeypatch, capsys):
        # The production JQL under the default type: no new line anywhere — the stderr trace is
        # the pre-3c one and stdout the five counters.
        snap_dir = str(tmp_path / "auto-fix-runs")
        monkeypatch.setattr("snapshot_fetch.require_env", lambda: ("http://x", "u", "t"))
        monkeypatch.setattr(
            "snapshot_fetch.fetch_all_issues",
            lambda *a, **kw: {"RHAIRFE-1": {"content_hash": "aaa", "labels": []}},
        )
        monkeypatch.setattr("snapshot_fetch.find_previous_snapshot", lambda **kw: (None, None))
        monkeypatch.setattr("snapshot_fetch.SNAPSHOT_DIR", snap_dir)
        args = self._args(tmp_path, "project = RHAIRFE AND issuetype = 'Feature Request'", "rfe")
        args.data_dir = None
        cmd_fetch(args)
        out, err = capsys.readouterr()
        assert out == "TOTAL=1\nCHANGED=0\nNEW=1\nUNCHANGED_SELECTED=0\nUNCHANGED_SKIPPED=0\n"
        assert err == (
            "Previous snapshot: none (first run)\n"
            "JQL=(project = RHAIRFE AND issuetype = 'Feature Request') AND statusCategory != Done "
            "AND (labels not in (rfe-creator-ignore, rfe-creator-split-quarantine) "
            "OR labels is EMPTY)\n"
            "Fetched 1 issues\n"
        )
        assert read_id_file(args.ids_file) == ["RHAIRFE-1"]

    def test_cli_conflict_needs_no_credentials(self, tmp_path):
        # The default --type is rfe: a JQL naming the initiative project fails before the
        # credential check, with the one ERROR line and nothing written.
        env = _clean_env()
        for var in ("JIRA_SERVER", "JIRA_USER", "JIRA_TOKEN"):
            env.pop(var, None)
        result = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "fetch",
                "project = RHOAIENG AND issuetype = Initiative",
                "--ids-file",
                str(tmp_path / "ids.txt"),
                "--changed-file",
                str(tmp_path / "changed.txt"),
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 1
        assert result.stdout == ""
        assert (
            result.stderr == "ERROR: --jql names project RHOAIENG but the rfe binding is RHAIRFE\n"
        )
        assert sorted(os.listdir(tmp_path)) == []

    def test_reprocess_without_jql_is_not_checked(self, tmp_path):
        # No JQL, nothing to compare: the prior-ids shortcut is untouched.
        import argparse

        ids_file = str(tmp_path / "all-ids.txt")
        changed_file = str(tmp_path / "changed-ids.txt")
        write_id_file(ids_file, ["RHOAIENG-1"])
        cmd_fetch(
            argparse.Namespace(
                reprocess=True,
                jql=None,
                random=None,
                type="rfe",
                ids_file=ids_file,
                changed_file=changed_file,
            )
        )
        assert read_id_file(changed_file) == ["RHOAIENG-1"]


class TestHardFilterJqlWrapperIsByteStable:
    """The wrapper sent to Jira, per type, exactly as before the registry derivation."""

    def _captured_jql(self, tmp_path, monkeypatch, type_name, jql):
        import argparse

        captured = {}

        def mock_fetch_all(server, user, token, wrapped):
            captured["jql"] = wrapped
            return {}

        monkeypatch.setattr("snapshot_fetch.require_env", lambda: ("http://x", "u", "t"))
        monkeypatch.setattr("snapshot_fetch.fetch_all_issues", mock_fetch_all)
        monkeypatch.setattr("snapshot_fetch.find_previous_snapshot", lambda **kw: (None, None))
        monkeypatch.setattr("snapshot_fetch.SNAPSHOT_DIR", str(tmp_path / "auto-fix-runs"))
        cmd_fetch(
            argparse.Namespace(
                reprocess=False,
                jql=jql,
                random=None,
                limit=None,
                data_dir=None,
                type=type_name,
                ids_file=str(tmp_path / "ids.txt"),
                changed_file=str(tmp_path / "changed.txt"),
            )
        )
        return captured["jql"]

    def test_rfe(self, tmp_path, monkeypatch):
        assert self._captured_jql(tmp_path, monkeypatch, "rfe", "project = RHAIRFE") == (
            "(project = RHAIRFE) AND statusCategory != Done "
            "AND (labels not in (rfe-creator-ignore, rfe-creator-split-quarantine) "
            "OR labels is EMPTY)"
        )

    def test_initiative(self, tmp_path, monkeypatch):
        assert self._captured_jql(tmp_path, monkeypatch, "initiative", "project = RHOAIENG") == (
            "(project = RHOAIENG) AND statusCategory != Done "
            "AND (labels not in (initiative-ignore, initiative-split-quarantine) "
            "OR labels is EMPTY)"
        )
