#!/usr/bin/env python3
"""Search for potential duplicate RFEs in Jira.

Maintains a local YAML cache of RFE summaries for fast lookups,
with JQL text search as fallback.

Usage:
    python3 scripts/dedup_search.py search "problem text" --keywords "kw1,kw2" [--max-results 10]
    python3 scripts/dedup_search.py refresh-cache [--force]
    python3 scripts/dedup_search.py cache-info
"""

import argparse
import json
import os
import sys
import urllib.parse
from datetime import datetime, timezone

try:
    import yaml
except ImportError:
    yaml = None

# Add parent directory so we can import jira_utils
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jira_utils import require_env, api_call_with_retry

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "tmp", "dedup-cache.yaml")
CACHE_TTL_HOURS = 4
JQL_BASE = "project = RHAIRFE AND statusCategory != Done"

_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "out", "off", "over",
    "under", "again", "further", "then", "once", "and", "but", "or",
    "nor", "not", "so", "yet", "both", "either", "neither", "each",
    "every", "all", "any", "few", "more", "most", "other", "some", "such",
    "no", "only", "own", "same", "than", "too", "very", "just", "because",
    "if", "when", "where", "how", "what", "which", "who", "whom", "this",
    "that", "these", "those", "i", "me", "my", "we", "our", "you", "your",
    "he", "him", "his", "she", "her", "it", "its", "they", "them", "their",
    "want", "need", "like", "use", "using", "support", "enable", "allow",
    "make", "get", "set", "add", "new", "also", "about",
})


# ─── Cache Management ────────────────────────────────────────────────────────

def _load_cache():
    """Load cache from disk. Returns dict or None if unavailable/corrupt."""
    if yaml is None:
        return None
    try:
        with open(CACHE_PATH, "r") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict) or "issues" not in data:
            return None
        return data
    except (FileNotFoundError, yaml.YAMLError, OSError):
        return None


def _save_cache(data):
    """Write cache to disk."""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    if yaml is None:
        return
    with open(CACHE_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def _cache_age_hours(cache):
    """Return cache age in hours, or None if unknown."""
    if not cache or "refreshed_at" not in cache:
        return None
    try:
        refreshed = datetime.fromisoformat(cache["refreshed_at"])
        if refreshed.tzinfo is None:
            refreshed = refreshed.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - refreshed).total_seconds() / 3600
        return round(age, 2)
    except (ValueError, TypeError):
        return None


def _cache_is_fresh(cache, server=None):
    """Check if cache is fresh (within TTL) and matches given server."""
    age = _cache_age_hours(cache)
    if age is None or age > CACHE_TTL_HOURS:
        return False
    if server and cache.get("server") != server:
        return False
    return True


def refresh_cache(server, user, token):
    """Fetch all open RFE summaries and write to cache."""
    issues = {}
    page_size = 100
    next_page_token = None

    while True:
        jql = urllib.parse.quote(JQL_BASE, safe="")
        path = (f"/search/jql?jql={jql}"
                f"&maxResults={page_size}&fields=key,summary")
        if next_page_token:
            path += f"&nextPageToken={urllib.parse.quote(next_page_token, safe='')}"
        data = api_call_with_retry(server, path, user, token)

        for issue in data.get("issues", []):
            key = issue["key"]
            summary = issue.get("fields", {}).get("summary", "")
            issues[key] = summary

        if not data.get("issues") or data.get("isLast", True):
            break

        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

    cache = {
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "server": server,
        "issue_count": len(issues),
        "issues": issues,
    }
    _save_cache(cache)
    print(f"Cache refreshed: {len(issues)} issues", file=sys.stderr)
    return cache


def _ensure_fresh_cache(server, user, token):
    """Return a fresh cache, refreshing if needed."""
    cache = _load_cache()
    if _cache_is_fresh(cache, server):
        return cache
    # Stale or missing — need credentials to refresh
    if not all([server, user, token]):
        return cache  # Return stale cache (or None) if no creds
    try:
        return refresh_cache(server, user, token)
    except Exception as e:
        print(f"Cache refresh failed: {e}", file=sys.stderr)
        return cache  # Fall back to stale cache


# ─── Search ───────────────────────────────────────────────────────────────────

def _search_cache(cache, keywords, max_results):
    """Search local cache by keyword substring matching. Returns matches."""
    if not cache or not cache.get("issues"):
        return []

    scored = []
    for key, summary in cache["issues"].items():
        summary_lower = summary.lower()
        hits = sum(1 for kw in keywords if kw.lower() in summary_lower)
        if hits > 0:
            scored.append((hits, key, summary))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[:max_results]


def _escape_jql_keyword(kw):
    """Escape JQL special characters in a keyword for use inside quotes."""
    # Backslash must be escaped first to avoid double-escaping
    kw = kw.replace("\\", "\\\\")
    kw = kw.replace('"', '\\"')
    for ch in "{}[]()+-&|!^~*?:":
        kw = kw.replace(ch, f"\\{ch}")
    return kw


