#!/usr/bin/env python3
"""Tests for scripts/next_rfe_id.py — atomic ID allocation with registry-derived rfe defaults."""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import next_rfe_id  # noqa: E402
import type_registry  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "next_rfe_id.py")


def _run(cwd, *args):
    return subprocess.run(["python3", SCRIPT, *args], cwd=str(cwd), capture_output=True, text=True)


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestDefaultsDeriveFromTheRfeDescriptor:
    def test_defaults_are_the_pre_registry_literals(self):
        # Behaviour-neutral migration: the values every caller relied on are unchanged.
        assert next_rfe_id.DEFAULT_PREFIX == "RFE"  # dash-less: the allocator adds the dash
        assert next_rfe_id.DEFAULT_DIR == "artifacts/rfe-tasks"

    def test_defaults_are_the_rfe_descriptor_projection(self):
        rfe = type_registry.load(extra_roots=[], env={}).get("rfe")
        assert next_rfe_id.DEFAULT_PREFIX == rfe.local_prefix.rstrip("-")
        assert next_rfe_id.DEFAULT_DIR == rfe.dirs()["tasks"]

    def test_help_shows_the_defaults(self, tmp_path):
        result = _run(tmp_path, "--help")
        assert result.returncode == 0
        assert "(default: RFE)" in result.stdout
        assert "(default: artifacts/rfe-tasks)" in result.stdout


class TestAllocation:
    def test_allocates_sequential_ids_and_placeholders_in_the_default_dir(self, tmp_path):
        result = _run(tmp_path, "2")
        assert result.returncode == 0, result.stderr
        assert result.stdout == "RFE-001\nRFE-002\n"
        tasks = tmp_path / "artifacts" / "rfe-tasks"
        assert (tasks / "RFE-001.md").exists()
        assert (tasks / "RFE-002.md").exists()
        assert (tasks / ".id-lock").exists()

    def test_continues_after_the_highest_existing_id(self, tmp_path):
        _write(tmp_path / "artifacts" / "rfe-tasks" / "RFE-007.md", "---\nrfe_id: RFE-007\n---\n")
        _write(tmp_path / "artifacts" / "rfe-tasks" / "RFE-003.md", "")
        result = _run(tmp_path, "1")
        assert result.returncode == 0, result.stderr
        assert result.stdout == "RFE-008\n"

    def test_other_prefixes_in_the_same_dir_do_not_count(self, tmp_path):
        _write(tmp_path / "artifacts" / "rfe-tasks" / "RHAIRFE-1595.md", "")
        _write(tmp_path / "artifacts" / "rfe-tasks" / "RHAIRFE-1595-comments.md", "")
        result = _run(tmp_path, "1")
        assert result.returncode == 0, result.stderr
        assert result.stdout == "RFE-001\n"

    def test_three_digit_zero_pad_widens_past_999(self, tmp_path):
        _write(tmp_path / "artifacts" / "rfe-tasks" / "RFE-999.md", "")
        result = _run(tmp_path, "1")
        assert result.stdout == "RFE-1000\n"

    def test_prefix_and_dir_flags_serve_the_initiative_callers(self, tmp_path):
        result = _run(tmp_path, "--prefix", "INIT", "--dir", "artifacts/initiatives", "2")
        assert result.returncode == 0, result.stderr
        assert result.stdout == "INIT-001\nINIT-002\n"
        assert (tmp_path / "artifacts" / "initiatives" / "INIT-002.md").exists()
        assert not (tmp_path / "artifacts" / "rfe-tasks").exists()

    def test_get_highest_number_reads_only_the_given_prefix(self, tmp_path):
        _write(tmp_path / "RFE-004.md", "")
        _write(tmp_path / "INIT-009.md", "")
        assert next_rfe_id.get_highest_number(str(tmp_path), "RFE") == 4
        assert next_rfe_id.get_highest_number(str(tmp_path), "INIT") == 9
        assert next_rfe_id.get_highest_number(str(tmp_path / "missing"), "RFE") == 0


class TestFromBatch:
    def test_allocates_one_id_per_entry(self, tmp_path):
        batch = tmp_path / "batch.yaml"
        batch.write_text("- prompt: a\n- prompt: b\n- prompt: c\n")
        result = _run(tmp_path, "--from-batch", str(batch))
        assert result.returncode == 0, result.stderr
        assert result.stdout == "RFE-001\nRFE-002\nRFE-003\n"

    def test_non_list_batch_is_a_usage_error(self, tmp_path):
        batch = tmp_path / "batch.yaml"
        batch.write_text("prompt: a\n")
        result = _run(tmp_path, "--from-batch", str(batch))
        assert result.returncode == 2
        assert "must contain a YAML list" in result.stderr
        assert not (tmp_path / "artifacts").exists()

    def test_empty_batch_is_rejected(self, tmp_path):
        batch = tmp_path / "batch.yaml"
        batch.write_text("[]\n")
        result = _run(tmp_path, "--from-batch", str(batch))
        assert result.returncode == 2
        assert "Count must be >= 1" in result.stderr


class TestUsageErrors:
    def test_missing_count_and_batch_is_a_usage_error(self, tmp_path):
        result = _run(tmp_path)
        assert result.returncode == 2
        assert "either count or --from-batch is required" in result.stderr

    def test_zero_count_is_rejected(self, tmp_path):
        result = _run(tmp_path, "0")
        assert result.returncode == 2
        assert "Count must be >= 1" in result.stderr
