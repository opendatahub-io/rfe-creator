#!/usr/bin/env python3
"""Tests for scripts/check_revised.py — content comparison between original and task files."""
import os
import subprocess

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "check_revised.py")


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
