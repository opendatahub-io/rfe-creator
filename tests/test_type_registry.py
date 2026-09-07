#!/usr/bin/env python3
"""Tests for scripts/type_registry.py — the import-clean work-item type registry (PR-1).

design-proposals/work-item-types-unified.md §3.2 / §3.2.1 / §10 item 1. The registry
is inert in PR-1 (no production script imports it), so these tests are the only
consumer contract: discovery over one or more roots, the ``names()`` order today's
argparse ``choices`` lists use, dotted access, the two ``dirs`` spellings (Q13), label
flattening, the effective binding overlay (§3.2.1) and the CLI exit codes.

Every registry here is built with ``load(root=..., extra_roots=[], env={})`` so a
developer's ``RFE_CREATOR_EXTRA_TYPES`` / ``RFE_CREATOR_BINDING_*`` never leaks in;
subprocess tests scrub the same variables.
"""

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import type_registry  # noqa: E402
from type_registry import (  # noqa: E402
    MISSING,
    Descriptor,
    RegistryError,
    binding_env_var,
    load,
    parse_extra_roots,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TYPES_ROOT = REPO_ROOT / "types"
SCRIPT = "scripts/type_registry.py"
EPIC_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "types" / "epic" / "type.yaml"
SHIPPED = ("rfe", "initiative")


# ── helpers ──────────────────────────────────────────────────────────────────────


def _read_yaml(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    return path


def _copy_types(dest, names=SHIPPED, with_schema=False):
    """Copy shipped descriptors into ``dest`` (a fresh root the tests may mutate)."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copytree(TYPES_ROOT / name, dest / name)
    if with_schema:
        shutil.copytree(TYPES_ROOT / "_schema", dest / "_schema")
    return dest


def _minimal(name, project, issue_type="Task", local_prefix=None, **identity_extra):
    """The smallest mapping the loader accepts: ``type:`` + a coherent identity block.

    Shape validation is validate_types' job (gate 1); the loader is deliberately
    permissive so mutated descriptors reach the JSON-Schema validator.
    """
    identity = {
        "tracker": "jira",
        "jira": {"project": project, "issue_type": issue_type, "key_prefixes": [f"{project}-"]},
        "local_prefix": local_prefix or f"{name.upper()}-",
        "local_id_pattern": rf"^{(local_prefix or name.upper() + '-')}\d+$",
        "id_field": f"{name}_id",
    }
    identity.update(identity_extra)
    return {"schema_version": 1, "type": name, "identity": identity}


def _add_type(root, name, data=None, **kw):
    return _write_yaml(root / name / "type.yaml", data or _minimal(name, **kw))


def _shipped():
    return load(root=TYPES_ROOT, extra_roots=[], env={})


def _clean_env(**extra):
    """The developer's seams AND the headless/CI markers stay out of subprocess tests: under
    GitHub Actions ``CI``/``GITHUB_ACTIONS`` would otherwise gate RFE_CREATOR_EXTRA_TYPES."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("RFE_CREATOR_") and k not in type_registry.HEADLESS_MARKER_VARS
    }
    env.update(extra)
    return env


def _cli(*args, env=None, cwd=REPO_ROOT):
    """Run ``python3 scripts/type_registry.py`` by its relative path from the repo root."""
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_clean_env(**(env or {})),
    )


# ── discovery ────────────────────────────────────────────────────────────────────


