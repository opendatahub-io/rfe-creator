#!/usr/bin/env python3
"""Tests for scripts/check_resume.py — resume checking with changed-ID bypass
and file-based I/O."""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                      "check_resume.py")

from check_resume import check_resume, read_ids_from_file


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


PASSING_REVIEW = """\
---
rfe_id: {rfe_id}
score: 9
pass: true
recommendation: submit
feasibility: feasible
auto_revised: false
needs_attention: false
error: null
scores:
  what: 2
  why: 2
  open_to_how: 2
  not_a_task: 2
  right_sized: 1
---

Looks good.
"""

FAILING_REVIEW = """\
---
rfe_id: {rfe_id}
score: 3
pass: false
recommendation: reject
feasibility: feasible
auto_revised: false
needs_attention: false
error: null
scores:
  what: 0
  why: 1
  open_to_how: 1
  not_a_task: 1
  right_sized: 0
---

Needs work.
"""

ERROR_REVIEW = """\
---
rfe_id: {rfe_id}
score: 0
pass: false
recommendation: reject
feasibility: unknown
auto_revised: false
needs_attention: false
error: "fetch_failed"
scores:
  what: 0
  why: 0
  open_to_how: 0
  not_a_task: 0
  right_sized: 0
---

Error during processing.
"""


@pytest.fixture
def art_dir(tmp_path):
    """Create a minimal artifacts directory."""
    os.makedirs(tmp_path / "rfe-reviews")
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield str(tmp_path)
    os.chdir(orig)


class TestCheckResumeFunction:
    def test_no_reviews_all_process(self, art_dir):
        """IDs with no review files → all need processing."""
        process, skip = check_resume(
            ["RHAIRFE-1", "RHAIRFE-2"], [], art_dir)
        assert process == ["RHAIRFE-1", "RHAIRFE-2"]
        assert skip == []

    def test_passing_review_skipped(self, art_dir):
        """ID with passing review → skipped."""
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1-review.md",
               PASSING_REVIEW.format(rfe_id="RHAIRFE-1"))
        process, skip = check_resume(["RHAIRFE-1"], [], art_dir)
        assert process == []
        assert skip == ["RHAIRFE-1"]

    def test_failing_review_processed(self, art_dir):
        """ID with failing review → needs processing."""
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1-review.md",
               FAILING_REVIEW.format(rfe_id="RHAIRFE-1"))
        process, skip = check_resume(["RHAIRFE-1"], [], art_dir)
        assert process == ["RHAIRFE-1"]
        assert skip == []

    def test_error_review_processed(self, art_dir):
        """ID with error in review → needs processing."""
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1-review.md",
               ERROR_REVIEW.format(rfe_id="RHAIRFE-1"))
        process, skip = check_resume(["RHAIRFE-1"], [], art_dir)
        assert process == ["RHAIRFE-1"]
        assert skip == []

    def test_changed_id_bypasses_passing_review(self, art_dir):
        """Changed ID with passing review → still processed (bypass)."""
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1-review.md",
               PASSING_REVIEW.format(rfe_id="RHAIRFE-1"))
        process, skip = check_resume(
            ["RHAIRFE-1"], ["RHAIRFE-1"], art_dir)
        assert process == ["RHAIRFE-1"]
        assert skip == []

    def test_mixed_changed_and_new(self, art_dir):
        """Mix of changed and unchanged IDs with various review states."""
        # RHAIRFE-1: changed, has passing review → process (bypass)
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1-review.md",
               PASSING_REVIEW.format(rfe_id="RHAIRFE-1"))
        # RHAIRFE-2: not changed, has passing review → skip
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-2-review.md",
               PASSING_REVIEW.format(rfe_id="RHAIRFE-2"))
        # RHAIRFE-3: not changed, no review → process
        # RHAIRFE-4: changed, no review → process

        ids = ["RHAIRFE-1", "RHAIRFE-2", "RHAIRFE-3", "RHAIRFE-4"]
        changed = ["RHAIRFE-1", "RHAIRFE-4"]
        process, skip = check_resume(ids, changed, art_dir)
        assert process == ["RHAIRFE-1", "RHAIRFE-3", "RHAIRFE-4"]
        assert skip == ["RHAIRFE-2"]

    def test_preserves_input_order(self, art_dir):
        """Output preserves the order of input IDs."""
        process, skip = check_resume(
            ["RHAIRFE-3", "RHAIRFE-1", "RHAIRFE-2"], [], art_dir)
        assert process == ["RHAIRFE-3", "RHAIRFE-1", "RHAIRFE-2"]


