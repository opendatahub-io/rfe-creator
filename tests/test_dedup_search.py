#!/usr/bin/env python3
"""Tests for scripts/dedup_search.py — cache management, keyword matching,
search logic, and graceful failure modes."""
import json
import os
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from unittest.mock import patch, MagicMock

from dedup_search import (
    _build_text_query_jql,
    _cache_age_hours,
    _cache_is_fresh,
    _extract_fallback_keywords,
    _jql_string_escape,
    _load_cache,
    _lucene_escape,
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
                        str(tmp_path / "tmp" / "dedup-cache.json"))
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
            f.write("{{invalid json::")
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


# ─── JQL / Lucene Escaping ───────────────────────────────────────────────────

class TestLuceneEscape:
    def test_plain_term(self):
        assert _lucene_escape("model") == "model"

    def test_special_chars(self):
        # All Lucene specials get a backslash prefix
        assert _lucene_escape("a+b") == "a\\+b"
        assert _lucene_escape("a(b)") == "a\\(b\\)"
        assert _lucene_escape("a:b") == "a\\:b"

    def test_backslash_first(self):
        # Literal backslash must be escaped BEFORE we add more escapes
        # (otherwise we'd double-escape our own escapes)
        assert _lucene_escape("a\\b") == "a\\\\b"

    def test_quote(self):
        assert _lucene_escape('a"b') == 'a\\"b'


class TestJqlStringEscape:
    def test_plain(self):
        assert _jql_string_escape("hello") == "hello"

    def test_quote_and_backslash(self):
        # JQL string layer: only \ and " need escaping
        assert _jql_string_escape('a"b') == 'a\\"b'
        assert _jql_string_escape("a\\b") == "a\\\\b"

    def test_lucene_specials_untouched(self):
        # Lucene specials are NOT JQL specials; pass through
        assert _jql_string_escape("a+b") == "a+b"
        assert _jql_string_escape("a(b)") == "a(b)"


class TestBuildTextQueryJql:
    """Regression tests — the previous implementation double-wrapped keywords
    in quotes, producing malformed JQL like `text ~ ""a" OR "b""`."""

    def test_single_word_keywords(self):
        # Bug reproducer: this was producing `text ~ ""mlflow" OR "registry"…"`
        clause = _build_text_query_jql(
            ["mlflow", "registry", "sync", "integration"])
        # No nested double quotes
        assert '""' not in clause
        # Single-word terms are bare, not phrase-quoted
        assert clause == 'text ~ "mlflow OR registry OR sync OR integration"'

    def test_multi_word_phrase(self):
        # Multi-word keywords become Lucene phrases; JQL escapes the
        # surrounding Lucene quotes as \"
        clause = _build_text_query_jql(["model registry"])
        assert clause == 'text ~ "\\"model registry\\""'

    def test_mixed_single_and_phrase(self):
        clause = _build_text_query_jql(["mlflow", "model registry"])
        assert clause == 'text ~ "mlflow OR \\"model registry\\""'

    def test_empty_keyword_stripped(self):
        # Empty / whitespace-only entries are skipped (defensive — callers
        # already strip, but don't trust the arg)
        clause = _build_text_query_jql(["mlflow", "", "  "])
        assert clause == 'text ~ "mlflow"'

    def test_all_empty_returns_none(self):
        assert _build_text_query_jql([]) is None
        assert _build_text_query_jql(["", "  "]) is None

    def test_lucene_specials_escaped(self):
        # `+` is a Lucene metachar → becomes `\+` in Lucene, then `\\+`
        # after JQL escapes the backslash.
        clause = _build_text_query_jql(["a+b"])
        assert clause == 'text ~ "a\\\\+b"'

    def test_embedded_quote_in_phrase(self):
        # A `"` inside a phrase: Lucene-escape to `\"`, then JQL-escape
        # each backslash and quote.
        clause = _build_text_query_jql(['say "hi"'])
        # Lucene layer: `say \"hi\"` wrapped as `"say \"hi\""`
        # JQL layer: every `\` → `\\`, every `"` → `\"`
        # Final: text ~ "\"say \\\"hi\\\"\""
        assert clause == 'text ~ "\\"say \\\\\\"hi\\\\\\"\\""'


