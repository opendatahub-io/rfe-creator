#!/usr/bin/env python3
"""Tests for scripts/validate_batch_input.py — batch YAML preflight validation."""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "validate_batch_input.py")

from validate_batch_input import validate_entries  # noqa: E402


def _write(path, content):
    with open(path, "w") as f:
        f.write(content)


class TestValidateEntriesFunction:
    def test_minimal_valid_entry(self):
        errors, warnings = validate_entries([{"prompt": "Users need X"}])
        assert errors == []
        assert warnings == []

    def test_missing_prompt(self):
        errors, warnings = validate_entries([{"priority": "Major"}])
        assert len(errors) == 1
        assert "prompt" in errors[0]

    def test_blank_prompt(self):
        errors, warnings = validate_entries([{"prompt": "   "}])
        assert len(errors) == 1
        assert "prompt" in errors[0]

    def test_non_dict_entry(self):
        errors, warnings = validate_entries(["just a string"])
        assert len(errors) == 1
        assert "mapping" in errors[0]

    def test_all_valid_priorities_accepted(self):
        for priority in ["Blocker", "Critical", "Major", "Normal", "Minor", "Undefined"]:
            errors, warnings = validate_entries([{"prompt": "x", "priority": priority}])
            assert errors == [], f"{priority} should be valid"

    def test_invalid_priority(self):
        errors, warnings = validate_entries([{"prompt": "x", "priority": "High"}])
        assert len(errors) == 1
        assert "priority" in errors[0]

    def test_labels_not_a_list(self):
        errors, warnings = validate_entries([{"prompt": "x", "labels": "candidate-3.5"}])
        assert len(errors) == 1
        assert "labels" in errors[0]

    def test_labels_list_is_valid(self):
        errors, warnings = validate_entries([{"prompt": "x", "labels": ["candidate-3.5"]}])
        assert errors == []

    def test_unknown_field_is_warning_not_error(self):
        errors, warnings = validate_entries([{"prompt": "x", "team": "aipcc"}])
        assert errors == []
        assert len(warnings) == 1
        assert "team" in warnings[0]

    def test_duplicate_prompts_exact(self):
        errors, warnings = validate_entries([{"prompt": "same thing"}, {"prompt": "same thing"}])
        assert errors == []
        assert len(warnings) == 1
        assert "duplicate" in warnings[0]

    def test_duplicate_prompts_case_and_whitespace_insensitive(self):
        errors, warnings = validate_entries(
            [{"prompt": "Same Thing"}, {"prompt": "  same thing  "}]
        )
        assert len(warnings) == 1
        assert "duplicate" in warnings[0]

    def test_no_duplicate_warning_for_unique_prompts(self):
        errors, warnings = validate_entries([{"prompt": "a"}, {"prompt": "b"}])
        assert warnings == []

    def test_empty_list_is_valid(self):
        errors, warnings = validate_entries([])
        assert errors == []
        assert warnings == []


class TestCLI:
    def test_valid_file_exits_zero(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "- prompt: Users need X\n  priority: Major\n")
        result = subprocess.run(["python3", SCRIPT, path], capture_output=True, text=True)
        assert result.returncode == 0
        assert "ERROR_COUNT=0" in result.stdout
        assert "VALID=true" in result.stdout

    def test_invalid_priority_exits_one(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "- prompt: Users need X\n  priority: High\n")
        result = subprocess.run(["python3", SCRIPT, path], capture_output=True, text=True)
        assert result.returncode == 1
        assert "ERROR_COUNT=1" in result.stdout
        assert "VALID=false" in result.stdout

    def test_warnings_alone_exit_zero_without_strict(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "- prompt: Users need X\n  team: aipcc\n")
        result = subprocess.run(["python3", SCRIPT, path], capture_output=True, text=True)
        assert result.returncode == 0
        assert "WARNING_COUNT=1" in result.stdout
        assert "VALID=true" in result.stdout

    def test_strict_fails_on_warnings(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "- prompt: Users need X\n  team: aipcc\n")
        result = subprocess.run(
            ["python3", SCRIPT, path, "--strict"], capture_output=True, text=True
        )
        assert result.returncode == 1
        assert "VALID=false" in result.stdout

    def test_missing_file_exits_two(self, tmp_path):
        path = str(tmp_path / "does-not-exist.yaml")
        result = subprocess.run(["python3", SCRIPT, path], capture_output=True, text=True)
        assert result.returncode == 2

    def test_malformed_yaml_exits_two(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "- prompt: [unterminated\n")
        result = subprocess.run(["python3", SCRIPT, path], capture_output=True, text=True)
        assert result.returncode == 2

    def test_root_not_a_list_exits_two(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "prompt: Users need X\n")
        result = subprocess.run(["python3", SCRIPT, path], capture_output=True, text=True)
        assert result.returncode == 2
