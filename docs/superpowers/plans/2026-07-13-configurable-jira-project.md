# Configurable Jira Project Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all hardcoded `RHAIRFE` project key and `Feature Request` issue type references with configurable `JIRA_PROJECT` and `JIRA_ISSUE_TYPE` environment variables.

**Architecture:** A new `scripts/resolve_project.py` validates env vars. A new `is_jira_key()` helper in `artifact_utils.py` replaces all `.startswith("RHAIRFE-")` checks. Schema patterns are generalized to accept any Jira key format. Skills call `resolve_project.py` at startup and prompt the user if `JIRA_PROJECT` is unset.

**Tech Stack:** Python 3, pytest, markdown skill files

---

### Task 1: Add `is_jira_key()` helper and generalize schema patterns in `artifact_utils.py`

**Files:**
- Modify: `scripts/artifact_utils.py`
- Test: `tests/test_artifact_utils.py`

- [ ] **Step 1: Write tests for `is_jira_key()`**

Add to `tests/test_artifact_utils.py`:

```python
class TestIsJiraKey:
    def test_standard_jira_key(self):
        from artifact_utils import is_jira_key
        assert is_jira_key("RHAIRFE-1234") is True

    def test_other_project_key(self):
        from artifact_utils import is_jira_key
        assert is_jira_key("MYPROJ-42") is True

    def test_single_letter_project(self):
        from artifact_utils import is_jira_key
        assert is_jira_key("X-1") is True

    def test_draft_is_not_jira_key(self):
        from artifact_utils import is_jira_key
        assert is_jira_key("DRAFT-001") is False

    def test_lowercase_is_not_jira_key(self):
        from artifact_utils import is_jira_key
        assert is_jira_key("rhairfe-1234") is False

    def test_no_number_is_not_jira_key(self):
        from artifact_utils import is_jira_key
        assert is_jira_key("RHAIRFE-") is False

    def test_empty_string(self):
        from artifact_utils import is_jira_key
        assert is_jira_key("") is False

    def test_alphanumeric_project(self):
        from artifact_utils import is_jira_key
        assert is_jira_key("ABC123-456") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/rbean/code/rfe-creator && python -m pytest tests/test_artifact_utils.py::TestIsJiraKey -v`
Expected: FAIL — `is_jira_key` not found

- [ ] **Step 3: Implement `is_jira_key()` and update schema patterns**

In `scripts/artifact_utils.py`, add at module level (after imports, before SCHEMAS):

```python
_JIRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")


def is_jira_key(identifier):
    """True if identifier looks like a Jira issue key (e.g., PROJ-1234)."""
    return bool(_JIRA_KEY_RE.match(identifier))
```

In the same file, update the three schema pattern fields — change `RHAIRFE-\d+` to `[A-Z][A-Z0-9]+-\d+`:

Line 65: `"pattern": r"^(DRAFT-\d+|[A-Z][A-Z0-9]+-\d+)$",`
Line 90: `"pattern": r"^(DRAFT-\d+|[A-Z][A-Z0-9]+-\d+)$",`
Line 103: `"pattern": r"^(DRAFT-\d+|[A-Z][A-Z0-9]+-\d+)$",`

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/rbean/code/rfe-creator && python -m pytest tests/test_artifact_utils.py -v`
Expected: ALL PASS (including existing tests — the generalized pattern still accepts RHAIRFE-*)

- [ ] **Step 5: Lint**

Run: `cd /home/rbean/code/rfe-creator && ruff check scripts/artifact_utils.py tests/test_artifact_utils.py && ruff format --check scripts/artifact_utils.py tests/test_artifact_utils.py`

- [ ] **Step 6: Commit**

```bash
git add scripts/artifact_utils.py tests/test_artifact_utils.py
git commit -S -s -m "$(cat <<'EOF'
Add is_jira_key() helper and generalize schema patterns

Replace hardcoded RHAIRFE regex patterns with generic Jira key
pattern [A-Z][A-Z0-9]+-\d+ in schema validation. Add is_jira_key()
helper for runtime checks.

Assisted-by: Claude claude-opus-4-6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Replace `.startswith("RHAIRFE-")` with `is_jira_key()` in `artifact_utils.py`

**Files:**
- Modify: `scripts/artifact_utils.py`
- Test: `tests/test_artifact_utils.py`

- [ ] **Step 1: Write test for find_artifact_file with non-RHAIRFE key**

Add to `tests/test_artifact_utils.py`:

```python
class TestFindArtifactFileGenericKey:
    def test_finds_generic_jira_key(self, tmp_path):
        from artifact_utils import find_artifact_file, write_frontmatter
        tasks_dir = tmp_path / "rfe-tasks"
        tasks_dir.mkdir()
        task_path = tasks_dir / "MYPROJ-42.md"
        write_frontmatter(
            str(task_path),
            {"rfe_id": "MYPROJ-42", "title": "Test", "priority": "Normal", "status": "Ready"},
            "rfe-task",
        )
        result = find_artifact_file(str(tmp_path), "MYPROJ-42")
        assert result is not None
        assert "MYPROJ-42.md" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/rbean/code/rfe-creator && python -m pytest tests/test_artifact_utils.py::TestFindArtifactFileGenericKey -v`
Expected: FAIL — function returns None because `.startswith("RHAIRFE-")` doesn't match MYPROJ-42

- [ ] **Step 3: Replace all `.startswith("RHAIRFE-")` in artifact_utils.py**

In `scripts/artifact_utils.py`, replace every `identifier.startswith("RHAIRFE-")` with `is_jira_key(identifier)`. These are at lines 495, 528, 549, 570, 594. Each follows the same pattern — the function checks `startswith("RHAIRFE-")` and `startswith("DRAFT-")` as two branches. After the change, the DRAFT branch stays as-is, and the RHAIRFE branch becomes `is_jira_key(identifier)`.

For example, in `find_artifact_file` (line 495):
```python
        # Match by Jira key (exact: PROJ-1595.md)
        if is_jira_key(identifier):
            if filename == f"{identifier}.md":
```

Apply the same change at lines 528, 549, 570, 594.

- [ ] **Step 4: Run all artifact_utils tests**

Run: `cd /home/rbean/code/rfe-creator && python -m pytest tests/test_artifact_utils.py -v`
Expected: ALL PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check scripts/artifact_utils.py tests/test_artifact_utils.py && ruff format --check scripts/artifact_utils.py tests/test_artifact_utils.py
git add scripts/artifact_utils.py tests/test_artifact_utils.py
git commit -S -s -m "$(cat <<'EOF'
Replace .startswith("RHAIRFE-") with is_jira_key() in artifact_utils

All artifact discovery functions now accept any Jira key format,
not just RHAIRFE-*.

Assisted-by: Claude claude-opus-4-6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Create `scripts/resolve_project.py`

**Files:**
- Create: `scripts/resolve_project.py`
- Create: `tests/test_resolve_project.py`

- [ ] **Step 1: Write tests**

Create `tests/test_resolve_project.py`:

```python
"""Tests for resolve_project.py."""

import os
import subprocess
import sys

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "resolve_project.py")


class TestResolveProject:
    def test_both_set(self, monkeypatch):
        monkeypatch.setenv("JIRA_PROJECT", "MYPROJ")
        monkeypatch.setenv("JIRA_ISSUE_TYPE", "Bug")
        result = subprocess.run(
            [sys.executable, SCRIPT],
            capture_output=True,
            text=True,
            env={**os.environ, "JIRA_PROJECT": "MYPROJ", "JIRA_ISSUE_TYPE": "Bug"},
        )
        assert result.returncode == 0
        assert "JIRA_PROJECT=MYPROJ" in result.stdout
        assert "JIRA_ISSUE_TYPE=Bug" in result.stdout

    def test_project_only_defaults_issue_type(self):
        env = {k: v for k, v in os.environ.items() if k != "JIRA_ISSUE_TYPE"}
        env["JIRA_PROJECT"] = "RHAIRFE"
        result = subprocess.run(
            [sys.executable, SCRIPT], capture_output=True, text=True, env=env
        )
        assert result.returncode == 0
        assert "JIRA_PROJECT=RHAIRFE" in result.stdout
        assert "JIRA_ISSUE_TYPE=Feature Request" in result.stdout

    def test_missing_project_exits_nonzero(self):
        env = {k: v for k, v in os.environ.items() if k not in ("JIRA_PROJECT", "JIRA_ISSUE_TYPE")}
        result = subprocess.run(
            [sys.executable, SCRIPT], capture_output=True, text=True, env=env
        )
        assert result.returncode == 1
        assert "JIRA_PROJECT" in result.stderr
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/rbean/code/rfe-creator && python -m pytest tests/test_resolve_project.py -v`
Expected: FAIL — script doesn't exist

