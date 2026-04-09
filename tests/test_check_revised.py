#!/usr/bin/env python3
"""Tests for scripts/check_revised.py — content comparison between original and task files."""
import os
import subprocess

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "check_revised.py")
FM_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "frontmatter.py")


def _write(path, content):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _run(original, task):
    result = subprocess.run(
        ["python3", SCRIPT, original, task],
        capture_output=True, text=True,
    )
    return result.stdout.strip(), result.returncode


@pytest.fixture
def tmp_dir(tmp_path):
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(orig)


class TestCheckRevised:
    def test_identical_content(self, tmp_dir):
        body = "## Problem\n\nSame content here.\n"
        _write("original.md", body)
        _write("task.md", body)
        out, rc = _run("original.md", "task.md")
        assert rc == 0
        assert out == "REVISED=false"

    def test_different_content(self, tmp_dir):
        _write("original.md", "## Problem\n\nOriginal.\n")
        _write("task.md", "## Problem\n\nRevised and improved.\n")
        out, rc = _run("original.md", "task.md")
        assert rc == 0
        assert out == "REVISED=true"

    def test_frontmatter_stripped_before_comparison(self, tmp_dir):
        """Frontmatter differences don't count — only body matters."""
        body = "## Problem\n\nSame body content.\n"
        _write("original.md", body)
        _write("task.md", f"---\nrfe_id: RHAIRFE-1234\ntitle: Test\n---\n{body}")
        out, rc = _run("original.md", "task.md")
        assert rc == 0
        assert out == "REVISED=false"

    def test_whitespace_only_difference(self, tmp_dir):
        """Trailing whitespace differences are ignored (.strip())."""
        _write("original.md", "Content here.\n\n\n")
        _write("task.md", "---\nrfe_id: X\n---\nContent here.\n")
        out, rc = _run("original.md", "task.md")
        assert rc == 0
        assert out == "REVISED=false"

    def test_missing_original_file(self, tmp_dir):
        _write("task.md", "Some content.\n")
        out, rc = _run("nonexistent.md", "task.md")
        assert rc == 1
        assert "FILE_MISSING" in out

    def test_missing_task_file(self, tmp_dir):
        _write("original.md", "Some content.\n")
        out, rc = _run("original.md", "nonexistent.md")
        assert rc == 1
        assert "FILE_MISSING" in out

    def test_wrong_arg_count(self):
        result = subprocess.run(
            ["python3", SCRIPT],
            capture_output=True, text=True,
        )
        assert result.returncode == 2


REVIEW_TEMPLATE = """\
---
rfe_id: {rfe_id}
score: 7
pass: false
recommendation: revise
feasibility: feasible
auto_revised: {auto_revised}
needs_attention: false
scores:
  what: 2
  why: 1
  open_to_how: 2
  not_a_task: 2
  right_sized: 0
---
Review body here.
"""


def _setup_batch(tmp_path, rfe_id, original_body, task_body, auto_revised=False):
    """Create originals, tasks, and review dirs with test content."""
    originals = tmp_path / "artifacts" / "rfe-originals"
    tasks = tmp_path / "artifacts" / "rfe-tasks"
    reviews = tmp_path / "artifacts" / "rfe-reviews"
    originals.mkdir(parents=True, exist_ok=True)
    tasks.mkdir(parents=True, exist_ok=True)
    reviews.mkdir(parents=True, exist_ok=True)

    (originals / f"{rfe_id}.md").write_text(original_body)
    (tasks / f"{rfe_id}.md").write_text(
        f"---\nrfe_id: {rfe_id}\ntitle: Test\npriority: Normal\nstatus: Draft\n---\n{task_body}"
    )
    (reviews / f"{rfe_id}-review.md").write_text(
        REVIEW_TEMPLATE.format(rfe_id=rfe_id, auto_revised=str(auto_revised).lower())
    )


