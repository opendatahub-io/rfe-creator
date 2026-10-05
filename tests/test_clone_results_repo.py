#!/usr/bin/env python3
"""Tests for scripts/clone_results_repo.py — URL building, the per-type sparse set
(DATA_REPO_TYPES, AISDLC-202) and the sparse checkout against a local git repo."""

import os
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "clone_results_repo.py")

import clone_results_repo  # noqa: E402
import type_registry  # noqa: E402
from clone_results_repo import (  # noqa: E402
    DEFAULT_SPARSE_PATTERNS,
    TYPES_ENV,
    build_clone_url,
    parse_types,
    sparse_patterns,
)

REG = type_registry.load()
RFE_PREFIX = REG.get("rfe").get("snapshot.prefix")
INIT_SUBDIR = REG.get("initiative").get("snapshot.results_subdir")
INIT_PREFIX = REG.get("initiative").get("snapshot.prefix")
# The production job's clone, as .gitlab-ci.yml has always run it (no DATA_REPO_TYPES).
LEGACY_PATTERNS = ["/latest", "/*/auto-fix-runs/issue-snapshot-*.yaml", "!/test-data/**"]


def _clone_env(types=None):
    """The script's environment: never the developer's DATA_REPO_TYPES unless asked."""
    env = {k: v for k, v in os.environ.items() if k != TYPES_ENV}
    env["DATA_REPO_TOKEN"] = ""
    if types is not None:
        env[TYPES_ENV] = types
    return env