- [ ] **Step 3: Implement `scripts/resolve_project.py`**

Create `scripts/resolve_project.py`:

```python
#!/usr/bin/env python3
"""Resolve Jira project configuration from environment variables.

Prints JIRA_PROJECT and JIRA_ISSUE_TYPE as KEY=VALUE lines.
Exits non-zero if JIRA_PROJECT is not set.

Usage:
    python3 scripts/resolve_project.py
"""

import os
import sys


def main():
    project = os.environ.get("JIRA_PROJECT")
    issue_type = os.environ.get("JIRA_ISSUE_TYPE", "Feature Request")

    if not project:
        print(
            "ERROR: JIRA_PROJECT is not set.\n"
            "Set the Jira project key for this RFE workflow:\n"
            "  export JIRA_PROJECT=RHAIRFE\n"
            "Optionally set the issue type (default: Feature Request):\n"
            "  export JIRA_ISSUE_TYPE='Feature Request'",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"JIRA_PROJECT={project}")
    print(f"JIRA_ISSUE_TYPE={issue_type}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/rbean/code/rfe-creator && python -m pytest tests/test_resolve_project.py -v`
Expected: ALL PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check scripts/resolve_project.py tests/test_resolve_project.py && ruff format --check scripts/resolve_project.py tests/test_resolve_project.py
git add scripts/resolve_project.py tests/test_resolve_project.py
git commit -S -s -m "$(cat <<'EOF'
Add resolve_project.py for Jira project env var validation

Centralizes JIRA_PROJECT and JIRA_ISSUE_TYPE resolution. Skills
call this at startup; exits non-zero if JIRA_PROJECT is unset.

Assisted-by: Claude claude-opus-4-6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Update `submit.py` to use env vars instead of hardcoded values

**Files:**
- Modify: `scripts/submit.py`
- Test: `tests/test_submit.py`

- [ ] **Step 1: Write test for env-var-based project key**

Add to `tests/test_submit.py`:

```python
class TestProjectEnvVar:
    """submit.py reads JIRA_PROJECT and JIRA_ISSUE_TYPE from env."""

    def test_uses_jira_project_env(self, tmp_path, monkeypatch):
        """Dry-run output uses JIRA_PROJECT, not hardcoded RHAIRFE."""
        monkeypatch.setenv("JIRA_PROJECT", "TESTPROJ")
        monkeypatch.setenv("JIRA_ISSUE_TYPE", "Story")
        art_dir = str(tmp_path)
        _write(
            f"{art_dir}/rfe-tasks/DRAFT-001.md",
            "---\nrfe_id: DRAFT-001\ntitle: Test\npriority: Normal\nstatus: Draft\n---\n\nBody.\n",
        )
        _write(
            f"{art_dir}/rfe-reviews/DRAFT-001-review.md",
            REVIEW_FM.format(rfe_id="DRAFT-001", auto_revised="false"),
        )
        out = _run_submit(art_dir, dry_run=True)
        assert "Would create TESTPROJ ticket" in out
        assert "RHAIRFE" not in out
```

Note: `_write`, `_run_submit`, and `REVIEW_FM` are existing test helpers in `tests/test_submit.py`. Check the existing test file for their exact definitions and import them or use them as-is.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/rbean/code/rfe-creator && python -m pytest tests/test_submit.py::TestProjectEnvVar -v`
Expected: FAIL — output still says "RHAIRFE"

- [ ] **Step 3: Update `scripts/submit.py`**

Make these changes in `scripts/submit.py`:

1. **Line 698** — change dry-run message:
```python
                    project = os.environ.get("JIRA_PROJECT", "UNKNOWN")
                    print(f"  {rfe_id}: Would create {project} ticket: {title}")
                    results[rfe_id] = f"{project}-DRY"
```

2. **Lines 701-706** — change `create_issue` call:
```python
                    new_key = create_issue(
                        server,
                        user,
                        token,
                        os.environ["JIRA_PROJECT"],
                        os.environ.get("JIRA_ISSUE_TYPE", "Feature Request"),
                        title,
                        description_adf,
                        entry["priority"],
                        labels=labels,
                    )
```

3. **Line 746** — change dry-run key check:
```python
                if new_key and not new_key.endswith("-DRY"):
