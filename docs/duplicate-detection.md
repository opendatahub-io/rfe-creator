# Duplicate Detection for New RFEs

## Problem

`/rfe.create` helps PMs turn ideas into RFEs, but without any duplicate
check an organization quickly accumulates near-identical tickets — the
same request worded slightly differently by three different people. We
want to surface potential overlaps before the PM invests time answering
clarifying questions.

Two tensions shaped the design:

1. **Recall vs. latency**: the most thorough check would fetch every
   open RFE and score it against the new problem statement. That's
   minutes of pagination on a project with thousands of issues, which
   wrecks the `/rfe.create` UX where the conversation is supposed to
   start immediately.

2. **Server load**: Jira's `text ~` operator works but is slow and rate-
   sensitive; running it against the full project on every `/rfe.create`
   invocation is wasteful.

## Solution

A two-tier lookup with a locally cached summary index:

1. **Tier 1 — local cache**: A JSON file at `tmp/dedup-cache.json`
   stores `{issue_key: summary}` for every open RFE. Substring keyword
   matching runs in-process — no network round trip.

2. **Tier 2 — JQL fallback**: If the cache returns fewer than 3 hits
   (or the cache is bypassed entirely), fall back to Jira's `text ~`
   operator for a single targeted search.

The cache has a 4-hour TTL. When it's missing or stale, `search()`
rebuilds it by paginating `project = RHAIRFE AND statusCategory != Done`.
That rebuild is the expensive operation we need to be careful about.

## Opt-in Cache Build

A full rebuild can take 10+ seconds on a large project. Silently
blocking for that long before the PM sees the first clarifying question
would regress the `/rfe.create` UX, so the cache build is **opt-in** in
interactive mode and **skipped entirely** in headless mode.

The opt-in happens at the skill layer (`.claude/skills/rfe.create/SKILL.md`,
Step 1.5), not inside the Python script — `dedup_search.py` is invoked
as a subprocess and has no meaningful stdin for `AskUserQuestion`.

| Mode | Cache state | Behavior |
|------|-------------|----------|
| Interactive | Fresh | Silent use of cache. No prompt. |
| Interactive | Stale | Silent refresh (user already opted in previously). |
| Interactive | Missing | Prompt: "Build full cache (recommended)" vs. "Just check recent issues". |
| Headless | Fresh | Silent use. |
| Headless | Stale or missing | Skip refresh. Fall through to narrow JQL scoped to `created >= -30d`. |

The narrow JQL path trades recall (only catches duplicates created in
the last 30 days) for speed (no pagination, one API call). That's the
right tradeoff for CI — a `/rfe.speedrun --headless` run should never
wait a minute to build a dedup cache.

Whenever an implicit cache build does happen, `_ensure_fresh_cache`
prints a `"Building duplicate-detection cache..."` or
`"Refreshing duplicate-detection cache..."` message to stderr so the
user isn't left staring at nothing.

## CLI Surface

```bash
# Interactive default: cache-backed search, build cache if needed
python3 scripts/dedup_search.py search "<text>" --keywords "kw1,kw2"

# User declined the cache build: skip cache entirely
python3 scripts/dedup_search.py search "<text>" --keywords "kw1,kw2" --recent-only

# Headless / CI: use cache if fresh, else narrow JQL — never build
python3 scripts/dedup_search.py search "<text>" --keywords "kw1,kw2" --headless

# Headless + no cache available: explicitly recent-only
python3 scripts/dedup_search.py search "<text>" --keywords "kw1,kw2" --headless --recent-only

# Probe cache state (used by the skill to decide whether to prompt)
python3 scripts/dedup_search.py cache-info --json

# Explicit manual refresh
python3 scripts/dedup_search.py refresh-cache [--force]
```

`--keywords` is optional — a stopword-filtered fallback extractor runs
when it's omitted. The skill always passes explicit keywords because
LLM-extracted phrases produce much better matches than raw token splits,
but the fallback keeps the tool usable on its own.

## Files

```
tmp/
  dedup-cache.json   # {key: summary} index of open RFEs with refreshed_at + server
```

Cache schema:

```json
{
  "refreshed_at": "2026-04-15T12:00:00+00:00",
  "server": "https://your-site.atlassian.net",
  "issue_count": 342,
  "issues": {
    "RHAIRFE-1595": "Add widget export to PDF format",
    "RHAIRFE-1596": "..."
  }
}
```

The `server` field guards against a cache built against one Jira
instance being reused after `JIRA_SERVER` changes. `_cache_is_fresh`
invalidates the cache if the stored server doesn't match the caller's.

## Output Format

`search` returns JSON:

```json
{
  "source": "cache | cache+jira | jira | jira-recent | none",
  "cache_age_hours": 1.3,
  "keywords_used": ["kw1", "kw2"],
  "matches": [
    {"key": "RHAIRFE-1595", "summary": "...", "url": "https://.../browse/RHAIRFE-1595"}
  ],
  "error": null
}
```

`source` values:
- `cache` — all results from local cache, no JQL call made
- `cache+jira` — cache had some hits, JQL added more
- `jira` — cache had no hits, JQL-only
- `jira-recent` — `--recent-only` path; cache bypassed
- `none` — empty input

`error` is populated for `no_credentials` and
`no_credentials_using_stale_cache` — never `null` for "no matches
found" (that's just an empty `matches` list).

## Known Gaps (Tracked, Not Yet Implemented)

- **Incremental cache refresh.** Currently every TTL-expiry triggers a
  full rebuild. The cache already stores `refreshed_at`, so a future
  revision can fetch only issues with `updated >= <last_refresh>` plus
  a second query for closures (`statusCategory = Done AND updated >=
  <last_refresh>`) to evict them. Expected to drop a minute-long
  refresh to seconds on large projects.
- **Periodic full reconciliation.** Once incremental refresh lands,
  run a full rebuild every 24 hours regardless to catch drift from
  missed events (label-only changes, permission shifts, etc.).

## Design Invariants

Before modifying `scripts/dedup_search.py`, preserve these:

1. **Never block `/rfe.create` on a silent cache build.** If a build
   is about to happen implicitly, it must either print a stderr
   notice (interactive) or be suppressed via `--headless`.
2. **The cache is advisory, not a source of truth.** `search()` must
   tolerate a missing, stale, or corrupt cache gracefully — returning
   a structured error is always preferable to raising.
3. **`_cache_is_fresh` stays pure.** It takes `cache` and an optional
   `server`; it does not read `os.environ`. Callers that need the
   server read it once and pass it in.
4. **`--headless` must never prompt, pause, or paginate.** A CI run
   with no cache should complete in one API call (the narrow JQL
   path), not fifty.