class TestDiscovery:
    def test_shipped_root_holds_exactly_the_two_types(self):
        reg = _shipped()
        assert reg.names() == ["rfe", "initiative"]
        assert len(reg) == 2
        # _schema/ is a support dir and README.md is a file: neither is a type.
        assert "_schema" not in reg
        assert "README.md" not in reg
        assert reg.get("rfe").path == (TYPES_ROOT / "rfe" / "type.yaml").resolve()

    def test_underscore_and_dot_dirs_are_skipped(self, tmp_path):
        root = _copy_types(tmp_path / "types", names=("rfe",))
        _add_type(root, "_draft", project="DRAFT")
        _add_type(root, ".hidden", project="HIDDEN")
        assert load(root=root, extra_roots=[], env={}).names() == ["rfe"]

    def test_dirs_without_descriptor_and_stray_files_are_skipped(self, tmp_path):
        root = _copy_types(tmp_path / "types", names=("rfe",))
        (root / "empty").mkdir()
        (root / "notes").mkdir()
        (root / "notes" / "README.md").write_text("# not a descriptor\n")
        (root / "stray.yaml").write_text("type: stray\n")
        assert load(root=root, extra_roots=[], env={}).names() == ["rfe"]

    def test_two_roots_are_merged_in_order(self, tmp_path):
        primary = _copy_types(tmp_path / "primary", names=("rfe",))
        extra = _copy_types(tmp_path / "extra", names=("initiative",))
        reg = load(root=primary, extra_roots=[extra], env={})
        assert reg.names() == ["rfe", "initiative"]
        assert reg.roots == [primary, extra]
        assert reg.get("initiative").path == (extra / "initiative" / "type.yaml").resolve()

    def test_duplicate_type_across_roots_is_an_error(self, tmp_path):
        primary = _copy_types(tmp_path / "primary", names=("rfe",))
        extra = _copy_types(tmp_path / "extra", names=("rfe",))
        with pytest.raises(RegistryError, match=r"duplicate type 'rfe'") as excinfo:
            load(root=primary, extra_roots=[extra], env={})
        assert str(primary / "rfe" / "type.yaml") in str(excinfo.value)
        assert str(extra / "rfe" / "type.yaml") in str(excinfo.value)

    def test_registry_error_is_a_value_error(self):
        assert issubclass(RegistryError, ValueError)

    def test_missing_primary_root_is_an_error(self, tmp_path):
        with pytest.raises(RegistryError, match="type root not found"):
            load(root=tmp_path / "nope", extra_roots=[], env={})

    def test_missing_extra_root_is_an_error(self, tmp_path):
        with pytest.raises(RegistryError, match="type root not found"):
            load(root=TYPES_ROOT, extra_roots=[tmp_path / "nope"], env={})

    def test_type_field_must_equal_directory_name(self, tmp_path):
        root = tmp_path / "types"
        shutil.copytree(TYPES_ROOT / "rfe", root / "feature")
        with pytest.raises(RegistryError, match="'type: rfe' does not match its directory name"):
            load(root=root, extra_roots=[], env={})

    def test_non_mapping_descriptor_is_an_error(self, tmp_path):
        root = tmp_path / "types"
        (root / "listy").mkdir(parents=True)
        (root / "listy" / "type.yaml").write_text("- just\n- a list\n")
        with pytest.raises(RegistryError, match="descriptor must be a mapping, got list"):
            load(root=root, extra_roots=[], env={})

    def test_invalid_yaml_is_an_error(self, tmp_path):
        root = tmp_path / "types"
        (root / "broken").mkdir(parents=True)
        (root / "broken" / "type.yaml").write_text("type: broken\nidentity: [unclosed\n")
        with pytest.raises(RegistryError, match="invalid YAML"):
            load(root=root, extra_roots=[], env={})

    def test_symlink_escaping_the_root_is_rejected(self, tmp_path):
        outside = _write_yaml(tmp_path / "elsewhere" / "type.yaml", _minimal("esc", "ESC"))
        root = tmp_path / "types"
        (root / "esc").mkdir(parents=True)
        os.symlink(outside, root / "esc" / "type.yaml")
        with pytest.raises(RegistryError, match="resolves outside its root"):
            load(root=root, extra_roots=[], env={})

    def test_load_returns_a_fresh_registry_each_call(self):
        assert _shipped() is not _shipped()

    def test_loader_does_not_gate_shape(self, tmp_path):
        """Shape problems are gate-1 findings (validate_types), not load errors."""
        root = tmp_path / "types"
        _add_type(root, "bare", data={"type": "bare"})
        reg = load(root=root, extra_roots=[], env={})
        assert reg.names() == ["bare"]
        assert reg.get("bare").data == {"type": "bare"}


# ── enumeration ──────────────────────────────────────────────────────────────────


class TestEnumeration:
    def test_rfe_first_then_the_rest_sorted(self, tmp_path):
        root = tmp_path / "types"
        for name in ("zeta", "rfe", "alpha", "mid"):
            _add_type(root, name, project=name.upper())
        reg = load(root=root, extra_roots=[], env={})
        assert reg.names() == ["rfe", "alpha", "mid", "zeta"]
        assert reg.choices() == reg.names()

    def test_without_rfe_names_are_sorted(self, tmp_path):
        root = tmp_path / "types"
        for name in ("zeta", "alpha"):
            _add_type(root, name, project=name.upper())
        assert load(root=root, extra_roots=[], env={}).names() == ["alpha", "zeta"]

    def test_shipped_order_matches_todays_argparse_choices(self):
        # check_conflicts.py:60, batch_summary.py:22, error_collect.py:67, ... all spell
        # choices=["rfe", "initiative"]; the registry must reproduce that order (PR-2 swaps
        # the literal for registry.choices()).
        assert _shipped().choices() == ["rfe", "initiative"]

    def test_iteration_follows_names_order(self):
        reg = _shipped()
        assert [d.name for d in reg] == reg.names()
        assert all(isinstance(d, Descriptor) for d in reg)

    def test_contains_and_len(self):
        reg = _shipped()
        assert "rfe" in reg
        assert "initiative" in reg
        assert "epic" not in reg
        assert len(reg) == 2

    def test_get_unknown_type_lists_available_names(self):
        with pytest.raises(KeyError) as excinfo:
            _shipped().get("epic")
        assert excinfo.value.args[0] == "unknown type 'epic'; available: rfe, initiative"

    def test_get_unknown_type_on_empty_registry(self, tmp_path):
        root = tmp_path / "types"
        root.mkdir()
        with pytest.raises(KeyError, match=r"available: \(none\)"):
            load(root=root, extra_roots=[], env={}).get("rfe")


# ── Descriptor.get ────────────────────────────────────────────────────────────────