```

4. **Lines 193, 199, 214, 218, 327** — replace all `.startswith("RHAIRFE-")` with `is_jira_key()`:

Add to imports (around line 49):
```python
from artifact_utils import (  # noqa: E402
    ValidationError,
    find_removed_context_yaml,
    find_review_file,
    is_jira_key,
    read_frontmatter_validated,
    rebuild_index,
    rename_to_jira_key,
    scan_task_files,
    update_frontmatter,
)
```

Then replace:
- Line 199: `and is_jira_key(data["rfe_id"])` (was `.startswith("RHAIRFE-")`)
- Line 218: `if is_jira_key(pk):` (was `pk.startswith("RHAIRFE-")`)
- Line 327: `if is_jira_key(rfe_id):` (was `rfe_id.startswith("RHAIRFE-")`)
- Line 390: `is_existing = is_jira_key(rfe_id)` (was `rfe_id.startswith("RHAIRFE-")`)

5. **Update comments/docstring** — line 5: change `(RHAIRFE with status: Archived)` to `(Jira issues with status: Archived)`; line 193 comment, line 206 comment, line 355 comment, line 357-358 comments: replace `RHAIRFE` with generic references.

- [ ] **Step 4: Run tests**

Run: `cd /home/rbean/code/rfe-creator && python -m pytest tests/test_submit.py -v`
Expected: ALL PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check scripts/submit.py tests/test_submit.py && ruff format --check scripts/submit.py tests/test_submit.py
git add scripts/submit.py tests/test_submit.py
git commit -S -s -m "$(cat <<'EOF'
Use JIRA_PROJECT and JIRA_ISSUE_TYPE env vars in submit.py

Replace hardcoded "RHAIRFE" project key and "Feature Request"
issue type with environment variable lookups.

Assisted-by: Claude claude-opus-4-6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Update `split_submit.py` to use env vars

**Files:**
- Modify: `scripts/split_submit.py`
- Test: `tests/test_split_submit.py`

- [ ] **Step 1: Write test for env-var-based project key in split_submit**

Add to `tests/test_split_submit.py`:

```python
class TestSplitProjectEnvVar:
    def test_dry_run_uses_jira_project(self, tmp_path, monkeypatch):
        """Dry-run output uses JIRA_PROJECT, not hardcoded RHAIRFE."""
        monkeypatch.setenv("JIRA_PROJECT", "TESTPROJ")
        # ... set up parent + child artifacts with TESTPROJ-1000 as parent ...
        # Run split_submit.py TESTPROJ-1000 --dry-run
        # Assert "Would create TESTPROJ ticket" in output
        # Assert "RHAIRFE" not in output
```

Note: Check `tests/test_split_submit.py` for existing test structure and helpers. The test should follow the same pattern.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/rbean/code/rfe-creator && python -m pytest tests/test_split_submit.py::TestSplitProjectEnvVar -v`

- [ ] **Step 3: Update `scripts/split_submit.py`**

1. **Line 242** — change dry-run message:
```python
                f"  Phase 2: Would create {os.environ.get('JIRA_PROJECT', 'UNKNOWN')} ticket for child "
```

2. **Line 255** — change dry-run key:
```python
            state.phase2_done[idx] = f"{os.environ.get('JIRA_PROJECT', 'UNKNOWN')}-DRY"
```

3. **Lines 263-264** — change `create_issue` call:
```python
            "RHAIRFE",      ->  os.environ["JIRA_PROJECT"],
            "Feature Request",  ->  os.environ.get("JIRA_ISSUE_TYPE", "Feature Request"),
```

4. **Line 556** — change dry-run key check:
```python
        if not assigned_key or assigned_key.endswith("-DRY"):
```

5. **Line 442** — update comment: `RHAIRFE parent` -> `Jira parent`

6. **Line 13** — update docstring usage example: `RHAIRFE-XXXX` -> `PROJ-XXXX`

- [ ] **Step 4: Run tests**

Run: `cd /home/rbean/code/rfe-creator && python -m pytest tests/test_split_submit.py -v`
Expected: ALL PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check scripts/split_submit.py tests/test_split_submit.py && ruff format --check scripts/split_submit.py tests/test_split_submit.py
git add scripts/split_submit.py tests/test_split_submit.py
git commit -S -s -m "$(cat <<'EOF'
Use JIRA_PROJECT and JIRA_ISSUE_TYPE env vars in split_submit.py

Replace hardcoded project key and issue type with environment
variable lookups.

Assisted-by: Claude claude-opus-4-6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Update remaining scripts with `.startswith("RHAIRFE-")` checks