def _read_frontmatter(path):
    """Read YAML frontmatter from a file."""
    import yaml
    with open(path) as f:
        content = f.read()
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end == -1:
        return {}
    return yaml.safe_load(content[3:end]) or {}


class TestBatchMode:
    def test_sets_auto_revised_true_when_content_differs(self, tmp_path):
        _setup_batch(tmp_path, "RHAIRFE-1001", "Original text.", "Revised text.",
                     auto_revised=False)
        result = subprocess.run(
            ["python3", SCRIPT, "--batch", "RHAIRFE-1001"],
            capture_output=True, text=True,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPT)},
        )
        assert result.returncode == 0
        assert "RHAIRFE-1001: auto_revised False -> True" in result.stdout
        assert "UPDATED=1" in result.stdout
        # Verify frontmatter was actually changed
        review = (tmp_path / "artifacts" / "rfe-reviews" / "RHAIRFE-1001-review.md").read_text()
        assert "auto_revised: true" in review

    def test_sets_auto_revised_false_when_content_identical(self, tmp_path):
        _setup_batch(tmp_path, "RHAIRFE-1002", "Same content.", "Same content.",
                     auto_revised=True)
        result = subprocess.run(
            ["python3", SCRIPT, "--batch", "RHAIRFE-1002"],
            capture_output=True, text=True,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPT)},
        )
        assert result.returncode == 0
        assert "RHAIRFE-1002: auto_revised True -> False" in result.stdout
        assert "UPDATED=1" in result.stdout
        review = (tmp_path / "artifacts" / "rfe-reviews" / "RHAIRFE-1002-review.md").read_text()
        assert "auto_revised: false" in review

    def test_no_update_when_flag_already_correct(self, tmp_path):
        _setup_batch(tmp_path, "RHAIRFE-1003", "Original.", "Revised.",
                     auto_revised=True)
        result = subprocess.run(
            ["python3", SCRIPT, "--batch", "RHAIRFE-1003"],
            capture_output=True, text=True,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPT)},
        )
        assert result.returncode == 0
        assert "auto_revised=True (correct)" in result.stdout
        assert "UPDATED=0" in result.stdout

    def test_discovers_ids_when_none_given(self, tmp_path):
        _setup_batch(tmp_path, "RHAIRFE-1004", "Original.", "Changed.",
                     auto_revised=False)
        _setup_batch(tmp_path, "RHAIRFE-1005", "Same.", "Same.",
                     auto_revised=False)
        result = subprocess.run(
            ["python3", SCRIPT, "--batch"],
            capture_output=True, text=True,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPT)},
        )
        assert result.returncode == 0
        assert "RHAIRFE-1004: auto_revised False -> True" in result.stdout
        assert "RHAIRFE-1005: auto_revised=False (correct)" in result.stdout
        assert "UPDATED=1" in result.stdout

    def test_skips_missing_review_file(self, tmp_path):
        """If an original+task exist but no review file, skip without error."""
        originals = tmp_path / "artifacts" / "rfe-originals"
        tasks = tmp_path / "artifacts" / "rfe-tasks"
        reviews = tmp_path / "artifacts" / "rfe-reviews"
        originals.mkdir(parents=True)
        tasks.mkdir(parents=True)
        reviews.mkdir(parents=True)
        (originals / "RHAIRFE-1006.md").write_text("Original.")
        (tasks / "RHAIRFE-1006.md").write_text("---\nrfe_id: RHAIRFE-1006\ntitle: T\npriority: Normal\nstatus: Draft\n---\nChanged.")
        result = subprocess.run(
            ["python3", SCRIPT, "--batch", "RHAIRFE-1006"],
            capture_output=True, text=True,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPT)},
        )
        assert result.returncode == 0
        assert "UPDATED=0" in result.stdout


