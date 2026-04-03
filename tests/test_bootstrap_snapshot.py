#!/usr/bin/env python3
"""Tests for scripts/bootstrap_snapshot.py."""
import hashlib
import json
import os
import subprocess
import sys
import threading
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

SCRIPT = os.path.join(os.path.dirname(__file__), "..",
                      "scripts", "bootstrap_snapshot.py")

from snapshot_fetch import normalize_for_hash
from bootstrap_snapshot import (
    find_latest_run_timestamp,
    get_description_at_time,
    _parse_adf,
    _description_at_time,
)


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
        "type": "doc", "version": 1,
        "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": text}
        ]}],
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
        changelog = [{
            "created": datetime(2026, 4, 2, 10, 0, tzinfo=timezone.utc),
            "items": [{
                "field": "description",
                "from": json.dumps(_text_to_adf("before")),
                "to": json.dumps(_text_to_adf("after")),
                "fromString": "before",
                "toString": "after",
            }],
        }]
        result = _description_at_time(changelog, target)
        # Should use ADF from "from", not fromString
        assert isinstance(result, dict)
        assert result == _text_to_adf("before")

    def test_falls_back_to_fromstring(self):
        """Falls back to fromString/toString when from/to are None (Jira Server/DC)."""
        from datetime import datetime, timezone
        target = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
        changelog = [{
            "created": datetime(2026, 4, 2, 10, 0, tzinfo=timezone.utc),
            "items": [{
                "field": "description",
                "from": None,
                "to": None,
                "fromString": "h2. Before\n\nOriginal text.",
                "toString": "h2. After\n\nEdited text.",
            }],
        }]
        result = _description_at_time(changelog, target)
        assert result == "h2. Before\n\nOriginal text."

    def test_to_value_for_pre_target_change(self):
        """Change before target → uses 'to' value."""
        from datetime import datetime, timezone
        target = datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc)
        changelog = [{
            "created": datetime(2026, 4, 2, 10, 0, tzinfo=timezone.utc),
            "items": [{
                "field": "description",
                "from": None,
                "to": None,
                "fromString": "old wiki",
                "toString": "new wiki",
            }],
        }]
        result = _description_at_time(changelog, target)
        assert result == "new wiki"

    def test_no_description_changes(self):
        """No description items → returns None."""
        from datetime import datetime, timezone
        target = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
        changelog = [{
            "created": datetime(2026, 4, 2, 10, 0, tzinfo=timezone.utc),
            "items": [{"field": "status", "fromString": "New", "toString": "Done"}],
        }]
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
                issues.append({
                    "key": key,
                    "fields": {"description": adf, "labels": []},
                })
        self._json(200, {"issues": issues, "isLast": True})

    def _handle_changelog(self, path):
        # Extract issue key from path like /issue/RHAIRFE-1234/changelog
        parts = path.split("/issue/")[1].split("/changelog")[0]
        key = parts.split("?")[0]

        histories = self.server.changelogs.get(key, [])
        self._json(200, {
            "values": histories,
            "total": len(histories),
        })

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


