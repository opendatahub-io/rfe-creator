#!/usr/bin/env python3
"""Tests for scripts/bootstrap_snapshot.py."""

import hashlib
import json
import os
import subprocess
import sys
import textwrap
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "bootstrap_snapshot.py")

import type_registry  # noqa: E402
from bootstrap_snapshot import (  # noqa: E402
    _RFE_SNAPSHOT_PREFIX,
    BOOTSTRAP_CONFIG,
    _description_at_time,
    _load_run_report,
    _parse_adf,
    _run_dir_has_snapshots,
    find_latest_run_timestamp,
)
from snapshot_fetch import normalize_for_hash  # noqa: E402


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _md_hash(text):
    """Compute the expected hash for markdown content."""
    normalized = normalize_for_hash(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _text_to_adf(text):
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }


# ── Unit Tests ────────────────────────────────────────────────────────────────


class TestFindLatestRunTimestamp:
    def test_follows_latest_symlink(self, tmp_path):
        (tmp_path / "20260401-120000").mkdir()
        (tmp_path / "20260402-080000").mkdir()
        os.symlink("20260401-120000", str(tmp_path / "latest"))

        name, dt = find_latest_run_timestamp(str(tmp_path))
        assert name == "20260401-120000"
        assert dt.year == 2026
        assert dt.month == 4
        assert dt.day == 1

    def test_newest_dir_without_symlink(self, tmp_path):
        (tmp_path / "20260401-120000").mkdir()
        (tmp_path / "20260402-080000").mkdir()

        name, dt = find_latest_run_timestamp(str(tmp_path))
        assert name == "20260402-080000"

    def test_empty_dir_returns_none(self, tmp_path):
        name, dt = find_latest_run_timestamp(str(tmp_path))
        assert name is None
        assert dt is None

    def test_skips_non_timestamp_dirs(self, tmp_path):
        (tmp_path / "not-a-timestamp").mkdir()
        (tmp_path / "20260401-120000").mkdir()

        name, dt = find_latest_run_timestamp(str(tmp_path))
        assert name == "20260401-120000"

    def test_skips_test_data_dir(self, tmp_path):
        """test-data/ is not considered a run directory."""
        (tmp_path / "test-data").mkdir()
        (tmp_path / "20260401-120000").mkdir()

        name, dt = find_latest_run_timestamp(str(tmp_path))
        assert name == "20260401-120000"

    def test_test_data_only_returns_none(self, tmp_path):
        """Only test-data/ present → no valid runs."""
        (tmp_path / "test-data").mkdir()

        name, dt = find_latest_run_timestamp(str(tmp_path))
        assert name is None
        assert dt is None


class TestParseAdf:
    def test_none_returns_none(self):
        assert _parse_adf(None) is None

    def test_dict_passthrough(self):
        adf = {"type": "doc", "version": 1, "content": []}
        assert _parse_adf(adf) == adf

    def test_json_string_parsed(self):
        adf = {"type": "doc", "version": 1, "content": []}
        assert _parse_adf(json.dumps(adf)) == adf

    def test_wiki_markup_returned_as_string(self):
        wiki = "h2. Business Goal\n\nSome description text."
        assert _parse_adf(wiki) == wiki

    def test_non_dict_json_returned_as_string(self):
        # JSON that isn't a dict isn't ADF — returned as raw string
        assert _parse_adf(json.dumps([1, 2, 3])) == "[1, 2, 3]"


class TestDescriptionAtTime:
    """Unit tests for _description_at_time with from/to and fromString/toString."""

    def test_adf_from_to(self):
        """Uses from/to when available (Jira Cloud)."""
        from datetime import datetime, timezone

        target = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
        changelog = [
            {
                "created": datetime(2026, 4, 2, 10, 0, tzinfo=timezone.utc),
                "items": [
                    {
                        "field": "description",
                        "from": json.dumps(_text_to_adf("before")),
                        "to": json.dumps(_text_to_adf("after")),
                        "fromString": "before",
                        "toString": "after",
                    }
                ],
            }
        ]
        result = _description_at_time(changelog, target)
        # Should use ADF from "from", not fromString
        assert isinstance(result, dict)
        assert result == _text_to_adf("before")

    def test_falls_back_to_fromstring(self):
        """Falls back to fromString/toString when from/to are None (Jira Server/DC)."""
        from datetime import datetime, timezone

        target = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
        changelog = [
            {
                "created": datetime(2026, 4, 2, 10, 0, tzinfo=timezone.utc),
                "items": [
                    {
                        "field": "description",
                        "from": None,
                        "to": None,
                        "fromString": "h2. Before\n\nOriginal text.",
                        "toString": "h2. After\n\nEdited text.",
                    }
                ],
            }
        ]
        result = _description_at_time(changelog, target)
        assert result == "h2. Before\n\nOriginal text."

    def test_to_value_for_pre_target_change(self):
        """Change before target → uses 'to' value."""
        from datetime import datetime, timezone

        target = datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc)
        changelog = [
            {
                "created": datetime(2026, 4, 2, 10, 0, tzinfo=timezone.utc),
                "items": [
                    {
                        "field": "description",
                        "from": None,
                        "to": None,
                        "fromString": "old wiki",
                        "toString": "new wiki",
                    }
                ],
            }
        ]
        result = _description_at_time(changelog, target)
        assert result == "new wiki"

    def test_no_description_changes(self):
        """No description items → returns None."""
        from datetime import datetime, timezone

        target = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
        changelog = [
            {
                "created": datetime(2026, 4, 2, 10, 0, tzinfo=timezone.utc),
                "items": [{"field": "status", "fromString": "New", "toString": "Done"}],
            }
        ]
        assert _description_at_time(changelog, target) is None


# ── Mock Jira Server ─────────────────────────────────────────────────────────