class TestDescriptorGet:
    def test_dotted_path(self):
        desc = _shipped().get("rfe")
        assert desc.get("conventions.labels.split_quarantine") == "rfe-creator-split-quarantine"
        assert desc.get("identity.jira.project") == "RHAIRFE"
        assert desc.get("identity") is desc.data["identity"]

    def test_default_when_absent(self):
        desc = _shipped().get("rfe")
        assert desc.get("conventions.labels.processing", None) is None
        assert desc.get("no.such.path", "fallback") == "fallback"

    def test_key_error_when_absent_without_default(self):
        desc = _shipped().get("rfe")
        with pytest.raises(KeyError) as excinfo:
            desc.get("conventions.labels.processing")
        assert (
            excinfo.value.args[0] == "rfe: no such descriptor field 'conventions.labels.processing'"
        )

    def test_null_value_is_returned_not_defaulted(self):
        # initiative pipeline.rubric.export is a legitimate null (nothing exports it);
        # the MISSING sentinel keeps "absent" distinct from "null".
        desc = _shipped().get("initiative")
        assert desc.get("pipeline.rubric.export") is None
        assert desc.get("pipeline.rubric.export", "default") is None
        assert MISSING is not None

    def test_integer_segments_index_lists(self):
        desc = _shipped().get("initiative")
        assert desc.get("pipeline.dimensions.0.name") == "feasibility"
        assert desc.get("pipeline.dimensions.1.name") == "alignment"
        assert desc.get("schema.review.score_fields.2") == "scope"

    def test_list_index_out_of_range(self):
        desc = _shipped().get("rfe")
        assert desc.get("pipeline.dimensions.5.name", "none") == "none"
        with pytest.raises(KeyError):
            desc.get("pipeline.dimensions.5")

    def test_traversing_into_a_scalar_is_absent(self):
        desc = _shipped().get("rfe")
        assert desc.get("identity.local_prefix.more", "x") == "x"
        with pytest.raises(KeyError):
            desc.get("identity.local_prefix.more")

    def test_repr_names_type_and_path(self):
        desc = _shipped().get("rfe")
        assert repr(desc).startswith("Descriptor('rfe', path=")


# ── projections ───────────────────────────────────────────────────────────────────


class TestDescriptorProjections:
    def test_identity_properties_rfe(self):
        desc = _shipped().get("rfe")
        assert desc.tracker == "jira"
        assert desc.key_prefixes == ["RHAIRFE-"]
        assert desc.write_prefix == "RHAIRFE-"
        assert desc.local_prefix == "RFE-"
        assert desc.local_id_pattern == r"^RFE-\d+$"
        assert desc.id_field == "rfe_id"
        assert desc.score_fields == ["what", "why", "open_to_how", "not_a_task", "right_sized"]

    def test_identity_properties_initiative(self):
        desc = _shipped().get("initiative")
        assert desc.key_prefixes == ["RHOAIENG-"]
        assert desc.write_prefix == "RHOAIENG-"
        assert desc.local_prefix == "INIT-"
        assert desc.local_id_pattern == r"^INIT-\d+$"
        assert desc.id_field == "initiative_id"
        assert desc.score_fields == ["what", "why", "scope", "open_to_how", "right_sized"]

    def test_list_properties_are_copies(self):
        desc = _shipped().get("rfe")
        desc.key_prefixes.append("X-")
        desc.score_fields.append("x")
        assert desc.key_prefixes == ["RHAIRFE-"]
        assert desc.score_fields == ["what", "why", "open_to_how", "not_a_task", "right_sized"]

    def test_dirs_artifacts_form_is_the_stored_form(self):
        reg = _shipped()
        assert reg.get("rfe").dirs() == {
            "tasks": "artifacts/rfe-tasks",
            "originals": "artifacts/rfe-originals",
            "reviews": "artifacts/rfe-reviews",
        }
        assert reg.get("initiative").dirs("artifacts") == {
            "tasks": "artifacts/initiatives",
            "originals": "artifacts/initiative-originals",
            "reviews": "artifacts/initiative-reviews",
        }

    def test_dirs_bare_form_strips_the_artifacts_component(self):
        # Q13: submit.py holds the bare spelling, pipeline_state.py the artifacts/ one.
        reg = _shipped()
        assert reg.get("rfe").dirs("bare") == {
            "tasks": "rfe-tasks",
            "originals": "rfe-originals",
            "reviews": "rfe-reviews",
        }
        assert reg.get("initiative").dirs(form="bare") == {
            "tasks": "initiatives",
            "originals": "initiative-originals",
            "reviews": "initiative-reviews",
        }

    def test_dirs_unknown_form_is_a_value_error(self):
        with pytest.raises(ValueError, match="unknown dirs form 'relative'"):
            _shipped().get("rfe").dirs("relative")

    def test_dirs_returns_a_fresh_mapping(self):
        desc = _shipped().get("rfe")
        desc.dirs()["tasks"] = "mutated"
        assert desc.dirs()["tasks"] == "artifacts/rfe-tasks"

    def test_bare_dir_only_strips_a_leading_artifacts_component(self):
        assert type_registry._bare_dir("artifacts/rfe-tasks") == "rfe-tasks"
        assert type_registry._bare_dir("rfe-tasks") == "rfe-tasks"
        assert type_registry._bare_dir("my-artifacts/x") == "my-artifacts/x"

    def test_labels_are_flattened_with_dotted_keys(self):
        labels = _shipped().get("rfe").labels
        assert labels["rubric_pass"] == "rfe-creator-autofix-rubric-pass"
        assert labels["feasibility.feasible"] == "rfe-creator-feasibility-pass"
        assert labels["feasibility.infeasible"] == "rfe-creator-feasibility-fail"
        assert labels["feasibility.indeterminate"] == "rfe-creator-feasibility-unknown"
        assert "feasibility" not in labels
        assert not any(isinstance(v, dict) for v in labels.values())
        assert "alignment.strong" not in labels  # rfe has no alignment dimension

    def test_labels_nested_form_stays_reachable(self):
        desc = _shipped().get("initiative")
        assert desc.labels["alignment.strong"] == "initiative-alignment-strong"
        assert desc.get("conventions.labels.alignment") == {
            "strong": "initiative-alignment-strong",
            "partial": "initiative-alignment-partial",
            "weak": "initiative-alignment-weak",
        }

    def test_labels_pass_lists_through_unchanged(self):
        # The epic fixture uses the reserved list-valued key `templates`.
        desc = Descriptor("epic", _read_yaml(EPIC_FIXTURE), path=EPIC_FIXTURE)
        templates = desc.labels["templates"]
        assert isinstance(templates, list) and len(templates) == 2
        assert templates[0] == {
            "from_field": "implementation_type",
            "pattern": "epic-creator-impl-{value}",
        }
        assert desc.labels["auto_created"] == "epic-creator-auto-created"

    def test_github_style_binding_exposes_alias_prefix(self):
        data = {
            "type": "gh",
            "identity": {
                "tracker": "github",
                "github": {"repo": "acme/widgets", "kind": "issue", "alias_prefix": "GH-"},
                "local_prefix": "GHX-",
            },
        }
        desc = Descriptor("gh", data)
        assert desc.key_prefixes == ["GH-"]
        assert desc.write_prefix == "GH-"

    def test_binding_without_any_prefix(self):
        desc = Descriptor("gh", {"type": "gh", "identity": {"tracker": "github", "github": {}}})
        assert desc.key_prefixes == []
        assert desc.write_prefix is None

    def test_missing_tracker_block_is_a_key_error(self):
        desc = Descriptor("odd", {"type": "odd", "identity": {"tracker": "jira"}})
        with pytest.raises(KeyError, match="identity.jira is missing"):
            desc.key_prefixes  # noqa: B018 - property access is the assertion