def _run_clone(src, dest, types=None):
    return subprocess.run(
        [sys.executable, SCRIPT, src, dest],
        capture_output=True,
        text=True,
        env=_clone_env(types),
    )


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
        url = build_clone_url("https://gitlab.com/org/repo.git", "glpat-xxx")
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
        url = build_clone_url("https://gitlab.example.com:8443/org/repo.git", "tok")
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
    subprocess.run(
        ["git", "-C", path, "config", "user.email", "test@test"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", path, "config", "user.name", "test"], check=True, capture_output=True
    )
    return path


def _commit_file(repo, relpath, content="placeholder"):
    """Write a file and commit it."""
    full = os.path.join(repo, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    subprocess.run(["git", "-C", repo, "add", relpath], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", repo, "commit", "-m", f"add {relpath}"], check=True, capture_output=True
    )


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
        snap_content = yaml.dump(
            {
                "query_timestamp": "2026-04-01T00:00:00Z",
                "timestamp": "2026-04-01T00:00:01Z",
                "issues": {"RHAIRFE-1": "abc123"},
            }
        )
        _commit_file(
            src, "20260401-120000/auto-fix-runs/issue-snapshot-20260401-120000.yaml", snap_content
        )
        _commit_file(src, "20260401-120000/auto-fix-runs/20260401-120000.yaml", "run report data")
        _commit_file(src, "20260401-120000/rfe-tasks/RHAIRFE-1.md", "task content")
        _commit_file(src, "20260401-120000/rfe-reviews/RHAIRFE-1-review.md", "review content")
        _commit_file(src, "20260401-120000/rfe-originals/RHAIRFE-1.md", "original content")

        # Create the latest symlink (committed as a file in git)
        latest_path = os.path.join(src, "latest")
        os.symlink("20260401-120000", latest_path)
        subprocess.run(["git", "-C", src, "add", "latest"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", src, "commit", "-m", "add latest"], check=True, capture_output=True
        )

        # Clone with the script
        dest = str(tmp_path / "clone")
        r = _run_clone(src, dest)
        assert r.returncode == 0, r.stderr

        # Snapshot file should exist
        snap_file = os.path.join(
            dest, "20260401-120000", "auto-fix-runs", "issue-snapshot-20260401-120000.yaml"
        )
        assert os.path.exists(snap_file)
        with open(snap_file) as f:
            data = yaml.safe_load(f)
        assert data["issues"]["RHAIRFE-1"] == "abc123"

        # latest symlink should exist
        assert os.path.exists(os.path.join(dest, "latest"))

        # Files outside the sparse patterns should NOT be materialized
        assert not os.path.exists(
            os.path.join(dest, "20260401-120000", "rfe-tasks", "RHAIRFE-1.md")
        )
        assert not os.path.exists(
            os.path.join(dest, "20260401-120000", "rfe-reviews", "RHAIRFE-1-review.md")
        )
        assert not os.path.exists(
            os.path.join(dest, "20260401-120000", "rfe-originals", "RHAIRFE-1.md")
        )

    def test_run_report_not_materialized(self, tmp_path):
        """Run report YAML in auto-fix-runs/ is excluded (not a snapshot)."""
        src = _init_source_repo(str(tmp_path / "source"))

        snap_content = yaml.dump(
            {
                "query_timestamp": "2026-04-01T00:00:00Z",
                "timestamp": "2026-04-01T00:00:01Z",
                "issues": {},
            }
        )
        _commit_file(
            src, "20260401-120000/auto-fix-runs/issue-snapshot-20260401-120000.yaml", snap_content
        )
        _commit_file(src, "20260401-120000/auto-fix-runs/20260401-120000.yaml", "run report")

        dest = str(tmp_path / "clone")
        r = _run_clone(src, dest)
        assert r.returncode == 0, r.stderr

        # Snapshot: yes
        assert os.path.exists(
            os.path.join(
                dest, "20260401-120000", "auto-fix-runs", "issue-snapshot-20260401-120000.yaml"
            )
        )
        # Run report: no
        assert not os.path.exists(
            os.path.join(dest, "20260401-120000", "auto-fix-runs", "20260401-120000.yaml")
        )

    def test_test_data_not_materialized(self, tmp_path):
        """test-data/ directory is excluded from sparse checkout."""
        src = _init_source_repo(str(tmp_path / "source"))

        snap_content = yaml.dump(
            {
                "query_timestamp": "2026-04-01T00:00:00Z",
                "timestamp": "2026-04-01T00:00:01Z",
                "issues": {"RHAIRFE-1": "abc123"},
            }
        )
        _commit_file(
            src, "20260401-120000/auto-fix-runs/issue-snapshot-20260401-120000.yaml", snap_content
        )

        fake_snap = yaml.dump(
            {
                "query_timestamp": "2026-04-01T00:00:00Z",
                "timestamp": "2026-04-01T00:00:01Z",
                "issues": {"RHAIRFE-FAKE": "fake"},
            }
        )
        _commit_file(src, "test-data/auto-fix-runs/issue-snapshot-test.yaml", fake_snap)

        dest = str(tmp_path / "clone")
        r = _run_clone(src, dest)
        assert r.returncode == 0, r.stderr

        # Real snapshot materialized
        assert os.path.exists(
            os.path.join(
                dest, "20260401-120000", "auto-fix-runs", "issue-snapshot-20260401-120000.yaml"
            )
        )

        # test-data snapshot NOT materialized
        assert not os.path.exists(
            os.path.join(dest, "test-data", "auto-fix-runs", "issue-snapshot-test.yaml")
        )


# ── Per-type sparse set (AISDLC-202) ────────────────────────────────────────


class TestParseTypes:
    @pytest.mark.parametrize("value", [None, "", " ", ",", " , "])
    def test_empty_forms(self, value):
        assert parse_types(value) == []

    def test_comma_separated_trimmed_and_deduplicated(self):
        assert parse_types(" rfe, initiative ,rfe,") == ["rfe", "initiative"]


class TestSparsePatterns:
    def test_default_is_the_legacy_literal_set(self):
        """DATA_REPO_TYPES unset: today's three patterns, byte for byte."""
        assert list(DEFAULT_SPARSE_PATTERNS) == LEGACY_PATTERNS
        assert sparse_patterns([]) == LEGACY_PATTERNS

    def test_default_never_imports_the_registry(self, monkeypatch):
        """The production RFE job runs the default path in a fail-open before_script line;
        it must not depend on the registry (or PyYAML) being importable."""
        monkeypatch.setitem(sys.modules, "type_registry", None)  # `import` now raises
        assert sparse_patterns([]) == LEGACY_PATTERNS
        assert clone_results_repo.type_layouts([]) == []
        with pytest.raises(ImportError):
            sparse_patterns(["rfe"])

    def test_rfe_through_the_registry_equals_the_legacy_set(self):
        """The pending-script pin: the literal default IS the rfe descriptor's projection
        (snapshot.results_subdir "" -> root forms, snapshot.prefix), so DATA_REPO_TYPES=rfe
        and unset clone the same files."""
        assert REG.get("rfe").get("snapshot.results_subdir") == ""
        assert sparse_patterns(["rfe"]) == LEGACY_PATTERNS
        assert LEGACY_PATTERNS[1] == f"/*/auto-fix-runs/{RFE_PREFIX}*.yaml"

    def test_initiative_subtree(self):
        assert sparse_patterns(["initiative"]) == [
            "/initiative/latest",
            "/initiative/*/auto-fix-runs/initiative-snapshot-*.yaml",
            "!/test-data/**",
        ]
        assert INIT_SUBDIR == "initiative" and INIT_PREFIX == "initiative-snapshot-"

    def test_both_types_in_order_with_one_exclusion(self):
        assert sparse_patterns(["rfe", "initiative"]) == [
            "/latest",
            "/*/auto-fix-runs/issue-snapshot-*.yaml",
            "/initiative/latest",
            "/initiative/*/auto-fix-runs/initiative-snapshot-*.yaml",
            "!/test-data/**",
        ]
        assert sparse_patterns(["initiative", "rfe"])[:-1] == [
            "/initiative/latest",
            "/initiative/*/auto-fix-runs/initiative-snapshot-*.yaml",
            "/latest",
            "/*/auto-fix-runs/issue-snapshot-*.yaml",
        ]

    def test_unknown_type_names_it_and_the_registered_ones(self):
        with pytest.raises(ValueError, match=r"DATA_REPO_TYPES: unknown type 'bogus'") as exc:
            sparse_patterns(["initiative", "bogus"])
        assert "registered: rfe, initiative" in str(exc.value)


class _GitRecorder:
    """Stand-in for subprocess.run: records argv/cwd, runs nothing."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs.get("cwd")))
        return subprocess.CompletedProcess(argv, 0, "", "")


class TestMainArgv:
    """main() against a recorded git: the exact argv each DATA_REPO_TYPES value produces."""

    def _main(self, monkeypatch, tmp_path, types=None):
        rec = _GitRecorder()
        monkeypatch.setattr(clone_results_repo.subprocess, "run", rec)
        monkeypatch.delenv(TYPES_ENV, raising=False)
        if types is not None:
            monkeypatch.setenv(TYPES_ENV, types)
        dest = str(tmp_path / "clone")
        monkeypatch.setattr(sys, "argv", ["clone_results_repo.py", "/src/results.git", dest])
        clone_results_repo.main()
        return rec.calls, dest

    def test_default_argv_is_the_production_clone(self, monkeypatch, tmp_path, capsys):
        calls, dest = self._main(monkeypatch, tmp_path)
        assert calls == [
            (
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--filter=blob:none",
                    "--sparse",
                    "/src/results.git",
                    dest,
                ],
                None,
            ),
            (["git", "sparse-checkout", "set", "--no-cone", *LEGACY_PATTERNS], dest),
        ]
        assert capsys.readouterr().out == dest + "\n"
        # No subtree directory is created on the default path.
        assert not os.path.exists(dest)

    def test_empty_value_is_the_default(self, monkeypatch, tmp_path):
        calls, dest = self._main(monkeypatch, tmp_path, types="")
        assert calls[1] == (["git", "sparse-checkout", "set", "--no-cone", *LEGACY_PATTERNS], dest)

    def test_initiative_argv_and_subtree_dir(self, monkeypatch, tmp_path):
        calls, dest = self._main(monkeypatch, tmp_path, types="initiative")
        assert calls[1] == (
            [
                "git",
                "sparse-checkout",
                "set",
                "--no-cone",
                "/initiative/latest",
                "/initiative/*/auto-fix-runs/initiative-snapshot-*.yaml",
                "!/test-data/**",
            ],
            dest,
        )
        assert os.path.isdir(os.path.join(dest, "initiative"))

    def test_unknown_type_fails_before_any_git_call(self, monkeypatch, tmp_path, capsys):
        rec = _GitRecorder()
        monkeypatch.setattr(clone_results_repo.subprocess, "run", rec)
        monkeypatch.setenv(TYPES_ENV, "rfe,bogus")
        monkeypatch.setattr(sys, "argv", ["clone_results_repo.py", "/src/results.git", "/x"])
        with pytest.raises(SystemExit) as exc:
            clone_results_repo.main()
        assert exc.value.code == 1
        assert rec.calls == []
        assert "DATA_REPO_TYPES: unknown type 'bogus'" in capsys.readouterr().err


# ── Sparse checkout of the shared layout (root = rfe, initiative/ = initiative) ─────


def _snapshot(issues):
    return yaml.dump(
        {
            "query_timestamp": "2026-04-01T00:00:00Z",
            "timestamp": "2026-04-01T00:00:01Z",
            "issues": issues,
        }
    )


def _commit_symlink(repo, relpath, target):
    full = os.path.join(repo, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    os.symlink(target, full)
    subprocess.run(["git", "-C", repo, "add", relpath], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", repo, "commit", "-m", f"add {relpath}"], check=True, capture_output=True
    )


RFE_RUN = "20260401-120000"
INIT_RUN = "20260402-130000"
RFE_SNAP = f"{RFE_RUN}/auto-fix-runs/{RFE_PREFIX}{RFE_RUN}.yaml"
INIT_SNAP = f"{INIT_SUBDIR}/{INIT_RUN}/auto-fix-runs/{INIT_PREFIX}{INIT_RUN}.yaml"
INIT_REPORT = f"{INIT_SUBDIR}/{INIT_RUN}/auto-fix-runs/initiative-run-{INIT_RUN}.yaml"
INIT_TASK = f"{INIT_SUBDIR}/{INIT_RUN}/initiatives/RHOAIENG-1.md"


def _shared_layout_repo(tmp_path, with_initiative=True):
    """The production results repository after AISDLC-202: an rfe run and `latest` at
    the root, an initiative run and `latest` under initiative/, plus test-data/."""
    src = _init_source_repo(str(tmp_path / "source"))
    _commit_file(src, RFE_SNAP, _snapshot({"RHAIRFE-1": "abc123"}))
    _commit_file(src, f"{RFE_RUN}/auto-fix-runs/{RFE_RUN}.yaml", "rfe run report")
    _commit_file(src, f"{RFE_RUN}/rfe-tasks/RHAIRFE-1.md", "task content")
    _commit_symlink(src, "latest", RFE_RUN)
    _commit_file(src, "test-data/auto-fix-runs/issue-snapshot-test.yaml", _snapshot({"X-1": "f"}))
    if with_initiative:
        _commit_file(src, INIT_SNAP, _snapshot({"RHOAIENG-1": "def456"}))
        _commit_file(src, INIT_REPORT, "initiative run report")
        _commit_file(src, INIT_TASK, "initiative content")
        _commit_symlink(src, f"{INIT_SUBDIR}/latest", INIT_RUN)
    return src


def _exists(dest, rel):
    return os.path.lexists(os.path.join(dest, rel))


class TestSharedLayoutCheckout:
    def test_default_materializes_the_root_only(self, tmp_path):
        """No DATA_REPO_TYPES: today's clone, with the initiative subtree present in the
        repository and absent from the checkout."""
        src = _shared_layout_repo(tmp_path)
        dest = str(tmp_path / "clone")
        r = _run_clone(src, dest)
        assert r.returncode == 0, r.stderr
        assert _exists(dest, RFE_SNAP)
        assert os.path.islink(os.path.join(dest, "latest"))
        assert not _exists(dest, INIT_SUBDIR)
        assert not _exists(dest, f"{RFE_RUN}/auto-fix-runs/{RFE_RUN}.yaml")
        assert not _exists(dest, "test-data")

    def test_initiative_materializes_its_subtree_only(self, tmp_path):
        src = _shared_layout_repo(tmp_path)
        dest = str(tmp_path / "clone")
        r = _run_clone(src, dest, types="initiative")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == dest
        assert _exists(dest, INIT_SNAP)
        with open(os.path.join(dest, INIT_SNAP)) as f:
            assert yaml.safe_load(f)["issues"] == {"RHOAIENG-1": "def456"}
        latest = os.path.join(dest, INIT_SUBDIR, "latest")
        assert os.path.islink(latest) and os.readlink(latest) == INIT_RUN
        # Not the subtree's reports or tasks, not the root, not test-data.
        assert not _exists(dest, INIT_REPORT)
        assert not _exists(dest, INIT_TASK)
        assert not _exists(dest, "latest")
        assert not _exists(dest, RFE_SNAP)
        assert not _exists(dest, "test-data")

    def test_both_types_materialize_both_subtrees(self, tmp_path):
        src = _shared_layout_repo(tmp_path)
        dest = str(tmp_path / "clone")
        r = _run_clone(src, dest, types="rfe,initiative")
        assert r.returncode == 0, r.stderr
        assert _exists(dest, RFE_SNAP) and os.path.islink(os.path.join(dest, "latest"))
        assert _exists(dest, INIT_SNAP)
        assert os.path.islink(os.path.join(dest, INIT_SUBDIR, "latest"))
        assert not _exists(dest, INIT_REPORT)
        assert not _exists(dest, "test-data")

    def test_first_run_sees_an_empty_subtree_not_a_missing_path(self, tmp_path, capsys):
        """Before the first Initiative push nothing matches the subtree patterns; the
        script still creates initiative/ so the fetch reads "no 'latest' symlink" (an
        empty baseline) rather than "Data repo path not found" (a setup failure)."""
        src = _shared_layout_repo(tmp_path, with_initiative=False)
        dest = str(tmp_path / "clone")
        r = _run_clone(src, dest, types="initiative")
        assert r.returncode == 0, r.stderr
        subtree = os.path.join(dest, INIT_SUBDIR)
        assert os.path.isdir(subtree) and os.listdir(subtree) == []
        assert not _exists(dest, "latest")

        import snapshot_fetch

        assert snapshot_fetch.load_snapshot_from_dir(subtree, prefix=INIT_PREFIX) is None
        err = capsys.readouterr().err
        assert "no 'latest' symlink" in err
        assert "Data repo path not found" not in err

    def test_unknown_type_is_a_clean_failure(self, tmp_path):
        src = _shared_layout_repo(tmp_path)
        dest = str(tmp_path / "clone")
        r = _run_clone(src, dest, types="bogus")
        assert r.returncode == 1
        assert "DATA_REPO_TYPES: unknown type 'bogus'" in r.stderr
        assert "Traceback" not in r.stderr
        assert not os.path.exists(dest)