class JiraHandler(BaseHTTPRequestHandler):
    """Mock Jira that serves search results and changelogs."""

    def do_GET(self):
        decoded = urllib.parse.unquote(self.path)

        if "/search/jql" in decoded:
            self._handle_search(decoded)
        elif "/changelog" in decoded:
            self._handle_changelog(decoded)
        elif "/rest/api/2/issue/" in decoded:
            self._handle_v2_issue(decoded)
        else:
            self._json(404, {"error": "not found"})

    def _handle_search(self, path):
        fields = ""
        if "fields=" in path:
            fields = path.split("fields=")[1].split("&")[0]

        issues = []
        for key, desc in self.server.issues.items():
            if fields == "key":
                issues.append({"key": key})
            else:
                adf = _text_to_adf(desc) if desc else None
                issues.append(
                    {
                        "key": key,
                        "fields": {"description": adf, "labels": []},
                    }
                )
        self._json(200, {"issues": issues, "isLast": True})

    def _handle_changelog(self, path):
        # Extract issue key from path like /issue/RHAIRFE-1234/changelog
        parts = path.split("/issue/")[1].split("/changelog")[0]
        key = parts.split("?")[0]

        histories = self.server.changelogs.get(key, [])
        self._json(
            200,
            {
                "values": histories,
                "total": len(histories),
            },
        )

    def _handle_v2_issue(self, path):
        # /rest/api/2/issue/RHAIRFE-1234?fields=description
        key = path.split("/rest/api/2/issue/")[1].split("?")[0]
        wiki = self.server.wiki_descriptions.get(key, "")
        self._json(200, {"fields": {"description": wiki}})

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
    server = HTTPServer(("127.0.0.1", 0), JiraHandler)
    server.issues = {}
    server.changelogs = {}
    server.wiki_descriptions = {}
    url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    yield url, server
    server.shutdown()


def _make_results_dir(tmp_path, run_names, latest=None, processed_ids=None, reports=None):
    """Create a results directory with run dirs.

    If processed_ids is provided and latest is set, writes a run report
    with per_rfe entries for those IDs. `reports` writes one report per
    run name: a list becomes per_rfe entries, a dict is dumped as the
    whole report, a string is written raw (for corrupt-report tests).
    """
    results = str(tmp_path / "results")
    os.makedirs(results)
    for name in run_names:
        os.makedirs(os.path.join(results, name))
    if latest:
        os.symlink(latest, os.path.join(results, "latest"))
    if processed_ids is not None and latest:
        reports = {**(reports or {}), latest: processed_ids}
    for name, spec in (reports or {}).items():
        report_dir = os.path.join(results, name, "auto-fix-runs")
        os.makedirs(report_dir, exist_ok=True)
        path = os.path.join(report_dir, f"{name}.yaml")
        if isinstance(spec, str):
            with open(path, "w") as f:
                f.write(spec)
            continue
        if isinstance(spec, dict):
            report = spec
        else:
            report = {"per_rfe": [{"id": pid, "recommendation": "submit"} for pid in spec]}
        with open(path, "w") as f:
            yaml.dump(report, f)
    return results


# ── Integration Tests ─────────────────────────────────────────────────────────