# ── binding (§3.2.1) ──────────────────────────────────────────────────────────────

RFE_DESCRIPTOR_BINDING = {
    "tracker": "jira",
    "project": "RHAIRFE",
    "issue_type": "Feature Request",
    "key_prefixes": ["RHAIRFE-"],
    "split_link_type": "Work item split",
    "state_map": {
        "approved": "Approved",
        "close_superseded": {"transition": "Closed", "resolution": "Obsolete"},
    },
    "local_prefix": "RFE-",
    "source": "descriptor",
}


class TestBinding:
    def test_default_is_the_descriptor_binding(self):
        assert _shipped().get("rfe").binding() == RFE_DESCRIPTOR_BINDING

    def test_initiative_default(self):
        binding = _shipped().get("initiative").binding()
        assert binding["source"] == "descriptor"
        assert (binding["project"], binding["issue_type"]) == ("RHOAIENG", "Initiative")
        assert binding["key_prefixes"] == ["RHOAIENG-"]
        assert binding["local_prefix"] == "INIT-"

    def test_project_override_derives_write_prefix_and_keeps_read_prefixes(self):
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "ACME"}
        binding = _shipped().get("rfe").binding(env)
        assert binding["project"] == "ACME"
        assert binding["key_prefixes"] == ["ACME-", "RHAIRFE-"]
        assert binding["source"] == "env"
        # only the overridden field moves; everything else is verbatim
        assert binding["issue_type"] == "Feature Request"
        assert binding["local_prefix"] == "RFE-"
        assert binding["state_map"] == RFE_DESCRIPTOR_BINDING["state_map"]

    def test_project_override_equal_to_descriptor_does_not_duplicate_prefix(self):
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "RHAIRFE"}
        binding = _shipped().get("rfe").binding(env)
        assert binding["key_prefixes"] == ["RHAIRFE-"]
        assert binding["source"] == "env"  # set is set, even when equal

    def test_issue_type_override(self):
        env = {"RFE_CREATOR_BINDING_RFE_ISSUE_TYPE": "Story"}
        binding = _shipped().get("rfe").binding(env)
        assert binding["issue_type"] == "Story"
        assert binding["project"] == "RHAIRFE"
        assert binding["key_prefixes"] == ["RHAIRFE-"]
        assert binding["source"] == "env"

    def test_local_prefix_override(self):
        env = {"RFE_CREATOR_BINDING_RFE_LOCAL_PREFIX": "REQ-"}
        binding = _shipped().get("rfe").binding(env)
        assert binding["local_prefix"] == "REQ-"
        assert binding["source"] == "env"

    def test_all_three_overrides(self):
        env = {
            "RFE_CREATOR_BINDING_INITIATIVE_PROJECT": "PLAN",
            "RFE_CREATOR_BINDING_INITIATIVE_ISSUE_TYPE": "Epic",
            "RFE_CREATOR_BINDING_INITIATIVE_LOCAL_PREFIX": "PL-",
        }
        binding = _shipped().get("initiative").binding(env)
        assert (binding["project"], binding["issue_type"]) == ("PLAN", "Epic")
        assert binding["key_prefixes"] == ["PLAN-", "RHOAIENG-"]
        assert binding["local_prefix"] == "PL-"
        assert binding["source"] == "env"

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_blank_variables_count_as_unset(self, blank):
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": blank}
        assert _shipped().get("rfe").binding(env) == RFE_DESCRIPTOR_BINDING

    def test_overrides_are_type_scoped(self):
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "ACME"}
        reg = _shipped()
        assert reg.get("initiative").binding(env)["source"] == "descriptor"
        assert reg.get("initiative").binding(env)["project"] == "RHOAIENG"
        assert reg.get("rfe").binding(env)["project"] == "ACME"

    @pytest.mark.parametrize("value", ["acme", "1ACME", "AC ME", "ACME-"])
    def test_invalid_project_value_is_rejected(self, value):
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": value}
        with pytest.raises(RegistryError, match="RFE_CREATOR_BINDING_RFE_PROJECT"):
            _shipped().get("rfe").binding(env)

    @pytest.mark.parametrize("value", ["req-", "REQ", "-REQ-", "RE Q-"])
    def test_invalid_local_prefix_value_is_rejected(self, value):
        env = {"RFE_CREATOR_BINDING_RFE_LOCAL_PREFIX": value}
        with pytest.raises(RegistryError, match="expected an upper-case prefix ending in '-'"):
            _shipped().get("rfe").binding(env)

    def test_issue_type_accepts_any_non_empty_string(self):
        env = {"RFE_CREATOR_BINDING_RFE_ISSUE_TYPE": "feature request (legacy)"}
        assert _shipped().get("rfe").binding(env)["issue_type"] == "feature request (legacy)"

    def test_binding_never_mutates_the_descriptor(self):
        desc = _shipped().get("rfe")
        before = json.dumps(desc.data, sort_keys=True)
        desc.binding({"RFE_CREATOR_BINDING_RFE_PROJECT": "ACME"})
        assert json.dumps(desc.data, sort_keys=True) == before
        assert desc.key_prefixes == ["RHAIRFE-"]
        assert desc.binding()["source"] == "descriptor"

    def test_binding_is_detached_from_the_descriptor(self):
        """The returned dict must not alias identity.<tracker>: editing it (nested maps and
        lists included) leaves the descriptor and every later binding() untouched."""
        reg = _shipped()
        desc = reg.get("rfe")
        before = json.dumps(desc.data, sort_keys=True)
        binding = desc.binding({})
        binding["state_map"]["approved"] = "MUTATED"
        binding["state_map"]["close_superseded"]["resolution"] = "MUTATED"
        binding["key_prefixes"].append("BOGUS-")
        binding["project"] = "MUTATED"
        assert json.dumps(desc.data, sort_keys=True) == before
        assert desc.get("identity.jira.state_map.approved") == "Approved"
        fresh = desc.binding({})
        assert fresh["state_map"]["approved"] == "Approved"
        assert fresh["state_map"]["close_superseded"]["resolution"] == "Obsolete"
        assert fresh["key_prefixes"] == ["RHAIRFE-"]
        assert reg.bindings({})["rfe"] == RFE_DESCRIPTOR_BINDING

    def test_registry_env_is_handed_to_descriptors(self):
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "ACME"}
        reg = load(root=TYPES_ROOT, extra_roots=[], env=env)
        assert reg.get("rfe").binding() == reg.bindings()["rfe"]
        assert reg.get("rfe").binding()["project"] == "ACME"

    def test_explicit_env_wins_over_registry_env(self):
        reg = load(root=TYPES_ROOT, extra_roots=[], env={"RFE_CREATOR_BINDING_RFE_PROJECT": "A"})
        assert reg.get("rfe").binding({})["source"] == "descriptor"
        assert reg.bindings({"RFE_CREATOR_BINDING_RFE_PROJECT": "B"})["rfe"]["project"] == "B"

    def test_bindings_keyed_in_names_order(self):
        bindings = _shipped().bindings()
        assert list(bindings) == ["rfe", "initiative"]
        assert bindings["rfe"] == RFE_DESCRIPTOR_BINDING

    def test_descriptor_without_registry_env_reads_os_environ(self, monkeypatch):
        monkeypatch.setenv("RFE_CREATOR_BINDING_RFE_PROJECT", "OSENV")
        desc = Descriptor("rfe", _read_yaml(TYPES_ROOT / "rfe" / "type.yaml"))
        assert desc.binding()["project"] == "OSENV"
        monkeypatch.delenv("RFE_CREATOR_BINDING_RFE_PROJECT")
        assert desc.binding()["source"] == "descriptor"

    def test_binding_env_var_names(self):
        assert binding_env_var("rfe", "PROJECT") == "RFE_CREATOR_BINDING_RFE_PROJECT"
        assert binding_env_var("initiative", "issue_type") == (
            "RFE_CREATOR_BINDING_INITIATIVE_ISSUE_TYPE"
        )
        assert binding_env_var("docs-request", "LOCAL_PREFIX") == (
            "RFE_CREATOR_BINDING_DOCS_REQUEST_LOCAL_PREFIX"
        )

    def test_hyphenated_type_reads_its_underscored_variable(self, tmp_path):
        root = tmp_path / "types"
        _add_type(root, "docs-request", project="DOCS", local_prefix="DR-")
        env = {"RFE_CREATOR_BINDING_DOCS_REQUEST_PROJECT": "ACME"}
        binding = load(root=root, extra_roots=[], env=env).get("docs-request").binding()
        assert binding["project"] == "ACME"
        assert binding["key_prefixes"] == ["ACME-", "DOCS-"]


