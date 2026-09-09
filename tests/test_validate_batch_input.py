#!/usr/bin/env python3
"""Tests for scripts/validate_batch_input.py — batch YAML preflight validation."""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "validate_batch_input.py")

import artifact_utils  # noqa: E402
import type_registry  # noqa: E402
import validate_batch_input  # noqa: E402
from validate_batch_input import validate_entries  # noqa: E402

REG = type_registry.load(extra_roots=[], env={})


def _write(path, content):
    with open(path, "w") as f:
        f.write(content)


class TestRegistryDerivedConstants:
    def test_known_fields_are_the_pre_registry_literals(self):
        # Behaviour-neutral migration: the sets every batch author relied on are unchanged.
        assert validate_batch_input.RFE_KNOWN_FIELDS == {
            "prompt",
            "priority",
            "labels",
            "clarifying_context",
        }
        assert validate_batch_input.INITIATIVE_KNOWN_FIELDS == {
            "prompt",
            "priority",
            "labels",
            "clarifying_context",
            "parent_key",
        }
        assert list(validate_batch_input.KNOWN_FIELDS) == ["rfe", "initiative"]

    def test_known_fields_are_base_plus_the_descriptors_batch_extra_fields(self):
        for name in REG.names():
            extra = REG.get(name).get("batch.extra_fields", [])
            assert validate_batch_input.KNOWN_FIELDS[name] == (
                set(validate_batch_input.BASE_KNOWN_FIELDS) | set(extra)
            )

    def test_allowed_priorities_equal_the_task_schemas_until_they_derive_too(self):
        # The validator now reads the rfe descriptor; artifact_utils.SCHEMAS still holds the
        # literal enum for both task schemas. They must stay equal (same error message text).
        enum = validate_batch_input.ALLOWED_PRIORITIES
        assert enum == ["Blocker", "Critical", "Major", "Normal", "Minor", "Undefined"]
        assert enum == artifact_utils.SCHEMAS["rfe-task"]["priority"]["enum"]
        assert enum == artifact_utils.SCHEMAS["initiative-task"]["priority"]["enum"]
        assert enum == REG.get("rfe").get("schema.task.priority.enum")

    def test_parent_key_pattern_stays_the_literal_until_pr3(self):
        # PR-1 checklist Q14: narrower than the initiative descriptor's parent_key_patterns.
        assert validate_batch_input.PARENT_KEY_PATTERN.pattern == (
            r"^(RHAISTRAT-\d+|RHOAIENG-\d+)$"
        )
        assert validate_batch_input.PARENT_KEY_PATTERN.match("INIT-001") is None

    def test_unknown_entry_type_falls_back_to_the_rfe_fields(self):
        errors, warnings = validate_entries(
            [{"prompt": "x", "parent_key": "RHAISTRAT-100"}], entry_type="nope"
        )
        assert errors == []
        assert warnings == ["entry 0: unknown field 'parent_key'"]

    def test_type_choices_are_the_registry_choices(self):
        result = subprocess.run(["python3", SCRIPT, "--help"], capture_output=True, text=True)
        assert result.returncode == 0
        assert "--type {rfe,initiative}" in result.stdout

    def test_a_drop_in_type_brings_its_own_batch_fields(self, tmp_path):
        root = tmp_path / "types"
        (root / "docs").mkdir(parents=True)
        (root / "docs" / "type.yaml").write_text(
            "schema_version: 1\ntype: docs\nbatch: {extra_fields: [audience]}\n"
        )
        env = {
            **os.environ,
            "RFE_CREATOR_EXTRA_TYPES": str(root),
            "RFE_CREATOR_EXTRA_TYPES_ALLOWLIST": str(root),
        }
        path = str(tmp_path / "batch.yaml")
        _write(path, "- prompt: Write the guide\n  audience: admins\n  parent_key: RHAISTRAT-1\n")
        result = subprocess.run(
            ["python3", SCRIPT, path, "--type", "docs"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 0, result.stderr
        assert "WARNING: entry 0: unknown field 'parent_key'" in result.stdout
        assert "audience" not in result.stdout


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

    def test_labels_with_non_string_entry(self):
        errors, warnings = validate_entries([{"prompt": "x", "labels": [123, "ok"]}])
        assert len(errors) == 1
        assert "labels" in errors[0]

    def test_labels_with_blank_string_entry(self):
        errors, warnings = validate_entries([{"prompt": "x", "labels": ["   "]}])
        assert len(errors) == 1
        assert "labels" in errors[0]

    def test_clarifying_context_wrong_type(self):
        errors, warnings = validate_entries(
            [{"prompt": "x", "clarifying_context": ["not", "a", "string"]}]
        )
        assert len(errors) == 1
        assert "clarifying_context" in errors[0]

    def test_clarifying_context_string_is_valid(self):
        errors, warnings = validate_entries([{"prompt": "x", "clarifying_context": "some context"}])
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

    def test_empty_list_is_invalid(self):
        errors, warnings = validate_entries([])
        assert len(errors) == 1
        assert "at least one" in errors[0]


class TestInitiativeValidation:
    def test_parent_key_accepted_for_initiative(self):
        errors, warnings = validate_entries(
            [{"prompt": "Improve onboarding", "parent_key": "RHAISTRAT-100"}],
            entry_type="initiative",
        )
        assert errors == []
        assert warnings == []

    def test_parent_key_warning_for_rfe(self):
        errors, warnings = validate_entries(
            [{"prompt": "Improve onboarding", "parent_key": "RHAISTRAT-100"}],
            entry_type="rfe",
        )
        assert errors == []
        assert len(warnings) == 1
        assert "parent_key" in warnings[0]

    def test_parent_key_rhoaieng_accepted(self):
        errors, warnings = validate_entries(
            [{"prompt": "x", "parent_key": "RHOAIENG-5000"}],
            entry_type="initiative",
        )
        assert errors == []

    def test_parent_key_invalid_format(self):
        errors, warnings = validate_entries(
            [{"prompt": "x", "parent_key": "BAD-123"}],
            entry_type="initiative",
        )
        assert len(errors) == 1
        assert "parent_key" in errors[0]

    def test_parent_key_non_string(self):
        errors, warnings = validate_entries(
            [{"prompt": "x", "parent_key": 123}],
            entry_type="initiative",
        )
        assert len(errors) == 1
        assert "parent_key" in errors[0]

    def test_initiative_unknown_field_still_warned(self):
        errors, warnings = validate_entries(
            [{"prompt": "x", "team": "aipcc"}],
            entry_type="initiative",
        )
        assert errors == []
        assert len(warnings) == 1
        assert "team" in warnings[0]


class TestInitiativeCLI:
    def test_type_initiative_accepts_parent_key(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "- prompt: Improve onboarding\n  parent_key: RHAISTRAT-100\n")
        result = subprocess.run(
            ["python3", SCRIPT, path, "--type", "initiative"], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "VALID=true" in result.stdout

    def test_type_rfe_warns_on_parent_key(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "- prompt: Improve onboarding\n  parent_key: RHAISTRAT-100\n")
        result = subprocess.run(
            ["python3", SCRIPT, path, "--strict"], capture_output=True, text=True
        )
        assert result.returncode == 1
        assert "WARNING_COUNT=1" in result.stdout


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

    def test_empty_batch_exits_one(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "[]\n")
        result = subprocess.run(["python3", SCRIPT, path], capture_output=True, text=True)
        assert result.returncode == 1
        assert "ERROR_COUNT=1" in result.stdout

    def test_unreadable_path_exits_two(self, tmp_path):
        # A directory is not a valid file to open — should be caught as an OSError, not crash.
        result = subprocess.run(["python3", SCRIPT, str(tmp_path)], capture_output=True, text=True)
        assert result.returncode == 2
        assert "ERROR:" in result.stderr
