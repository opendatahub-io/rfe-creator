#!/usr/bin/env python3
"""Tests for scripts/dedup_search.py — cache management, keyword matching,
search logic, and graceful failure modes."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from unittest.mock import patch, MagicMock

from dedup_search import (
    _cache_age_hours,
    _cache_is_fresh,
    _escape_jql_keyword,
    _extract_fallback_keywords,
    _load_cache,
    _save_cache,
    _search_cache,
    _search_jql,
    search,
    CACHE_TTL_HOURS,
)

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                      "dedup_search.py")


def run_dedup(*args, env_override=None):
    """Run dedup_search.py and return (stdout, stderr, returncode)."""
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    result = subprocess.run(
        ["python3", SCRIPT, *args],
        capture_output=True, text=True, env=env,
    )
    return result.stdout, result.stderr, result.returncode


@pytest.fixture
def tmp_dir(tmp_path, monkeypatch):
    """Run tests from a temp directory to isolate cache files."""
    orig = os.getcwd()
    os.chdir(tmp_path)
    # Point CACHE_PATH to temp dir
    import dedup_search
    monkeypatch.setattr(dedup_search, "CACHE_PATH",
                        str(tmp_path / "tmp" / "dedup-cache.yaml"))
    yield tmp_path
    os.chdir(orig)


@pytest.fixture
def sample_cache(tmp_dir):
    """Write a fresh sample cache and return its path."""
    import dedup_search
    cache = {
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "server": os.environ.get("JIRA_SERVER", "https://test.atlassian.net"),
        "issue_count": 5,
        "issues": {
            "TEST-100": "Add widget export to PDF format",
            "TEST-101": "Improve search performance for large datasets",
            "TEST-102": "Widget import and export guided wizard",
            "TEST-103": "Automated batch processing for reports",
            "TEST-104": "Dashboard for viewing export results",
        },
    }
    _save_cache(cache)
    return cache


# ─── Cache Age ────────────────────────────────────────────────────────────────

class TestCacheAge:
    def test_fresh_cache(self):
        cache = {"refreshed_at": datetime.now(timezone.utc).isoformat()}
        age = _cache_age_hours(cache)
        assert age is not None
        assert age < 0.1  # Just created

    def test_stale_cache(self):
        old = datetime.now(timezone.utc) - timedelta(hours=5)
        cache = {"refreshed_at": old.isoformat()}
        age = _cache_age_hours(cache)
        assert age >= 4.9

    def test_missing_timestamp(self):
        assert _cache_age_hours({}) is None
        assert _cache_age_hours(None) is None

    def test_corrupt_timestamp(self):
        assert _cache_age_hours({"refreshed_at": "not-a-date"}) is None


class TestCacheFreshness:
    def test_fresh(self):
        cache = {
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "server": "https://test.atlassian.net",
            "issues": {},
        }
        assert _cache_is_fresh(cache, "https://test.atlassian.net") is True

    def test_fresh_no_server(self):
        cache = {
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "server": "https://test.atlassian.net",
            "issues": {},
        }
        # No server passed — skip server check
        assert _cache_is_fresh(cache) is True

    def test_stale(self):
        old = datetime.now(timezone.utc) - timedelta(hours=5)
        cache = {
            "refreshed_at": old.isoformat(),
            "server": "https://test.atlassian.net",
            "issues": {},
        }
        assert _cache_is_fresh(cache, "https://test.atlassian.net") is False

    def test_server_mismatch(self):
        cache = {
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "server": "https://test.atlassian.net",
            "issues": {},
        }
        assert _cache_is_fresh(cache, "https://other.atlassian.net") is False

    def test_none_cache(self):
        assert _cache_is_fresh(None) is False


# ─── Cache Load/Save ──────────────────────────────────────────────────────────

class TestCacheIO:
    def test_roundtrip(self, tmp_dir):
        data = {
            "refreshed_at": "2026-04-09T14:00:00+00:00",
            "server": "https://test.atlassian.net",
            "issue_count": 2,
            "issues": {
                "TEST-100": "Test issue one",
                "TEST-101": "Test issue two",
            },
        }
        _save_cache(data)
        loaded = _load_cache()
        assert loaded["issue_count"] == 2
        assert loaded["issues"]["TEST-100"] == "Test issue one"

    def test_load_missing(self, tmp_dir):
        assert _load_cache() is None

    def test_load_corrupt(self, tmp_dir):
        import dedup_search
        os.makedirs(os.path.dirname(dedup_search.CACHE_PATH), exist_ok=True)
        with open(dedup_search.CACHE_PATH, "w") as f:
            f.write("{{invalid yaml::")
        assert _load_cache() is None


# ─── Keyword Matching ─────────────────────────────────────────────────────────

class TestSearchCache:
    def test_single_keyword(self, sample_cache):
        results = _search_cache(sample_cache, ["PDF"], 10)
        assert len(results) == 1
        assert results[0][1] == "TEST-100"

    def test_case_insensitive(self, sample_cache):
        results = _search_cache(sample_cache, ["pdf"], 10)
        assert len(results) == 1

    def test_multiple_keywords(self, sample_cache):
        results = _search_cache(sample_cache, ["wizard", "export"], 10)
        # "wizard" matches only 102; "export" matches 100, 102, 104
        # TEST-102 should rank highest (2 hits)
        assert results[0][1] == "TEST-102"
        assert results[0][0] == 2  # hit count

    def test_no_matches(self, sample_cache):
        results = _search_cache(sample_cache, ["nonexistent"], 10)
        assert results == []

    def test_max_results(self, sample_cache):
        results = _search_cache(sample_cache, ["export"], 2)
        assert len(results) == 2

    def test_empty_cache(self):
        assert _search_cache(None, ["test"], 10) == []
        assert _search_cache({"issues": {}}, ["test"], 10) == []

    def test_empty_keywords(self, sample_cache):
        results = _search_cache(sample_cache, [], 10)
        assert results == []


# ─── Fallback Keyword Extraction ──────────────────────────────────────────────

class TestFallbackKeywords:
    def test_extracts_meaningful_words(self):
        kws = _extract_fallback_keywords(
            "I want RHOAI AI Hub to have model fine-tuning")
        assert "rhoai" in kws
        assert "hub" in kws
        assert "fine-tuning" in kws
        # Stopwords removed
        assert "want" not in kws
        assert "to" not in kws
        assert "have" not in kws

    def test_max_five(self):
        kws = _extract_fallback_keywords(
            "alpha bravo charlie delta echo foxtrot golf hotel")
        assert len(kws) <= 5

    def test_deduplication(self):
        kws = _extract_fallback_keywords("model model model serving serving")
        assert kws.count("model") == 1
        assert kws.count("serving") == 1

    def test_empty_input(self):
        assert _extract_fallback_keywords("") == []
        assert _extract_fallback_keywords("the a an is") == []


# ─── CLI Integration ──────────────────────────────────────────────────────────

class TestCLI:
    def test_search_no_creds(self, tmp_dir):
        """Search without credentials returns graceful error."""
        env = {
            "JIRA_SERVER": "",
            "JIRA_USER": "",
            "JIRA_TOKEN": "",
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
        }
        out, err, rc = run_dedup(
            "search", "model fine-tuning", "--keywords", "model,fine-tuning",
            env_override=env)
        assert rc == 0
        result = json.loads(out)
        assert result["error"] is not None
        assert isinstance(result["matches"], list)

    def test_cache_info_runs(self):
        """cache-info runs without error."""
        out, _, rc = run_dedup("cache-info")
        assert rc == 0
        # Output is either "No cache found." or cache stats — both valid
        assert "cache" in out.lower() or "Cache" in out

    def test_load_missing_cache(self, tmp_dir):
        """In-process: _load_cache returns None when no cache file exists."""
        assert _load_cache() is None


# ─── JQL Escaping ────────────────────────────────────────────────────────────

class TestEscapeJqlKeyword:
    def test_plain_keyword(self):
        assert _escape_jql_keyword("model") == "model"

    def test_double_quotes(self):
        assert _escape_jql_keyword('say "hello"') == 'say \\"hello\\"'

    def test_backslash(self):
        assert _escape_jql_keyword("path\\to") == "path\\\\to"

    def test_braces_and_brackets(self):
        assert _escape_jql_keyword("a{b}[c]") == "a\\{b\\}\\[c\\]"

    def test_special_chars(self):
        result = _escape_jql_keyword("a+b-c*d?e:")
        assert result == "a\\+b\\-c\\*d\\?e\\:"

    def test_combined(self):
        # Backslash + quotes + brackets
        result = _escape_jql_keyword('x\\y"z[w]')
        assert result == 'x\\\\y\\"z\\[w\\]'


# ─── _search_jql (mocked HTTP) ──────────────────────────────────────────────

class TestSearchJql:
    def _mock_api(self, issues):
        """Return a mock that simulates api_call_with_retry."""
        return MagicMock(return_value={
            "issues": [{"key": k, "fields": {"summary": s}}
                       for k, s in issues],
        })

    @patch("dedup_search.api_call_with_retry")
    def test_returns_results(self, mock_api):
        mock_api.return_value = {
            "issues": [
                {"key": "RHAIRFE-100", "fields": {"summary": "Model serving"}},
                {"key": "RHAIRFE-101", "fields": {"summary": "Model training"}},
            ],
        }
        results = _search_jql("https://s", "u", "t", ["model"], 10)
        assert len(results) == 2
        assert results[0] == ("RHAIRFE-100", "Model serving")
        mock_api.assert_called_once()

    @patch("dedup_search.api_call_with_retry")
    def test_escapes_keywords_in_jql(self, mock_api):
        mock_api.return_value = {"issues": []}
        _search_jql("https://s", "u", "t", ['say "hi"', "a\\b"], 5)
        call_path = mock_api.call_args[0][1]
        # Keywords should be escaped in the encoded JQL
        assert "say" in call_path
        mock_api.assert_called_once()

    def test_no_creds_returns_empty(self):
        assert _search_jql("", "", "", ["model"], 10) == []

    @patch("dedup_search.api_call_with_retry", side_effect=Exception("timeout"))
    def test_api_error_returns_empty(self, mock_api):
        results = _search_jql("https://s", "u", "t", ["model"], 10)
        assert results == []


# ─── search() (mocked internals) ────────────────────────────────────────────

class TestSearch:
    """Tests for the top-level search() function with mocked dependencies."""

    @patch("dedup_search.require_env",
           return_value=("https://s", "u", "t"))
    @patch("dedup_search._ensure_fresh_cache")
    @patch("dedup_search._search_jql", return_value=[])
    def test_cache_only_when_enough_results(self, mock_jql, mock_cache, mock_env):
        """When cache has >= 3 results, JQL is not called."""
        mock_cache.return_value = {
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "server": "https://s",
            "issues": {
                "T-1": "model serving infra",
                "T-2": "model training pipeline",
                "T-3": "model registry integration",
                "T-4": "model monitoring dashboard",
            },
        }
        result = search("model stuff", ["model"], max_results=10)
        assert result["source"] == "cache"
        assert len(result["matches"]) == 4
        mock_jql.assert_not_called()

    @patch("dedup_search.require_env",
           return_value=("https://s", "u", "t"))
    @patch("dedup_search._ensure_fresh_cache")
    @patch("dedup_search._search_jql")
    def test_jql_fallback_when_sparse_cache(self, mock_jql, mock_cache, mock_env):
        """When cache has < 3 results, JQL fallback is triggered."""
        mock_cache.return_value = {
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "server": "https://s",
            "issues": {
                "T-1": "model serving infra",
            },
        }
        mock_jql.return_value = [
            ("T-50", "model fine-tuning"),
            ("T-1", "model serving infra"),  # duplicate of cache
        ]
        result = search("model stuff", ["model"], max_results=10)
        assert result["source"] == "cache+jira"
        mock_jql.assert_called_once()
        # T-1 should appear once (deduped)
        keys = [m["key"] for m in result["matches"]]
        assert keys.count("T-1") == 1
        assert "T-50" in keys

    @patch("dedup_search.require_env",
           return_value=("https://s", "u", "t"))
    @patch("dedup_search._ensure_fresh_cache")
    @patch("dedup_search._search_jql")
    def test_jql_only_when_no_cache_results(self, mock_jql, mock_cache, mock_env):
        """When cache has 0 results, source is 'jira' not 'cache+jira'."""
        mock_cache.return_value = {
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "server": "https://s",
            "issues": {},
        }
        mock_jql.return_value = [("T-50", "model fine-tuning")]
        result = search("model", ["model"], max_results=10)
        assert result["source"] == "jira"

    @patch("dedup_search.require_env", return_value=("", "", ""))
    @patch("dedup_search._ensure_fresh_cache", return_value=None)
    def test_no_creds_no_cache(self, mock_cache, mock_env):
        result = search("model", ["model"])
        assert result["error"] == "no_credentials"
        assert result["matches"] == []

    @patch("dedup_search.require_env", return_value=("", "", ""))
    @patch("dedup_search._ensure_fresh_cache")
    def test_no_creds_stale_cache(self, mock_cache, mock_env):
        mock_cache.return_value = {
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "server": "https://s",
            "issues": {"T-1": "model serving"},
        }
        result = search("model", ["model"])
        assert result["error"] == "no_credentials_using_stale_cache"
        assert len(result["matches"]) == 1

    @patch("dedup_search.require_env",
           return_value=("https://s", "u", "t"))
    def test_empty_input(self, mock_env):
        result = search("", [])
        assert result["source"] == "none"
        assert result["matches"] == []
        assert result["error"] is None

    @patch("dedup_search.require_env",
           return_value=("https://s", "u", "t"))
    @patch("dedup_search._ensure_fresh_cache")
    @patch("dedup_search._search_jql", return_value=[])
    def test_max_results_respected(self, mock_jql, mock_cache, mock_env):
        """Matches list is capped at max_results."""
        issues = {f"T-{i}": f"model variant {i}" for i in range(20)}
        mock_cache.return_value = {
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "server": "https://s",
            "issues": issues,
        }
        result = search("model", ["model"], max_results=5)
        assert len(result["matches"]) == 5