class TestBootstrapIntegration:
    def test_dry_run(self, tmp_path, mock_jira):
        """Dry run prints plan without writing files."""
        url, server = mock_jira
        server.issues = {
            "RHAIRFE-1234": "Description 1.",
            "RHAIRFE-5678": "Description 2.",
        }
        results = _make_results_dir(
            tmp_path,
            ["20260401-120000"],
            latest="20260401-120000",
            processed_ids=["RHAIRFE-1234", "RHAIRFE-5678"],
        )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--dry-run",
                "--results-dir",
                results,
                "--artifacts-dir",
                art_dir,
                "project = RHAIRFE",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr
        assert "Dry run" in r.stdout
        assert "2 hashes" in r.stdout
        # The count alone cannot tell an operator whether the run-report filter applied, which is
        # the difference between a correct snapshot and a silently over-inclusive one.
        assert "issues from the run report" in r.stdout

        # No files written
        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        assert not os.path.exists(snapshot_dir)

    def test_creates_snapshot_with_current_hashes(self, tmp_path, mock_jira):
        """Issues not updated since run use current Jira hashes."""
        url, server = mock_jira
        server.issues = {
            "RHAIRFE-1234": "Current description.",
            "RHAIRFE-5678": "Another description.",
        }
        # No issues match "updated since run" query (mock returns all
        # for any query, so we set the run time to now — the JQL filter
        # for updated >= will still match all, but changelogs are empty
        # so current hashes are used)
        results = _make_results_dir(
            tmp_path,
            ["20260401-120000"],
            latest="20260401-120000",
            processed_ids=["RHAIRFE-1234", "RHAIRFE-5678"],
        )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--results-dir",
                results,
                "--artifacts-dir",
                art_dir,
                "project = RHAIRFE",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir) if f.startswith("issue-snapshot-")]
        assert len(snapshots) == 1

        with open(os.path.join(snapshot_dir, snapshots[0])) as f:
            snap = yaml.safe_load(f)

        assert len(snap["issues"]) == 2
        expected_1234 = _md_hash("Current description.")
        assert snap["issues"]["RHAIRFE-1234"] == expected_1234

    def test_run_timestamp_used(self, tmp_path, mock_jira):
        """Snapshot query_timestamp and filename come from the run directory name."""
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Content."}
        results = _make_results_dir(
            tmp_path, ["20260401-120000"], latest="20260401-120000", processed_ids=["RHAIRFE-1"]
        )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--results-dir",
                results,
                "--artifacts-dir",
                art_dir,
                "project = RHAIRFE",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir) if f.startswith("issue-snapshot-")]
        with open(os.path.join(snapshot_dir, snapshots[0])) as f:
            snap = yaml.safe_load(f)

        assert snap["query_timestamp"] == "2026-04-01T12:00:00Z"
        assert snap["bootstrapped_from"] == "20260401-120000"
        # Filename uses the run directory name, not current time
        assert snapshots[0] == "issue-snapshot-20260401-120000.yaml"

    def test_historical_description_via_changelog(self, tmp_path, mock_jira):
        """Issue updated since run gets historical hash from changelog."""
        url, server = mock_jira
        # Current description (after someone edited it)
        server.issues = {"RHAIRFE-1": "Edited after run."}
        # Changelog shows description was changed AFTER the run
        server.changelogs["RHAIRFE-1"] = [
            {
                "created": "2026-04-02T10:00:00.000+0000",
                "items": [
                    {
                        "field": "description",
                        "from": json.dumps(_text_to_adf("Original at run time.")),
                        "to": json.dumps(_text_to_adf("Edited after run.")),
                    }
                ],
            }
        ]
        results = _make_results_dir(
            tmp_path, ["20260401-120000"], latest="20260401-120000", processed_ids=["RHAIRFE-1"]
        )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--results-dir",
                results,
                "--artifacts-dir",
                art_dir,
                "project = RHAIRFE",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir) if f.startswith("issue-snapshot-")]
        with open(os.path.join(snapshot_dir, snapshots[0])) as f:
            snap = yaml.safe_load(f)

        # Should use the HISTORICAL hash (from before the edit)
        historical_hash = _md_hash("Original at run time.")
        current_hash = _md_hash("Edited after run.")
        assert snap["issues"]["RHAIRFE-1"] == historical_hash
        assert snap["issues"]["RHAIRFE-1"] != current_hash

    def test_adf_changelog_unchanged_uses_current_hash(self, tmp_path, mock_jira):
        """ADF changelog change before run → 'to' hash matches current ADF hash."""
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Updated before run."}
        # Description changed BEFORE the run — 'to' matches current
        server.changelogs["RHAIRFE-1"] = [
            {
                "created": "2026-03-30T10:00:00.000+0000",
                "items": [
                    {
                        "field": "description",
                        "from": json.dumps(_text_to_adf("Old version.")),
                        "to": json.dumps(_text_to_adf("Updated before run.")),
                    }
                ],
            }
        ]
        results = _make_results_dir(
            tmp_path, ["20260401-120000"], latest="20260401-120000", processed_ids=["RHAIRFE-1"]
        )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--results-dir",
                results,
                "--artifacts-dir",
                art_dir,
                "project = RHAIRFE",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir) if f.startswith("issue-snapshot-")]
        with open(os.path.join(snapshot_dir, snapshots[0])) as f:
            snap = yaml.safe_load(f)

        # ADF 'to' hash == current ADF hash (same content, same format)
        current_hash = _md_hash("Updated before run.")
        assert snap["issues"]["RHAIRFE-1"] == current_hash

    def test_only_snapshot_written(self, tmp_path, mock_jira):
        """Bootstrap writes only an issue-snapshot file, nothing else."""
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Content."}
        results = _make_results_dir(
            tmp_path, ["20260401-120000"], latest="20260401-120000", processed_ids=["RHAIRFE-1"]
        )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--results-dir",
                results,
                "--artifacts-dir",
                art_dir,
                "project = RHAIRFE",
            ],
            capture_output=True,
            text=True,
            env=env,
        )

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        files = os.listdir(snapshot_dir)
        assert all(f.startswith("issue-snapshot-") for f in files)

    def test_wiki_markup_fallback_changed(self, tmp_path, mock_jira):
        """Jira Server/DC: from/to are None, falls back to fromString/toString.

        When historical wiki differs from current wiki, uses historical hash.
        """
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Edited after run."}
        server.wiki_descriptions = {"RHAIRFE-1": "h2. Edited after run."}
        server.changelogs["RHAIRFE-1"] = [
            {
                "created": "2026-04-02T10:00:00.000+0000",
                "items": [
                    {
                        "field": "description",
                        "from": None,
                        "to": None,
                        "fromString": "h2. Original at run time.",
                        "toString": "h2. Edited after run.",
                    }
                ],
            }
        ]
        results = _make_results_dir(
            tmp_path, ["20260401-120000"], latest="20260401-120000", processed_ids=["RHAIRFE-1"]
        )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--results-dir",
                results,
                "--artifacts-dir",
                art_dir,
                "project = RHAIRFE",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir) if f.startswith("issue-snapshot-")]
        with open(os.path.join(snapshot_dir, snapshots[0])) as f:
            snap = yaml.safe_load(f)

        # Should use historical wiki hash, NOT current ADF hash
        hist_hash = _md_hash("h2. Original at run time.")
        current_adf_hash = _md_hash("Edited after run.")
        assert snap["issues"]["RHAIRFE-1"] == hist_hash
        assert snap["issues"]["RHAIRFE-1"] != current_adf_hash

    def test_wiki_markup_fallback_unchanged(self, tmp_path, mock_jira):
        """Jira Server/DC: pre-run description change, wiki matches current.

        When historical wiki matches current wiki via v2, uses current ADF hash
        (avoids false positive from wiki vs ADF format difference).
        """
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Same description."}
        server.wiki_descriptions = {"RHAIRFE-1": "h2. Same description."}
        # Description was changed BEFORE the run
        server.changelogs["RHAIRFE-1"] = [
            {
                "created": "2026-03-30T10:00:00.000+0000",
                "items": [
                    {
                        "field": "description",
                        "from": None,
                        "to": None,
                        "fromString": "h2. Old version.",
                        "toString": "h2. Same description.",
                    }
                ],
            }
        ]
        results = _make_results_dir(
            tmp_path, ["20260401-120000"], latest="20260401-120000", processed_ids=["RHAIRFE-1"]
        )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--results-dir",
                results,
                "--artifacts-dir",
                art_dir,
                "project = RHAIRFE",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir) if f.startswith("issue-snapshot-")]
        with open(os.path.join(snapshot_dir, snapshots[0])) as f:
            snap = yaml.safe_load(f)

        # Should use current ADF hash (not wiki hash) since content is the same
        current_adf_hash = _md_hash("Same description.")
        wiki_hash = _md_hash("h2. Same description.")
        assert snap["issues"]["RHAIRFE-1"] == current_adf_hash
        assert snap["issues"]["RHAIRFE-1"] != wiki_hash

    def test_reopened_issue_excluded(self, tmp_path, mock_jira):
        """Issue in Done status at run time is excluded from snapshot."""
        url, server = mock_jira
        server.issues = {
            "RHAIRFE-1": "Normal issue.",
            "RHAIRFE-2": "Was closed, now reopened.",
        }
        # RHAIRFE-2 was Closed at run time, reopened after
        server.changelogs["RHAIRFE-2"] = [
            {
                "created": "2026-04-02T10:00:00.000+0000",
                "items": [
                    {
                        "field": "status",
                        "fromString": "Closed",
                        "toString": "New",
                    }
                ],
            }
        ]
        results = _make_results_dir(
            tmp_path,
            ["20260401-120000"],
            latest="20260401-120000",
            processed_ids=["RHAIRFE-1", "RHAIRFE-2"],
        )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--results-dir",
                results,
                "--artifacts-dir",
                art_dir,
                "project = RHAIRFE",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir) if f.startswith("issue-snapshot-")]
        with open(os.path.join(snapshot_dir, snapshots[0])) as f:
            snap = yaml.safe_load(f)

        assert "RHAIRFE-1" in snap["issues"]
        assert "RHAIRFE-2" not in snap["issues"]
        assert len(snap["issues"]) == 1

    def test_filters_to_run_report_ids(self, tmp_path, mock_jira):
        """Only issues listed in run report's per_rfe are included."""
        url, server = mock_jira
        server.issues = {
            "RHAIRFE-1": "Issue one.",
            "RHAIRFE-2": "Issue two.",
            "RHAIRFE-3": "Issue three.",
        }
        # Run report only contains 2 of the 3 issues
        results = _make_results_dir(
            tmp_path,
            ["20260401-120000"],
            latest="20260401-120000",
            processed_ids=["RHAIRFE-1", "RHAIRFE-3"],
        )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--results-dir",
                results,
                "--artifacts-dir",
                art_dir,
                "project = RHAIRFE",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr
        assert "Filtered to 2/3 issues" in r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir) if f.startswith("issue-snapshot-")]
        with open(os.path.join(snapshot_dir, snapshots[0])) as f:
            snap = yaml.safe_load(f)

        assert "RHAIRFE-1" in snap["issues"]
        assert "RHAIRFE-3" in snap["issues"]
        assert "RHAIRFE-2" not in snap["issues"]
        assert len(snap["issues"]) == 2

    def test_no_run_report_includes_all(self, tmp_path, mock_jira):
        """Without a run report, all fetched issues are included."""
        url, server = mock_jira
        server.issues = {
            "RHAIRFE-1": "Issue one.",
            "RHAIRFE-2": "Issue two.",
        }
        # No processed_ids → no run report file
        results = _make_results_dir(tmp_path, ["20260401-120000"], latest="20260401-120000")
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--results-dir",
                results,
                "--artifacts-dir",
                art_dir,
                "project = RHAIRFE",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr
        assert "no run report" in r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir) if f.startswith("issue-snapshot-")]
        with open(os.path.join(snapshot_dir, snapshots[0])) as f:
            snap = yaml.safe_load(f)

        assert len(snap["issues"]) == 2

    def test_auto_revised_keeps_historical_hash(self, tmp_path, mock_jira):
        """auto_revised issues keep historical hashes, not current.

        The bootstrap should not overwrite historical hashes for
        auto_revised issues. submit.py's update_snapshot_hashes is the
        sole authority for recording post-submit state. The bootstrap
        uses only changelog-derived historical hashes so that genuinely
        changed issues are detected on the next fetch.
        """
        url, server = mock_jira
        server.issues = {
            "RHAIRFE-1": "Auto-revised and submitted.",
            "RHAIRFE-2": "Also revised, submitted.",
            "RHAIRFE-3": "Rejected, not submitted.",
        }
        # All three were updated after the run
        for key in ["RHAIRFE-1", "RHAIRFE-2", "RHAIRFE-3"]:
            server.changelogs[key] = [
                {
                    "created": "2026-04-02T10:00:00.000+0000",
                    "items": [
                        {
                            "field": "description",
                            "from": json.dumps(_text_to_adf(f"Original {key}.")),
                            "to": json.dumps(_text_to_adf(server.issues[key])),
                        }
                    ],
                }
            ]

        # Build run report with mixed recommendations
        results = str(tmp_path / "results")
        run_name = "20260401-120000"
        report_dir = os.path.join(results, run_name, "auto-fix-runs")
        os.makedirs(report_dir)
        os.symlink(run_name, os.path.join(results, "latest"))
        report = {
            "per_rfe": [
                {"id": "RHAIRFE-1", "recommendation": "revise", "auto_revised": True},
                {"id": "RHAIRFE-2", "recommendation": "submit", "auto_revised": True},
                {"id": "RHAIRFE-3", "recommendation": "autorevise_reject", "auto_revised": True},
            ]
        }
        with open(os.path.join(report_dir, f"{run_name}.yaml"), "w") as f:
            yaml.dump(report, f)

        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--results-dir",
                results,
                "--artifacts-dir",
                art_dir,
                "project = RHAIRFE",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir) if f.startswith("issue-snapshot-")]
        with open(os.path.join(snapshot_dir, snapshots[0])) as f:
            snap = yaml.safe_load(f)

        historical_hash_1 = _md_hash("Original RHAIRFE-1.")
        historical_hash_2 = _md_hash("Original RHAIRFE-2.")
        historical_hash_3 = _md_hash("Original RHAIRFE-3.")

        # All three keep historical hashes — bootstrap never overwrites
        assert snap["issues"]["RHAIRFE-1"] == historical_hash_1
        assert snap["issues"]["RHAIRFE-2"] == historical_hash_2
        assert snap["issues"]["RHAIRFE-3"] == historical_hash_3

    def _run_bootstrap(self, results, art_dir, url, extra=None):
        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        cmd = [
            sys.executable,
            SCRIPT,
            "--results-dir",
            results,
            "--artifacts-dir",
            art_dir,
        ]
        if extra:
            cmd.extend(extra)
        cmd.append("project = RHAIRFE")
        return subprocess.run(cmd, capture_output=True, text=True, env=env)

    @staticmethod
    def _load_snapshot(art_dir):
        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir) if f.startswith("issue-snapshot-")]
        assert len(snapshots) == 1, snapshots
        with open(os.path.join(snapshot_dir, snapshots[0])) as f:
            return snapshots[0], yaml.safe_load(f)

    def test_empty_report_walks_back_to_previous_run(self, tmp_path, mock_jira):
        """A zero-count latest run is evidence, not absence: filter and
        timestamp come from the newest run that processed anything —
        the newer timestamp would hash issues at post-edit state and hide
        any edit made between the two runs (RHAIFIRST-569)."""
        url, server = mock_jira
        server.issues = {
            "RHAIRFE-1": "Issue one.",
            "RHAIRFE-2": "Issue two.",
        }
        results = _make_results_dir(
            tmp_path,
            ["20260331-110000", "20260401-120000"],
            latest="20260401-120000",
            reports={
                "20260331-110000": {
                    "report_stage": "pre_submit",
                    "per_rfe": [{"id": "RHAIRFE-1", "recommendation": "submit"}],
                },
                "20260401-120000": [],
            },
        )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        r = self._run_bootstrap(results, art_dir, url)
        assert r.returncode == 0, r.stderr
        assert "walking back to 20260331-110000" in r.stderr
        # The hard filter excludes quarantined split parents (RHAIFIRST-570).
        assert "rfe-creator-split-quarantine" in r.stderr
        # The stage warning is evaluated against the report actually USED —
        # the walked-back one — not the empty tip (which has no stage field).
        assert "run report 20260331-110000 is report_stage: pre_submit" in r.stderr

        name, snap = self._load_snapshot(art_dir)
        # The FILE keeps the tip run's name: find_previous_snapshot picks the
        # reverse-lexically newest, so an older-named file would be shadowed
        # forever by any stale snapshot a pre-walk-back bootstrap wrote, and
        # a re-run must overwrite that file in place.
        assert name == "issue-snapshot-20260401-120000.yaml"
        assert snap["bootstrapped_from"] == "20260331-110000"
        assert snap["query_timestamp"] == "2026-03-31T11:00:00Z"
        # Filtered by the older run's report — not include-all.
        assert set(snap["issues"]) == {"RHAIRFE-1"}

    def test_all_reports_empty_includes_all_unprocessed(self, tmp_path, mock_jira):
        """Every report saying "processed nothing" is positive evidence, so no
        --include-all gate: everything is snapshotted as unprocessed."""
        url, server = mock_jira
        server.issues = {
            "RHAIRFE-1": "Issue one.",
            "RHAIRFE-2": "Issue two.",
        }
        results = _make_results_dir(
            tmp_path,
            ["20260331-110000", "20260401-120000"],
            latest="20260401-120000",
            reports={"20260331-110000": [], "20260401-120000": []},
        )
        # Real pushed runs carry snapshots. Their presence must NOT trip the
        # partial-clone guard here: the reports were read and say "processed
        # nothing" — evidence, not absence.
        for run in ("20260331-110000", "20260401-120000"):
            _write(
                os.path.join(results, run, "auto-fix-runs", f"issue-snapshot-{run}.yaml"),
                "issues: {}\n",
            )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        r = self._run_bootstrap(results, art_dir, url)
        assert r.returncode == 0, r.stderr
        assert "no run with a non-empty item list" in r.stderr

        _, snap = self._load_snapshot(art_dir)
        assert len(snap["issues"]) == 2
        # Fail-safe: no run-report evidence of processing, so no bare hash —
        # snapshot_fetch reads a missing `processed` as True and nothing
        # ever demotes such an entry.
        assert all(e == {"hash": e["hash"], "processed": False} for e in snap["issues"].values())

    def test_walk_back_skips_reportless_dir(self, tmp_path, mock_jira):
        """A run directory without a report cannot provide evidence either
        way — the walk-back warns and keeps looking."""
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Issue one."}
        results = _make_results_dir(
            tmp_path,
            ["20260330-100000", "20260331-110000", "20260401-120000"],
            latest="20260401-120000",
            reports={"20260330-100000": ["RHAIRFE-1"], "20260401-120000": []},
        )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        r = self._run_bootstrap(results, art_dir, url)
        assert r.returncode == 0, r.stderr
        assert "20260331-110000 has no run report — skipped in walk-back" in r.stderr
        assert "walking back to 20260330-100000" in r.stderr

        name, snap = self._load_snapshot(art_dir)
        assert snap["bootstrapped_from"] == "20260330-100000"

    def test_corrupt_report_during_walk_back_errors(self, tmp_path, mock_jira):
        """The chain of evidence stops at the first unreadable report — same
        strictness as a corrupt latest, same --include-all escape hatch."""
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Issue one."}
        results = _make_results_dir(
            tmp_path,
            ["20260331-110000", "20260401-120000"],
            latest="20260401-120000",
            reports={"20260331-110000": "per_rfe: [unclosed", "20260401-120000": []},
        )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        r = self._run_bootstrap(results, art_dir, url)
        assert r.returncode == 1
        assert "Error:" in r.stderr and "20260331-110000" in r.stderr

        r = self._run_bootstrap(results, art_dir, url, extra=["--include-all"])
        assert r.returncode == 0, r.stderr
        _, snap = self._load_snapshot(art_dir)
        assert set(snap["issues"]) == {"RHAIRFE-1"}
        assert all(e == {"hash": e["hash"], "processed": False} for e in snap["issues"].values())

    def test_walk_back_refuses_reportless_dir_with_snapshots(self, tmp_path, mock_jira):
        """A dir met during walk-back that has snapshots but no report is the
        aborted-run / partial-clone shape: its work is unknown, so bootstrap
        hard-stops exactly like the tip-level guard (RHAIFIRST-582 review
        finding). --include-all remains the escape hatch."""
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Issue one."}
        results = _make_results_dir(
            tmp_path,
            ["20260330-100000", "20260401-120000"],
            latest="20260401-120000",
            reports={"20260401-120000": []},
        )
        _write(
            os.path.join(
                results,
                "20260330-100000",
                "auto-fix-runs",
                "issue-snapshot-20260330-100000.yaml",
            ),
            "issues: {}\n",
        )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        r = self._run_bootstrap(results, art_dir, url)
        assert r.returncode == 1
        assert "20260330-100000" in r.stderr and "looks partial" in r.stderr

        r = self._run_bootstrap(results, art_dir, url, extra=["--include-all"])
        assert r.returncode == 0, r.stderr
        _, snap = self._load_snapshot(art_dir)
        assert all(e == {"hash": e["hash"], "processed": False} for e in snap["issues"].values())

    def test_walk_back_ignores_replay_dirs_newer_than_latest(self, tmp_path, mock_jira):
        """Replay dirs pushed with --no-update-latest sit NEWER than the
        latest symlink target; the walk-back must only look older."""
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Issue one.", "RHAIRFE-2": "Issue two."}
        results = _make_results_dir(
            tmp_path,
            ["20260331-110000", "20260401-120000", "20260402-130000"],
            latest="20260401-120000",
            reports={
                "20260331-110000": ["RHAIRFE-1"],
                "20260401-120000": [],
                "20260402-130000": ["RHAIRFE-2"],
            },
        )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        r = self._run_bootstrap(results, art_dir, url)
        assert r.returncode == 0, r.stderr
        assert "walking back to 20260331-110000" in r.stderr

        _, snap = self._load_snapshot(art_dir)
        assert set(snap["issues"]) == {"RHAIRFE-1"}

    def test_initiative_walk_back_uses_initiative_report_names(self, tmp_path, mock_jira):
        """The walk-back must read reports through the type config — the
        initiative report is initiative-run-<ts>.yaml with per_initiative."""
        url, server = mock_jira
        server.issues = {"RHOAIENG-1": "Initiative one."}
        results = _make_results_dir(
            tmp_path, ["20260331-110000", "20260401-120000"], latest="20260401-120000"
        )
        for run, items in (
            ("20260331-110000", [{"id": "RHOAIENG-1", "recommendation": "submit"}]),
            ("20260401-120000", []),
        ):
            _write(
                os.path.join(results, run, "auto-fix-runs", f"initiative-run-{run}.yaml"),
                yaml.dump({"per_initiative": items}),
            )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        r = self._run_bootstrap(results, art_dir, url, extra=["--type", "initiative"])
        assert r.returncode == 0, r.stderr
        assert "walking back to 20260331-110000" in r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snaps = [f for f in os.listdir(snapshot_dir) if f.startswith("initiative-snapshot-")]
        assert snaps == ["initiative-snapshot-20260401-120000.yaml"]
        with open(os.path.join(snapshot_dir, snaps[0])) as f:
            snap = yaml.safe_load(f)
        assert set(snap["issues"]) == {"RHOAIENG-1"}

    def test_walk_back_ignores_symlinked_run_dirs(self, tmp_path, mock_jira):
        """A timestamp-named symlink must not route the walk-back to a report
        outside results_dir (CWE-59) — the filter would be attacker-chosen."""
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Issue one.", "RHAIRFE-2": "Issue two."}
        results = _make_results_dir(
            tmp_path,
            ["20260330-100000", "20260401-120000"],
            latest="20260401-120000",
            reports={"20260330-100000": ["RHAIRFE-1"], "20260401-120000": []},
        )
        # Outside dir with a newer-stamped report claiming a different filter.
        outside = tmp_path / "outside" / "20260331-110000"
        _write(
            str(outside / "auto-fix-runs" / "20260331-110000.yaml"),
            yaml.dump({"per_rfe": [{"id": "RHAIRFE-2", "recommendation": "submit"}]}),
        )
        os.symlink(str(outside), os.path.join(results, "20260331-110000"))
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        r = self._run_bootstrap(results, art_dir, url)
        assert r.returncode == 0, r.stderr
        # The symlinked dir is skipped; the walk lands on the real older run.
        assert "walking back to 20260330-100000" in r.stderr
        _, snap = self._load_snapshot(art_dir)
        assert set(snap["issues"]) == {"RHAIRFE-1"}

    def test_pre_submit_report_warns(self, tmp_path, mock_jira):
        """A pre_submit tip predates that run's Jira writes — one warning
        naming the consequence (RHAIFIRST-569 item 2)."""
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Issue one."}
        results = _make_results_dir(
            tmp_path,
            ["20260401-120000"],
            latest="20260401-120000",
            reports={
                "20260401-120000": {
                    "report_stage": "pre_submit",
                    "per_rfe": [{"id": "RHAIRFE-1", "recommendation": "submit"}],
                }
            },
        )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        r = self._run_bootstrap(results, art_dir, url)
        assert r.returncode == 0, r.stderr
        assert "report_stage: pre_submit" in r.stderr
        assert "split children" in r.stderr

    def test_report_without_stage_is_silent(self, tmp_path, mock_jira):
        """Pre-versioned reports say nothing about their stage — no warning."""
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Issue one."}
        results = _make_results_dir(
            tmp_path,
            ["20260401-120000"],
            latest="20260401-120000",
            processed_ids=["RHAIRFE-1"],
        )
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        r = self._run_bootstrap(results, art_dir, url)
        assert r.returncode == 0, r.stderr
        assert "pre_submit" not in r.stderr

    def test_partial_results_dir_is_refused(self, tmp_path, mock_jira):
        """Snapshots but no run report means a partial clone — do not silently include all."""
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Issue one."}
        results = _make_results_dir(tmp_path, ["20260401-120000"], latest="20260401-120000")
        snap_dir = os.path.join(results, "20260401-120000", "auto-fix-runs")
        os.makedirs(snap_dir, exist_ok=True)
        with open(os.path.join(snap_dir, "issue-snapshot-20260401-120000.yaml"), "w") as f:
            yaml.dump({"issues": {}}, f)
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)
        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        cmd = [
            sys.executable,
            SCRIPT,
            "--results-dir",
            results,
            "--artifacts-dir",
            art_dir,
            "project = RHAIRFE",
        ]

        r = subprocess.run(cmd, capture_output=True, text=True, env=env)

        assert r.returncode == 1
        assert "looks partial" in r.stderr
        assert "--include-all" in r.stderr
        assert not os.path.exists(os.path.join(art_dir, "auto-fix-runs"))

    def test_corrupt_report_fails_loudly(self, tmp_path, mock_jira):
        """A report that exists but cannot be parsed is not treated as absent."""
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Issue one."}
        results = _make_results_dir(tmp_path, ["20260401-120000"], latest="20260401-120000")
        report_dir = os.path.join(results, "20260401-120000", "auto-fix-runs")
        os.makedirs(report_dir, exist_ok=True)
        with open(os.path.join(report_dir, "20260401-120000.yaml"), "w") as f:
            f.write("per_rfe: [ {id: RHAIRFE-1")
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)
        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }

        r = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--results-dir",
                results,
                "--artifacts-dir",
                art_dir,
                "project = RHAIRFE",
            ],
            capture_output=True,
            text=True,
            env=env,
        )

        assert r.returncode == 1
        assert "unreadable" in r.stderr
        assert "--include-all" in r.stderr
        assert "Traceback" not in r.stderr
        assert not os.path.exists(os.path.join(art_dir, "auto-fix-runs"))

    def test_include_all_overrides_a_corrupt_report(self, tmp_path, mock_jira):
        """The recovery override applies to corruption too, and stays fail-safe."""
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Issue one."}
        results = _make_results_dir(tmp_path, ["20260401-120000"], latest="20260401-120000")
        report_dir = os.path.join(results, "20260401-120000", "auto-fix-runs")
        os.makedirs(report_dir, exist_ok=True)
        with open(os.path.join(report_dir, "20260401-120000.yaml"), "w") as f:
            f.write("per_rfe: [ {id: RHAIRFE-1")
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)
        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }

        r = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--results-dir",
                results,
                "--artifacts-dir",
                art_dir,
                "--include-all",
                "project = RHAIRFE",
            ],
            capture_output=True,
            text=True,
            env=env,
        )

        assert r.returncode == 0, r.stderr
        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir) if f.startswith("issue-snapshot-")]
        with open(os.path.join(snapshot_dir, snapshots[0])) as f:
            snap = yaml.safe_load(f)
        assert all(e["processed"] is False for e in snap["issues"].values())

    def test_include_all_overrides_the_refusal(self, tmp_path, mock_jira):
        """The escape hatch must exist: bootstrap is the tool you reach for in a bad state."""
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Issue one."}
        results = _make_results_dir(tmp_path, ["20260401-120000"], latest="20260401-120000")
        snap_dir = os.path.join(results, "20260401-120000", "auto-fix-runs")
        os.makedirs(snap_dir, exist_ok=True)
        with open(os.path.join(snap_dir, "issue-snapshot-20260401-120000.yaml"), "w") as f:
            yaml.dump({"issues": {}}, f)
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)
        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }

        r = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--results-dir",
                results,
                "--artifacts-dir",
                art_dir,
                "--include-all",
                "project = RHAIRFE",
            ],
            capture_output=True,
            text=True,
            env=env,
        )

        assert r.returncode == 0, r.stderr
        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir) if f.startswith("issue-snapshot-")]
        with open(os.path.join(snapshot_dir, snapshots[0])) as f:
            snap = yaml.safe_load(f)
        assert all(e["processed"] is False for e in snap["issues"].values())


