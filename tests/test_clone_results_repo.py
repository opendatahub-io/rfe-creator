#!/usr/bin/env python3
"""Tests for scripts/clone_results_repo.py — URL building and sparse checkout."""
import os
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

SCRIPT = os.path.join(os.path.dirname(__file__), "..",
                      "scripts", "clone_results_repo.py")

from clone_results_repo import build_clone_url


class TestBuildCloneUrl:
    def test_bare_path_with_token(self):
        """Bare project path + token → authenticated GitLab URL."""
        url = build_clone_url("org/group/repo", "glpat-xxx")
        assert url == "https://bot:glpat-xxx@gitlab.com/org/group/repo.git"

    def test_bare_path_no_token_raises(self):
        """Bare project path without token → ValueError."""
        with pytest.raises(ValueError, match="DATA_REPO_TOKEN required"):
            build_clone_url("org/group/repo", "")

    def test_https_url_with_token(self):
        """Full HTTPS URL + token → token injected."""
        url = build_clone_url(
            "https://gitlab.com/org/repo.git", "glpat-xxx")
        assert url == "https://bot:glpat-xxx@gitlab.com/org/repo.git"

    def test_https_url_no_token_passthrough(self):
        """Full HTTPS URL without token → unchanged."""
        orig = "https://gitlab.com/org/repo.git"
        assert build_clone_url(orig, "") == orig

    def test_ssh_url_passthrough(self):
        """SSH URL → unchanged regardless of token."""
        orig = "git@gitlab.com:org/repo.git"
        assert build_clone_url(orig, "glpat-xxx") == orig

    def test_https_url_with_port(self):
        """HTTPS URL with port → port preserved after token injection."""
        url = build_clone_url(
            "https://gitlab.example.com:8443/org/repo.git", "tok")
        assert "bot:tok@gitlab.example.com:8443" in url

    def test_absolute_path_passthrough(self):
        """Local absolute path → unchanged, no token required."""
        path = "/tmp/my-local-repo"
        assert build_clone_url(path, "") == path

    def test_absolute_path_ignores_token(self):
        """Local absolute path with token → path unchanged."""
        path = "/tmp/my-local-repo"
        assert build_clone_url(path, "glpat-xxx") == path


# ── Sparse Checkout Integration ─────────────────────────────────────────────

def _init_source_repo(path):
    """Create a bare-like git repo simulating the results data repo."""
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", path], check=True, capture_output=True)
    subprocess.run(["git", "-C", path, "config", "user.email", "test@test"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", path, "config", "user.name", "test"],
                   check=True, capture_output=True)
    return path


def _commit_file(repo, relpath, content="placeholder"):
    """Write a file and commit it."""
    full = os.path.join(repo, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    subprocess.run(["git", "-C", repo, "add", relpath],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", f"add {relpath}"],
                   check=True, capture_output=True)


class TestSparseCheckout:
    """Verify clone_results_repo.py sparse-checkout patterns work.

    Creates a local git repo with the same structure as the data repo,
    clones it with the script, and asserts only snapshot files and the
    latest symlink are materialized.
    """

    def test_only_snapshots_and_latest_materialized(self, tmp_path):
        """Sparse checkout materializes snapshots and latest, skips rest."""
        src = _init_source_repo(str(tmp_path / "source"))

        # Populate source repo with a realistic structure
        snap_content = yaml.dump({
            "query_timestamp": "2026-04-01T00:00:00Z",
            "timestamp": "2026-04-01T00:00:01Z",
            "issues": {"RHAIRFE-1": "abc123"},
        })
        _commit_file(src, "20260401-120000/auto-fix-runs/"
                     "issue-snapshot-20260401-120000.yaml", snap_content)
        _commit_file(src, "20260401-120000/auto-fix-runs/"
                     "20260401-120000.yaml", "run report data")
        _commit_file(src, "20260401-120000/rfe-tasks/RHAIRFE-1.md",
                     "task content")
        _commit_file(src, "20260401-120000/rfe-reviews/RHAIRFE-1-review.md",
                     "review content")
        _commit_file(src, "20260401-120000/rfe-originals/RHAIRFE-1.md",
                     "original content")

        # Create the latest symlink (committed as a file in git)
        latest_path = os.path.join(src, "latest")
        os.symlink("20260401-120000", latest_path)
        subprocess.run(["git", "-C", src, "add", "latest"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", src, "commit", "-m", "add latest"],
                       check=True, capture_output=True)

        # Clone with the script
        dest = str(tmp_path / "clone")
        r = subprocess.run(
            [sys.executable, SCRIPT, src, dest],
            capture_output=True, text=True,
            env={**os.environ, "DATA_REPO_TOKEN": ""},
        )
        assert r.returncode == 0, r.stderr

        # Snapshot file should exist
        snap_file = os.path.join(
            dest, "20260401-120000", "auto-fix-runs",
            "issue-snapshot-20260401-120000.yaml")
        assert os.path.exists(snap_file)
        with open(snap_file) as f:
            data = yaml.safe_load(f)
        assert data["issues"]["RHAIRFE-1"] == "abc123"

        # latest symlink should exist
        assert os.path.exists(os.path.join(dest, "latest"))

        # Files outside the sparse patterns should NOT be materialized
        assert not os.path.exists(os.path.join(
            dest, "20260401-120000", "rfe-tasks", "RHAIRFE-1.md"))
        assert not os.path.exists(os.path.join(
            dest, "20260401-120000", "rfe-reviews",
            "RHAIRFE-1-review.md"))
        assert not os.path.exists(os.path.join(
            dest, "20260401-120000", "rfe-originals", "RHAIRFE-1.md"))

    def test_run_report_not_materialized(self, tmp_path):
        """Run report YAML in auto-fix-runs/ is excluded (not a snapshot)."""
        src = _init_source_repo(str(tmp_path / "source"))

        snap_content = yaml.dump({
            "query_timestamp": "2026-04-01T00:00:00Z",
            "timestamp": "2026-04-01T00:00:01Z",
            "issues": {},
        })
        _commit_file(src, "20260401-120000/auto-fix-runs/"
                     "issue-snapshot-20260401-120000.yaml", snap_content)
        _commit_file(src, "20260401-120000/auto-fix-runs/"
                     "20260401-120000.yaml", "run report")

        dest = str(tmp_path / "clone")
        r = subprocess.run(
            [sys.executable, SCRIPT, src, dest],
            capture_output=True, text=True,
            env={**os.environ, "DATA_REPO_TOKEN": ""},
        )
        assert r.returncode == 0, r.stderr

        # Snapshot: yes
        assert os.path.exists(os.path.join(
            dest, "20260401-120000", "auto-fix-runs",
            "issue-snapshot-20260401-120000.yaml"))
        # Run report: no
        assert not os.path.exists(os.path.join(
            dest, "20260401-120000", "auto-fix-runs",
            "20260401-120000.yaml"))
