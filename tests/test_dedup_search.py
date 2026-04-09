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

from dedup_search import (
    _cache_age_hours,
    _cache_is_fresh,
    _extract_fallback_keywords,
    _load_cache,
    _save_cache,
    _search_cache,
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
    def test_fresh(self, monkeypatch):
        monkeypatch.setenv("JIRA_SERVER", "https://test.atlassian.net")
        cache = {
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "server": "https://test.atlassian.net",
            "issues": {},
        }
        assert _cache_is_fresh(cache) is True

    def test_stale(self, monkeypatch):
        monkeypatch.setenv("JIRA_SERVER", "https://test.atlassian.net")
        old = datetime.now(timezone.utc) - timedelta(hours=5)
        cache = {
            "refreshed_at": old.isoformat(),
            "server": "https://test.atlassian.net",
            "issues": {},
        }
        assert _cache_is_fresh(cache) is False

    def test_server_mismatch(self, monkeypatch):
        monkeypatch.setenv("JIRA_SERVER", "https://other.atlassian.net")
        cache = {
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "server": "https://test.atlassian.net",
            "issues": {},
        }
        assert _cache_is_fresh(cache) is False

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