class TestLoadRunReportRejectsCorruptFiles:
    """Present-but-unreadable is not absence.

    Absence has a documented include-all fallback; a corrupt report says nothing about what the
    run processed. Normalizing these to (None, None) would either mislabel the failure as a
    partial clone or silently include everything — the conflation this reader was just taught
    to avoid. None of the 278 published reports have these shapes; this is defence.
    """

    def _write_report(self, tmp_path, body, run_name="20260401-120000"):
        results = str(tmp_path / "results")
        report_dir = os.path.join(results, run_name, "auto-fix-runs")
        os.makedirs(report_dir)
        with open(os.path.join(report_dir, f"{run_name}.yaml"), "w") as f:
            f.write(body)
        return results, run_name

    def test_malformed_yaml_raises(self, tmp_path):
        results, run = self._write_report(tmp_path, "per_rfe: [ {id: RHAIRFE-1")

        with pytest.raises(ValueError, match="unreadable"):
            _load_run_report(results, run)

    def test_non_mapping_raises(self, tmp_path):
        results, run = self._write_report(tmp_path, "- just\n- a\n- list\n")

        with pytest.raises(ValueError, match="not a mapping"):
            _load_run_report(results, run)

    def test_empty_file_raises(self, tmp_path):
        """yaml.safe_load('') is None — a present-but-empty file is corrupt, not absent."""
        results, run = self._write_report(tmp_path, "")

        with pytest.raises(ValueError, match="not a mapping"):
            _load_run_report(results, run)

    def test_entries_without_id_raise(self, tmp_path):
        results, run = self._write_report(tmp_path, "per_rfe:\n  - recommendation: submit\n")

        with pytest.raises(ValueError, match="malformed per_rfe entries"):
            _load_run_report(results, run)

    def test_missing_item_key_raises(self, tmp_path):
        """A mapping without the configured key is the drift shape, not an empty run.

        An empty LIST keeps the documented fallback (zero-count runs are legitimate since
        bc2f553); a missing KEY means writer and reader disagree about the schema.
        """
        results, run = self._write_report(tmp_path, "results:\n  passed: 3\n")

        with pytest.raises(ValueError, match="has no per_rfe item list"):
            _load_run_report(results, run)

    def test_empty_item_list_returns_empty_set(self, tmp_path):
        """Key present, list empty — positive evidence of a zero-count run,
        distinct from (None, None) absence so the caller can walk back."""
        results, run = self._write_report(tmp_path, "per_rfe: []\n")

        ids, report = _load_run_report(results, run)

        assert ids == set()
        assert isinstance(report, dict)

    def test_null_item_list_raises(self, tmp_path):
        """per_rfe: null is schema drift, not a zero-count run — and it must
        raise the documented ValueError, not escape as a bare TypeError."""
        results, run = self._write_report(tmp_path, "per_rfe: null\n")

        with pytest.raises(ValueError, match="non-list"):
            _load_run_report(results, run)

    def test_mapping_item_list_raises(self, tmp_path):
        """per_rfe: {} iterates like an empty list — it must not masquerade
        as a legitimate zero-count run and trigger the walk-back."""
        results, run = self._write_report(tmp_path, "per_rfe: {}\n")

        with pytest.raises(ValueError, match="non-list"):
            _load_run_report(results, run)

    def test_unhashable_id_raises_value_error(self, tmp_path):
        """id: {} must follow the malformed-report path, not escape as a
        TypeError traceback past main's ValueError handling."""
        results, run = self._write_report(tmp_path, "per_rfe:\n- id: {}\n")

        with pytest.raises(ValueError, match="malformed"):
            _load_run_report(results, run)

    @pytest.mark.parametrize(
        "entry_yaml",
        [
            "- failed_reason: 'split_submit_failed: exit 5'",
            "- blocked_reason: too many leaf children",
            "- error: review file not found",
        ],
    )
    def test_outcome_entry_without_id_raises(self, tmp_path, entry_yaml):
        """An id-less entry is corrupt whatever its outcome key — skipping it
        silently would build a snapshot from an incomplete processed-id set
        instead of refusing (CodeRabbit on #170, CWE-20)."""
        results, run = self._write_report(tmp_path, f"per_rfe:\n{entry_yaml}\n")

        with pytest.raises(ValueError, match="malformed"):
            _load_run_report(results, run)

    def test_non_dict_entry_raises_even_containing_error(self, tmp_path):
        """A malformed entry must refuse loudly — the error-entry skip is for
        error DICTS, not for anything whose text happens to contain 'error'."""
        results, run = self._write_report(tmp_path, 'per_rfe:\n- "RHAIRFE-2 had an error"\n')

        with pytest.raises(ValueError, match="malformed"):
            _load_run_report(results, run)

    def test_failed_and_blocked_entries_do_not_count_as_processed(self, tmp_path):
        """failed_reason (crashed/never-attempted split) and blocked_reason
        (refusal) entries were not disposed of: the live snapshot leaves
        them processed:false, and bootstrap recovery must agree or a
        quarantine-cleared parent would be frozen at processed:true
        (RHAIFIRST-571 review finding)."""
        results, run = self._write_report(
            tmp_path,
            "per_rfe:\n"
            "- id: RHAIRFE-1\n"
            "  recommendation: submit\n"
            "- id: RHAIRFE-2\n"
            "  recommendation: split\n"
            "  failed_reason: 'split_submit_failed: exit 5'\n"
            "- id: RHAIRFE-3\n"
            "  recommendation: split\n"
            "  blocked_reason: too many leaf children\n",
        )

        ids, _ = _load_run_report(results, run)

        assert ids == {"RHAIRFE-1"}

    def test_error_entries_do_not_count_as_processed(self, tmp_path):
        """An error entry records that the run could NOT dispose of the item —
        counting it as processed would freeze it out of every future fetch
        (the RHAIRFE-3201 shape, RHAIFIRST-582)."""
        results, run = self._write_report(
            tmp_path,
            "per_rfe:\n"
            "- id: RHAIRFE-1\n"
            "  recommendation: submit\n"
            "- id: RHAIRFE-2\n"
            "  error: review file not found\n",
        )

        ids, _ = _load_run_report(results, run)

        assert ids == {"RHAIRFE-1"}

    def test_missing_file_is_still_none(self, tmp_path):
        """Absence keeps its meaning — only corruption raises."""
        results = str(tmp_path / "results")
        os.makedirs(os.path.join(results, "20260401-120000"))

        ids, report = _load_run_report(results, "20260401-120000")

        assert ids is None and report is None