**Files:**
- Modify: `scripts/check_conflicts.py`
- Modify: `scripts/generate_review_pdf.py`
- Modify: `scripts/jira_utils.py`

- [ ] **Step 1: Update `scripts/check_conflicts.py`**

Line 104: change `if not rfe_id.startswith("RHAIRFE-"):` to:

```python
        if not is_jira_key(rfe_id):
```

Add import at top (after existing imports from artifact_utils, line 35):
```python
from artifact_utils import is_jira_key, scan_task_files
```

(Replace the existing `from artifact_utils import scan_task_files` line.)

- [ ] **Step 2: Update `scripts/generate_review_pdf.py`**

Lines 1304 and 1310: change `rfe_id.startswith("RHAIRFE-")` to:

```python
        if jira_server and is_jira_key(rfe_id):
```

Add import. Find the existing imports from artifact_utils or add near top:
```python
from artifact_utils import is_jira_key
```

Note: `generate_review_pdf.py` is a large file. Check existing imports from artifact_utils and add `is_jira_key` to them.

- [ ] **Step 3: Update `scripts/jira_utils.py`**

Line 701: change the title heading regex:
```python
        if re.match(r"^#\s+(DRAFT-\d+|[A-Z][A-Z0-9]+-\d+):", line):
```

Also update the comment on line 678:
```python
    - Title headings (# DRAFT-NNN: / # PROJ-NNN:)
```

- [ ] **Step 4: Run all tests**

Run: `cd /home/rbean/code/rfe-creator && python -m pytest -v`
Expected: ALL PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check scripts/check_conflicts.py scripts/generate_review_pdf.py scripts/jira_utils.py && ruff format --check scripts/check_conflicts.py scripts/generate_review_pdf.py scripts/jira_utils.py
git add scripts/check_conflicts.py scripts/generate_review_pdf.py scripts/jira_utils.py
git commit -S -s -m "$(cat <<'EOF'
Replace remaining RHAIRFE checks in scripts with is_jira_key()

Update check_conflicts.py, generate_review_pdf.py, and
jira_utils.py strip_metadata to use generic Jira key patterns.

Assisted-by: Claude claude-opus-4-6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Update docstrings and help text in scripts (no logic changes)

**Files:**
- Modify: `scripts/split_submit.py` (if not done in Task 5)
- Modify: `scripts/fetch_issue.py`
- Modify: `scripts/prep_assess.py`
- Modify: `scripts/check_revised.py`
- Modify: `scripts/check_resume.py`
- Modify: `scripts/jql_query.py`
- Modify: `scripts/cleanup_partial_split.py`
- Modify: `scripts/collect_children.py`
- Modify: `scripts/batch_summary.py`
- Modify: `scripts/collect_recommendations.py`

- [ ] **Step 1: Update docstring/help examples**

In each file, replace `RHAIRFE-XXXX` or `RHAIRFE-1234` in docstrings, help text, and comments with generic equivalents like `PROJ-1234` or `<KEY>`. These are documentation-only changes — no logic.

Files and specific lines:

- `scripts/fetch_issue.py` lines 9, 14, 148: `RHAIRFE-1234` -> `PROJ-1234`
- `scripts/prep_assess.py` line 8: `RHAIRFE-1234` -> `PROJ-1234`
- `scripts/check_revised.py` line 14: `RHAIRFE-1504 RHAIRFE-1510` -> `PROJ-1504 PROJ-1510`
- `scripts/check_resume.py` line 22: `RHAIRFE-1234 RHAIRFE-5678` -> `PROJ-1234 PROJ-5678`
- `scripts/jql_query.py` lines 5, 9, 10: `RHAIRFE` -> `PROJ`
- `scripts/cleanup_partial_split.py` line 24: `RHAIRFE-100` -> `PROJ-100`
- `scripts/collect_children.py` line 16: `RHAIRFE-100` -> `PROJ-100`
- `scripts/batch_summary.py` line 17: `RHAIRFE-100` -> `PROJ-100`
- `scripts/collect_recommendations.py` line 84 comment: `RHAIRFE-1234-review.md` -> `PROJ-1234-review.md`

- [ ] **Step 2: Run tests**

Run: `cd /home/rbean/code/rfe-creator && python -m pytest -v`
Expected: ALL PASS (docs-only changes)

- [ ] **Step 3: Lint and commit**