class TestSearchJqlEndToEnd:
    """End-to-end validation that the URL sent to Jira contains syntactically
    valid JQL (no double-quote collisions)."""

    @patch("dedup_search.api_call_with_retry")
    def test_generated_jql_is_parseable(self, mock_api):
        """Regression for the 'Expecting OR/AND but got mlflow' HTTP 400."""
        mock_api.return_value = {"issues": []}
        _search_jql("https://s", "u", "t",
                    ["mlflow", "registry", "sync", "integration"], 10)
        call_path = mock_api.call_args[0][1]
        decoded_jql = urllib.parse.unquote(
            call_path.split("jql=", 1)[1].split("&", 1)[0])
        # The decoded JQL must NOT contain the broken `""term"` pattern
        assert '""' not in decoded_jql, \
            f"Malformed JQL — nested empty quotes: {decoded_jql!r}"
        # Sanity check the overall shape
        assert decoded_jql.startswith(
            'project = RHAIRFE AND statusCategory != Done AND text ~ ')
        assert "mlflow" in decoded_jql
        assert " OR " in decoded_jql

    @patch("dedup_search.api_call_with_retry")
    def test_generated_jql_with_phrase(self, mock_api):
        mock_api.return_value = {"issues": []}
        _search_jql("https://s", "u", "t",
                    ["mlflow", "model registry"], 10)
        call_path = mock_api.call_args[0][1]
        decoded_jql = urllib.parse.unquote(
            call_path.split("jql=", 1)[1].split("&", 1)[0])
        # The phrase should survive as an escaped Lucene phrase
        assert '\\"model registry\\"' in decoded_jql
        # Whole JQL should be the expected exact form
        assert decoded_jql == (
            'project = RHAIRFE AND statusCategory != Done AND '
            'text ~ "mlflow OR \\"model registry\\""'
        )


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


# ─── --headless and --recent-only modes ──────────────────────────────────────

class TestHeadlessMode:
    @patch("dedup_search.require_env",
           return_value=("https://s", "u", "t"))
    @patch("dedup_search.refresh_cache")
    @patch("dedup_search._load_cache", return_value=None)
    @patch("dedup_search._search_jql")
    def test_headless_skips_cache_build(self, mock_jql, mock_load,
                                         mock_refresh, mock_env):
        """Headless mode must never call refresh_cache, even if cache missing."""
        mock_jql.return_value = [("T-50", "model fine-tuning")]
        result = search("model", ["model"], headless=True)
        mock_refresh.assert_not_called()
        # Should have fallen through to JQL with recent_days set
        assert mock_jql.called
        kwargs = mock_jql.call_args.kwargs
        # recent_days should be set when headless with no cache
        assert kwargs.get("recent_days") == 30
        assert result["source"] == "jira"
        assert len(result["matches"]) == 1

    @patch("dedup_search.require_env",
           return_value=("https://s", "u", "t"))
    @patch("dedup_search.refresh_cache")
    @patch("dedup_search._load_cache")
    @patch("dedup_search._search_jql", return_value=[])
    def test_headless_uses_fresh_cache(self, mock_jql, mock_load,
                                        mock_refresh, mock_env):
        """Headless mode uses a fresh cache normally — no refresh, no narrowed JQL."""
        mock_load.return_value = {
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "server": "https://s",
            "issues": {
                "T-1": "model serving",
                "T-2": "model training",
                "T-3": "model registry",
                "T-4": "model monitoring",
            },
        }
        result = search("model", ["model"], headless=True)
        mock_refresh.assert_not_called()
        mock_jql.assert_not_called()  # 4 cache hits → no fallback
        assert result["source"] == "cache"


class TestRecentOnlyMode:
    @patch("dedup_search.require_env",
           return_value=("https://s", "u", "t"))
    @patch("dedup_search._ensure_fresh_cache")
    @patch("dedup_search._search_jql")
    def test_recent_only_bypasses_cache(self, mock_jql, mock_cache, mock_env):
        """--recent-only must skip _ensure_fresh_cache entirely."""
        mock_jql.return_value = [("T-99", "recent model RFE")]
        result = search("model", ["model"], recent_only=True)
        mock_cache.assert_not_called()
        mock_jql.assert_called_once()
        kwargs = mock_jql.call_args.kwargs
        assert kwargs.get("recent_days") == 30
        assert result["source"] == "jira-recent"
        assert result["cache_age_hours"] is None
        assert len(result["matches"]) == 1

    @patch("dedup_search.require_env", return_value=("", "", ""))
    @patch("dedup_search._ensure_fresh_cache")
    def test_recent_only_no_creds(self, mock_cache, mock_env):
        """--recent-only with no credentials returns structured error, no crash."""
        result = search("model", ["model"], recent_only=True)
        mock_cache.assert_not_called()
        assert result["source"] == "jira-recent"
        assert result["error"] == "no_credentials"
        assert result["matches"] == []


class TestSearchJqlRecentDays:
    @patch("dedup_search.api_call_with_retry")
    def test_recent_days_appended_to_jql(self, mock_api):
        mock_api.return_value = {"issues": []}
        _search_jql("https://s", "u", "t", ["model"], 10, recent_days=30)
        call_path = mock_api.call_args[0][1]
        # JQL gets URL-encoded, so look for the encoded form of "created >= -30d"
        decoded = urllib.parse.unquote(call_path)
        assert "created >= -30d" in decoded

    @patch("dedup_search.api_call_with_retry")
    def test_recent_days_omitted_by_default(self, mock_api):
        mock_api.return_value = {"issues": []}
        _search_jql("https://s", "u", "t", ["model"], 10)
        call_path = mock_api.call_args[0][1]
        decoded = urllib.parse.unquote(call_path)
        assert "created" not in decoded