class TestLoadRunReportConfig:
    """Verify _load_run_report uses config for report path and item key."""

    def test_rfe_default(self, tmp_path):
        """Default (rfe) config reads per_rfe from <run_name>.yaml."""
        results = str(tmp_path / "results")
        run_name = "20260401-120000"
        report_dir = os.path.join(results, run_name, "auto-fix-runs")
        os.makedirs(report_dir)
        report = {"per_rfe": [{"id": "RHAIRFE-1"}, {"id": "RHAIRFE-2"}]}
        with open(os.path.join(report_dir, f"{run_name}.yaml"), "w") as f:
            yaml.dump(report, f)

        ids, _ = _load_run_report(results, run_name)
        assert ids == {"RHAIRFE-1", "RHAIRFE-2"}

    def test_initiative_config(self, tmp_path):
        """Initiative config reads per_initiative from initiative-run-<name>.yaml."""
        results = str(tmp_path / "results")
        run_name = "20260401-120000"
        report_dir = os.path.join(results, run_name, "auto-fix-runs")
        os.makedirs(report_dir)
        report = {"per_initiative": [{"id": "RHOAIENG-1"}, {"id": "RHOAIENG-2"}]}
        with open(os.path.join(report_dir, f"initiative-run-{run_name}.yaml"), "w") as f:
            yaml.dump(report, f)

        config = BOOTSTRAP_CONFIG["initiative"]
        ids, _ = _load_run_report(results, run_name, config=config)
        assert ids == {"RHOAIENG-1", "RHOAIENG-2"}

    def test_initiative_config_no_rfe_report(self, tmp_path):
        """Initiative config doesn't find RFE-format report."""
        results = str(tmp_path / "results")
        run_name = "20260401-120000"
        report_dir = os.path.join(results, run_name, "auto-fix-runs")
        os.makedirs(report_dir)
        report = {"per_rfe": [{"id": "RHAIRFE-1"}]}
        with open(os.path.join(report_dir, f"{run_name}.yaml"), "w") as f:
            yaml.dump(report, f)

        config = BOOTSTRAP_CONFIG["initiative"]
        ids, _ = _load_run_report(results, run_name, config=config)
        assert ids is None