```bash
ruff check scripts/fetch_issue.py scripts/prep_assess.py scripts/check_revised.py scripts/check_resume.py scripts/jql_query.py scripts/cleanup_partial_split.py scripts/collect_children.py scripts/batch_summary.py scripts/collect_recommendations.py
git add scripts/fetch_issue.py scripts/prep_assess.py scripts/check_revised.py scripts/check_resume.py scripts/jql_query.py scripts/cleanup_partial_split.py scripts/collect_children.py scripts/batch_summary.py scripts/collect_recommendations.py
git commit -S -s -m "$(cat <<'EOF'
Update script docstrings to use generic Jira project examples

Replace RHAIRFE-* examples in help text and comments with
generic PROJ-* placeholders.

Assisted-by: Claude claude-opus-4-6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Update tests to not depend on hardcoded RHAIRFE

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/test_submit.py`
- Modify: `tests/test_split_submit.py`
- Modify: `tests/test_artifact_utils.py`
- Modify: other test files as needed

- [ ] **Step 1: Update `tests/conftest.py`**

Lines 106-111: The jira emulator fixture patches `RHAIRFE Workflow`. This is emulator-internal config — keep the workflow name as-is since it matches the emulator's seed data. But update the docstring example on line 88 from `RHAIRFE-1` to a generic example.

The `jira.create()` helper on line 126 derives the project from the key (`key.split("-")[0]`), so it already works with any project key. No code changes needed, just the docstring.

- [ ] **Step 2: Update `tests/test_submit.py`**

Set `JIRA_PROJECT` in tests that exercise the create path. Add `monkeypatch.setenv("JIRA_PROJECT", "RHAIRFE")` at the top of test methods that call `_run_submit` with `dry_run=False` or that check for RHAIRFE in output. Alternatively, add it to the test class setup.

For tests that already use `RHAIRFE-1234` as fixture data — these are fine since the schema now accepts any Jira key. The key change is ensuring `JIRA_PROJECT` is set in tests that exercise `submit.py`'s create path (which now reads the env var).

- [ ] **Step 3: Update other test files**

For each test file with RHAIRFE references (`test_split_submit.py`, `test_pipeline_state.py`, `test_generate_run_report.py`, `test_check_review_progress.py`, `test_state.py`, `test_snapshot_fetch.py`, `test_snapshot_fetch_integration.py`, `test_markdown_to_adf.py`, `test_clone_results_repo.py`, `test_check_revised.py`, `test_check_resume.py`, `test_bootstrap_snapshot.py`, `test_draft_prefix.py`):

Most of these use `RHAIRFE-*` as fixture data for IDs — these still work because `is_jira_key()` and the schema accept any Jira key. Only fix tests that:
1. Assert on `RHAIRFE` appearing in output (update to match the env var or use the fixture ID)
2. Need `JIRA_PROJECT` set to run `submit.py` or `split_submit.py`

- [ ] **Step 4: Run full test suite**

Run: `cd /home/rbean/code/rfe-creator && python -m pytest -v`
Expected: ALL PASS

- [ ] **Step 5: Lint and commit**

```bash
git add tests/
git commit -S -s -m "$(cat <<'EOF'
Update tests for configurable JIRA_PROJECT

Set JIRA_PROJECT env var in tests that exercise submit paths.
Existing RHAIRFE-* fixture data still works with generalized
schema patterns.

Assisted-by: Claude claude-opus-4-6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Update skills to call `resolve_project.py` at startup

**Files:**
- Modify: `.claude/skills/rfe.review/SKILL.md`
- Modify: `.claude/skills/rfe.submit/SKILL.md`
- Modify: `.claude/skills/rfe.split/SKILL.md`
- Modify: `.claude/skills/rfe.speedrun/SKILL.md`
- Modify: `.claude/skills/rfe.auto-fix/SKILL.md`
- Modify: `.claude/skills/rfe-feasibility-review/SKILL.md`
- Modify: `.claude/skills/rfe.split/prompts/split-agent.md`

- [ ] **Step 1: Add resolve-project gate to each skill**

For `rfe.review`, `rfe.split`, `rfe.speedrun`, `rfe.auto-fix` — add before the first step (after argument parsing):

```markdown
### Resolve Jira Project

Before any Jira interaction, run:

```bash
python3 scripts/resolve_project.py
```

If it exits non-zero, ask the user:
> What Jira project key should I use? (e.g., RHAIRFE, MYPROJECT)

Then export:
```bash
export JIRA_PROJECT=<answer>
```