def _make_results_dir(tmp_path, run_names, latest=None):
    """Create a results directory with run dirs."""
    results = str(tmp_path / "results")
    os.makedirs(results)
    for name in run_names:
        os.makedirs(os.path.join(results, name))
    if latest:
        os.symlink(latest, os.path.join(results, "latest"))
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
            tmp_path, ["20260401-120000"], latest="20260401-120000")
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [sys.executable, SCRIPT, "--dry-run",
             "--results-dir", results,
             "--artifacts-dir", art_dir,
             "project = RHAIRFE"],
            capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0, r.stderr
        assert "Dry run" in r.stdout
        assert "2 issue hashes" in r.stdout

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
            tmp_path, ["20260401-120000"], latest="20260401-120000")
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [sys.executable, SCRIPT,
             "--results-dir", results,
             "--artifacts-dir", art_dir,
             "project = RHAIRFE"],
            capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0, r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir)
                     if f.startswith("issue-snapshot-")]
        assert len(snapshots) == 1

        with open(os.path.join(snapshot_dir, snapshots[0])) as f:
            snap = yaml.safe_load(f)

        assert len(snap["issues"]) == 2
        expected_1234 = _md_hash("Current description.")
        assert snap["issues"]["RHAIRFE-1234"] == expected_1234

    def test_run_timestamp_used(self, tmp_path, mock_jira):
        """Snapshot query_timestamp comes from the run directory name."""
        url, server = mock_jira
        server.issues = {"RHAIRFE-1": "Content."}
        results = _make_results_dir(
            tmp_path, ["20260401-120000"], latest="20260401-120000")
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [sys.executable, SCRIPT,
             "--results-dir", results,
             "--artifacts-dir", art_dir,
             "project = RHAIRFE"],
            capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0, r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir)
                     if f.startswith("issue-snapshot-")]
        with open(os.path.join(snapshot_dir, snapshots[0])) as f:
            snap = yaml.safe_load(f)

        assert snap["query_timestamp"] == "2026-04-01T12:00:00Z"
        assert snap["bootstrapped_from"] == "20260401-120000"

    def test_historical_description_via_changelog(self, tmp_path, mock_jira):
        """Issue updated since run gets historical hash from changelog."""
        url, server = mock_jira
        # Current description (after someone edited it)
        server.issues = {"RHAIRFE-1": "Edited after run."}
        # Changelog shows description was changed AFTER the run
        server.changelogs["RHAIRFE-1"] = [{
            "created": "2026-04-02T10:00:00.000+0000",
            "items": [{
                "field": "description",
                "from": json.dumps(_text_to_adf("Original at run time.")),
                "to": json.dumps(_text_to_adf("Edited after run.")),
            }],
        }]
        results = _make_results_dir(
            tmp_path, ["20260401-120000"], latest="20260401-120000")
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [sys.executable, SCRIPT,
             "--results-dir", results,
             "--artifacts-dir", art_dir,
             "project = RHAIRFE"],
            capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0, r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir)
                     if f.startswith("issue-snapshot-")]
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
        server.changelogs["RHAIRFE-1"] = [{
            "created": "2026-03-30T10:00:00.000+0000",
            "items": [{
                "field": "description",
                "from": json.dumps(_text_to_adf("Old version.")),
                "to": json.dumps(_text_to_adf("Updated before run.")),
            }],
        }]
        results = _make_results_dir(
            tmp_path, ["20260401-120000"], latest="20260401-120000")
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [sys.executable, SCRIPT,
             "--results-dir", results,
             "--artifacts-dir", art_dir,
             "project = RHAIRFE"],
            capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0, r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir)
                     if f.startswith("issue-snapshot-")]
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
            tmp_path, ["20260401-120000"], latest="20260401-120000")
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        subprocess.run(
            [sys.executable, SCRIPT,
             "--results-dir", results,
             "--artifacts-dir", art_dir,
             "project = RHAIRFE"],
            capture_output=True, text=True, env=env,
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
        server.changelogs["RHAIRFE-1"] = [{
            "created": "2026-04-02T10:00:00.000+0000",
            "items": [{
                "field": "description",
                "from": None,
                "to": None,
                "fromString": "h2. Original at run time.",
                "toString": "h2. Edited after run.",
            }],
        }]
        results = _make_results_dir(
            tmp_path, ["20260401-120000"], latest="20260401-120000")
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [sys.executable, SCRIPT,
             "--results-dir", results,
             "--artifacts-dir", art_dir,
             "project = RHAIRFE"],
            capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0, r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir)
                     if f.startswith("issue-snapshot-")]
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
        server.changelogs["RHAIRFE-1"] = [{
            "created": "2026-03-30T10:00:00.000+0000",
            "items": [{
                "field": "description",
                "from": None,
                "to": None,
                "fromString": "h2. Old version.",
                "toString": "h2. Same description.",
            }],
        }]
        results = _make_results_dir(
            tmp_path, ["20260401-120000"], latest="20260401-120000")
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [sys.executable, SCRIPT,
             "--results-dir", results,
             "--artifacts-dir", art_dir,
             "project = RHAIRFE"],
            capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0, r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir)
                     if f.startswith("issue-snapshot-")]
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
        server.changelogs["RHAIRFE-2"] = [{
            "created": "2026-04-02T10:00:00.000+0000",
            "items": [{
                "field": "status",
                "fromString": "Closed",
                "toString": "New",
            }],
        }]
        results = _make_results_dir(
            tmp_path, ["20260401-120000"], latest="20260401-120000")
        art_dir = str(tmp_path / "artifacts")
        os.makedirs(art_dir)

        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "test@example.com",
            "JIRA_TOKEN": "test-token",
        }
        r = subprocess.run(
            [sys.executable, SCRIPT,
             "--results-dir", results,
             "--artifacts-dir", art_dir,
             "project = RHAIRFE"],
            capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0, r.stderr

        snapshot_dir = os.path.join(art_dir, "auto-fix-runs")
        snapshots = [f for f in os.listdir(snapshot_dir)
                     if f.startswith("issue-snapshot-")]
        with open(os.path.join(snapshot_dir, snapshots[0])) as f:
            snap = yaml.safe_load(f)

        assert "RHAIRFE-1" in snap["issues"]
        assert "RHAIRFE-2" not in snap["issues"]
        assert len(snap["issues"]) == 1
