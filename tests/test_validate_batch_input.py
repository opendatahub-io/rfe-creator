#!/usr/bin/env python3
"""Tests for scripts/validate_batch_input.py — batch YAML preflight validation.

PR-3b: both batch root forms (the legacy bare list and the ``{type, items}`` mapping) go through
the registry's one parser (``type_registry.read_batch``) and one ladder (``type_registry.resolve``);
the per-type vocabularies come from the resolved type's descriptor; the resolve line goes to
stderr only for a non-default rung (D3). The legacy default (bare list, no --type) is
byte-identical to main on stdout and stderr whatever the environment or the file's channel (read
once; string items are entry errors, not ids; the binding is not read); an explicit --type keeps
main's stdout and adds exactly one stderr line. Deliberate malformed-input differences from main:
an rfe entry with a malformed parent_key is a warning only, and INIT- parents are accepted for
initiatives.
"""

import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "validate_batch_input.py")

import artifact_utils  # noqa: E402
import type_registry  # noqa: E402
import validate_batch_input  # noqa: E402
from validate_batch_input import validate_entries  # noqa: E402

REG = type_registry.load(extra_roots=[], env={})
PROTOCOL_PREFIXES = ("ERROR_COUNT=", "WARNING_COUNT=", "ERROR: ", "WARNING: ", "VALID=")
CLEAN_STDOUT = "ERROR_COUNT=0\nWARNING_COUNT=0\nVALID=true\n"
INIT_PARENT_ERROR = (
    "entry 0: 'parent_key' must match one of RHAISTRAT-\\d+, RHOAIENG-\\d+, INIT-\\d+"
)

LIST_FORM = "- prompt: Improve onboarding\n  parent_key: RHAISTRAT-100\n"
MAPPING_FORM = (
    "type: initiative\nitems:\n- prompt: Improve onboarding\n  parent_key: RHAISTRAT-100\n"
)


def _write(path, content):
    with open(path, "w") as f:
        f.write(content)