Optionally ask about issue type (default: Feature Request). Re-run `resolve_project.py` to confirm.
```

For `rfe.submit` — replace the existing "Step 0: Check Credentials" to also check `JIRA_PROJECT`:

```markdown
## Step 0: Check Configuration

Run `python3 scripts/resolve_project.py` to verify `JIRA_PROJECT` is set.
If it exits non-zero, tell the user:

> RFE submission requires a Jira project. Set:
> ```
> export JIRA_PROJECT=RHAIRFE
> ```

Then check `JIRA_SERVER`, `JIRA_USER`, `JIRA_TOKEN` as before.
```

For `rfe-feasibility-review` — this is a subagent skill that doesn't interact with Jira directly. Only update the example on line 56: `RHAIRFE-1234` -> `PROJ-1234`.

- [ ] **Step 2: Update RHAIRFE references in skill descriptions and examples**

In each skill file:

- `rfe.review/SKILL.md` line 3: `RHAIRFE-1234 RHAIRFE-5678` -> `PROJ-1234 PROJ-5678`; line 15: `RHAIRFE-NNNN` -> Jira key
- `rfe.submit/SKILL.md` line 3: `Creates new RHAIRFE tickets` -> `Creates new Jira tickets`; line 8: `create or update RHAIRFE Jira tickets` -> `create or update Jira tickets`
- `rfe.split/SKILL.md` line 3: `RHAIRFE-1234 RHAIRFE-5678` -> `PROJ-1234 PROJ-5678`; line 14: `RHAIRFE-NNNN` -> Jira key; line 160: `RHAIRFE-1234` -> `PROJ-1234`
- `rfe.speedrun/SKILL.md` line 18: `RHAIRFE-NNNN` -> Jira key; line 30: `RHAIRFE-NNNN` -> Jira key; line 187: `RHAIRFE-NNNN` -> `<JIRA_PROJECT>-NNNN`
- `rfe.auto-fix/SKILL.md` lines 125, 130: `RHAIRFE-1234` -> `PROJ-1234`
- `rfe.split/prompts/split-agent.md` line 13: `RHAIRFE level` -> `RFE level`; `RHAISTRAT level` -> `strategy level`

- [ ] **Step 3: Run skillsaw lint if available**

Run: `cd /home/rbean/code/rfe-creator && make skillsaw 2>/dev/null || echo "skillsaw not available"`

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/
git commit -S -s -m "$(cat <<'EOF'
Add resolve_project.py gate to skills and remove RHAIRFE refs

Each skill now calls resolve_project.py at startup and prompts
the user if JIRA_PROJECT is unset. Skill descriptions and
examples updated to use generic project references.

Assisted-by: Claude claude-opus-4-6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Update documentation (README, AGENTS.md, CLAUDE.md)

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update `README.md`**

Replace all `RHAIRFE-*` examples with generic equivalents. Key changes:

- Line 3: `to the RHAIRFE Jira project` -> `to Jira`
- Lines 19-21: `RHAIRFE-1234` -> `PROJ-1234`
- Lines 26-27: `project = RHAIRFE AND ...` -> `project = $JIRA_PROJECT AND ...`; `RHAIRFE-1234 RHAIRFE-5678` -> `PROJ-1234 PROJ-5678`
- Lines 48, 51: `RHAIRFE-1234` -> `PROJ-1234`
- Lines 74-75: same treatment
- Line 90: `Creates new RHAIRFE tickets` -> `Creates new Jira tickets`
- Add to the Jira Integration section (after the env vars block):

```markdown
# Set the Jira project key (required)
export JIRA_PROJECT=RHAIRFE

# Optionally set the issue type (default: Feature Request)
export JIRA_ISSUE_TYPE='Feature Request'
```

- [ ] **Step 2: Update `AGENTS.md`**

- Line 3: `to the RHAIRFE Jira project` -> `to Jira`
- Lines 15-18: `RHAIRFE-1595` -> `PROJ-1595`
- Line 22: same
- Lines 25, 68, 70: same
- Lines 99-105: Replace the "RHAIRFE Project" section with a generic "Jira Project Configuration" section:

```markdown
## Jira Project Configuration
- **Project**: Set via `JIRA_PROJECT` environment variable (required)
- **Issue Type**: Set via `JIRA_ISSUE_TYPE` environment variable (default: `Feature Request`)
- **Priority values** (use these exactly): Blocker, Critical, Major, Normal, Minor, Undefined
- **Status on creation**: `New`
```

- Add `JIRA_PROJECT` and `JIRA_ISSUE_TYPE` to the env vars block.

- [ ] **Step 3: Update `CLAUDE.md`**

The CLAUDE.md is also the basis for the system prompt. Apply the same changes as AGENTS.md (they share most content). The key section is the "Jira Field Mappings" / "RHAIRFE Project" block.

- [ ] **Step 4: Commit**

```bash
git add README.md AGENTS.md CLAUDE.md
git commit -S -s -m "$(cat <<'EOF'
Update docs for configurable JIRA_PROJECT