class TestReadIdsFromFile:
    def test_reads_ids(self, tmp_path):
        path = str(tmp_path / "ids.txt")
        with open(path, "w") as f:
            f.write("RHAIRFE-1\nRHAIRFE-2\nRHAIRFE-3\n")
        assert read_ids_from_file(path) == [
            "RHAIRFE-1", "RHAIRFE-2", "RHAIRFE-3"]

    def test_missing_file_returns_empty(self):
        assert read_ids_from_file("/nonexistent/file.txt") == []

    def test_none_path_returns_empty(self):
        assert read_ids_from_file(None) == []

    def test_skips_blank_lines(self, tmp_path):
        path = str(tmp_path / "ids.txt")
        with open(path, "w") as f:
            f.write("RHAIRFE-1\n\nRHAIRFE-2\n\n")
        assert read_ids_from_file(path) == ["RHAIRFE-1", "RHAIRFE-2"]


class TestFileBasedMode:
    """Integration tests using subprocess to validate file-based CLI."""

    def test_writes_output_file(self, art_dir, tmp_path):
        """--output-file receives the filtered process IDs."""
        ids_file = str(tmp_path / "all.txt")
        changed_file = str(tmp_path / "changed.txt")
        output_file = str(tmp_path / "process.txt")

        with open(ids_file, "w") as f:
            f.write("RHAIRFE-1\nRHAIRFE-2\n")
        with open(changed_file, "w") as f:
            f.write("")

        result = subprocess.run(
            ["python3", SCRIPT,
             "--ids-file", ids_file,
             "--changed-file", changed_file,
             "--output-file", output_file,
             "--artifacts-dir", art_dir],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        with open(output_file) as f:
            ids = [l.strip() for l in f if l.strip()]
        assert ids == ["RHAIRFE-1", "RHAIRFE-2"]

    def test_changed_ids_bypass_resume(self, art_dir, tmp_path):
        """Changed IDs bypass passing reviews in file-based mode."""
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-1-review.md",
               PASSING_REVIEW.format(rfe_id="RHAIRFE-1"))

        ids_file = str(tmp_path / "all.txt")
        changed_file = str(tmp_path / "changed.txt")
        output_file = str(tmp_path / "process.txt")

        with open(ids_file, "w") as f:
            f.write("RHAIRFE-1\n")
        with open(changed_file, "w") as f:
            f.write("RHAIRFE-1\n")

        result = subprocess.run(
            ["python3", SCRIPT,
             "--ids-file", ids_file,
             "--changed-file", changed_file,
             "--output-file", output_file,
             "--artifacts-dir", art_dir],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "PROCESS=1" in result.stdout
        assert "SKIP=0" in result.stdout
        with open(output_file) as f:
            ids = [l.strip() for l in f if l.strip()]
        assert ids == ["RHAIRFE-1"]

    def test_stdout_counts(self, art_dir, tmp_path):
        """File-based mode prints PROCESS=, SKIP=, CHANGED= counts."""
        _write(f"{art_dir}/rfe-reviews/RHAIRFE-2-review.md",
               PASSING_REVIEW.format(rfe_id="RHAIRFE-2"))

        ids_file = str(tmp_path / "all.txt")
        changed_file = str(tmp_path / "changed.txt")
        output_file = str(tmp_path / "process.txt")

        with open(ids_file, "w") as f:
            f.write("RHAIRFE-1\nRHAIRFE-2\nRHAIRFE-3\n")
        with open(changed_file, "w") as f:
            f.write("RHAIRFE-3\n")

        result = subprocess.run(
            ["python3", SCRIPT,
             "--ids-file", ids_file,
             "--changed-file", changed_file,
             "--output-file", output_file,
             "--artifacts-dir", art_dir],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "PROCESS=2" in result.stdout
        assert "SKIP=1" in result.stdout
        assert "CHANGED=1" in result.stdout