class TestReassessCyclePreservation:
    """Test that auto_revised flag survives reassess cycles where the
    re-review agent sets frontmatter WITHOUT including auto_revised."""

    def test_flag_survives_frontmatter_set_without_auto_revised(self, tmp_path):
        """Simulates re-review agent setting scores without auto_revised —
        the existing auto_revised=true should be preserved."""
        _setup_batch(tmp_path, "RHAIRFE-2001", "Original.", "Revised.",
                     auto_revised=True)
        subprocess.run([
            "python3", FM_SCRIPT, "set",
            str(tmp_path / "artifacts/rfe-reviews/RHAIRFE-2001-review.md"),
            "score=9", "pass=true", "recommendation=submit",
        ], check=True, capture_output=True)
        fm = _read_frontmatter(tmp_path / "artifacts/rfe-reviews/RHAIRFE-2001-review.md")
        assert fm["auto_revised"] is True

    def test_flag_clobbered_when_set_explicitly_false(self, tmp_path):
        """If something explicitly sets auto_revised=false, it sticks."""
        _setup_batch(tmp_path, "RHAIRFE-2002", "Original.", "Revised.",
                     auto_revised=True)
        subprocess.run([
            "python3", FM_SCRIPT, "set",
            str(tmp_path / "artifacts/rfe-reviews/RHAIRFE-2002-review.md"),
            "auto_revised=false",
        ], check=True, capture_output=True)
        fm = _read_frontmatter(tmp_path / "artifacts/rfe-reviews/RHAIRFE-2002-review.md")
        assert fm["auto_revised"] is False

    def test_reassess_cycle_preserves_flag(self, tmp_path):
        """End-to-end: FIXUP sets true, re-review without auto_revised
        doesn't clobber it, and check_revised confirms it stays true."""
        _setup_batch(tmp_path, "RHAIRFE-2003", "Original.", "Revised content.",
                     auto_revised=False)

        # Step 1: FIXUP sets auto_revised=true
        subprocess.run(
            ["python3", SCRIPT, "--batch", "RHAIRFE-2003"],
            capture_output=True, text=True, cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPT)},
        )
        fm = _read_frontmatter(tmp_path / "artifacts/rfe-reviews/RHAIRFE-2003-review.md")
        assert fm["auto_revised"] is True

        # Step 2: Re-review agent writes new scores WITHOUT auto_revised
        subprocess.run([
            "python3", FM_SCRIPT, "set",
            str(tmp_path / "artifacts/rfe-reviews/RHAIRFE-2003-review.md"),
            "score=9", "pass=true", "recommendation=submit",
            "scores.what=2", "scores.why=1", "scores.open_to_how=2",
            "scores.not_a_task=2", "scores.right_sized=2",
        ], check=True, capture_output=True)
        fm = _read_frontmatter(tmp_path / "artifacts/rfe-reviews/RHAIRFE-2003-review.md")
        assert fm["auto_revised"] is True, \
            "re-review agent must not clobber auto_revised"

        # Step 3: FIXUP again confirms
        subprocess.run(
            ["python3", SCRIPT, "--batch", "RHAIRFE-2003"],
            capture_output=True, text=True, cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPT)},
        )
        fm = _read_frontmatter(tmp_path / "artifacts/rfe-reviews/RHAIRFE-2003-review.md")
        assert fm["auto_revised"] is True

    def test_review_agent_prompt_excludes_auto_revised(self):
        """The review agent prompt must NOT include auto_revised in its
        frontmatter.py set call — only the revise agent and FIXUP set it."""
        prompt_path = os.path.join(
            os.path.dirname(__file__), "..",
            ".claude/skills/rfe.review/prompts/review-agent.md")
        with open(prompt_path) as f:
            content = f.read()
        lines = content.split("\n")
        in_set_block = False
        set_block = []
        for line in lines:
            if "frontmatter.py set" in line and "rfe_id=" in line:
                in_set_block = True
            if in_set_block:
                set_block.append(line)
                if not line.rstrip().endswith("\\"):
                    break
        set_cmd = " ".join(set_block)
        assert "auto_revised" not in set_cmd, \
            "review-agent.md frontmatter.py set must not include auto_revised"