Replace RHAIRFE references in README, AGENTS.md, and CLAUDE.md
with generic project references and document the new JIRA_PROJECT
and JIRA_ISSUE_TYPE environment variables.

Assisted-by: Claude claude-opus-4-6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Update config files (eval, fullsend, ambient)

**Files:**
- Modify: `eval.yaml`
- Modify: `eval.md`
- Modify: `.fullsend/scripts/post-submit.sh`
- Modify: `.fullsend/README.md`
- Modify: `.ambient/ambient.json`

- [ ] **Step 1: Update `eval.yaml`**

Line 92: `RHAIRFE-NNNN.md (fetched)` -> `<PROJECT>-NNNN.md (fetched)`

- [ ] **Step 2: Update `eval.md`**

Lines 47, 50: `RHAIRFE` -> generic references. These are analysis docs describing the pipeline — update examples to use `PROJ-NNNN` or `$JIRA_PROJECT`.

- [ ] **Step 3: Update `.fullsend/scripts/post-submit.sh`**

Line 4 comment: `SUBMIT=RHAIRFE-1234,DRAFT-001` -> `SUBMIT=PROJ-1234,DRAFT-001`

- [ ] **Step 4: Update `.fullsend/README.md`**

Replace any `RHAIRFE` references with generic equivalents.

- [ ] **Step 5: Update `.ambient/ambient.json`**

Line 3: `"Submit to Jira as RHAIRFEs"` -> `"Submit to Jira"`
Line 4 systemPrompt: `"Submit to Jira as RHAIRFEs"` -> `"Submit to Jira"`
Line 6 greeting: `"Submit to Jira as RHAIRFEs"` -> `"Submit to Jira"`

- [ ] **Step 6: Update `docs/` files with RHAIRFE references**

Check `docs/superpowers/plans/2026-07-13-fullsend-harness.md`, `docs/superpowers/specs/2026-07-13-fullsend-harness-design.md`, `docs/state-machine/pipeline-correctness-reference.md`, `docs/snapshot-incremental-fetch.md` — update any RHAIRFE references to generic form. These are internal docs, so use `PROJ-NNNN` or `$JIRA_PROJECT` as appropriate.

Also update `design-proposals/plan-a-thin-dispatcher.md`.

- [ ] **Step 7: Commit**

```bash
git add eval.yaml eval.md .fullsend/ .ambient/ docs/ design-proposals/
git commit -S -s -m "$(cat <<'EOF'
Update config and docs for configurable JIRA_PROJECT

Replace RHAIRFE references in eval configs, fullsend harness,
ambient config, and internal docs with generic project references.

Assisted-by: Claude claude-opus-4-6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Final verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `cd /home/rbean/code/rfe-creator && python -m pytest -v`
Expected: ALL PASS

- [ ] **Step 2: Run linter**

Run: `cd /home/rbean/code/rfe-creator && make lint`
Expected: PASS

- [ ] **Step 3: Verify no remaining RHAIRFE in code (except test fixtures)**

Run: `cd /home/rbean/code/rfe-creator && grep -rn "RHAIRFE" --include="*.py" --include="*.md" --include="*.yaml" --include="*.json" --include="*.sh" | grep -v "^tests/" | grep -v "^docs/superpowers/specs/2026-07-13-configurable-jira-project" | grep -v "^docs/superpowers/plans/2026-07-13-configurable-jira-project"`

Expected: Zero results (or only in test fixtures and the design/plan docs themselves). Any remaining hits need to be addressed.

- [ ] **Step 4: Verify RHAIRFE in tests is only fixture data, not logic**

Run: `cd /home/rbean/code/rfe-creator && grep -rn "RHAIRFE" tests/ | grep -v "RHAIRFE-" | head -20`

Expected: Only the jira emulator workflow name reference in `conftest.py` (which is emulator-internal config).

- [ ] **Step 5: Commit any remaining fixes**

If Step 3 or 4 found issues, fix and commit.