def _search_jql(server, user, token, keywords, max_results):
    """Search Jira via JQL text operator. Returns list of (key, summary)."""
    if not all([server, user, token]):
        return []

    # Build text query: "kw1 OR kw2 OR kw3"
    escaped = []
    for kw in keywords:
        # Escape JQL special characters in keyword
        clean = _escape_jql_keyword(kw)
        escaped.append(f'"{clean}"')
    text_query = " OR ".join(escaped)

    jql = f'{JQL_BASE} AND text ~ "{text_query}"'
    jql_encoded = urllib.parse.quote(jql, safe="")
    path = (f"/search/jql?jql={jql_encoded}&maxResults={max_results}"
            f"&fields=key,summary")

    try:
        data = api_call_with_retry(server, path, user, token)
        results = []
        for issue in data.get("issues", []):
            key = issue["key"]
            summary = issue.get("fields", {}).get("summary", "")
            results.append((key, summary))
        return results
    except Exception as e:
        print(f"JQL search failed: {e}", file=sys.stderr)
        return []


def search(text, keywords, max_results=10):
    """Run two-tier duplicate search. Returns JSON-serializable dict."""
    server, user, token = require_env()
    has_creds = all([server, user, token])

    if not text.strip() and not keywords:
        return {"source": "none", "cache_age_hours": None,
                "keywords_used": [], "matches": [], "error": None}

    # Ensure cache is fresh
    cache = _ensure_fresh_cache(server, user, token)
    age = _cache_age_hours(cache)

    # Tier 1: local cache search
    cache_results = _search_cache(cache, keywords, max_results)
    source = "cache"

    # Tier 2: JQL fallback if cache results are sparse
    jql_results = []
    if has_creds and len(cache_results) < 3:
        jql_results = _search_jql(server, user, token, keywords, max_results)
        source = "cache+jira" if cache_results else "jira"

    # Merge and deduplicate
    seen = set()
    matches = []

    # Cache results first (scored by keyword hits)
    for hits, key, summary in cache_results:
        if key not in seen:
            seen.add(key)
            url = f"{server}/browse/{key}" if server else ""
            matches.append({"key": key, "summary": summary, "url": url})

    # Then JQL results
    for key, summary in jql_results:
        if key not in seen:
            seen.add(key)
            url = f"{server}/browse/{key}" if server else ""
            matches.append({"key": key, "summary": summary, "url": url})

    matches = matches[:max_results]

    error = None
    if not has_creds and not cache:
        error = "no_credentials"
    elif not has_creds and cache:
        error = "no_credentials_using_stale_cache"

    return {
        "source": source,
        "cache_age_hours": age,
        "keywords_used": keywords,
        "matches": matches,
        "error": error,
    }


# ─── CLI ──────────────────────────────────────────────────────────────────────

def cmd_search(args):
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] \
        if args.keywords else _extract_fallback_keywords(args.text)
    result = search(args.text, keywords, args.max_results)
    print(json.dumps(result, indent=2))


def cmd_refresh_cache(args):
    server, user, token = require_env()
    if not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN must be set",
              file=sys.stderr)
        sys.exit(1)
    cache = _load_cache()
    if not args.force and _cache_is_fresh(cache, server):
        age = _cache_age_hours(cache)
        print(f"Cache is fresh ({age}h old, TTL={CACHE_TTL_HOURS}h). "
              f"Use --force to override.", file=sys.stderr)
        return
    refresh_cache(server, user, token)


def cmd_cache_info(args):
    cache = _load_cache()
    if not cache:
        print("No cache found.")
        return
    age = _cache_age_hours(cache)
    server, _, _ = require_env()
    fresh = _cache_is_fresh(cache, server)
    info = {
        "cache_file": CACHE_PATH,
        "refreshed_at": cache.get("refreshed_at", "unknown"),
        "age_hours": age,
        "ttl_hours": CACHE_TTL_HOURS,
        "fresh": fresh,
        "server": cache.get("server", "unknown"),
        "issue_count": cache.get("issue_count", 0),
    }
    if getattr(args, "json", False):
        print(json.dumps(info, indent=2))
    else:
        print(f"Cache file: {info['cache_file']}")
        print(f"Refreshed: {info['refreshed_at']}")
        print(f"Age: {info['age_hours']}h (TTL: {info['ttl_hours']}h, "
              f"{'fresh' if info['fresh'] else 'stale'})")
        print(f"Server: {info['server']}")
        print(f"Issues: {info['issue_count']}")


def _extract_fallback_keywords(text):
    """Basic keyword extraction when LLM doesn't provide --keywords."""
    words = text.lower().split()
    candidates = [w for w in words if w not in _STOPWORDS and len(w) > 2]
    # Deduplicate preserving order
    seen = set()
    unique = []
    for w in candidates:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return unique[:5]


def main():
    parser = argparse.ArgumentParser(
        description="Search for potential duplicate RFEs in Jira.")
    sub = parser.add_subparsers(dest="command", required=True)

    # search
    p_search = sub.add_parser("search", help="Search for duplicates")
    p_search.add_argument("text", help="Problem statement text")
    p_search.add_argument("--keywords",
                          help="Comma-separated key phrases (LLM-extracted)")
    p_search.add_argument("--max-results", type=int, default=10,
                          help="Maximum matches to return")
    p_search.set_defaults(func=cmd_search)

    # refresh-cache
    p_refresh = sub.add_parser("refresh-cache", help="Refresh the local cache")
    p_refresh.add_argument("--force", action="store_true",
                           help="Ignore TTL and force refresh")
    p_refresh.set_defaults(func=cmd_refresh_cache)

    # cache-info
    p_info = sub.add_parser("cache-info", help="Show cache status")
    p_info.add_argument("--json", action="store_true",
                        help="Output as JSON for scripting")
    p_info.set_defaults(func=cmd_cache_info)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