def _clean_env(**extra):
    """No drop-in roots, no headless/CI markers, no shorthand binding: the subprocess sees the
    shipped registry and an interactive run whatever the developer's or CI's environment."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("RFE_CREATOR_")
        and k not in type_registry.HEADLESS_MARKER_VARS
        and k not in {"JIRA_PROJECT", "JIRA_ISSUE_TYPE"}
    }
    env.update(extra)
    return env


def _run(*args, env=None):
    return subprocess.run(
        ["python3", SCRIPT, *args], capture_output=True, text=True, env=_clean_env(**(env or {}))
    )


def _protocol_only(stdout):
    return all(line.startswith(PROTOCOL_PREFIXES) for line in stdout.splitlines())


class TestRegistryDerivedConstants:
    def test_known_fields_are_the_pre_registry_literals(self):
        # Behaviour-neutral migration: the sets every batch author relied on are unchanged.
        assert validate_batch_input.KNOWN_FIELDS["rfe"] == {
            "prompt",
            "priority",
            "labels",
            "clarifying_context",
        }
        assert validate_batch_input.KNOWN_FIELDS["initiative"] == {
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

    def test_allowed_priorities_are_per_type_and_equal_the_task_schemas(self):
        # PR-3b: the enum of the RESOLVED type. Both shipped enums are the same list today, and
        # each equals its task schema (same error message text whichever type resolved).
        literal = ["Blocker", "Critical", "Major", "Normal", "Minor", "Undefined"]
        assert list(validate_batch_input.ALLOWED_PRIORITIES) == REG.names()
        for name in REG.names():
            enum = validate_batch_input.ALLOWED_PRIORITIES[name]
            assert enum == literal
            assert enum == REG.get(name).get("schema.task.priority.enum")
            assert enum == artifact_utils.SCHEMAS[f"{name}-task"]["priority"]["enum"]

    def test_parent_key_pattern_is_the_registry_join_for_every_type(self):
        # PR-1 checklist Q14, reconciled in PR-3b: one join (Descriptor.parent_key_pattern)
        # behind the batch validator and the task schema, so they cannot diverge again.
        for name in REG.names():
            desc = REG.get(name)
            pattern = validate_batch_input.PARENT_KEY_PATTERN[name]
            assert pattern == desc.parent_key_pattern
            assert pattern == artifact_utils.SCHEMAS[f"{name}-task"]["parent_key"]["pattern"]
            assert validate_batch_input.PARENT_KEY_PATTERNS[name] == desc.get(
                "conventions.parent_key_patterns"
            )
        assert validate_batch_input.PARENT_KEY_PATTERN["initiative"] == (
            r"^(RHAISTRAT-\d+|RHOAIENG-\d+|INIT-\d+)$"
        )
        assert re.match(validate_batch_input.PARENT_KEY_PATTERN["initiative"], "INIT-001")

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
            "schema_version: 1\n"
            "type: docs\n"
            "identity:\n"
            "  tracker: jira\n"
            "  jira: {project: DOCS, issue_type: Task, key_prefixes: ['DOCS-']}\n"
            "  local_prefix: 'DOC-'\n"
            "  local_id_pattern: '^DOC-\\d+$'\n"
            "  id_field: doc_id\n"
            "batch: {extra_fields: [audience]}\n"
        )
        env = {
            "RFE_CREATOR_EXTRA_TYPES": str(root),
            "RFE_CREATOR_EXTRA_TYPES_ALLOWLIST": str(root),
        }
        path = str(tmp_path / "batch.yaml")
        _write(path, "- prompt: Write the guide\n  audience: admins\n  parent_key: RHAISTRAT-1\n")
        result = _run(path, "--type", "docs", env=env)
        assert result.returncode == 0, result.stderr
        assert "WARNING: entry 0: unknown field 'parent_key'" in result.stdout
        assert "audience" not in result.stdout
        assert result.stderr == "TYPE RESOLVED: docs (--type)\n"

    def test_a_drop_in_without_a_task_schema_keeps_the_rfe_priorities(self, tmp_path):
        root = tmp_path / "types"
        (root / "docs").mkdir(parents=True)
        (root / "docs" / "type.yaml").write_text("schema_version: 1\ntype: docs\n")
        env = {
            "RFE_CREATOR_EXTRA_TYPES": str(root),
            "RFE_CREATOR_EXTRA_TYPES_ALLOWLIST": str(root),
        }
        code = (
            "import sys; sys.path.insert(0, 'scripts'); import validate_batch_input as v; "
            "print(v.ALLOWED_PRIORITIES['docs'] == v.ALLOWED_PRIORITIES['rfe'])"
        )
        result = subprocess.run(
            ["python3", "-c", code],
            capture_output=True,
            text=True,
            env=_clean_env(**env),
            cwd=os.path.join(os.path.dirname(__file__), ".."),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "True"


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

    def test_invalid_priority_text_is_the_pre_registry_rendering(self):
        errors, _ = validate_entries([{"prompt": "x", "priority": "High"}])
        assert errors == [
            "entry 0: 'priority' 'High' is not one of "
            "['Blocker', 'Critical', 'Major', 'Normal', 'Minor', 'Undefined']"
        ]
        assert (
            errors
            == validate_entries([{"prompt": "x", "priority": "High"}], entry_type="initiative")[0]
        )

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

    def test_parent_key_init_accepted(self):
        # PR-3b: the batch rule is the task schema's list, which always accepted a local
        # INIT- parent (a child of an Initiative created in the same run).
        errors, warnings = validate_entries(
            [{"prompt": "x", "parent_key": "INIT-001"}],
            entry_type="initiative",
        )
        assert errors == []
        assert warnings == []

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

    def test_parent_key_error_text_is_rendered_from_the_descriptor(self):
        # No hand-written key names: the alternatives are conventions.parent_key_patterns.
        for bad in ("BAD-123", 123, ""):
            errors, _ = validate_entries(
                [{"prompt": "x", "parent_key": bad}], entry_type="initiative"
            )
            assert errors == [INIT_PARENT_ERROR], bad
        alternatives = REG.get("initiative").get("conventions.parent_key_patterns")
        assert INIT_PARENT_ERROR == "entry 0: 'parent_key' must match one of " + ", ".join(
            alternatives
        )

    def test_rfe_entry_with_a_malformed_parent_key_is_warning_only(self):
        # rfe declares no parent_key batch field: the key is unknown, never a pattern error.
        errors, warnings = validate_entries(
            [{"prompt": "x", "parent_key": "BAD-123"}], entry_type="rfe"
        )
        assert errors == []
        assert warnings == ["entry 0: unknown field 'parent_key'"]

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

    def test_type_initiative_accepts_an_init_parent(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "- prompt: Improve onboarding\n  parent_key: INIT-001\n")
        result = _run(path, "--type", "initiative", "--strict")
        assert result.returncode == 0, result.stdout
        assert result.stdout == CLEAN_STDOUT

    def test_type_initiative_reports_the_rendered_parent_key_error(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "- prompt: Improve onboarding\n  parent_key: BAD-1\n")
        result = _run(path, "--type", "initiative")
        assert result.returncode == 1
        assert (
            result.stdout
            == f"ERROR_COUNT=1\nWARNING_COUNT=0\nERROR: {INIT_PARENT_ERROR}\nVALID=false\n"
        )

    def test_type_rfe_warns_on_parent_key(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "- prompt: Improve onboarding\n  parent_key: RHAISTRAT-100\n")
        result = subprocess.run(
            ["python3", SCRIPT, path, "--strict"], capture_output=True, text=True
        )
        assert result.returncode == 1
        assert "WARNING_COUNT=1" in result.stdout


class TestMappingForm:
    def test_valid_mapping_resolves_its_type(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, MAPPING_FORM)
        result = _run(path, "--strict")
        assert result.returncode == 0, result.stdout
        assert result.stdout == CLEAN_STDOUT
        assert result.stderr == "TYPE RESOLVED: initiative (batch type)\n"

    def test_mapping_type_rfe_keeps_parent_key_a_warning(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "type: rfe\nitems:\n- prompt: Users need X\n  parent_key: RHAISTRAT-100\n")
        result = _run(path)
        assert result.returncode == 0
        assert result.stdout == (
            "ERROR_COUNT=0\nWARNING_COUNT=1\nWARNING: entry 0: unknown field 'parent_key'\n"
            "VALID=true\n"
        )
        assert result.stderr == "TYPE RESOLVED: rfe (batch type)\n"

    def test_mapping_form_validates_the_items(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "type: initiative\nitems:\n- prompt: x\n  priority: High\n- prompt: x\n")
        result = _run(path)
        assert result.returncode == 1
        assert "ERROR_COUNT=1\nWARNING_COUNT=1\n" in result.stdout
        assert "ERROR: entry 0: 'priority' 'High' is not one of" in result.stdout
        assert "WARNING: entries [0, 1]: duplicate prompt 'x'" in result.stdout

    def test_unknown_mapping_type_is_a_protocol_error(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "type: epic\nitems:\n- prompt: x\n")
        result = _run(path)
        assert result.returncode == 1
        assert result.stdout == (
            "ERROR_COUNT=1\nWARNING_COUNT=0\n"
            f"ERROR: batch: unknown type 'epic' ({path} type:); registered types: rfe, initiative\n"
            "VALID=false\n"
        )
        assert result.stderr == ""

    def test_empty_items_is_invalid_content(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "type: rfe\nitems: []\n")
        result = _run(path)
        assert result.returncode == 1
        assert "ERROR_COUNT=1" in result.stdout
        assert "at least one entry" in result.stdout

    @pytest.mark.parametrize(
        "content, match",
        [
            ("type: initiative\n", r"expected a list of items .* got a mapping with keys type"),
            ("items:\n- prompt: x\n", r"expected a list of items .* got a mapping with keys items"),
            (
                "type: initiative\nitems:\n- prompt: x\nextra: 1\n",
                r"exactly the keys 'type' and 'items'; unexpected key\(s\): extra",
            ),
            ("type: initiative\nitems: nope\n", r"'items' must be a list, got str"),
            ("type: initiative\nitems:\n  prompt: x\n", r"'items' must be a list, got"),
            ("type: 5\nitems: []\n", r"'type' must be a non-empty string, got 5"),
            ("type: ''\nitems: []\n", r"'type' must be a non-empty string"),
        ],
    )
    def test_malformed_mapping_roots_are_usage_errors(self, tmp_path, content, match):
        # Root neither form: the existing exit-2 path (stderr), nothing on stdout.
        path = str(tmp_path / "batch.yaml")
        _write(path, content)
        result = _run(path)
        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr.startswith("ERROR: ")
        assert re.search(match, result.stderr), result.stderr


class TestTypeConflicts:
    def test_explicit_type_disagreeing_with_the_mapping_is_a_protocol_error(self, tmp_path):
        # PR-3 D1: both are explicit, so neither is guessed.
        path = str(tmp_path / "batch.yaml")
        _write(path, MAPPING_FORM)
        result = _run(path, "--type", "rfe")
        assert result.returncode == 1
        lines = result.stdout.splitlines()
        assert lines[:2] == ["ERROR_COUNT=1", "WARNING_COUNT=0"]
        assert lines[2].startswith(
            f"ERROR: batch: --type rfe disagrees with {path} type: initiative"
        )
        assert "D1" in lines[2]
        assert lines[3:] == ["VALID=false"]
        assert result.stderr == ""

    def test_explicit_type_agreeing_with_the_mapping_is_rung_one(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, MAPPING_FORM)
        result = _run(path, "--type", "initiative", "--strict")
        assert result.returncode == 0
        assert result.stdout == CLEAN_STDOUT
        assert result.stderr == "TYPE RESOLVED: initiative (--type)\n"

    @pytest.mark.parametrize(
        "content",
        [
            "- prompt: x\n  type: rfe\n",
            "- prompt: x\n- prompt: y\n  type: initiative\n",
            "type: rfe\nitems:\n- prompt: x\n  type: rfe\n",
        ],
        ids=["list-form", "list-form-second-item", "mapping-form"],
    )
    def test_per_item_type_is_a_protocol_error(self, tmp_path, content):
        # PR-3 D2: a run is single-typed; the message tells the author to split the batch.
        path = str(tmp_path / "batch.yaml")
        _write(path, content)
        result = _run(path)
        assert result.returncode == 1
        lines = result.stdout.splitlines()
        assert lines[:2] == ["ERROR_COUNT=1", "WARNING_COUNT=0"]
        assert lines[2].startswith("ERROR: batch: ")
        assert "per-item 'type' key" in lines[2]
        assert "split the batch by type" in lines[2]
        assert lines[3:] == ["VALID=false"]
        assert result.stderr == ""

    def test_per_item_type_is_rejected_even_when_it_agrees_with_type(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "- prompt: x\n  type: rfe\n")
        result = _run(path, "--type", "rfe")
        assert result.returncode == 1
        assert "ERROR: batch: " in result.stdout and "per-item 'type' key" in result.stdout


class TestResolveLineOnStderr:
    """PR-3 D3: the line only when a non-default rung decided, never on stdout."""

    def test_legacy_default_prints_nothing_beyond_the_protocol(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "- prompt: Users need X\n  priority: Major\n")
        result = _run(path, "--strict")
        assert result.returncode == 0
        assert result.stdout == CLEAN_STDOUT
        assert result.stderr == ""

    def test_explicit_type_prints_the_line(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, LIST_FORM)
        result = _run(path, "--type", "initiative", "--strict")
        assert result.returncode == 0
        assert result.stdout == CLEAN_STDOUT
        assert result.stderr == "TYPE RESOLVED: initiative (--type)\n"

    def test_explicit_type_rfe_is_a_non_default_rung_too(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, "- prompt: Users need X\n")
        result = _run(path, "--type", "rfe")
        assert result.returncode == 0
        assert result.stdout == CLEAN_STDOUT
        assert result.stderr == "TYPE RESOLVED: rfe (--type)\n"

    def test_mapping_type_prints_the_line(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, MAPPING_FORM)
        result = _run(path)
        assert result.stderr == "TYPE RESOLVED: initiative (batch type)\n"

    @pytest.mark.parametrize(
        "content, args",
        [
            ("- prompt: x\n", []),
            ("- prompt: x\n", ["--type", "initiative"]),
            (MAPPING_FORM, []),
            (MAPPING_FORM, ["--type", "rfe"]),
            ("type: epic\nitems: [{prompt: x}]\n", []),
            ("- prompt: x\n  type: rfe\n", []),
        ],
    )
    def test_stdout_is_protocol_only(self, tmp_path, content, args):
        path = str(tmp_path / "batch.yaml")
        _write(path, content)
        result = _run(path, *args)
        assert result.returncode in (0, 1)
        assert result.stdout.endswith("\nVALID=true\n") or result.stdout.endswith("\nVALID=false\n")
        assert _protocol_only(result.stdout), result.stdout


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


MALFORMED_OVERRIDES = [
    {"JIRA_PROJECT": "rhairfe"},
    {"JIRA_PROJECT": "bad project"},
    {"RFE_CREATOR_BINDING_RFE_PROJECT": "foo bar"},
    {"RFE_CREATOR_BINDING_RFE_LOCAL_PREFIX": "X"},
    {"RFE_CREATOR_BINDING_INITIATIVE_PROJECT": "bad!"},
]


class TestLegacyListNeutrality:
    """The list form is byte-identical to main whatever the environment or the file's channel:
    the file is read once, a string item is an entry error, the binding is never read."""

    def test_a_pipe_is_read_once(self):
        # /dev/stdin cannot be read twice: parse once, resolve over the parsed items.
        result = subprocess.run(
            ["python3", SCRIPT, "/dev/stdin", "--strict"],
            input="- prompt: Users need X\n  priority: Major\n",
            capture_output=True,
            text=True,
            env=_clean_env(),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == CLEAN_STDOUT
        assert result.stderr == ""

    def test_a_pipe_with_an_explicit_type(self):
        result = subprocess.run(
            ["python3", SCRIPT, "/dev/stdin", "--type", "initiative", "--strict"],
            input="- prompt: Users need X\n  parent_key: INIT-001\n",
            capture_output=True,
            text=True,
            env=_clean_env(),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == CLEAN_STDOUT
        assert result.stderr == "TYPE RESOLVED: initiative (--type)\n"

    @pytest.mark.parametrize("env", MALFORMED_OVERRIDES, ids=lambda e: "=".join(*e.items()))
    @pytest.mark.parametrize("args", [[], ["--type", "initiative"]], ids=["default", "--type"])
    def test_a_malformed_binding_override_is_not_read(self, tmp_path, env, args):
        # main never read these variables; the verdict-only resolve (binding=False) does not
        # either, so the run is identical to a clean-environment run — no exit 2, no traceback.
        path = str(tmp_path / "batch.yaml")
        _write(path, "- prompt: Users need X\n")
        clean = _run(path, *args, "--strict")
        result = _run(path, *args, "--strict", env=env)
        assert (result.returncode, result.stdout, result.stderr) == (
            clean.returncode,
            clean.stdout,
            clean.stderr,
        )
        assert result.returncode == 0
        assert result.stdout == CLEAN_STDOUT

    def test_a_well_formed_override_adds_no_binding_clause(self, tmp_path):
        # The line names the rung only: this script does not apply the binding it would name.
        path = str(tmp_path / "batch.yaml")
        _write(path, "- prompt: Users need X\n")
        result = _run(path, "--type", "initiative", "--strict", env={"JIRA_PROJECT": "PLAN"})
        assert result.returncode == 0
        assert result.stderr == "TYPE RESOLVED: initiative (--type)\n"
        result = _run(path, "--strict", env={"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"})
        assert (result.returncode, result.stdout, result.stderr) == (0, CLEAN_STDOUT, "")

    @pytest.mark.parametrize(
        "content, args, env, bad",
        [
            ("- just a string\n- prompt: ok\n", [], {"CI": "true"}, [(0, "str")]),
            ("- just a string\n- prompt: ok\n", [], {"RFE_CREATOR_HEADLESS": "1"}, [(0, "str")]),
            ("- just a string\n- prompt: ok\n", [], {}, [(0, "str")]),
            ("- RHOAIENG-123\n- prompt: ok\n", ["--type", "rfe"], {"CI": "true"}, [(0, "str")]),
            ("- RHAIRFE-123\n- prompt: ok\n", [], {}, [(0, "str")]),
            ("- alpha\n- beta\n", [], {"CI": "true"}, [(0, "str"), (1, "str")]),
            ("- prompt: ok\n- 42\n", [], {"CI": "true"}, [(1, "int")]),
        ],
        ids=["string-ci", "string-headless", "string", "foreign-key", "own-key", "two", "int"],
    )
    def test_a_string_item_is_an_entry_error_not_an_id(self, tmp_path, content, args, env, bad):
        # main's error, not a D5 / conflicting-signal verdict: a speedrun item is a mapping, so
        # a bare string is a malformed entry and never an id signal (items_are_ids=False).
        path = str(tmp_path / "batch.yaml")
        _write(path, content)
        result = _run(path, *args, env=env)
        assert result.returncode == 1
        assert result.stdout == (
            f"ERROR_COUNT={len(bad)}\nWARNING_COUNT=0\n"
            + "".join(f"ERROR: entry {i}: must be a mapping, got {kind}\n" for i, kind in bad)
            + "VALID=false\n"
        )
        assert result.stderr == ("TYPE RESOLVED: rfe (--type)\n" if args else "")


class TestParentKeyPatternIsTheEffectiveJoin:
    """PARENT_KEY_PATTERN is ``Descriptor.parent_key_pattern_effective`` — the task schema's
    join: under a project override the effective write prefix is an alternative, so an entry
    whose parent was fetched under the override validates; with no override (and under a
    malformed one, which falls back) it is the descriptor join and the protocol is unchanged."""

    BATCH = "type: initiative\nitems:\n- prompt: Improve onboarding\n  parent_key: KONFLUX-1\n"

    def _run(self, path, **env):
        return subprocess.run(
            ["python3", SCRIPT, path], capture_output=True, text=True, env=_clean_env(**env)
        )

    def test_an_overridden_parent_validates_under_the_override_only(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, self.BATCH)
        plain = self._run(path)
        assert plain.returncode == 1
        assert "ERROR: entry 0: 'parent_key' must match one of RHAISTRAT-" in plain.stdout
        overridden = self._run(path, RFE_CREATOR_BINDING_INITIATIVE_PROJECT="KONFLUX")
        assert overridden.returncode == 0, overridden.stderr
        assert overridden.stdout == "ERROR_COUNT=0\nWARNING_COUNT=0\nVALID=true\n"
        assert overridden.stderr == "TYPE RESOLVED: initiative (batch type)\n"
        # Another type's override does not reach this type's pattern.
        other = self._run(path, RFE_CREATOR_BINDING_RFE_PROJECT="KONFLUX")
        assert other.returncode == 1 and other.stdout == plain.stdout

    def test_a_malformed_override_falls_back_to_the_descriptor_join(self, tmp_path):
        path = str(tmp_path / "batch.yaml")
        _write(path, self.BATCH)
        plain = self._run(path)
        malformed = self._run(path, RFE_CREATOR_BINDING_INITIATIVE_PROJECT="lower")
        assert (malformed.returncode, malformed.stdout) == (plain.returncode, plain.stdout)
        assert "Traceback" not in malformed.stderr