# ── RFE_CREATOR_EXTRA_TYPES ───────────────────────────────────────────────────────


class TestExtraRoots:
    def test_env_variable_adds_roots(self, tmp_path):
        extra = tmp_path / "extra"
        _add_type(extra, "docs", project="DOCS")
        env = {"RFE_CREATOR_EXTRA_TYPES": str(extra)}
        reg = load(root=TYPES_ROOT, env=env)
        assert reg.names() == ["rfe", "docs", "initiative"]
        assert reg.extra_roots == [extra]

    def test_env_variable_pathsep_and_empty_entries(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        _add_type(a, "aaa", project="AAA")
        _add_type(b, "bbb", project="BBB")
        env = {"RFE_CREATOR_EXTRA_TYPES": os.pathsep.join(["", str(a), " ", str(b), ""])}
        reg = load(root=TYPES_ROOT, env=env)
        assert reg.names() == ["rfe", "aaa", "bbb", "initiative"]
        assert reg.extra_roots == [a, b]

    def test_explicit_extra_roots_ignore_the_env_variable(self, tmp_path):
        env = {"RFE_CREATOR_EXTRA_TYPES": str(tmp_path / "does-not-exist")}
        assert load(root=TYPES_ROOT, extra_roots=[], env=env).names() == ["rfe", "initiative"]

    def test_env_pointing_at_a_missing_root_fails_loudly(self, tmp_path):
        env = {"RFE_CREATOR_EXTRA_TYPES": str(tmp_path / "does-not-exist")}
        with pytest.raises(RegistryError, match="type root not found"):
            load(root=TYPES_ROOT, env=env)

    def test_parse_extra_roots(self):
        assert parse_extra_roots("") == []
        assert parse_extra_roots(None) == []
        assert parse_extra_roots(os.pathsep.join(["/a", "", "/b"])) == [Path("/a"), Path("/b")]
        assert parse_extra_roots("~/x") == [Path("~/x").expanduser()]

    def test_registry_env_defaults_to_os_environ(self, tmp_path, monkeypatch):
        extra = tmp_path / "extra"
        _add_type(extra, "docs", project="DOCS")
        for marker in type_registry.HEADLESS_MARKER_VARS:  # a CI runner would gate the seam
            monkeypatch.delenv(marker, raising=False)
        monkeypatch.setenv("RFE_CREATOR_EXTRA_TYPES", str(extra))
        assert "docs" in load(root=TYPES_ROOT)
        monkeypatch.delenv("RFE_CREATOR_EXTRA_TYPES")
        assert "docs" not in load(root=TYPES_ROOT)


# ── the headless/CI gate on the env seam (PR1-05, design §3.5) ──────────────────


class TestHeadlessGate:
    """The seam is development and test only: a headless or CI run honours an
    RFE_CREATOR_EXTRA_TYPES entry only when its canonical path is allowlisted (env
    RFE_CREATOR_EXTRA_TYPES_ALLOWLIST — a protected CI variable — or the constructor
    argument); explicit ``extra_roots`` are a deliberate caller action and never gated.
    """

    @pytest.fixture
    def extra(self, tmp_path):
        root = tmp_path / "extra"
        _add_type(root, "docs", project="DOCS")
        return root

    @pytest.mark.parametrize("marker", type_registry.HEADLESS_MARKER_VARS)
    def test_env_root_is_ignored_under_a_headless_marker(self, extra, marker, capsys):
        env = {"RFE_CREATOR_EXTRA_TYPES": str(extra), marker: "true"}
        reg = load(root=TYPES_ROOT, env=env)
        assert reg.names() == ["rfe", "initiative"]
        assert reg.extra_roots == []
        assert reg.roots == [TYPES_ROOT]
        assert reg.ignored_extra_roots == [extra]
        err = capsys.readouterr().err
        assert err.count("\n") == 1, err  # exactly one stderr line
        assert "headless/CI run" in err and "RFE_CREATOR_EXTRA_TYPES" in err and str(extra) in err

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", " False "])
    def test_false_marker_values_do_not_gate(self, extra, value):
        env = {"RFE_CREATOR_EXTRA_TYPES": str(extra), "CI": value, "GITHUB_ACTIONS": value}
        reg = load(root=TYPES_ROOT, env=env)
        assert "docs" in reg
        assert reg.ignored_extra_roots == []

    def test_allowlisted_root_via_env_is_kept(self, extra, capsys):
        env = {
            "RFE_CREATOR_EXTRA_TYPES": str(extra),
            "CI": "true",
            "RFE_CREATOR_EXTRA_TYPES_ALLOWLIST": str(extra),
        }
        reg = load(root=TYPES_ROOT, env=env)
        assert reg.names() == ["rfe", "docs", "initiative"]
        assert reg.ignored_extra_roots == []
        assert capsys.readouterr().err == ""

    def test_allowlisted_root_via_argument_is_kept(self, extra):
        env = {"RFE_CREATOR_EXTRA_TYPES": str(extra), "GITHUB_ACTIONS": "true"}
        reg = load(root=TYPES_ROOT, env=env, allowlisted_extra_roots=[extra])
        assert "docs" in reg
        assert reg.ignored_extra_roots == []

    def test_allowlist_compares_canonical_paths(self, extra, tmp_path):
        """A symlink alias or a non-normalised spelling of an allowlisted root still matches."""
        alias = tmp_path / "alias"
        alias.symlink_to(extra, target_is_directory=True)
        spelled = tmp_path / "extra" / "." / ".." / "extra"
        env = {
            "RFE_CREATOR_EXTRA_TYPES": str(alias),
            "CI": "1",
            "RFE_CREATOR_EXTRA_TYPES_ALLOWLIST": str(spelled),
        }
        assert "docs" in load(root=TYPES_ROOT, env=env)

    def test_only_the_non_allowlisted_entries_are_dropped(self, extra, tmp_path):
        other = tmp_path / "other"
        _add_type(other, "aaa", project="AAA")
        env = {
            "RFE_CREATOR_EXTRA_TYPES": os.pathsep.join([str(extra), str(other)]),
            "CI": "true",
            "RFE_CREATOR_EXTRA_TYPES_ALLOWLIST": str(extra),
        }
        reg = load(root=TYPES_ROOT, env=env)
        assert reg.names() == ["rfe", "docs", "initiative"]
        assert reg.extra_roots == [extra]
        assert reg.ignored_extra_roots == [other]

    def test_explicit_extra_roots_are_never_gated(self, extra, capsys):
        reg = load(root=TYPES_ROOT, extra_roots=[extra], env={"CI": "true"})
        assert "docs" in reg
        assert reg.ignored_extra_roots == []
        assert capsys.readouterr().err == ""

    def test_explicit_empty_extra_roots_switch_the_seam_off_without_noise(self, extra, capsys):
        env = {"CI": "true", "RFE_CREATOR_EXTRA_TYPES": str(extra)}
        reg = load(root=TYPES_ROOT, extra_roots=[], env=env)
        assert reg.names() == ["rfe", "initiative"]
        assert reg.ignored_extra_roots == []
        assert capsys.readouterr().err == ""

    def test_no_seam_no_noise(self, capsys):
        reg = load(root=TYPES_ROOT, env={"CI": "true"})
        assert reg.names() == ["rfe", "initiative"]
        assert capsys.readouterr().err == ""

    def test_is_headless(self):
        assert not type_registry.is_headless({})
        assert type_registry.is_headless({"CI": "true"})
        assert type_registry.is_headless({"GITHUB_ACTIONS": "true"})
        assert type_registry.is_headless({"RFE_CREATOR_HEADLESS": "1"})
        assert not type_registry.is_headless({"CI": "0", "GITHUB_ACTIONS": "", "HOME": "/x"})

    def test_marker_and_allowlist_names_are_pinned(self):
        """Documented in types/README.md and docs/type-provider-guide.md."""
        assert type_registry.HEADLESS_MARKER_VARS == (
            "RFE_CREATOR_HEADLESS",
            "CI",
            "GITHUB_ACTIONS",
        )
        assert type_registry.EXTRA_ROOTS_ALLOWLIST_ENV == "RFE_CREATOR_EXTRA_TYPES_ALLOWLIST"

    def test_cli_honours_the_gate_from_the_process_environment(self, extra):
        gated = _cli("list", env={"RFE_CREATOR_EXTRA_TYPES": str(extra), "CI": "true"})
        assert gated.returncode == 0, gated.stderr
        assert gated.stdout == "rfe\ninitiative\n"
        assert "ignoring RFE_CREATOR_EXTRA_TYPES" in gated.stderr
        allowed = _cli(
            "list",
            env={
                "RFE_CREATOR_EXTRA_TYPES": str(extra),
                "CI": "true",
                "RFE_CREATOR_EXTRA_TYPES_ALLOWLIST": str(extra),
            },
        )
        assert allowed.returncode == 0, allowed.stderr
        assert allowed.stdout == "rfe\ndocs\ninitiative\n"
        assert allowed.stderr == ""

    def test_cli_explicit_extra_roots_are_not_gated(self, extra):
        result = _cli("--extra-roots", str(extra), "list", env={"CI": "true"})
        assert result.returncode == 0, result.stderr
        assert result.stdout == "rfe\ndocs\ninitiative\n"


# ── import-clean invariant (Q5, design §10 item 1) ────────────────────────────────

# `copy` is stdlib: binding() deep-copies the identity block so callers never alias descriptor data.
ALLOWED_IMPORTS = {"argparse", "copy", "json", "os", "re", "sys", "pathlib", "yaml"}


class TestImportClean:
    def test_module_imports_only_stdlib_and_yaml(self):
        tree = ast.parse((REPO_ROOT / SCRIPT).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, "no relative imports"
                imported.add(node.module.split(".")[0])
        assert imported <= ALLOWED_IMPORTS, imported - ALLOWED_IMPORTS

    def test_import_pulls_in_no_repo_module_and_no_jsonschema(self, tmp_path):
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); import type_registry; "
            "print(sorted(m for m in sys.modules if m in "
            "{'artifact_utils', 'pipeline_state', 'validate_types', 'jsonschema', 'frontmatter'}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(REPO_ROOT / "scripts")],
            cwd=tmp_path,  # a cwd with no types/: import must not touch the filesystem
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_clean_env(),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "[]"

    def test_default_root_is_file_relative(self):
        assert type_registry.DEFAULT_ROOT == (REPO_ROOT / "types").resolve()

    def test_cwd_independent_default_root(self, tmp_path):
        """A lifted copy of the module finds its sibling types/ from any cwd."""
        lifted = tmp_path / "lifted"
        (lifted / "scripts").mkdir(parents=True)
        shutil.copy(REPO_ROOT / SCRIPT, lifted / "scripts" / "type_registry.py")
        _add_type(lifted / "types", "docs", project="DOCS")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        result = subprocess.run(
            [sys.executable, str(lifted / "scripts" / "type_registry.py"), "list"],
            cwd=elsewhere,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_clean_env(),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "docs\n"


# ── CLI ───────────────────────────────────────────────────────────────────────────


class TestCli:
    def test_list(self):
        result = _cli("list")
        assert result.returncode == 0, result.stderr
        assert result.stdout == "rfe\ninitiative\n"

    def test_list_json(self):
        result = _cli("list", "--json")
        assert result.returncode == 0
        assert json.loads(result.stdout) == ["rfe", "initiative"]

    def test_options_accepted_before_the_subcommand(self):
        result = _cli("--json", "list")
        assert result.returncode == 0
        assert json.loads(result.stdout) == ["rfe", "initiative"]

    def test_show_text_is_a_header_plus_yaml(self):
        result = _cli("show", "rfe")
        assert result.returncode == 0, result.stderr
        header, _, body = result.stdout.partition("\n")
        assert header == f"# rfe ({(TYPES_ROOT / 'rfe' / 'type.yaml').resolve()})"
        assert yaml.safe_load(body) == _read_yaml(TYPES_ROOT / "rfe" / "type.yaml")

    def test_show_json_is_the_descriptor(self):
        result = _cli("show", "initiative", "--json")
        assert result.returncode == 0
        assert json.loads(result.stdout) == _read_yaml(TYPES_ROOT / "initiative" / "type.yaml")

    def test_get_scalar(self):
        result = _cli("get", "rfe", "identity.local_prefix")
        assert result.returncode == 0
        assert result.stdout == "RFE-\n"

    def test_get_null_prints_null(self):
        result = _cli("get", "initiative", "pipeline.rubric.export")
        assert result.returncode == 0
        assert result.stdout == "null\n"

    def test_get_mapping_as_yaml_and_json(self):
        text = _cli("get", "rfe", "conventions.labels.feasibility")
        assert text.returncode == 0
        assert yaml.safe_load(text.stdout) == {
            "feasible": "rfe-creator-feasibility-pass",
            "infeasible": "rfe-creator-feasibility-fail",
            "indeterminate": "rfe-creator-feasibility-unknown",
        }
        as_json = _cli("get", "rfe", "conventions.labels.feasibility", "--json")
        assert json.loads(as_json.stdout) == yaml.safe_load(text.stdout)

    def test_get_list_index(self):
        result = _cli("get", "initiative", "pipeline.dimensions.1.name")
        assert result.stdout == "alignment\n"

    def test_get_missing_key_exits_1(self):
        result = _cli("get", "rfe", "nope.key")
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.strip() == "ERROR: rfe: no such descriptor field 'nope.key'"

    def test_binding_json_default(self):
        result = _cli("binding", "rfe", "--json")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == RFE_DESCRIPTOR_BINDING

    def test_binding_text_is_yaml(self):
        result = _cli("binding", "initiative")
        assert result.returncode == 0
        assert yaml.safe_load(result.stdout)["project"] == "RHOAIENG"

    def test_binding_honours_env_override(self):
        result = _cli("binding", "rfe", "--json", env={"RFE_CREATOR_BINDING_RFE_PROJECT": "ACME"})
        assert result.returncode == 0
        binding = json.loads(result.stdout)
        assert binding["source"] == "env"
        assert binding["key_prefixes"] == ["ACME-", "RHAIRFE-"]

    def test_binding_invalid_override_exits_1(self):
        result = _cli("binding", "rfe", env={"RFE_CREATOR_BINDING_RFE_PROJECT": "acme"})
        assert result.returncode == 1
        assert "RFE_CREATOR_BINDING_RFE_PROJECT='acme'" in result.stderr

    @pytest.mark.parametrize("command", ["show", "binding"])
    def test_unknown_type_exits_1_with_available_names(self, command):
        result = _cli(command, "nope")
        assert result.returncode == 1
        assert result.stderr.strip() == "ERROR: unknown type 'nope'; available: rfe, initiative"

    def test_no_subcommand_is_a_usage_error(self):
        result = _cli()
        assert result.returncode == 2
        assert "usage:" in result.stderr

    def test_unknown_subcommand_is_a_usage_error(self):
        assert _cli("frobnicate").returncode == 2

    def test_get_without_dotted_path_is_a_usage_error(self):
        assert _cli("get", "rfe").returncode == 2

    def test_root_not_found_exits_1(self, tmp_path):
        result = _cli("--root", str(tmp_path / "nope"), "list")
        assert result.returncode == 1
        assert "type root not found" in result.stderr

    def test_root_after_the_subcommand(self, tmp_path):
        root = _copy_types(tmp_path / "types", names=("initiative",))
        result = _cli("list", "--root", str(root))
        assert result.returncode == 0, result.stderr
        assert result.stdout == "initiative\n"

    def test_extra_roots_option(self, tmp_path):
        extra = tmp_path / "extra"
        _add_type(extra, "docs", project="DOCS")
        result = _cli("--extra-roots", str(extra), "list")
        assert result.returncode == 0, result.stderr
        assert result.stdout == "rfe\ndocs\ninitiative\n"

    def test_extra_roots_env_variable(self, tmp_path):
        extra = tmp_path / "extra"
        _add_type(extra, "docs", project="DOCS")
        result = _cli("list", env={"RFE_CREATOR_EXTRA_TYPES": str(extra)})
        assert result.returncode == 0, result.stderr
        assert result.stdout == "rfe\ndocs\ninitiative\n"

    def test_duplicate_across_roots_exits_1(self, tmp_path):
        extra = _copy_types(tmp_path / "extra", names=("rfe",))
        result = _cli("--extra-roots", str(extra), "list")
        assert result.returncode == 1
        assert "duplicate type 'rfe'" in result.stderr
