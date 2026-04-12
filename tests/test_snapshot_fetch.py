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
    cmd_fetch,
    compute_content_hash,
    diff_snapshots,
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

    # ── New dict format with processed flag ──

    def test_processed_true_same_hash_unchanged(self):
        """Processed + same hash → not in changed or new (unchanged)."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa", "labels": []},
        }
        previous = {"issues": {
            "RHAIRFE-1": {"hash": "aaa", "processed": True},
        }}
        changed, new = diff_snapshots(current, previous)
        assert changed == []
        assert new == []

    def test_processed_true_different_hash_changed(self):
        """Processed + different hash → in changed list."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa-new", "labels": []},
        }
        previous = {"issues": {
            "RHAIRFE-1": {"hash": "aaa", "processed": True},
        }}
        changed, new = diff_snapshots(current, previous)
        assert changed == ["RHAIRFE-1"]
        assert new == []

    def test_processed_false_treated_as_new(self):
        """Unprocessed entry → treated as new regardless of hash."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa", "labels": []},
        }
        previous = {"issues": {
            "RHAIRFE-1": {"hash": "aaa", "processed": False},
        }}
        changed, new = diff_snapshots(current, previous)
        assert changed == []
        assert new == ["RHAIRFE-1"]

    def test_processed_false_different_hash_still_new(self):
        """Unprocessed + different hash → still treated as new, not changed."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa-new", "labels": []},
        }
        previous = {"issues": {
            "RHAIRFE-1": {"hash": "aaa", "processed": False},
        }}
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
        previous = {"issues": {
            "RHAIRFE-1": "aaa",  # old format, implicitly processed
            "RHAIRFE-2": {"hash": "bbb", "processed": True},  # new, processed
            "RHAIRFE-3": {"hash": "ccc", "processed": False},  # new, unprocessed
        }}
        changed, new = diff_snapshots(current, previous)
        assert changed == []
        assert new == ["RHAIRFE-3"]

    def test_dict_missing_processed_defaults_true(self):
        """Dict entry without processed key → defaults to True."""
        current = {
            "RHAIRFE-1": {"content_hash": "aaa", "labels": []},
        }
        previous = {"issues": {
            "RHAIRFE-1": {"hash": "aaa"},  # no processed key
        }}
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
        snap_dir, path = self._seed(tmp_path, {
            "K1": {"hash": "aaa", "processed": False},
        })
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
        result = update_snapshot_hashes(
            {}, snap_dir, mark_processed=["MISSING"])
        assert result is not None
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["issues"]["K1"] == "aaa"  # untouched

    def test_submitted_and_mark_processed_together(self, tmp_path):
        """Both hashes and mark_processed in single call."""
        snap_dir, path = self._seed(tmp_path, {
            "K1": {"hash": "old", "processed": False},
            "K2": {"hash": "bbb", "processed": False},
        })
        result = update_snapshot_hashes(
            {"K1": "new-hash"}, snap_dir, mark_processed=["K2"])
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


class TestReprocess:
    def test_reprocess_without_jql_copies_all_to_changed(self, tmp_path):
        """--reprocess without --jql reuses prior IDs, all marked changed."""
        ids_file = str(tmp_path / "all-ids.txt")
        changed_file = str(tmp_path / "changed-ids.txt")
        write_id_file(ids_file, ["RHAIRFE-1", "RHAIRFE-2", "RHAIRFE-3"])

        import argparse
        args = argparse.Namespace(
            reprocess=True, jql=None, random=None,
            ids_file=ids_file, changed_file=changed_file)
        cmd_fetch(args)

        assert read_id_file(changed_file) == [
            "RHAIRFE-1", "RHAIRFE-2", "RHAIRFE-3"]

    def test_reprocess_without_jql_fails_without_prior_ids(self, tmp_path):
        """--reprocess with no prior IDs file exits with error."""
        ids_file = str(tmp_path / "missing.txt")
        changed_file = str(tmp_path / "changed-ids.txt")

        import argparse
        args = argparse.Namespace(
            reprocess=True, jql=None, random=None,
            ids_file=ids_file, changed_file=changed_file)
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
            reprocess=True, jql=None, random=None,
            ids_file=ids_file, changed_file=changed_file)
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

        monkeypatch.setattr("snapshot_fetch.require_env",
                            lambda: ("http://x", "u", "t"))
        monkeypatch.setattr("snapshot_fetch.fetch_all_issues",
                            lambda *a, **kw: current)
        monkeypatch.setattr("snapshot_fetch.find_previous_snapshot",
                            lambda: (None, None))
        monkeypatch.setattr("snapshot_fetch.SNAPSHOT_DIR", snap_dir)

        args = argparse.Namespace(
            reprocess=True, jql="project = TEST", random=random_n,
            limit=None, data_dir=None,
            ids_file=ids_file, changed_file=changed_file)
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
