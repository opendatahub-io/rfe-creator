#!/usr/bin/env python3
"""Search for potential duplicate RFEs in Jira.

Maintains a local JSON cache of RFE summaries for fast lookups,
with JQL text search as fallback.

Usage:
    python3 scripts/dedup_search.py search "problem text" --keywords "kw1,kw2" [--max-results 10]
    python3 scripts/dedup_search.py search "..." --keywords "..." --headless      # CI: never block on cache build
    python3 scripts/dedup_search.py search "..." --keywords "..." --recent-only   # Skip cache, narrow JQL only
    python3 scripts/dedup_search.py refresh-cache [--force]
    python3 scripts/dedup_search.py cache-info [--json]
"""

import argparse
import json
import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone

# Add parent directory so we can import jira_utils
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jira_utils import require_env, api_call_with_retry

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "tmp", "dedup-cache.json")
CACHE_TTL_HOURS = 4
JQL_BASE = "project = RHAIRFE AND statusCategory != Done"
RECENT_ONLY_DAYS = 30

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
    try:
        with open(CACHE_PATH, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "issues" not in data:
            return None
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _save_cache(data):
    """Write cache to disk."""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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


def _ensure_fresh_cache(server, user, token, allow_build=True):
    """Return a fresh cache, refreshing if needed.

    If allow_build is False, never trigger a build — return the existing
    cache (possibly stale or None) as-is. This is used by headless callers
    that can't afford to block on a full rebuild.
    """
    cache = _load_cache()
    if _cache_is_fresh(cache, server):
        return cache
    if not allow_build:
        return cache  # Caller opted out of blocking build
    # Stale or missing — need credentials to refresh
    if not all([server, user, token]):
        return cache  # Return stale cache (or None) if no creds
    # Let the user know why there's a pause
    if cache is None:
        print("Building duplicate-detection cache (first run, "
              "this may take a moment)...", file=sys.stderr)
    else:
        age = _cache_age_hours(cache)
        print(f"Refreshing duplicate-detection cache "
              f"(last built {age}h ago)...", file=sys.stderr)
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

    keywords_lower = [kw.lower() for kw in keywords]
    scored = []
    for key, summary in cache["issues"].items():
        summary_lower = summary.lower()
        hits = sum(1 for kw in keywords_lower if kw in summary_lower)
        if hits > 0:
            scored.append((hits, key, summary))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[:max_results]


_LUCENE_SPECIALS = '+-&|!(){}[]^"~*?:/'


def _lucene_escape(term):
    """Escape Lucene-syntax metacharacters in a single term."""
    # Backslash first so we don't double-escape the ones we add below
    term = term.replace("\\", "\\\\")
    for ch in _LUCENE_SPECIALS:
        term = term.replace(ch, f"\\{ch}")
    return term


def _jql_string_escape(s):
    """Escape a string for embedding inside a JQL double-quoted string."""
    # Only \ and " need escaping at the JQL string layer
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    return s


def _build_text_query_jql(keywords):
    """Build JQL text search clauses from keywords.

    Each keyword becomes a separate ``text ~ "keyword"`` clause joined
    with OR.  This avoids embedding Lucene OR/phrase syntax inside a
    single ``text ~`` value, which is not portable across all Jira
    implementations.

    Returns e.g. ``(text ~ "mlflow" OR text ~ "model registry")``.
    """
    clauses = []
    for kw in keywords:
        kw = kw.strip()
        if not kw:
            continue
        clauses.append(f'text ~ "{_jql_string_escape(kw)}"')
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return "(" + " OR ".join(clauses) + ")"


def _search_jql(server, user, token, keywords, max_results, recent_days=None):
    """Search Jira via JQL text operator. Returns list of (key, summary).

    If recent_days is set, scope the query to issues created within that
    window (e.g. 30) — used for fast "recent-only" searches that skip the
    cache entirely.
    """
    if not all([server, user, token]):
        return []

    text_clause = _build_text_query_jql(keywords)
    if text_clause is None:
        return []

    jql = f'{JQL_BASE} AND {text_clause}'
    if recent_days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(recent_days))
                  ).strftime("%Y-%m-%d")
        jql += f' AND created >= "{cutoff}"'
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


def search(text, keywords, max_results=10, headless=False, recent_only=False):
    """Run two-tier duplicate search. Returns JSON-serializable dict.

    headless:     never trigger a blocking cache build. If the cache is
                  missing or stale, fall straight to a scoped JQL query.
                  Intended for CI / non-interactive callers.
    recent_only:  bypass the cache entirely and run a narrow JQL scoped to
                  recently-created issues. Fast, but only catches recent
                  duplicates. Useful when the user declines a cache build.
    """
    server, user, token = require_env()
    has_creds = all([server, user, token])

    if not text.strip() and not keywords:
        return {"source": "none", "cache_age_hours": None,
                "keywords_used": [], "matches": [], "error": None}

    # Recent-only: skip the cache entirely
    if recent_only:
        jql_results = _search_jql(server, user, token, keywords, max_results,
                                  recent_days=RECENT_ONLY_DAYS) if has_creds else []
        matches = []
        seen = set()
        for key, summary in jql_results:
            if key not in seen:
                seen.add(key)
                url = f"{server}/browse/{key}" if server else ""
                matches.append({"key": key, "summary": summary, "url": url})
        matches = matches[:max_results]
        return {
            "source": "jira-recent",
            "cache_age_hours": None,
            "keywords_used": keywords,
            "matches": matches,
            "error": None if has_creds else "no_credentials",
        }

    # Normal path: cache + JQL fallback.
    # In headless mode, don't trigger a blocking cache build.
    cache = _ensure_fresh_cache(server, user, token,
                                allow_build=not headless)
    age = _cache_age_hours(cache)

    # Tier 1: local cache search
    cache_results = _search_cache(cache, keywords, max_results)
    source = "cache"

    # Tier 2: JQL fallback if cache results are sparse.
    # In headless mode without a usable cache, narrow the JQL to recent
    # issues so we don't fan out to a slow unbounded text search.
    jql_results = []
    if has_creds and len(cache_results) < 3:
        recent_days = RECENT_ONLY_DAYS if (headless and not cache) else None
        jql_results = _search_jql(server, user, token, keywords, max_results,
                                  recent_days=recent_days)
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
    result = search(args.text, keywords, args.max_results,
                    headless=args.headless, recent_only=args.recent_only)
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
                          help="Comma-separated key phrases (LLM-extracted). "
                               "Optional — a simple stopword-filtered fallback "
                               "is used if omitted.")
    p_search.add_argument("--max-results", type=int, default=10,
                          help="Maximum matches to return")
    p_search.add_argument("--headless", action="store_true",
                          help="Never trigger a blocking cache build. If the "
                               "cache is missing/stale, fall through to a "
                               "narrow JQL query scoped to recent issues.")
    p_search.add_argument("--recent-only", action="store_true",
                          help="Skip the cache entirely and run a targeted "
                               f"JQL scoped to issues created in the last "
                               f"{RECENT_ONLY_DAYS} days. Fast, but only "
                               f"catches recent duplicates.")
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