class TestBootstrapInitiativeType:
    """Verify --type initiative produces initiative-prefixed snapshots."""

    def test_initiative_snapshot_filename(self, tmp_path, mock_jira):
        url, server = mock_jira
        server.issues = {"RHOAIENG-1": "Initiative description."}
        results = str(tmp_path / "results")
        run_name = "20260401-120000"
        os.makedirs(os.path.join(results, run_name))
        os.symlink(run_name, os.path.join(results, "latest"))

        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [
                sys.executable,
                SCRIPT,
                "--results-dir",
                results,
                "--artifacts-dir",
                art_dir,
                "--type",
                "initiative",
                "project = RHOAIENG",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr
        assert "initiative-ignore" in r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snap_files = os.listdir(snapshot_dir)
        init_snaps = [f for f in snap_files if f.startswith("initiative-snapshot-")]
        rfe_snaps = [f for f in snap_files if f.startswith("issue-snapshot-")]
        assert len(init_snaps) == 1
        assert len(rfe_snaps) == 0


# ── Registry-derived BOOTSTRAP_CONFIG and the grandfathered probe (work-item-types PR-2c) ──

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


class TestBootstrapConfigDerivesFromTheRegistry:
    def test_values_are_the_pre_registry_literals(self):
        # Behaviour-neutral migration: same keys, same order, same values as the literal table
        # this replaced (rfe's empty report prefix is grandfathered: its reports are <run>.yaml).
        assert BOOTSTRAP_CONFIG == {
            "rfe": {"report_prefix": "", "item_key": "per_rfe"},
            "initiative": {"report_prefix": "initiative-run-", "item_key": "per_initiative"},
        }
        assert list(BOOTSTRAP_CONFIG) == ["rfe", "initiative"]
        for config in BOOTSTRAP_CONFIG.values():
            assert list(config) == ["report_prefix", "item_key"]

    @pytest.mark.parametrize("type_name", REG.names())
    def test_projection_from_the_descriptor(self, type_name):
        desc = REG.get(type_name)
        bc = BOOTSTRAP_CONFIG[type_name]
        assert bc["report_prefix"] == desc.get("snapshot.report_prefix")
        assert bc["item_key"] == desc.get("reporting.item_key")

    def test_type_choices_are_the_registry_choices(self):
        result = subprocess.run(
            [sys.executable, SCRIPT, "--help"], capture_output=True, text=True, env=_clean_env()
        )
        assert result.returncode == 0
        assert "--type {rfe,initiative}" in result.stdout

    def test_a_drop_in_type_is_offered_and_configured(self, tmp_path):
        root = _dropin_root(tmp_path)
        env = _clean_env(
            RFE_CREATOR_EXTRA_TYPES=root,
            RFE_CREATOR_EXTRA_TYPES_ALLOWLIST=root,
            JIRA_SERVER="http://127.0.0.1:9",
            JIRA_USER="u",
            JIRA_TOKEN="t",
        )
        result = subprocess.run(
            [sys.executable, SCRIPT, "--help"], capture_output=True, text=True, env=env
        )
        assert "--type {rfe,docs,initiative}" in result.stdout
        # Both per-type tables are indexed before the first Jira call; an empty results dir
        # stops the run at the offline gate right after, so this proves the drop-in type is
        # configured end to end without a server.
        results = tmp_path / "results"
        results.mkdir()
        result = subprocess.run(
            [sys.executable, SCRIPT, "--type", "docs", "--results-dir", str(results), "x"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 1
        assert result.stderr == "Error: no valid run directories found\n"


class TestRunDirSnapshotProbeIsGrandfatheredToTheRfePrefix:
    """_run_dir_has_snapshots probes for the rfe snapshot prefix whatever --type is running —
    the pre-registry literal 'issue-snapshot-', now read from the rfe descriptor. Making the
    probe per-type is a deliberate follow-up; this pins today's behaviour so that change is a
    visible test change and not a silent one."""

    def _plant(self, tmp_path, run, filename):
        path = tmp_path / run / "auto-fix-runs" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("issues: {}\n")

    def test_probe_prefix_is_the_rfe_descriptor_snapshot_prefix(self):
        assert _RFE_SNAPSHOT_PREFIX == REG.get("rfe").get("snapshot.prefix") == "issue-snapshot-"

    @pytest.mark.parametrize("type_name", REG.names())
    def test_only_rfe_prefixed_snapshots_are_seen(self, tmp_path, type_name):
        prefix = REG.get(type_name).get("snapshot.prefix")
        self._plant(tmp_path, "run", f"{prefix}20260401-120000.yaml")
        assert _run_dir_has_snapshots(str(tmp_path), "run") is (type_name == "rfe")

    def test_non_yaml_and_missing_dir_are_not_snapshots(self, tmp_path):
        self._plant(tmp_path, "run", "issue-snapshot-20260401-120000.yaml.bak")
        assert _run_dir_has_snapshots(str(tmp_path), "run") is False
        assert _run_dir_has_snapshots(str(tmp_path), "missing") is False
