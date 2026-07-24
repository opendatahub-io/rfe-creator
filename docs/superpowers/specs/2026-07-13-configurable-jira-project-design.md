# Configurable Jira Project Key

**Date:** 2026-07-13
**Status:** Draft

## Problem

The Jira project key `RHAIRFE` and issue type `Feature Request` are hardcoded across 53 files (989 occurrences). This couples the tooling to a single Jira project, preventing reuse with other projects.

## Solution

Replace all hardcoded references with:

- `JIRA_PROJECT` environment variable (required)
- `JIRA_ISSUE_TYPE` environment variable (optional, defaults to `Feature Request`)

A centralized script (`scripts/resolve_project.py`) validates these at skill startup. If `JIRA_PROJECT` is unset, the skill asks the user and exports it.

## Components

### 1. `scripts/resolve_project.py` (new)

Reads `JIRA_PROJECT` and `JIRA_ISSUE_TYPE` from environment. Prints them as `KEY=VALUE` lines on success. Exits non-zero with a descriptive message if `JIRA_PROJECT` is unset. `JIRA_ISSUE_TYPE` defaults to `Feature Request` if unset.

```
$ JIRA_PROJECT=RHAIRFE python3 scripts/resolve_project.py
JIRA_PROJECT=RHAIRFE
JIRA_ISSUE_TYPE=Feature Request

$ python3 scripts/resolve_project.py
ERROR: JIRA_PROJECT is not set. ...
(exit 1)
```

### 2. Schema patterns in `artifact_utils.py`

Change hardcoded `RHAIRFE` in regex patterns to accept any Jira-style project key:

```python
# Before
"pattern": r"^(DRAFT-\d+|RHAIRFE-\d+)$"

# After
"pattern": r"^(DRAFT-\d+|[A-Z][A-Z0-9]+-\d+)$"
```

Applies to three fields: `rfe_id` (rfe-task), `parent_key` (rfe-task), `rfe_id` (rfe-review).

### 3. Helper function: `is_jira_key()`

Add to `artifact_utils.py`:

```python
_JIRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")

def is_jira_key(identifier):
    """True if identifier looks like a Jira issue key (e.g., RHAIRFE-1234)."""
    return bool(_JIRA_KEY_RE.match(identifier))
```

All `.startswith("RHAIRFE-")` checks across scripts become `is_jira_key()` calls. This removes the env var dependency from code that just needs to distinguish Jira keys from DRAFT IDs.

### 4. Scripts using the project key for Jira API calls

These scripts pass the project key to `create_issue()`:

- **`submit.py`** — replace `"RHAIRFE"` literal with `os.environ["JIRA_PROJECT"]`; replace `"Feature Request"` literal with `os.environ.get("JIRA_ISSUE_TYPE", "Feature Request")`
- **`split_submit.py`** — same treatment

These are the only two scripts that need the env var at runtime. All other scripts use `is_jira_key()` pattern matching instead.

### 5. Scripts with `.startswith("RHAIRFE-")` checks

Replace with `is_jira_key()` (imported from `artifact_utils`):

- `submit.py` — line 199, 218, 327, 390
- `split_submit.py` — line 556
- `check_conflicts.py` — line 104
- `generate_review_pdf.py` — lines 1304, 1310
- `artifact_utils.py` — lines 495, 528, 549, 570, 594

### 6. `jira_utils.py` `strip_metadata`

Generalize the title heading regex:

```python
# Before
if re.match(r"^#\s+(DRAFT-\d+|RHAIRFE-\d+|STRAT-\d+|RHAISTRAT-\d+):", line):

# After
if re.match(r"^#\s+(DRAFT-\d+|[A-Z][A-Z0-9]+-\d+):", line):
```

This also covers `STRAT-` and `RHAISTRAT-` since they match the general pattern.

### 7. Skills

Each skill's SKILL.md gets an early-gate block:

```markdown
**Before any other work**, run:
    python3 scripts/resolve_project.py
If it exits non-zero, ask the user for their Jira project key (and optionally
issue type), then export JIRA_PROJECT=<answer> (and JIRA_ISSUE_TYPE if given).
Re-run to confirm.
```

Affected skills:
- `rfe.review`
- `rfe.submit`
- `rfe.split`
- `rfe.speedrun`
- `rfe.auto-fix`
- `rfe-feasibility-review`

### 8. Documentation

- **`CLAUDE.md`** — update env vars section to list `JIRA_PROJECT` and `JIRA_ISSUE_TYPE`; replace `RHAIRFE` in examples with generic placeholders or `$JIRA_PROJECT` references
- **`AGENTS.md`** — same treatment
- **`README.md`** — update examples to use generic project keys (e.g., `MYPROJECT-1234`)
- **Snapshot/pipeline docs** — update any `RHAIRFE` examples

### 9. Tests

Tests that reference `RHAIRFE`:
- Set `JIRA_PROJECT=TESTPROJ` in test fixtures/conftest where the env var is needed
- Replace `RHAIRFE-` prefixes in test data with `TESTPROJ-` for consistency
- Update assertions accordingly

### 10. Eval/fullsend config

- **`eval.yaml`** — update pattern references from `RHAIRFE-NNNN.md` to generic
- **`.fullsend/scripts/pre-fetch.sh`** — use `$JIRA_PROJECT`
- **`.fullsend/scripts/post-submit.sh`** — use `$JIRA_PROJECT`
- **`.ambient/ambient.json`** — update references

## What stays the same

- `JIRA_SERVER`, `JIRA_USER`, `JIRA_TOKEN` env vars — unchanged
- `DRAFT-NNN` naming convention — unchanged
- File/directory structure (artifacts/, rfe-tasks/, etc.) — unchanged
- All Jira API logic in `jira_utils.py` — unchanged (already parameterized)
- `create_issue()` signature — already takes `project` as a parameter

## Defaults

| Variable | Required | Default |
|----------|----------|---------|
| `JIRA_PROJECT` | Yes | (none — must ask user) |
| `JIRA_ISSUE_TYPE` | No | `Feature Request` |

## Migration

No data migration needed. Existing artifact files with `RHAIRFE-` prefixes will continue to work because `is_jira_key()` accepts any valid Jira key pattern. Users just need to set `JIRA_PROJECT=RHAIRFE` to get the current behavior.
