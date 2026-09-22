#!/usr/bin/env python3
"""Tests for scripts/type_registry.py — the import-clean work-item type registry.

design-proposals/work-item-types-unified.md §3.2 / §3.2.1 / §5 / §10 item 1 and the PR-3
plan (PR-3a). These tests are the registry's own contract (the adopting scripts test only
their projections): discovery over one or more roots, the ``names()`` order today's
argparse ``choices`` lists use, dotted access, the two ``dirs`` spellings (Q13), label
flattening, the effective binding overlay and its sources (§3.2.1: env, shorthand,
workspace file, D13 re-rendering, the headless trust boundary), multi-candidate detection
(``candidates``), the ownership check (``assert_registered_binding``), the resolution
ladder (``resolve`` and its CLI) and the CLI exit codes.

Every registry here is built with ``load(root=..., extra_roots=[], env={})`` so a
developer's ``RFE_CREATOR_EXTRA_TYPES`` / ``RFE_CREATOR_BINDING_*`` / ``JIRA_PROJECT`` never
leaks in; subprocess tests scrub the same variables.
"""

import ast
import inspect
import json
import os
import re
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
    Candidates,
    Descriptor,
    RegistryError,
    Resolution,
    ResolveError,
    assert_registered_binding,
    binding_env_var,
    load,
    load_workspace_bindings,
    parse_extra_roots,
    parse_type_arg,
    read_batch,
    resolve,
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


_SHORTHAND_VARS = frozenset(
    var for per_tracker in type_registry.SHORTHAND_ENV_VARS.values() for var in per_tracker
)


def _clean_env(**extra):
    """The developer's seams AND the headless/CI markers stay out of subprocess tests: under
    GitHub Actions ``CI``/``GITHUB_ACTIONS`` would otherwise gate RFE_CREATOR_EXTRA_TYPES, and
    a stray ``JIRA_PROJECT`` would move a ``resolve`` binding."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("RFE_CREATOR_")
        and k not in type_registry.HEADLESS_MARKER_VARS
        and k not in _SHORTHAND_VARS
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

    def test_parent_key_pattern_is_the_anchored_alternation(self):
        # PR-3b: the ONE join behind the task schema (artifact_utils) and the batch validator
        # (validate_batch_input) — PR-1 checklist Q14 reconciled by construction.
        reg = _shipped()
        assert reg.get("rfe").parent_key_pattern == r"^(RFE-\d+|RHAIRFE-\d+)$"
        assert reg.get("initiative").parent_key_pattern == (
            r"^(RHAISTRAT-\d+|RHOAIENG-\d+|INIT-\d+)$"
        )
        for name in SHIPPED:
            desc = reg.get(name)
            patterns = desc.get("conventions.parent_key_patterns")
            assert desc.parent_key_pattern == "^(" + "|".join(patterns) + ")$"
            assert re.fullmatch(desc.parent_key_pattern, f"{desc.write_prefix}12")
            assert re.fullmatch(desc.parent_key_pattern, f"x{desc.write_prefix}12") is None

    def test_parent_key_pattern_is_none_without_patterns(self):
        assert Descriptor("bare", {"type": "bare"}).parent_key_pattern is None
        assert Descriptor("gh", _minimal("gh", "GH")).parent_key_pattern is None
        empty = {"type": "e", "conventions": {"parent_key_patterns": []}}
        assert Descriptor("e", empty).parent_key_pattern is None
        one = {"type": "o", "conventions": {"parent_key_patterns": [r"OUT-\d+"]}}
        assert Descriptor("o", one).parent_key_pattern == r"^(OUT-\d+)$"

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
    "local_id_pattern": r"^RFE-\d+$",
    "source": "descriptor",
    "overrides": [],
}
# What ``python3 scripts/type_registry.py binding rfe`` prints with nothing overridden: the
# PR-1 keys, byte for byte (the CLI view drops the two keys that only restate the descriptor).
RFE_DESCRIPTOR_BINDING_CLI = {
    k: v for k, v in RFE_DESCRIPTOR_BINDING.items() if k not in ("local_id_pattern", "overrides")
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

    def test_headless_flag_gates_the_env_root(self, extra, capsys):
        """PR-3 D4, one predicate: ``load(headless=True)`` — the explicit ``--headless`` —
        gates the seam exactly like a marker, with no marker set."""
        env = {"RFE_CREATOR_EXTRA_TYPES": str(extra)}
        assert load(root=TYPES_ROOT, env=env).headless is False
        reg = load(root=TYPES_ROOT, env=env, headless=True)
        assert reg.headless is True
        assert reg.names() == ["rfe", "initiative"]
        assert reg.ignored_extra_roots == [extra]
        err = capsys.readouterr().err
        assert err.count("\n") == 1 and "headless/CI run" in err and str(extra) in err
        # allowlisted: honoured under the flag as under a marker
        env["RFE_CREATOR_EXTRA_TYPES_ALLOWLIST"] = str(extra)
        assert load(root=TYPES_ROOT, env=env, headless=True).names() == [
            "rfe",
            "docs",
            "initiative",
        ]
        assert capsys.readouterr().err == ""
        # explicit extra_roots stay a deliberate caller action, flag or not
        assert "docs" in load(root=TYPES_ROOT, extra_roots=[extra], env={}, headless=True)

    def test_resolve_cli_headless_flag_gates_the_env_root(self, extra):
        """``resolve --headless`` reaches the seam through ``load(headless=...)``: a drop-in
        that is not allowlisted is dropped before resolution, so an explicit ``--type``
        naming it is unknown and its ids fall to the provisional Jira grammar."""
        env = {"RFE_CREATOR_EXTRA_TYPES": str(extra)}
        interactive = _cli("resolve", "--type", "docs", env=env)
        assert interactive.returncode == 0, interactive.stderr
        assert interactive.stdout == "TYPE RESOLVED: docs (--type)\n"
        headless = _cli("resolve", "--headless", "--type", "docs", env=env)
        assert headless.returncode == 1
        assert headless.stdout == ""
        assert (
            "ignoring RFE_CREATOR_EXTRA_TYPES root(s) not in RFE_CREATOR_EXTRA_TYPES_ALLOWLIST"
            in headless.stderr
        )
        assert "ERROR: unknown type 'docs' (--type); registered types: rfe, initiative" in (
            headless.stderr
        )
        by_id = _cli("resolve", "--headless", "DOCS-1", env=env)
        assert by_id.returncode == 3
        assert "candidates rfe, initiative" in by_id.stderr
        allowed = _cli(
            "resolve",
            "--headless",
            "--type",
            "docs",
            env={**env, "RFE_CREATOR_EXTRA_TYPES_ALLOWLIST": str(extra)},
        )
        assert allowed.returncode == 0, allowed.stderr
        assert allowed.stdout == "TYPE RESOLVED: docs (--type)\n"


# ── import-clean invariant (Q5, design §10 item 1) ────────────────────────────────

# `copy` is stdlib: binding() deep-copies the identity block so callers never alias descriptor
# data; `dataclasses` is stdlib: the Candidates / Resolution result objects (PR-3a).
ALLOWED_IMPORTS = {"argparse", "copy", "dataclasses", "json", "os", "re", "sys", "pathlib", "yaml"}


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
        assert json.loads(result.stdout) == RFE_DESCRIPTOR_BINDING_CLI

    def test_binding_zero_override_output_is_the_pr1_output(self):
        """PR-3a neutrality: with nothing overridden the diagnostic CLI prints exactly the
        PR-1 keys in the PR-1 order — ``overrides`` (empty) and ``local_id_pattern`` (the
        descriptor's own) belong to the API dict, not to the CLI view (``_binding_view``)."""
        text = _cli("binding", "rfe")
        assert text.returncode == 0, text.stderr
        assert list(yaml.safe_load(text.stdout)) == list(RFE_DESCRIPTOR_BINDING_CLI)
        assert yaml.safe_load(text.stdout) == RFE_DESCRIPTOR_BINDING_CLI
        assert list(json.loads(_cli("binding", "rfe", "--json").stdout)) == list(
            RFE_DESCRIPTOR_BINDING_CLI
        )
        for name in ("rfe", "initiative"):
            for out in (_cli("binding", name).stdout, _cli("binding", name, "--json").stdout):
                assert "overrides" not in out and "local_id_pattern" not in out, (name, out)
        # the API keeps both keys: the CLI is a view over it
        assert _shipped().get("rfe").binding() == RFE_DESCRIPTOR_BINDING

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


class TestDottedIndexParsing:
    """List-index segments must be ASCII integers; str.isdigit() lookalikes must not raise."""

    def test_non_ascii_or_malformed_index_returns_default(self):
        reg = type_registry.load(extra_roots=[], env={})
        desc = reg.get("rfe")
        for segment in ("\u00b2", "\u0663", "--1", "1.5"):
            assert desc.get(f"pipeline.dimensions.{segment}.name", default=None) is None
            with pytest.raises(KeyError):
                desc.get(f"pipeline.dimensions.{segment}.name")

    def test_ascii_index_still_works(self):
        desc = type_registry.load(extra_roots=[], env={}).get("rfe")
        assert desc.get("pipeline.dimensions.0.name") == desc.get("pipeline.dimensions")[0]["name"]
        assert (
            desc.get("pipeline.dimensions.-1.name") == desc.get("pipeline.dimensions")[-1]["name"]
        )


# ── detect / owns (PR-2 seed of the design §5 ladder) ────────────────────────────


class TestDetect:
    """``TypeRegistry.detect`` / ``Descriptor.owns``: the deterministic id signal (§5 rung 3).

    Three rungs, each tried across every type before the next: local_id_pattern full-match,
    tracker key_prefixes prefix, local_prefix prefix (the parity rung of the sniffs PR-2
    replaces). Descriptor values only; the multi-candidate form over effective bindings is
    ``candidates()`` (``TestCandidates`` below).
    """

    @pytest.mark.parametrize(
        "item_id, expected",
        [
            ("RFE-001", "rfe"),
            ("RHAIRFE-1", "rfe"),
            ("INIT-001", "initiative"),
            ("RHOAIENG-1", "initiative"),
            ("RHAISTRAT-1", None),  # a peer pipeline's key: no shipped type owns it
            ("", None),
            ("rfe-001", None),  # case-sensitive, like every predicate it replaces
            ("init-001", None),
            ("rhoaieng-1", None),
        ],
    )
    def test_shipped_ids(self, item_id, expected):
        desc = _shipped().detect(item_id)
        assert (desc.name if desc else None) == expected

    def test_none_and_non_strings_detect_nothing(self):
        reg = _shipped()
        assert reg.detect(None) is None
        assert reg.detect(1234) is None
        assert reg.get("rfe").owns(None) is False

    @pytest.mark.parametrize(
        "item_id, expected",
        [
            ("INIT-x", "initiative"),  # local_prefix rung: what startswith("INIT-") did
            ("RFE-", "rfe"),
            ("RHAIRFE-1234x", "rfe"),  # key-prefix rung is a plain prefix test, as before
            ("RFE-001-review", "rfe"),
        ],
    )
    def test_parity_with_the_prefix_sniffs(self, item_id, expected):
        """Malformed but prefixed ids keep going where ``startswith(local_prefix)`` sent them."""
        assert _shipped().detect(item_id).name == expected

    def test_owns_agrees_with_detect_for_every_shipped_type(self):
        reg = _shipped()
        for item_id in ("RFE-001", "RHAIRFE-1", "INIT-001", "RHOAIENG-1", "RHAISTRAT-1", ""):
            owner = reg.detect(item_id)
            for desc in reg:
                assert desc.owns(item_id) is (owner is not None and owner.name == desc.name)

    def test_returns_the_registry_descriptor_instance(self):
        reg = _shipped()
        assert reg.detect("RFE-001") is reg.get("rfe")
        assert reg.detect("RHOAIENG-1") is reg.get("initiative")

    def test_local_id_pattern_beats_another_types_key_prefix(self, tmp_path):
        """Rung order, not names() order, decides: alpha sorts first and holds the ``AB-`` key
        prefix, yet ``AB-7`` full-matches beta's local pattern and goes to beta."""
        root = tmp_path / "types"
        _add_type(root, "alpha", project="AB")
        _add_type(root, "beta", project="ZZ", local_prefix="AB-")
        reg = load(root=root, extra_roots=[], env={})
        assert reg.names() == ["alpha", "beta"]
        assert reg.detect("AB-7").name == "beta"
        assert reg.detect("AB-7x").name == "alpha"  # no full match: the key-prefix rung wins
        assert reg.detect("ZZ-1").name == "beta"
        assert reg.detect("ALPHA-1").name == "alpha"

    def test_key_prefix_beats_a_local_prefix(self, tmp_path):
        root = tmp_path / "types"
        _add_type(root, "one", project="K")
        _add_type(root, "two", project="T", local_prefix="K-")  # placeholder-style local prefix
        reg = load(root=root, extra_roots=[], env={})
        assert reg.detect("K-abc").name == "one"  # rung 2 (one's key) before rung 3 (two's local)
        assert reg.detect("K-12").name == "two"  # but a full local-pattern match still wins

    def test_alias_prefix_binding_is_a_key_prefix(self, tmp_path):
        """A github-style binding (design §8.6) exposes alias_prefix as its key prefix."""
        root = tmp_path / "types"
        data = _minimal("gh", project="X")
        data["identity"]["tracker"] = "github"
        data["identity"]["github"] = {"owner": "o", "repo": "r", "alias_prefix": "GH-"}
        del data["identity"]["jira"]
        _add_type(root, "gh", data=data)
        reg = load(root=root, extra_roots=[], env={})
        assert reg.detect("GH-42").name == "gh"
        assert reg.get("gh").owns("GH-42x") is True

    def test_missing_optional_identity_fields_do_not_raise(self, tmp_path):
        root = tmp_path / "types"
        data = _minimal("bare", project="B")
        del data["identity"]["local_id_pattern"]
        del data["identity"]["local_prefix"]
        _add_type(root, "bare", data=data)
        reg = load(root=root, extra_roots=[], env={})
        assert reg.detect("B-1").name == "bare"
        assert reg.detect("BARE-1") is None

    def test_detect_ignores_binding_overrides(self, tmp_path):
        """Descriptor values only: an env project override (§3.2.1) does not move detection."""
        root = tmp_path / "types"
        _add_type(root, "rfe", project="RHAIRFE")
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "OTHER"}
        reg = load(root=root, extra_roots=[], env=env)
        assert reg.get("rfe").binding()["key_prefixes"][0] == "OTHER-"
        assert reg.detect("OTHER-1") is None
        assert reg.detect("RHAIRFE-1").name == "rfe"


class TestOwnsEffective:
    """``Descriptor.owns_effective``: ``owns`` over the EFFECTIVE binding (§3.2.1; PR-3c, the
    ownership test of the writers and the artifact helpers). The three definite rungs only —
    never the provisional tracker grammar — with the read parity ``candidates()`` applies;
    ``owns`` and ``detect`` stay descriptor-only for the per-id routers."""

    ENV = {"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"}
    IDS = ("RFE-001", "RHAIRFE-1", "INIT-001", "RHOAIENG-1", "RHAISTRAT-1", "KONFLUX-1", "RFE-x")

    def test_no_override_equals_owns_for_every_shipped_id(self):
        reg = _shipped()
        for item_id in (*self.IDS, "", None, 1234):
            for desc in reg:
                assert desc.owns_effective(item_id, env={}) is desc.owns(item_id), (
                    desc.name,
                    item_id,
                )

    def test_overridden_project_key_is_owned_only_under_the_override(self):
        reg = _shipped()
        rfe, init = reg.get("rfe"), reg.get("initiative")
        assert rfe.owns_effective("KONFLUX-1", env={}) is False
        assert rfe.owns_effective("KONFLUX-1", env=self.ENV) is True
        assert rfe.owns_effective("KONFLUX-1234x", env=self.ENV) is True  # a prefix test, as owns
        assert init.owns_effective("KONFLUX-1", env=self.ENV) is False  # the overridden type only
        # Descriptor-only readers do not move (test_detect_ignores_binding_overrides).
        assert rfe.owns("KONFLUX-1") is False
        assert reg.detect("KONFLUX-1") is None

    def test_descriptor_prefixes_stay_read_forms(self):
        rfe = _shipped().get("rfe")
        for item_id in ("RHAIRFE-1", "RFE-001", "RFE-x"):
            assert rfe.owns_effective(item_id, env={}) is True, item_id
            assert rfe.owns_effective(item_id, env=self.ENV) is True, item_id

    def test_never_the_provisional_rung(self):
        reg = _shipped()
        found = reg.candidates("RHAISTRAT-1", env=self.ENV)
        assert found.provisional and found.names == ["rfe", "initiative"]
        assert not any(d.owns_effective("RHAISTRAT-1", env=self.ENV) for d in reg)
        assert not any(d.owns_effective("AB1-9", env={}) for d in reg)

    def test_env_defaults_to_the_registry_environment(self):
        reg = load(root=TYPES_ROOT, extra_roots=[], env=self.ENV)
        assert reg.get("rfe").owns_effective("KONFLUX-1") is True
        assert reg.get("initiative").owns_effective("KONFLUX-1") is False
        assert _shipped().get("rfe").owns_effective("KONFLUX-1") is False
        # An explicit env wins over the registry's.
        assert reg.get("rfe").owns_effective("KONFLUX-1", env={}) is False

    def test_local_prefix_override_keeps_read_parity(self):
        env = {"RFE_CREATOR_BINDING_RFE_LOCAL_PREFIX": "DRAFT-"}
        rfe = _shipped().get("rfe")
        assert rfe.owns_effective("DRAFT-001", env=env) is True  # the re-rendered pattern (D13)
        assert rfe.owns_effective("DRAFT-x", env=env) is True  # the effective local prefix
        assert (
            rfe.owns_effective("RFE-001", env=env) is True
        )  # the descriptor's own stay read forms
        assert rfe.owns_effective("RFE-x", env=env) is True
        assert rfe.owns_effective("DRAFT-001", env={}) is False

    def test_workspace_source_is_applied_when_passed(self):
        rfe = _shipped().get("rfe")
        assert rfe.owns_effective("KONFLUX-1", env={}, workspace=WORKSPACE) is True
        assert rfe.owns_effective("KONFLUX-1", env={}) is False

    def test_shorthand_is_never_applied(self):
        # JIRA_PROJECT is a resolve-time source for the resolved type only (binding(shorthand=)).
        rfe = _shipped().get("rfe")
        assert rfe.owns_effective("KONFLUX-1", env={"JIRA_PROJECT": "KONFLUX"}) is False

    def test_agrees_with_candidates_definite_rungs_for_every_shipped_id(self):
        reg = _shipped()
        for env in ({}, self.ENV):
            for item_id in self.IDS:
                found = reg.candidates(item_id, env=env)
                definite = [] if found.provisional else found.names
                owners = [d.name for d in reg if d.owns_effective(item_id, env=env)]
                assert owners == definite, (env, item_id)


# ── the one headless predicate (PR-3 D4) ─────────────────────────────────────────


class TestHeadlessPredicate:
    def test_explicit_flag_short_circuits(self):
        assert type_registry.is_headless({}, True) is True
        assert type_registry.is_headless({"CI": "0"}, flag=True) is True

    def test_flag_false_leaves_the_markers_in_charge(self):
        assert type_registry.is_headless({"CI": "true"}, False) is True
        assert type_registry.is_headless({"CI": "0"}, False) is False

    def test_one_argument_form_is_unchanged(self):
        assert type_registry.is_headless({}) is False
        assert type_registry.is_headless({"RFE_CREATOR_HEADLESS": "1"}) is True


# ── binding sources (§3.2.1: env > shorthand > workspace > descriptor; D13) ──────

WORKSPACE = {"rfe": {"jira": {"project": "KONFLUX", "issue_type": "Feature Request"}}}
WORKSPACE_YAML = (
    "bindings:\n  rfe:\n    jira:\n      project: KONFLUX\n      issue_type: Feature Request\n"
)


class TestBindingSources:
    def test_default_binding_carries_the_pattern_and_no_overrides(self):
        binding = _shipped().get("rfe").binding()
        assert binding["local_id_pattern"] == r"^RFE-\d+$"
        assert binding["overrides"] == []
        assert binding["source"] == "descriptor"
        initiative = _shipped().get("initiative").binding()
        assert initiative["local_id_pattern"] == r"^INIT-\d+$"
        assert initiative["overrides"] == []

    def test_overrides_are_listed_in_field_order_whatever_the_env_order(self):
        env = {
            "RFE_CREATOR_BINDING_RFE_LOCAL_PREFIX": "REQ-",
            "RFE_CREATOR_BINDING_RFE_PROJECT": "ACME",
        }
        binding = _shipped().get("rfe").binding(env)
        assert binding["overrides"] == ["project", "local_prefix"]
        assert binding["source"] == "env"

    # -- D13: LOCAL_PREFIX re-renders local_id_pattern ---------------------------------

    def test_local_prefix_override_rerenders_the_pattern(self):
        desc = _shipped().get("rfe")
        binding = desc.binding({"RFE_CREATOR_BINDING_RFE_LOCAL_PREFIX": "REQ-"})
        assert binding["local_prefix"] == "REQ-"
        assert binding["local_id_pattern"] == "^" + re.escape("REQ-") + r"\d+$"
        assert re.fullmatch(binding["local_id_pattern"], "REQ-001")
        assert not re.fullmatch(binding["local_id_pattern"], "RFE-001")
        assert binding["overrides"] == ["local_prefix"]
        # the descriptor and the default binding are untouched
        assert desc.local_id_pattern == r"^RFE-\d+$"
        assert desc.binding({})["local_id_pattern"] == r"^RFE-\d+$"

    def test_an_escaped_descriptor_prefix_head_is_recognised(self, tmp_path):
        root = tmp_path / "types"
        _add_type(root, "esc", project="ESC", local_prefix="ES-", local_id_pattern=r"^ES\-\d{3}$")
        env = {"RFE_CREATOR_BINDING_ESC_LOCAL_PREFIX": "XY-"}
        binding = load(root=root, extra_roots=[], env=env).get("esc").binding()
        assert binding["local_id_pattern"] == r"^XY\-\d{3}$"
        assert re.fullmatch(binding["local_id_pattern"], "XY-007")

    def test_pattern_not_starting_with_the_descriptor_prefix_is_a_hard_error(self, tmp_path):
        root = tmp_path / "types"
        _add_type(root, "odd", project="ODD", local_prefix="OD-", local_id_pattern=r"^(OD-\d+)$")
        env = {"RFE_CREATOR_BINDING_ODD_LOCAL_PREFIX": "XY-"}
        reg = load(root=root, extra_roots=[], env=env)
        with pytest.raises(RegistryError, match=r"odd: cannot override local_prefix to 'XY-'") as e:
            reg.get("odd").binding()
        assert repr(r"^(OD-\d+)$") in str(e.value)
        assert "D13" in str(e.value)

    def test_prefix_only_in_the_middle_of_the_pattern_is_a_hard_error(self, tmp_path):
        root = tmp_path / "types"
        _add_type(root, "mid", project="MID", local_prefix="MI-", local_id_pattern=r"^X-MI-\d+$")
        reg = load(root=root, extra_roots=[], env={"RFE_CREATOR_BINDING_MID_LOCAL_PREFIX": "XY-"})
        with pytest.raises(RegistryError, match="does not start with"):
            reg.get("mid").binding()

    def test_rerendered_pattern_that_does_not_compile_is_a_hard_error(self, tmp_path):
        root = tmp_path / "types"
        _add_type(root, "bad", project="BAD", local_prefix="BA-", local_id_pattern=r"^BA-\d+(")
        reg = load(root=root, extra_roots=[], env={"RFE_CREATOR_BINDING_BAD_LOCAL_PREFIX": "XY-"})
        with pytest.raises(RegistryError, match="is not a valid regex"):
            reg.get("bad").binding()

    def test_descriptor_without_a_pattern_keeps_none(self, tmp_path):
        root = tmp_path / "types"
        data = _minimal("bare", project="B")
        del data["identity"]["local_id_pattern"]
        _add_type(root, "bare", data=data)
        env = {"RFE_CREATOR_BINDING_BARE_LOCAL_PREFIX": "XY-"}
        binding = load(root=root, extra_roots=[], env=env).get("bare").binding()
        assert binding["local_prefix"] == "XY-"
        assert binding["local_id_pattern"] is None

    def test_pattern_without_a_descriptor_prefix_cannot_be_rerendered(self, tmp_path):
        root = tmp_path / "types"
        data = _minimal("nop", project="NOP")
        del data["identity"]["local_prefix"]
        _add_type(root, "nop", data=data)
        env = {"RFE_CREATOR_BINDING_NOP_LOCAL_PREFIX": "XY-"}
        with pytest.raises(RegistryError, match="descriptor local_prefix None"):
            load(root=root, extra_roots=[], env=env).get("nop").binding()

    # -- shorthand: JIRA_PROJECT / JIRA_ISSUE_TYPE, resolved type only --------------------

    def test_shorthand_applies_only_when_asked_for(self):
        env = {"JIRA_PROJECT": "KONFLUX", "JIRA_ISSUE_TYPE": "Story"}
        desc = _shipped().get("rfe")
        assert desc.binding(env)["source"] == "descriptor"
        assert desc.binding(env)["project"] == "RHAIRFE"
        binding = desc.binding(env, shorthand=True)
        assert (binding["project"], binding["issue_type"]) == ("KONFLUX", "Story")
        assert binding["key_prefixes"] == ["KONFLUX-", "RHAIRFE-"]
        assert binding["source"] == "shorthand"
        assert binding["overrides"] == ["project", "issue_type"]

    def test_typed_variable_beats_the_shorthand(self):
        env = {"JIRA_PROJECT": "KONFLUX", "RFE_CREATOR_BINDING_RFE_PROJECT": "ACME"}
        binding = _shipped().get("rfe").binding(env, shorthand=True)
        assert binding["project"] == "ACME"
        assert binding["source"] == "env"  # the shorthand contributed nothing

    def test_env_and_shorthand_combine_in_precedence_order(self):
        env = {"JIRA_ISSUE_TYPE": "Story", "RFE_CREATOR_BINDING_RFE_PROJECT": "ACME"}
        binding = _shipped().get("rfe").binding(env, shorthand=True)
        assert (binding["project"], binding["issue_type"]) == ("ACME", "Story")
        assert binding["source"] == "env+shorthand"
        assert binding["overrides"] == ["project", "issue_type"]

    def test_shorthand_values_pass_the_same_grammar(self):
        with pytest.raises(RegistryError, match="JIRA_PROJECT='konflux'"):
            _shipped().get("rfe").binding({"JIRA_PROJECT": "konflux"}, shorthand=True)

    def test_blank_shorthand_counts_as_unset(self):
        binding = _shipped().get("rfe").binding({"JIRA_PROJECT": "  "}, shorthand=True)
        assert binding["source"] == "descriptor"

    def test_shorthand_is_jira_only(self):
        data = {
            "type": "gh",
            "identity": {
                "tracker": "github",
                "github": {"repo": "acme/widgets", "kind": "issue", "alias_prefix": "GH-"},
            },
        }
        binding = Descriptor("gh", data).binding({"JIRA_PROJECT": "KONFLUX"}, shorthand=True)
        assert binding["source"] == "descriptor"
        assert binding["project"] is None

    def test_registry_bindings_never_apply_the_shorthand(self):
        reg = load(root=TYPES_ROOT, extra_roots=[], env={"JIRA_PROJECT": "KONFLUX"})
        assert all(b["source"] == "descriptor" for b in reg.bindings().values())

    # -- workspace mapping -----------------------------------------------------------------

    def test_workspace_override(self):
        reg = _shipped()
        binding = reg.get("rfe").binding({}, workspace=WORKSPACE)
        assert binding["project"] == "KONFLUX"
        assert binding["key_prefixes"] == ["KONFLUX-", "RHAIRFE-"]
        assert binding["source"] == "workspace"
        assert binding["overrides"] == ["project", "issue_type"]
        assert reg.get("initiative").binding({}, workspace=WORKSPACE)["source"] == "descriptor"

    def test_env_beats_the_workspace(self):
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "ACME"}
        binding = _shipped().get("rfe").binding(env, workspace=WORKSPACE)
        assert binding["project"] == "ACME"
        assert binding["issue_type"] == "Feature Request"
        assert binding["source"] == "env+workspace"
        assert binding["overrides"] == ["project", "issue_type"]

    def test_shorthand_beats_the_workspace(self):
        env = {"JIRA_PROJECT": "SHORT"}
        binding = _shipped().get("rfe").binding(env, workspace=WORKSPACE, shorthand=True)
        assert binding["project"] == "SHORT"
        assert binding["source"] == "shorthand+workspace"

    def test_all_three_sources(self):
        env = {"RFE_CREATOR_BINDING_RFE_LOCAL_PREFIX": "REQ-", "JIRA_ISSUE_TYPE": "Story"}
        workspace = {"rfe": {"jira": {"project": "KONFLUX"}}}
        binding = _shipped().get("rfe").binding(env, workspace=workspace, shorthand=True)
        assert (binding["project"], binding["issue_type"]) == ("KONFLUX", "Story")
        assert binding["local_prefix"] == "REQ-"
        assert binding["local_id_pattern"] == r"^REQ\-\d+$"
        assert binding["source"] == "env+shorthand+workspace"
        assert binding["overrides"] == ["project", "issue_type", "local_prefix"]

    def test_workspace_values_pass_the_same_grammar(self):
        bad = {"rfe": {"jira": {"project": "konflux"}}}
        with pytest.raises(RegistryError, match=r"bindings\.rfe\.jira\.project='konflux'"):
            _shipped().get("rfe").binding({}, workspace=bad)
        bad = {"rfe": {"jira": {"local_prefix": "req-"}}}
        with pytest.raises(RegistryError, match="expected an upper-case prefix ending in '-'"):
            _shipped().get("rfe").binding({}, workspace=bad)
        with pytest.raises(RegistryError, match="expected a non-empty string"):
            _shipped().get("rfe").binding({}, workspace={"rfe": {"jira": {"project": 5}}})

    def test_workspace_entries_for_another_tracker_or_type_are_ignored(self):
        desc = _shipped().get("rfe")
        assert desc.binding({}, workspace={"rfe": {"github": {"project": "X"}}})["source"] == (
            "descriptor"
        )
        assert desc.binding({}, workspace={"epic": {"jira": {"project": "X"}}})["source"] == (
            "descriptor"
        )
        assert desc.binding({}, workspace={})["source"] == "descriptor"
        assert desc.binding({}, workspace=None)["source"] == "descriptor"

    def test_registry_bindings_pass_the_workspace_through(self):
        bindings = _shipped().bindings({}, workspace=WORKSPACE)
        assert bindings["rfe"]["project"] == "KONFLUX"
        assert bindings["initiative"]["source"] == "descriptor"

    def test_binding_never_mutates_the_workspace_mapping(self):
        workspace = {"rfe": {"jira": {"project": "KONFLUX"}}}
        before = json.dumps(workspace, sort_keys=True)
        binding = _shipped().get("rfe").binding({}, workspace=workspace)
        binding["project"] = "MUTATED"
        assert json.dumps(workspace, sort_keys=True) == before


# ── the workspace file and its trust boundary (§3.2.1 g) ─────────────────────────


class TestWorkspaceFile:
    @staticmethod
    def _write(root, content):
        (root / "rfe-creator.yaml").write_text(content, encoding="utf-8")
        return root

    def test_missing_file_block_or_root_reads_as_empty(self, tmp_path):
        assert load_workspace_bindings(None) == {}
        assert load_workspace_bindings(tmp_path) == {}
        assert load_workspace_bindings(tmp_path / "nope") == {}
        assert load_workspace_bindings(self._write(tmp_path, "other: 1\n")) == {}
        assert load_workspace_bindings(self._write(tmp_path, "")) == {}
        assert load_workspace_bindings(self._write(tmp_path, "bindings:\n")) == {}
        assert load_workspace_bindings(self._write(tmp_path, "bindings: {}\n")) == {}

    def test_bindings_block_is_returned(self, tmp_path):
        assert load_workspace_bindings(self._write(tmp_path, WORKSPACE_YAML)) == WORKSPACE
        assert load_workspace_bindings(str(tmp_path)) == WORKSPACE

    @pytest.mark.parametrize(
        "content, match",
        [
            ("- a\n", "expected a mapping at the top level, got list"),
            ("bindings: [rfe]\n", "'bindings' must be a mapping"),
            ("bindings:\n  rfe: jira\n", r"bindings\.rfe: expected a mapping of tracker"),
            (
                "bindings:\n  rfe:\n    jira: KONFLUX\n",
                r"bindings\.rfe\.jira: expected a mapping of binding fields",
            ),
            (
                "bindings:\n  rfe:\n    jira:\n      projekt: KONFLUX\n",
                r"unknown field\(s\) projekt; overridable fields: project, issue_type, "
                r"local_prefix",
            ),
            (
                "bindings:\n  rfe:\n    jira:\n      project: 5\n",
                r"bindings\.rfe\.jira\.project: expected a non-empty string, got 5",
            ),
            ("bindings:\n  rfe:\n    jira:\n      project: ''\n", "expected a non-empty string"),
            ("bindings: [unclosed\n", "invalid YAML"),
        ],
    )
    def test_invalid_shapes_raise(self, tmp_path, content, match):
        self._write(tmp_path, content)
        with pytest.raises(RegistryError, match=match) as excinfo:
            load_workspace_bindings(tmp_path)
        assert str(tmp_path / "rfe-creator.yaml") in str(excinfo.value)

    def test_registry_without_workspace_root_reads_nothing(self, tmp_path):
        self._write(tmp_path, WORKSPACE_YAML)
        reg = load(root=TYPES_ROOT, extra_roots=[], env={})
        assert reg.workspace_root is None
        assert reg.workspace_bindings() == {}

    def test_interactive_run_honours_the_file(self, tmp_path, capsys):
        self._write(tmp_path, WORKSPACE_YAML)
        reg = load(root=TYPES_ROOT, extra_roots=[], env={}, workspace_root=tmp_path)
        workspace = reg.workspace_bindings()
        assert workspace == WORKSPACE
        assert reg.bindings(workspace=workspace)["rfe"]["project"] == "KONFLUX"
        assert reg.get("rfe").binding(workspace=workspace)["source"] == "workspace"
        assert capsys.readouterr().err == ""

    @pytest.mark.parametrize("marker", type_registry.HEADLESS_MARKER_VARS)
    def test_headless_marker_ignores_the_file_with_one_stderr_line(self, tmp_path, marker, capsys):
        self._write(tmp_path, WORKSPACE_YAML)
        reg = load(root=TYPES_ROOT, extra_roots=[], env={marker: "1"}, workspace_root=tmp_path)
        assert reg.workspace_bindings() == {}
        err = capsys.readouterr().err
        assert err.count("\n") == 1, err
        assert "headless/CI run" in err
        assert str(tmp_path / "rfe-creator.yaml") in err
        assert "RFE_CREATOR_BINDING_" in err

    def test_headless_flag_ignores_the_file(self, tmp_path, capsys):
        self._write(tmp_path, WORKSPACE_YAML)
        reg = load(root=TYPES_ROOT, extra_roots=[], env={}, workspace_root=tmp_path)
        assert reg.workspace_bindings(headless=True) == {}
        assert capsys.readouterr().err.count("\n") == 1
        assert reg.workspace_bindings(headless=False) == WORKSPACE

    def test_registry_headless_flag_ignores_the_file(self, tmp_path, capsys):
        """``load(headless=True)`` (the ``resolve --headless`` path) is the same predicate:
        the per-call argument can add to it, never switch it back (PR-3 D4)."""
        self._write(tmp_path, WORKSPACE_YAML)
        reg = load(root=TYPES_ROOT, extra_roots=[], env={}, workspace_root=tmp_path, headless=True)
        assert reg.workspace_bindings() == {}
        assert capsys.readouterr().err.count("\n") == 1
        assert reg.workspace_bindings(headless=False) == {}

    def test_headless_without_an_effective_block_is_silent(self, tmp_path, capsys):
        reg = load(root=TYPES_ROOT, extra_roots=[], env={"CI": "true"}, workspace_root=tmp_path)
        assert reg.workspace_bindings() == {}  # no file at all
        self._write(tmp_path, "other: 1\n")
        assert reg.workspace_bindings() == {}
        self._write(tmp_path, "bindings: {}\n")
        assert reg.workspace_bindings() == {}
        assert capsys.readouterr().err == ""

    def test_malformed_file_raises_in_both_modes(self, tmp_path):
        self._write(tmp_path, "bindings: [x]\n")
        for env in ({}, {"CI": "true"}):
            reg = load(root=TYPES_ROOT, extra_roots=[], env=env, workspace_root=tmp_path)
            with pytest.raises(RegistryError, match="'bindings' must be a mapping"):
                reg.workspace_bindings()


# ── candidates (design §5 rung 3 over effective bindings, D5) ────────────────────


class TestCandidates:
    @pytest.mark.parametrize(
        "item_id, rung, names, provisional",
        [
            ("RFE-001", "local_id_pattern", ["rfe"], False),
            ("INIT-001", "local_id_pattern", ["initiative"], False),
            ("RHAIRFE-1", "key_prefix", ["rfe"], False),
            ("RHOAIENG-1", "key_prefix", ["initiative"], False),
            ("RHAIRFE-1234x", "key_prefix", ["rfe"], False),
            ("INIT-x", "local_prefix", ["initiative"], False),
            ("RFE-", "local_prefix", ["rfe"], False),
            ("KONFLUX-12", "tracker_grammar", ["rfe", "initiative"], True),
            ("RHAISTRAT-1", "tracker_grammar", ["rfe", "initiative"], True),
            ("AB1-9", "tracker_grammar", ["rfe", "initiative"], True),
            ("konflux-12", None, [], False),
            ("KONFLUX-", None, [], False),
            ("K-1", None, [], False),  # the grammar needs at least two leading characters
            ("KONFLUX-12x", None, [], False),
            ("free text idea", None, [], False),
            ("", None, [], False),
        ],
    )
    def test_shipped_ids(self, item_id, rung, names, provisional):
        found = _shipped().candidates(item_id)
        assert (found.rung, found.names, found.provisional) == (rung, names, provisional)
        assert all(isinstance(d, Descriptor) for d in found.matches)

    def test_none_and_non_strings_match_nothing(self):
        reg = _shipped()
        for value in (None, 1234, ["RFE-1"]):
            found = reg.candidates(value)
            assert found.matches == [] and found.rung is None and found.provisional is False

    def test_result_object(self):
        found = _shipped().candidates("RFE-001")
        assert isinstance(found, Candidates)
        assert found.matches[0] is _shipped().get("rfe") or found.matches[0].name == "rfe"
        assert type_registry.CANDIDATE_RUNGS == (
            "local_id_pattern",
            "key_prefix",
            "local_prefix",
            "tracker_grammar",
        )

    def test_rung_order_and_provisional_flag_are_consistent_with_detect(self):
        reg = _shipped()
        for item_id in ("RFE-001", "RHAIRFE-1", "INIT-x", "RHOAIENG-1", "RHAISTRAT-1", "nope"):
            found = reg.candidates(item_id)
            owner = reg.detect(item_id)
            if owner is not None:
                assert found.names == [owner.name] and not found.provisional
            else:
                assert found.provisional or not found.matches

    def test_project_override_moves_a_foreign_key_to_the_key_prefix_rung(self):
        reg = _shipped()
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"}
        found = reg.candidates("KONFLUX-12", env)
        assert (found.rung, found.names, found.provisional) == ("key_prefix", ["rfe"], False)
        # detect() keeps its descriptor-values-only contract
        assert reg.detect("KONFLUX-12") is None
        # the descriptor prefix stays a read prefix
        assert reg.candidates("RHAIRFE-1", env).names == ["rfe"]
        # other projects stay provisional
        assert reg.candidates("OTHER-1", env).rung == "tracker_grammar"

    def test_registry_env_is_the_default(self):
        env = {"RFE_CREATOR_BINDING_INITIATIVE_PROJECT": "PLAN"}
        reg = load(root=TYPES_ROOT, extra_roots=[], env=env)
        found = reg.candidates("PLAN-3")
        assert (found.rung, found.names) == ("key_prefix", ["initiative"])
        assert reg.candidates("PLAN-3", {}).rung == "tracker_grammar"  # explicit env wins

    def test_local_prefix_override_rerenders_rung_one(self):
        reg = _shipped()
        env = {"RFE_CREATOR_BINDING_RFE_LOCAL_PREFIX": "REQ-"}
        assert reg.candidates("REQ-7", env).rung == "local_id_pattern"
        assert reg.candidates("REQ-7", env).names == ["rfe"]
        assert reg.candidates("REQ-x", env).rung == "local_prefix"
        # read parity: the descriptor pattern stays a READ form, so the local ids minted before
        # the override are still rfe's (the effective pattern governs minting, not reading)
        found = reg.candidates("RFE-7", env)
        assert (found.rung, found.names, found.provisional) == ("local_id_pattern", ["rfe"], False)

    def test_workspace_mapping_is_honoured(self):
        found = _shipped().candidates("KONFLUX-12", {}, workspace=WORKSPACE)
        assert (found.rung, found.names) == ("key_prefix", ["rfe"])

    def test_every_type_matching_at_the_winning_rung_is_returned(self, tmp_path):
        root = tmp_path / "types"
        _add_type(root, "alpha", project="SHARED")
        _add_type(root, "beta", project="SHARED", issue_type="Epic")
        reg = load(root=root, extra_roots=[], env={})
        found = reg.candidates("SHARED-1")
        assert (found.rung, found.names, found.provisional) == (
            "key_prefix",
            ["alpha", "beta"],
            False,
        )
        assert reg.detect("SHARED-1").name == "alpha"  # the router keeps its first match

    def test_first_rung_with_a_match_wins_across_types(self, tmp_path):
        root = tmp_path / "types"
        _add_type(root, "alpha", project="AB")
        _add_type(root, "beta", project="ZZ", local_prefix="AB-")
        reg = load(root=root, extra_roots=[], env={})
        assert reg.candidates("AB-7").names == ["beta"]  # beta's local pattern beats alpha's key
        assert reg.candidates("AB-7").rung == "local_id_pattern"
        assert reg.candidates("AB-7x").names == ["alpha"]
        assert reg.candidates("AB-7x").rung == "key_prefix"

    def test_tracker_grammar_is_per_tracker(self, tmp_path):
        root = tmp_path / "types"
        _add_type(root, "jira-one", project="ONE")
        data = _minimal("gh", project="X")
        data["identity"]["tracker"] = "github"
        data["identity"]["github"] = {"repo": "o/r", "kind": "issue", "alias_prefix": "GH-"}
        del data["identity"]["jira"]
        _add_type(root, "gh", data=data)
        reg = load(root=root, extra_roots=[], env={})
        found = reg.candidates("ZZZ-1")
        assert (found.rung, found.names, found.provisional) == (
            "tracker_grammar",
            ["jira-one"],
            True,
        )
        assert reg.candidates("GH-1").names == ["gh"]


# ── assert_registered_binding (§3.2.1 g, §3.3 rule 1 at runtime) ─────────────────


class TestAssertRegisteredBinding:
    def test_default_binding_passes_and_is_returned(self):
        reg = _shipped()
        binding = assert_registered_binding(reg.get("rfe"), env={}, registry=reg)
        assert binding == RFE_DESCRIPTOR_BINDING
        assert assert_registered_binding(reg.get("initiative"), env={}, registry=reg)[
            "project"
        ] == ("RHOAIENG")

    def test_registry_defaults_to_a_fresh_load_with_the_same_env(self):
        desc = _shipped().get("rfe")
        assert assert_registered_binding(desc, env={})["source"] == "descriptor"
        env = {
            "RFE_CREATOR_BINDING_RFE_PROJECT": "RHOAIENG",
            "RFE_CREATOR_BINDING_RFE_ISSUE_TYPE": "Initiative",
        }
        with pytest.raises(RegistryError, match="registered for type 'initiative'"):
            assert_registered_binding(desc, env=env)

    def test_override_selecting_another_types_pair_is_rejected(self):
        reg = _shipped()
        env = {
            "RFE_CREATOR_BINDING_RFE_PROJECT": "RHOAIENG",
            "RFE_CREATOR_BINDING_RFE_ISSUE_TYPE": "Initiative",
        }
        with pytest.raises(RegistryError) as excinfo:
            assert_registered_binding(reg.get("rfe"), env=env, registry=reg)
        message = str(excinfo.value)
        assert message.startswith("rfe: effective binding ('jira', 'RHOAIENG', 'Initiative')")
        assert "registered for type 'initiative'" in message
        assert "--type initiative" in message

    def test_ownership_not_membership_for_the_shorthand(self):
        reg = _shipped()
        env = {"JIRA_PROJECT": "RHOAIENG", "JIRA_ISSUE_TYPE": "Initiative"}
        # without shorthand the pair is not applied at all
        assert assert_registered_binding(reg.get("rfe"), env=env, registry=reg)["source"] == (
            "descriptor"
        )
        with pytest.raises(RegistryError, match="registered for type 'initiative'"):
            assert_registered_binding(reg.get("rfe"), env=env, registry=reg, shorthand=True)
        # the initiative type itself owns the pair
        binding = assert_registered_binding(
            reg.get("initiative"), env=env, registry=reg, shorthand=True
        )
        assert binding["source"] == "shorthand"

    def test_same_project_with_a_distinct_issue_type_is_owned(self):
        reg = _shipped()
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "RHOAIENG"}
        binding = assert_registered_binding(reg.get("rfe"), env=env, registry=reg)
        assert (binding["project"], binding["issue_type"]) == ("RHOAIENG", "Feature Request")

    def test_effective_triples_are_compared(self):
        """The rule is over EFFECTIVE bindings: a pair another type moved away from is free."""
        reg = _shipped()
        env = {
            "RFE_CREATOR_BINDING_RFE_PROJECT": "RHOAIENG",
            "RFE_CREATOR_BINDING_RFE_ISSUE_TYPE": "Initiative",
            "RFE_CREATOR_BINDING_INITIATIVE_PROJECT": "PLAN",
        }
        assert assert_registered_binding(reg.get("rfe"), env=env, registry=reg)["project"] == (
            "RHOAIENG"
        )

    def test_workspace_override_is_rejected_when_headless(self):
        reg = _shipped()
        workspace = {"rfe": {"jira": {"project": "KONFLUX"}}}
        ok = assert_registered_binding(reg.get("rfe"), env={}, registry=reg, workspace=workspace)
        assert ok["project"] == "KONFLUX" and ok["source"] == "workspace"
        with pytest.raises(RegistryError, match="workspace file") as excinfo:
            assert_registered_binding(
                reg.get("rfe"), env={}, registry=reg, workspace=workspace, headless=True
            )
        assert "project" in str(excinfo.value) and "RFE_CREATOR_BINDING_" in str(excinfo.value)
        with pytest.raises(RegistryError, match="not trusted in a headless/CI run"):
            assert_registered_binding(
                reg.get("rfe"), env={"CI": "true"}, registry=reg, workspace=workspace
            )

    def test_registry_headless_flag_rejects_the_workspace_override(self):
        """A registry built with ``headless=True`` is headless here too (one predicate)."""
        reg = load(root=TYPES_ROOT, extra_roots=[], env={}, headless=True)
        workspace = {"rfe": {"jira": {"project": "KONFLUX"}}}
        with pytest.raises(RegistryError, match="not trusted in a headless/CI run"):
            assert_registered_binding(reg.get("rfe"), env={}, registry=reg, workspace=workspace)

    def test_env_override_is_fine_when_headless(self):
        reg = _shipped()
        env = {"CI": "true", "RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"}
        binding = assert_registered_binding(reg.get("rfe"), env=env, registry=reg, headless=True)
        assert binding["project"] == "KONFLUX" and binding["source"] == "env"

    def test_env_beating_the_workspace_is_fine_when_headless(self):
        """Only a CONTRIBUTING workspace source is untrusted: a field the env already set does
        not make the workspace a source."""
        reg = _shipped()
        env = {"CI": "true", "RFE_CREATOR_BINDING_RFE_PROJECT": "ACME"}
        workspace = {"rfe": {"jira": {"project": "KONFLUX"}}}
        binding = assert_registered_binding(
            reg.get("rfe"), env=env, registry=reg, workspace=workspace
        )
        assert binding["project"] == "ACME" and binding["source"] == "env"

    def test_env_defaults_to_the_descriptors_registry_env(self):
        env = {
            "RFE_CREATOR_BINDING_RFE_PROJECT": "RHOAIENG",
            "RFE_CREATOR_BINDING_RFE_ISSUE_TYPE": "Initiative",
        }
        reg = load(root=TYPES_ROOT, extra_roots=[], env=env)
        with pytest.raises(RegistryError, match="registered for type 'initiative'"):
            assert_registered_binding(reg.get("rfe"), registry=reg)

    def test_github_pair_is_compared_on_repo_and_kind(self, tmp_path):
        root = tmp_path / "types"
        for name, kind in (("gh-a", "issue"), ("gh-b", "pull")):
            data = _minimal(name, project="X")
            data["identity"]["tracker"] = "github"
            data["identity"]["github"] = {
                "repo": "o/r",
                "kind": kind,
                "alias_prefix": f"{kind[:2].upper()}-",
            }
            del data["identity"]["jira"]
            _add_type(root, name, data=data)
        reg = load(root=root, extra_roots=[], env={})
        assert (
            assert_registered_binding(reg.get("gh-a"), env={}, registry=reg)["tracker"] == "github"
        )


# ── resolve (design §5) ──────────────────────────────────────────────────────────


def _artifact(path, frontmatter=None, body="Body\n"):
    """Write a markdown artifact; ``frontmatter`` None writes no block at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = body
    if frontmatter is not None:
        block = yaml.safe_dump(frontmatter, sort_keys=False) if frontmatter else ""
        text = f"---\n{block}---\n{body}"
    path.write_text(text, encoding="utf-8")
    return path


class TestResolve:
    # -- rung 1: --type ---------------------------------------------------------------------

    def test_explicit_type(self):
        res = resolve(_shipped(), explicit_type="initiative", env={})
        assert isinstance(res, Resolution)
        assert res.type_name == "initiative" and res.desc.name == "initiative"
        assert res.rung == "--type" and res.provisional is False and res.candidates == []
        assert res.binding["source"] == "descriptor" and res.ambiguous is False
        assert res.line() == "TYPE RESOLVED: initiative (--type)"

    def test_unknown_explicit_type_lists_the_registered_types(self):
        with pytest.raises(ResolveError) as excinfo:
            resolve(_shipped(), explicit_type="epic", env={})
        assert excinfo.value.exit_code == 1
        assert (
            str(excinfo.value) == "unknown type 'epic' (--type); registered types: rfe, initiative"
        )

    def test_resolve_error_is_a_registry_error_with_an_exit_code(self):
        assert issubclass(ResolveError, RegistryError)
        assert ResolveError("x").exit_code == 1
        assert ResolveError("x", exit_code=3).exit_code == 3

    # -- rung 2: batch type ----------------------------------------------------------------

    def test_batch_mapping_type(self, tmp_path):
        batch = _write_yaml(tmp_path / "b.yaml", {"type": "initiative", "items": [{"prompt": "x"}]})
        res = resolve(_shipped(), batch=batch, env={})
        assert (res.type_name, res.rung) == ("initiative", "batch type")
        assert res.line() == "TYPE RESOLVED: initiative (batch type)"

    def test_batch_mapping_agreeing_with_explicit_type_is_rung_one(self, tmp_path):
        batch = _write_yaml(tmp_path / "b.yaml", {"type": "initiative", "items": []})
        res = resolve(_shipped(), explicit_type="initiative", batch=str(batch), env={})
        assert (res.type_name, res.rung) == ("initiative", "--type")

    def test_batch_mapping_disagreeing_with_explicit_type_is_an_error(self, tmp_path):
        batch = _write_yaml(tmp_path / "b.yaml", {"type": "initiative", "items": []})
        with pytest.raises(
            ResolveError, match="--type rfe disagrees with .*b.yaml type: initiative"
        ) as e:
            resolve(_shipped(), explicit_type="rfe", batch=batch, env={})
        assert e.value.exit_code == 1 and "D1" in str(e.value)

    def test_batch_mapping_with_an_unknown_type(self, tmp_path):
        batch = _write_yaml(tmp_path / "b.yaml", {"type": "epic", "items": []})
        with pytest.raises(ResolveError, match="unknown type 'epic' \\(.*b.yaml type:\\)"):
            resolve(_shipped(), batch=batch, env={})

    @pytest.mark.parametrize(
        "root",
        [
            [{"prompt": "x", "type": "initiative"}],
            [{"prompt": "x"}, {"prompt": "y", "type": "rfe"}],
            {"type": "rfe", "items": [{"prompt": "x", "type": "rfe"}]},
        ],
    )
    def test_per_item_type_is_rejected(self, tmp_path, root):
        batch = _write_yaml(tmp_path / "b.yaml", root)
        with pytest.raises(ResolveError, match="per-item 'type' key") as excinfo:
            resolve(_shipped(), batch=batch, env={})
        assert excinfo.value.exit_code == 1 and "D2" in str(excinfo.value)

    def test_per_item_type_is_rejected_even_with_an_explicit_type(self, tmp_path):
        batch = _write_yaml(tmp_path / "b.yaml", [{"prompt": "x", "type": "rfe"}])
        with pytest.raises(ResolveError, match="per-item 'type' key"):
            resolve(_shipped(), explicit_type="rfe", batch=batch, env={})

    def test_legacy_list_gives_no_rung_two_signal_but_its_string_items_join_the_ids(self, tmp_path):
        batch = _write_yaml(tmp_path / "b.yaml", ["RHOAIENG-1", {"prompt": "x"}, "RHOAIENG-2"])
        res = resolve(_shipped(), batch=batch, env={})
        assert (res.type_name, res.rung) == ("initiative", "id grammar")
        prompts_only = _write_yaml(tmp_path / "p.yaml", [{"prompt": "x"}, {"prompt": "y"}])
        res = resolve(_shipped(), batch=prompts_only, env={})
        assert (res.type_name, res.rung) == ("rfe", "legacy default")

    def test_mapping_form_string_items_join_the_ids_too(self, tmp_path):
        batch = _write_yaml(tmp_path / "b.yaml", {"type": "initiative", "items": ["RFE-1"]})
        with pytest.raises(
            ResolveError, match="conflicting type signals: batch type initiative vs RFE-1 -> rfe"
        ):
            resolve(_shipped(), batch=batch, env={})

    @pytest.mark.parametrize(
        "content, match",
        [
            ("type: rfe\n", "expected a list of items .* got a mapping with keys type"),
            ("items: []\n", "expected a list of items .* got a mapping with keys items"),
            ("42\n", "got int"),
            ("", "got nothing"),
            ("type: rfe\nitems: nope\n", "'items' must be a list, got str"),
            ("type: 5\nitems: []\n", "'type' must be a non-empty string, got 5"),
            ("type: ''\nitems: []\n", "'type' must be a non-empty string"),
            ("- [unclosed\n", "invalid YAML in batch file"),
        ],
    )
    def test_other_root_shapes_are_errors(self, tmp_path, content, match):
        batch = tmp_path / "b.yaml"
        batch.write_text(content, encoding="utf-8")
        with pytest.raises(ResolveError, match=match) as excinfo:
            resolve(_shipped(), batch=batch, env={})
        assert excinfo.value.exit_code == 1

    def test_unreadable_batch_is_an_error(self, tmp_path):
        with pytest.raises(ResolveError, match="cannot read batch file"):
            resolve(_shipped(), batch=tmp_path / "missing.yaml", env={})

    # -- rung 3: artifact and id signals --------------------------------------------------

    def test_frontmatter_type(self, tmp_path):
        art = _artifact(tmp_path / "anywhere" / "X-1.md", {"type": "initiative", "title": "t"})
        res = resolve(_shipped(), artifact=art, env={})
        assert (res.type_name, res.rung, res.provisional) == (
            "initiative",
            "frontmatter type",
            False,
        )
        assert res.line() == "TYPE RESOLVED: initiative (frontmatter type)"

    def test_frontmatter_type_beats_the_directory_and_the_stem(self, tmp_path):
        art = _artifact(tmp_path / "rfe-tasks" / "RFE-001.md", {"type": "initiative"})
        assert resolve(_shipped(), artifact=str(art), env={}).type_name == "initiative"

    def test_frontmatter_type_unknown(self, tmp_path):
        art = _artifact(tmp_path / "x" / "X-1.md", {"type": "epic"})
        with pytest.raises(
            ResolveError, match="unknown type 'epic' \\(.*X-1.md frontmatter type:\\)"
        ):
            resolve(_shipped(), artifact=art, env={})

    def test_frontmatter_type_must_be_a_string(self, tmp_path):
        art = _artifact(tmp_path / "x" / "X-1.md", {"type": 5})
        with pytest.raises(ResolveError, match="frontmatter 'type' must be a non-empty string"):
            resolve(_shipped(), artifact=art, env={})

    def test_null_frontmatter_type_is_no_signal(self, tmp_path):
        art = _artifact(tmp_path / "initiatives" / "X-1.md", {"type": None, "title": "t"})
        res = resolve(_shipped(), artifact=art, env={})
        assert (res.type_name, res.rung) == ("initiative", "artifact dir")

    def test_artifact_dir_when_no_frontmatter_type(self, tmp_path):
        art = _artifact(tmp_path / "initiatives" / "X-1.md", {"title": "t"})
        res = resolve(_shipped(), artifact=art, env={})
        assert (res.type_name, res.rung) == ("initiative", "artifact dir")
        assert res.line() == "TYPE RESOLVED: initiative (artifact dir)"
        for directory in ("rfe-tasks", "rfe-originals", "rfe-reviews"):
            art = _artifact(tmp_path / directory / "X-1-review.md")  # no frontmatter at all
            assert resolve(_shipped(), artifact=art, env={}).rung == "artifact dir"
            assert resolve(_shipped(), artifact=art, env={}).type_name == "rfe"

    def test_artifact_stem_joins_the_ids_when_the_dir_is_unknown(self, tmp_path):
        art = _artifact(tmp_path / "misc" / "RHOAIENG-42.md")
        res = resolve(_shipped(), artifact=art, env={})
        assert (res.type_name, res.rung) == ("initiative", "id grammar")
        art = _artifact(tmp_path / "misc" / "RHAIRFE-1595-comments.md")
        assert resolve(_shipped(), artifact=art, env={}).type_name == "rfe"
        art = _artifact(tmp_path / "misc" / "notes.md")
        assert resolve(_shipped(), artifact=art, env={}).rung == "legacy default"

    def test_bare_artifact_name_has_no_directory_signal(self, tmp_path, monkeypatch):
        monkeypatch.chdir(_artifact(tmp_path / "rfe-tasks" / "notes.md").parent)
        assert resolve(_shipped(), artifact="notes.md", env={}).rung == "legacy default"

    def test_missing_or_broken_artifact(self, tmp_path):
        with pytest.raises(ResolveError, match="artifact file not found"):
            resolve(_shipped(), artifact=tmp_path / "nope.md", env={})
        broken = tmp_path / "x" / "X.md"
        broken.parent.mkdir()
        broken.write_text("---\ntype: [unclosed\n---\nBody\n", encoding="utf-8")
        with pytest.raises(ResolveError, match="invalid frontmatter YAML"):
            resolve(_shipped(), artifact=broken, env={})

    def test_frontmatter_parser_edge_cases(self):
        parse = type_registry._parse_frontmatter
        assert parse("---\ntype: rfe\n---\nBody\n", "p") == {"type": "rfe"}
        assert parse("---\n---\nBody\n", "p") == {}
        assert parse("# Heading\n---\ntype: rfe\n---\n", "p") == {}  # must start on line 1
        assert parse("---\ntype: rfe\n", "p") == {}  # unterminated
        assert parse("---\n- a\n---\n", "p") == {}  # not a mapping
        assert parse("", "p") == {}

    def test_ids_resolve_at_the_id_grammar_rung(self):
        res = resolve(_shipped(), ids=["RHAIRFE-1", "RFE-002"], env={})
        assert (res.type_name, res.rung, res.provisional) == ("rfe", "id grammar", False)
        assert res.line() == "TYPE RESOLVED: rfe (id grammar)"
        res = resolve(_shipped(), ids=("INIT-1",), env={})
        assert res.type_name == "initiative"

    def test_unowned_ids_and_non_strings_are_no_signal(self):
        res = resolve(_shipped(), ids=["free text idea", "", None, 12], env={})
        assert (res.type_name, res.rung) == ("rfe", "legacy default")

    def test_the_strongest_rung_labels_the_resolution(self, tmp_path):
        typed = _artifact(tmp_path / "misc" / "X.md", {"type": "rfe"})
        assert (
            resolve(_shipped(), artifact=typed, ids=["RHAIRFE-1"], env={}).rung
            == "frontmatter type"
        )
        placed = _artifact(tmp_path / "rfe-tasks" / "X.md", {"title": "t"})
        assert (
            resolve(_shipped(), artifact=placed, ids=["RHAIRFE-1"], env={}).rung == "artifact dir"
        )

    # -- conflicts --------------------------------------------------------------------------

    def test_conflicting_id_signals(self):
        with pytest.raises(ResolveError) as excinfo:
            resolve(_shipped(), ids=["RFE-1", "INIT-2"], env={})
        message = str(excinfo.value)
        assert message.startswith("conflicting type signals: RFE-1 -> rfe, INIT-2 -> initiative")
        assert excinfo.value.exit_code == 1

    def test_conflict_between_an_artifact_and_an_id(self, tmp_path):
        art = _artifact(tmp_path / "x" / "X.md", {"type": "initiative"})
        with pytest.raises(
            ResolveError, match="X.md \\(type: initiative\\) -> initiative, RFE-1 -> rfe"
        ):
            resolve(_shipped(), artifact=art, ids=["RFE-1"], env={})
        art = _artifact(tmp_path / "initiatives" / "X.md")
        with pytest.raises(
            ResolveError, match="X.md \\(dir initiatives\\) -> initiative, RFE-1 -> rfe"
        ):
            resolve(_shipped(), artifact=art, ids=["RFE-1"], env={})

    def test_explicit_type_conflicting_with_a_deterministic_signal(self):
        with pytest.raises(ResolveError) as excinfo:
            resolve(_shipped(), explicit_type="initiative", ids=["RFE-1"], env={})
        assert str(excinfo.value).startswith(
            "conflicting type signals: --type initiative vs RFE-1 -> rfe"
        )
        assert excinfo.value.exit_code == 1

    def test_explicit_type_with_agreeing_and_provisional_signals(self):
        res = resolve(_shipped(), explicit_type="rfe", ids=["RFE-1", "KONFLUX-12"], env={})
        assert (res.type_name, res.rung, res.provisional) == ("rfe", "--type", False)
        res = resolve(_shipped(), explicit_type="initiative", ids=["KONFLUX-12"], env={})
        assert (res.type_name, res.rung, res.provisional) == ("initiative", "--type", False)

    def test_conflicts_are_detected_even_in_headless_runs(self):
        with pytest.raises(ResolveError, match="conflicting type signals"):
            resolve(_shipped(), ids=["RFE-1", "INIT-2"], env={"CI": "true"})

    # -- provisional (tracker grammar) ------------------------------------------------------

    def test_provisional_single_type(self, tmp_path):
        root = _copy_types(tmp_path / "types", names=("rfe",))
        res = resolve(load(root=root, extra_roots=[], env={}), ids=["KONFLUX-12"], env={})
        assert (res.type_name, res.rung, res.provisional) == ("rfe", "id grammar", True)
        assert res.candidates == []
        assert res.line() == "TYPE RESOLVED: rfe (id grammar)"

    def test_project_override_makes_the_key_deterministic(self):
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"}
        res = resolve(_shipped(), ids=["KONFLUX-12"], env=env)
        assert (res.type_name, res.rung, res.provisional) == ("rfe", "id grammar", False)
        assert res.line() == "TYPE RESOLVED: rfe (id grammar; binding override project=KONFLUX)"

    def test_a_deterministic_signal_beats_provisional_ones(self):
        res = resolve(_shipped(), ids=["KONFLUX-12", "INIT-1", "OTHER-3"], env={})
        assert (res.type_name, res.rung, res.provisional) == ("initiative", "id grammar", False)

    # -- ambiguity ---------------------------------------------------------------------------

    @pytest.mark.parametrize("kwargs", [{"env": {"CI": "true"}}, {"env": {}, "headless": True}])
    def test_ambiguous_headless_is_a_resolve_error_with_exit_3(self, kwargs):
        with pytest.raises(ResolveError) as excinfo:
            resolve(_shipped(), ids=["KONFLUX-12"], **kwargs)
        assert excinfo.value.exit_code == 3
        message = str(excinfo.value)
        assert "KONFLUX-12" in message and "candidates rfe, initiative" in message
        assert "--type" in message

    def test_ambiguous_interactive_returns_the_candidates(self):
        res = resolve(_shipped(), ids=["KONFLUX-12"], env={})
        assert res.ambiguous is True
        assert res.type_name is None and res.desc is None and res.rung is None
        assert res.binding is None
        assert res.candidates == ["rfe", "initiative"]
        assert res.provisional is True
        assert res.line() == "TYPE AMBIGUOUS: rfe, initiative - pass --type"

    def test_shared_prefix_ambiguity_is_deterministic_but_unresolved(self, tmp_path):
        root = tmp_path / "types"
        _add_type(root, "alpha", project="SHARED")
        _add_type(root, "beta", project="SHARED", issue_type="Epic")
        reg = load(root=root, extra_roots=[], env={})
        res = resolve(reg, ids=["SHARED-1"], env={})
        assert res.type_name is None and res.candidates == ["alpha", "beta"]
        assert res.provisional is False
        with pytest.raises(ResolveError) as excinfo:
            resolve(reg, ids=["SHARED-1"], env={}, headless=True)
        assert excinfo.value.exit_code == 3
        # a second, single-type signal narrows the candidate set down
        res = resolve(reg, ids=["SHARED-1", "ALPHA-2"], env={})
        assert (res.type_name, res.rung) == ("alpha", "id grammar")
        # and one outside the set conflicts
        with pytest.raises(ResolveError, match="conflicting type signals"):
            _add_type(root, "gamma", project="GAMMA")
            resolve(load(root=root, extra_roots=[], env={}), ids=["SHARED-1", "GAMMA-1"], env={})

    # -- rung 5: legacy default -------------------------------------------------------------

    def test_legacy_default(self):
        res = resolve(_shipped(), env={})
        assert (res.type_name, res.rung, res.provisional) == ("rfe", "legacy default", False)
        assert res.line() == "TYPE RESOLVED: rfe (legacy default)"
        assert resolve(_shipped(), env={"CI": "true"}).rung == "legacy default"

    def test_legacy_default_requires_rfe(self, tmp_path):
        root = _copy_types(tmp_path / "types", names=("initiative",))
        with pytest.raises(ResolveError) as excinfo:
            resolve(load(root=root, extra_roots=[], env={}), env={})
        assert excinfo.value.exit_code == 1
        assert "legacy default type 'rfe' is not registered" in str(excinfo.value)
        assert "registered types: initiative" in str(excinfo.value)

    # -- binding and the printed line -----------------------------------------------------

    def test_binding_applies_the_shorthand_to_the_resolved_type_only(self):
        reg = _shipped()
        env = {"JIRA_PROJECT": "KONFLUX"}
        res = resolve(reg, explicit_type="rfe", env=env)
        assert res.binding["project"] == "KONFLUX" and res.binding["source"] == "shorthand"
        assert res.line() == "TYPE RESOLVED: rfe (--type; binding override project=KONFLUX)"
        assert reg.get("initiative").binding(env)["source"] == "descriptor"
        assert reg.bindings(env)["rfe"]["source"] == "descriptor"

    def test_line_renders_overrides_in_field_order(self):
        env = {
            "RFE_CREATOR_BINDING_RFE_LOCAL_PREFIX": "REQ-",
            "RFE_CREATOR_BINDING_RFE_ISSUE_TYPE": "Story",
            "RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX",
        }
        res = resolve(_shipped(), explicit_type="rfe", env=env)
        assert res.line() == (
            "TYPE RESOLVED: rfe (--type; binding override project=KONFLUX issue_type=Story "
            "local_prefix=REQ-)"
        )
        res = resolve(_shipped(), explicit_type="rfe", env={"JIRA_ISSUE_TYPE": "Story"})
        assert res.line() == "TYPE RESOLVED: rfe (--type; binding override issue_type=Story)"

    def test_env_defaults_to_the_registry_env(self):
        reg = load(
            root=TYPES_ROOT, extra_roots=[], env={"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"}
        )
        assert resolve(reg, explicit_type="rfe").line() == (
            "TYPE RESOLVED: rfe (--type; binding override project=KONFLUX)"
        )
        headless = load(root=TYPES_ROOT, extra_roots=[], env={"CI": "true"})
        with pytest.raises(ResolveError) as excinfo:
            resolve(headless, ids=["KONFLUX-12"])
        assert excinfo.value.exit_code == 3

    def test_workspace_binding_in_the_resolution(self):
        workspace = {"rfe": {"jira": {"project": "KONFLUX"}}}
        res = resolve(_shipped(), explicit_type="rfe", env={}, workspace=workspace)
        assert res.binding["source"] == "workspace"
        assert res.line() == "TYPE RESOLVED: rfe (--type; binding override project=KONFLUX)"
        assert (
            resolve(_shipped(), ids=["KONFLUX-12"], env={}, workspace=workspace).type_name == "rfe"
        )

    def test_registry_headless_flag_makes_ambiguity_an_error(self):
        """A registry built with ``headless=True`` resolves headless without a per-call
        flag and without a marker (PR-3 D4)."""
        reg = load(root=TYPES_ROOT, extra_roots=[], env={}, headless=True)
        with pytest.raises(ResolveError) as excinfo:
            resolve(reg, ids=["KONFLUX-12"])
        assert excinfo.value.exit_code == 3
        assert resolve(reg, explicit_type="rfe").line() == "TYPE RESOLVED: rfe (--type)"

    def test_workspace_sourced_binding_is_refused_headless(self):
        workspace = {"rfe": {"jira": {"project": "KONFLUX"}}}
        with pytest.raises(ResolveError, match="workspace file") as excinfo:
            resolve(_shipped(), explicit_type="rfe", env={}, headless=True, workspace=workspace)
        assert excinfo.value.exit_code == 1
        with pytest.raises(ResolveError, match="not trusted in a headless/CI run"):
            resolve(_shipped(), explicit_type="rfe", env={"CI": "1"}, workspace=workspace)
        # an env override for the same field keeps the workspace out of the sources
        env = {"CI": "1", "RFE_CREATOR_BINDING_RFE_PROJECT": "ACME"}
        assert (
            resolve(_shipped(), explicit_type="rfe", env=env, workspace=workspace).binding["source"]
            == "env"
        )

    def test_invalid_override_surfaces_as_a_registry_error(self):
        with pytest.raises(RegistryError, match="RFE_CREATOR_BINDING_RFE_PROJECT='konflux'"):
            resolve(
                _shipped(), explicit_type="rfe", env={"RFE_CREATOR_BINDING_RFE_PROJECT": "konflux"}
            )

    def test_as_dict_shape(self):
        res = resolve(_shipped(), explicit_type="rfe", env={})
        data = res.as_dict()
        assert list(data) == ["type", "rung", "provisional", "binding", "candidates", "line"]
        assert data["type"] == "rfe" and data["line"] == res.line()
        assert json.loads(json.dumps(data)) == data
        ambiguous = resolve(_shipped(), ids=["KONFLUX-12"], env={}).as_dict()
        assert ambiguous["type"] is None and ambiguous["binding"] is None
        assert ambiguous["candidates"] == ["rfe", "initiative"]

    def test_rung_vocabulary_is_pinned(self):
        assert type_registry.RESOLVE_RUNGS == (
            "--type",
            "batch type",
            "frontmatter type",
            "artifact dir",
            "id grammar",
            "legacy default",
        )
        assert type_registry.LEGACY_DEFAULT_TYPE == "rfe"
        assert type_registry.EXIT_AMBIGUOUS == 3


# ── resolve / candidates CLI ─────────────────────────────────────────────────────


class TestResolveCli:
    def test_explicit_type_prints_the_line(self):
        result = _cli("resolve", "--type", "rfe")
        assert result.returncode == 0, result.stderr
        assert result.stdout == "TYPE RESOLVED: rfe (--type)\n"
        assert result.stderr == ""

    def test_unknown_type_exits_1_with_the_registered_list(self):
        result = _cli("resolve", "--type", "epic")
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.strip() == (
            "ERROR: unknown type 'epic' (--type); registered types: rfe, initiative"
        )

    def test_legacy_default_is_printed_by_the_resolve_cli(self):
        result = _cli("resolve")
        assert result.returncode == 0, result.stderr
        assert result.stdout == "TYPE RESOLVED: rfe (legacy default)\n"

    def test_ids(self):
        result = _cli("resolve", "RHOAIENG-1", "INIT-2")
        assert result.returncode == 0, result.stderr
        assert result.stdout == "TYPE RESOLVED: initiative (id grammar)\n"

    def test_conflict_exits_1(self):
        result = _cli("resolve", "RFE-1", "INIT-1")
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.startswith(
            "ERROR: conflicting type signals: RFE-1 -> rfe, INIT-1 -> initiative"
        )

    def test_ambiguous_interactive_exits_3_on_stdout(self):
        result = _cli("resolve", "KONFLUX-12")
        assert result.returncode == 3
        assert result.stdout == "TYPE AMBIGUOUS: rfe, initiative - pass --type\n"
        assert result.stderr == ""

    def test_ambiguous_headless_flag_exits_3_on_stderr(self):
        result = _cli("resolve", "--headless", "KONFLUX-12")
        assert result.returncode == 3
        assert result.stdout == ""
        assert result.stderr.startswith(
            "ERROR: ambiguous type for KONFLUX-12: candidates rfe, initiative"
        )

    @pytest.mark.parametrize("marker", type_registry.HEADLESS_MARKER_VARS)
    def test_ambiguous_headless_marker_exits_3(self, marker):
        result = _cli("resolve", "KONFLUX-12", env={marker: "true"})
        assert result.returncode == 3
        assert result.stdout == "" and "ERROR: ambiguous type" in result.stderr

    def test_override_shows_in_the_line(self):
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"}
        result = _cli("resolve", "KONFLUX-12", env=env)
        assert result.returncode == 0, result.stderr
        assert (
            result.stdout == "TYPE RESOLVED: rfe (id grammar; binding override project=KONFLUX)\n"
        )

    def test_shorthand_applies_to_the_resolved_type(self):
        result = _cli("resolve", "--type", "initiative", env={"JIRA_PROJECT": "PLAN"})
        assert result.returncode == 0, result.stderr
        assert (
            result.stdout == "TYPE RESOLVED: initiative (--type; binding override project=PLAN)\n"
        )

    def test_json_output(self):
        result = _cli("resolve", "--json", "--type", "rfe", env={"JIRA_PROJECT": "KONFLUX"})
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert list(data) == ["type", "rung", "provisional", "binding", "candidates", "line"]
        assert data["type"] == "rfe" and data["rung"] == "--type" and data["provisional"] is False
        assert data["binding"]["project"] == "KONFLUX"
        assert data["binding"]["source"] == "shorthand"
        assert data["binding"]["overrides"] == ["project"]
        assert data["candidates"] == []
        assert data["line"] == "TYPE RESOLVED: rfe (--type; binding override project=KONFLUX)"

    def test_json_ambiguous(self):
        result = _cli("--json", "resolve", "KONFLUX-12")
        assert result.returncode == 3
        data = json.loads(result.stdout)
        assert data["type"] is None and data["rung"] is None and data["binding"] is None
        assert data["provisional"] is True
        assert data["candidates"] == ["rfe", "initiative"]
        assert data["line"] == "TYPE AMBIGUOUS: rfe, initiative - pass --type"

    def test_json_provisional_single_type(self, tmp_path):
        root = _copy_types(tmp_path / "types", names=("rfe",))
        result = _cli("resolve", "--json", "--root", str(root), "KONFLUX-12")
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert (data["type"], data["rung"], data["provisional"]) == ("rfe", "id grammar", True)

    def test_batch_option(self, tmp_path):
        batch = _write_yaml(tmp_path / "b.yaml", {"type": "initiative", "items": [{"prompt": "x"}]})
        result = _cli("resolve", "--batch", str(batch))
        assert result.returncode == 0, result.stderr
        assert result.stdout == "TYPE RESOLVED: initiative (batch type)\n"
        conflict = _cli("resolve", "--type", "rfe", "--batch", str(batch))
        assert conflict.returncode == 1
        assert conflict.stderr.startswith("ERROR: --type rfe disagrees with")
        typed_item = _write_yaml(tmp_path / "t.yaml", [{"prompt": "x", "type": "rfe"}])
        rejected = _cli("resolve", "--batch", str(typed_item))
        assert rejected.returncode == 1 and "per-item 'type' key" in rejected.stderr

    def test_artifact_option(self, tmp_path):
        art = _artifact(tmp_path / "x" / "X.md", {"type": "initiative"})
        result = _cli("resolve", "--artifact", str(art))
        assert result.returncode == 0, result.stderr
        assert result.stdout == "TYPE RESOLVED: initiative (frontmatter type)\n"
        placed = _artifact(tmp_path / "rfe-tasks" / "X.md")
        assert (
            _cli("resolve", "--artifact", str(placed)).stdout
            == "TYPE RESOLVED: rfe (artifact dir)\n"
        )
        missing = _cli("resolve", "--artifact", str(tmp_path / "nope.md"))
        assert missing.returncode == 1 and "artifact file not found" in missing.stderr

    def test_workspace_root_is_honoured_interactively(self, tmp_path):
        (tmp_path / "rfe-creator.yaml").write_text(WORKSPACE_YAML, encoding="utf-8")
        result = _cli("resolve", "--workspace-root", str(tmp_path), "--type", "rfe")
        assert result.returncode == 0, result.stderr
        assert result.stdout == (
            "TYPE RESOLVED: rfe (--type; binding override project=KONFLUX "
            "issue_type=Feature Request)\n"
        )
        assert result.stderr == ""
        as_json = _cli("resolve", "--json", "--workspace-root", str(tmp_path), "--type", "rfe")
        assert json.loads(as_json.stdout)["binding"]["source"] == "workspace"

    def test_workspace_root_is_ignored_headless_with_one_stderr_line(self, tmp_path):
        (tmp_path / "rfe-creator.yaml").write_text(WORKSPACE_YAML, encoding="utf-8")
        for args, env in ((("--headless",), {}), ((), {"CI": "true"})):
            result = _cli(
                "resolve", *args, "--workspace-root", str(tmp_path), "--type", "rfe", env=env
            )
            assert result.returncode == 0, result.stderr
            assert result.stdout == "TYPE RESOLVED: rfe (--type)\n"
            assert result.stderr.count("\n") == 1
            assert "ignoring the workspace bindings file" in result.stderr

    def test_the_cwd_is_never_probed(self, tmp_path):
        (tmp_path / "rfe-creator.yaml").write_text(WORKSPACE_YAML, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / SCRIPT), "resolve", "--type", "rfe"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_clean_env(),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "TYPE RESOLVED: rfe (--type)\n"

    def test_invalid_workspace_file_exits_1(self, tmp_path):
        (tmp_path / "rfe-creator.yaml").write_text("bindings: [x]\n", encoding="utf-8")
        result = _cli("resolve", "--workspace-root", str(tmp_path), "--type", "rfe")
        assert result.returncode == 1
        assert (
            result.stderr.startswith("ERROR: ") and "'bindings' must be a mapping" in result.stderr
        )

    def test_invalid_env_override_exits_1(self):
        result = _cli(
            "resolve", "--type", "rfe", env={"RFE_CREATOR_BINDING_RFE_PROJECT": "konflux"}
        )
        assert result.returncode == 1
        assert "RFE_CREATOR_BINDING_RFE_PROJECT='konflux'" in result.stderr

    def test_resolve_help_lists_the_options(self):
        result = _cli("resolve", "--help")
        assert result.returncode == 0
        for option in (
            "--type",
            "--batch",
            "--artifact",
            "--headless",
            "--workspace-root",
            "--json",
        ):
            assert option in result.stdout

    def test_candidates_subcommand(self):
        assert (
            _cli("candidates", "KONFLUX-12").stdout
            == "tracker_grammar (provisional): rfe initiative\n"
        )
        assert _cli("candidates", "RFE-1").stdout == "local_id_pattern: rfe\n"
        assert _cli("candidates", "RHOAIENG-1").stdout == "key_prefix: initiative\n"
        assert _cli("candidates", "INIT-x").stdout == "local_prefix: initiative\n"
        none = _cli("candidates", "nope")
        assert none.returncode == 0 and none.stdout == "none: -\n"
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"}
        assert _cli("candidates", "KONFLUX-12", env=env).stdout == "key_prefix: rfe\n"

    def test_candidates_json(self):
        result = _cli("candidates", "RHAIRFE-1", "--json")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {
            "id": "RHAIRFE-1",
            "rung": "key_prefix",
            "provisional": False,
            "types": ["rfe"],
        }

    def test_binding_cli_carries_the_new_keys_once_they_inform(self):
        """The CLI view shows ``overrides`` as soon as something is overridden and the
        ``local_id_pattern`` as soon as it differs from the descriptor's (D13 re-render)."""
        env = {"RFE_CREATOR_BINDING_RFE_LOCAL_PREFIX": "REQ-"}
        binding = json.loads(_cli("binding", "rfe", "--json", env=env).stdout)
        assert binding["local_id_pattern"] == r"^REQ\-\d+$"
        assert binding["overrides"] == ["local_prefix"]
        # a project override lists itself but leaves the (unchanged) pattern out
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"}
        binding = json.loads(_cli("binding", "rfe", "--json", env=env).stdout)
        assert binding["overrides"] == ["project"] and binding["source"] == "env"
        assert "local_id_pattern" not in binding
        text = _cli("binding", "rfe", env=env).stdout
        assert text.endswith("source: env\noverrides:\n- project\n")
        assert "local_id_pattern" not in text
        # the binding CLI never applies the shorthand: it is not a resolve
        binding = json.loads(
            _cli("binding", "rfe", "--json", env={"JIRA_PROJECT": "KONFLUX"}).stdout
        )
        assert binding["source"] == "descriptor"
        assert "overrides" not in binding


# ── the shared --type hand-parser (PR-3a) ─────────────────────────────────────────


class TestParseTypeArg:
    """``parse_type_arg`` is the one hand-parser behind check_revised / check_right_sized /
    check_autofix_complete; each prints ``ERROR: <message>`` and exits with ``exit_code``, so
    the error text is pinned once, here."""

    UNKNOWN = "unknown --type 'bogus'; registered types: rfe, initiative"
    TRAILING = "--type requires a value; registered types: rfe, initiative"

    def test_pops_the_flag_and_value_wherever_they_sit(self):
        reg = _shipped()
        assert parse_type_arg(reg, ["--type", "initiative"]) == ("initiative", [])
        assert parse_type_arg(reg, ["a", "--type", "initiative", "b"]) == ("initiative", ["a", "b"])
        assert parse_type_arg(reg, ["a", "--type", "rfe"]) == ("rfe", ["a"])

    def test_default_when_the_flag_is_absent(self):
        reg = _shipped()
        assert parse_type_arg(reg, []) == ("rfe", [])
        assert parse_type_arg(reg, ["--batch", "X"]) == ("rfe", ["--batch", "X"])
        assert parse_type_arg(reg, ["x"], default="initiative") == ("initiative", ["x"])

    def test_argv_is_not_mutated_and_only_the_first_flag_is_consumed(self):
        argv = ["--type", "rfe", "--type", "bogus"]
        assert parse_type_arg(_shipped(), argv) == ("rfe", ["--type", "bogus"])
        assert argv == ["--type", "rfe", "--type", "bogus"]
        assert parse_type_arg(_shipped(), ("--type", "rfe")) == ("rfe", [])

    def test_unknown_type_is_a_usage_error_with_the_registered_list(self):
        with pytest.raises(ResolveError) as excinfo:
            parse_type_arg(_shipped(), ["--type", "bogus", "--batch"])
        assert excinfo.value.exit_code == 2
        assert str(excinfo.value) == self.UNKNOWN

    def test_trailing_flag_is_a_usage_error(self):
        with pytest.raises(ResolveError) as excinfo:
            parse_type_arg(_shipped(), ["--batch", "--type"])
        assert excinfo.value.exit_code == 2
        assert str(excinfo.value) == self.TRAILING

    def test_unregistered_default_is_a_usage_error(self, tmp_path):
        root = tmp_path / "root"
        _add_type(root, "docs", project="DOCS")
        reg = load(root=root, extra_roots=[], env={})
        with pytest.raises(ResolveError) as excinfo:
            parse_type_arg(reg, [])
        assert excinfo.value.exit_code == 2
        assert str(excinfo.value) == (
            "no --type given and the default type 'rfe' is not registered; registered types: docs"
        )
        assert parse_type_arg(reg, ["--type", "docs"]) == ("docs", [])

    def test_custom_flag(self):
        assert parse_type_arg(_shipped(), ["--kind", "initiative"], flag="--kind") == (
            "initiative",
            [],
        )
        with pytest.raises(ResolveError, match="unknown --kind 'x'"):
            parse_type_arg(_shipped(), ["--kind", "x"], flag="--kind")

    def test_the_three_gates_share_it(self):
        """Source-form pin: no gate carries its own copy — the drift this helper exists to
        prevent — and each prints the message behind an ``ERROR:`` prefix with its exit code."""
        for rel in ("check_revised.py", "check_right_sized.py", "check_autofix_complete.py"):
            text = (REPO_ROOT / "scripts" / rel).read_text(encoding="utf-8")
            assert "type_registry.parse_type_arg(_TYPES, sys.argv[1:])" in text, rel
            assert "def _parse_type_arg" not in text, rel
            assert 'print(f"ERROR: {exc}", file=sys.stderr)' in text, rel
            assert "sys.exit(exc.exit_code)" in text, rel


# ── PR-3a review follow-ups ─────────────────────────────────────────────────────


class TestReadParityStrictIdsAndWorkspaceNames:
    """Read parity under a ``LOCAL_PREFIX`` override, D5 for headless unowned ids, artifact
    stems as conflict signals, and unregistered type names in the workspace file."""

    def test_local_prefix_override_keeps_descriptor_forms_readable(self):
        reg = _shipped()
        env = {"RFE_CREATOR_BINDING_RFE_LOCAL_PREFIX": "REQ-"}
        assert reg.candidates("REQ-7", env).names == ["rfe"]
        found = reg.candidates("RFE-001", env)
        assert (found.names, found.rung, found.provisional) == (["rfe"], "local_id_pattern", False)
        assert reg.candidates("RFE-x", env).rung == "local_prefix"
        res = resolve(reg, ids=["RFE-001"], env=env)
        assert (res.type_name, res.rung, res.provisional) == ("rfe", "id grammar", False)

    def test_headless_unowned_id_is_an_error(self):
        with pytest.raises(ResolveError, match="no registered type owns id 'free text'") as excinfo:
            resolve(_shipped(), ids=["free text"], env={"RFE_CREATOR_HEADLESS": "1"})
        assert excinfo.value.exit_code == 1

    def test_headless_unowned_id_is_fine_under_an_explicit_or_batch_type(self, tmp_path):
        env = {"RFE_CREATOR_HEADLESS": "1"}
        assert resolve(_shipped(), explicit_type="rfe", ids=["free text"], env=env).rung == "--type"
        batch = _write_yaml(tmp_path / "b.yaml", {"type": "initiative", "items": ["free text"]})
        assert resolve(_shipped(), batch=batch, env=env).rung == "batch type"

    def test_interactive_unowned_id_is_no_signal(self):
        res = resolve(_shipped(), ids=["free text"], env={})
        assert (res.type_name, res.rung) == ("rfe", "legacy default")

    def test_artifact_stem_is_never_strict(self, tmp_path):
        art = _artifact(tmp_path / "misc" / "notes.md", {"title": "t"})
        res = resolve(_shipped(), artifact=art, env={"RFE_CREATOR_HEADLESS": "1"})
        assert (res.type_name, res.rung) == ("rfe", "legacy default")

    def test_artifact_stem_owned_by_another_type_conflicts_with_its_dir(self, tmp_path):
        art = _artifact(tmp_path / "initiatives" / "RHAIRFE-7.md", {"title": "t"})
        with pytest.raises(ResolveError) as excinfo:
            resolve(_shipped(), artifact=art, env={})
        message = str(excinfo.value)
        assert message.startswith("conflicting type signals:")
        assert "-> initiative" in message and "RHAIRFE-7 -> rfe" in message

    def test_artifact_stem_agreeing_with_its_dir_resolves_at_artifact_dir(self, tmp_path):
        art = _artifact(tmp_path / "initiatives" / "RHOAIENG-7.md", {"title": "t"})
        res = resolve(_shipped(), artifact=art, env={})
        assert (res.type_name, res.rung) == ("initiative", "artifact dir")

    def test_cli_headless_unowned_id_exits_1(self):
        result = _cli("resolve", "--headless", "free-text")
        assert result.returncode == 1
        assert result.stderr.startswith("ERROR: no registered type owns id 'free-text'")
        assert result.stdout == ""

    @pytest.mark.parametrize("env", [{}, {"RFE_CREATOR_HEADLESS": "1"}])
    def test_workspace_unknown_type_name_is_an_error(self, tmp_path, env):
        (tmp_path / "rfe-creator.yaml").write_text(
            "bindings:\n  rfes:\n    jira:\n      project: KONFLUX\n", encoding="utf-8"
        )
        reg = load(root=TYPES_ROOT, extra_roots=[], env=env, workspace_root=tmp_path)
        with pytest.raises(
            RegistryError, match="bindings.rfes: unknown type; registered types: rfe, initiative"
        ):
            reg.workspace_bindings()


# ── read_batch (PR-3b: the one parser of the batch root) ─────────────────────────


class TestReadBatch:
    """``read_batch`` is shared by ``resolve`` (rung 2), ``validate_batch_input.py`` and
    ``next_rfe_id.py --from-batch``: one parser of the two root forms, shape errors only (the
    per-item ``type`` rule stays the ladder's)."""

    def test_constants(self):
        assert type_registry.LEGACY_DEFAULT_RUNG == "legacy default"
        assert type_registry.RESOLVE_RUNGS[-1] == type_registry.LEGACY_DEFAULT_RUNG
        assert type_registry.BATCH_MAPPING_KEYS == ("type", "items")

    def test_legacy_list_is_returned_as_is_with_no_type(self, tmp_path):
        items = [{"prompt": "x"}, "RHOAIENG-1", {"prompt": "y", "type": "rfe"}]
        batch = _write_yaml(tmp_path / "b.yaml", items)
        # The per-item type key is NOT read_batch's business (resolve rejects it, D2).
        assert read_batch(batch) == (None, items)
        assert read_batch(str(batch)) == (None, items)

    def test_mapping_form_returns_the_stripped_type_and_its_items(self, tmp_path):
        data = {"type": " initiative ", "items": [{"prompt": "x"}, "RFE-1"]}
        batch = _write_yaml(tmp_path / "b.yaml", data)
        assert read_batch(batch) == ("initiative", [{"prompt": "x"}, "RFE-1"])

    def test_mapping_form_may_be_empty_and_its_type_is_not_validated_here(self, tmp_path):
        batch = _write_yaml(tmp_path / "b.yaml", {"type": "epic", "items": []})
        assert read_batch(batch) == ("epic", [])
        with pytest.raises(ResolveError, match="unknown type 'epic'"):
            resolve(_shipped(), batch=batch, env={})

    @pytest.mark.parametrize(
        "content, match",
        [
            ("type: rfe\n", "expected a list of items .* got a mapping with keys type"),
            ("items: []\n", "expected a list of items .* got a mapping with keys items"),
            ("42\n", "got int"),
            ("", "got nothing"),
            ("type: rfe\nitems: nope\n", "'items' must be a list, got str"),
            ("type: 5\nitems: []\n", "'type' must be a non-empty string, got 5"),
            ("type: ''\nitems: []\n", "'type' must be a non-empty string"),
            (
                "type: rfe\nitems: []\nextra: 1\n",
                "the mapping form takes exactly the keys 'type' and 'items'; unexpected "
                "key\\(s\\): extra",
            ),
            ("- [unclosed\n", "invalid YAML in batch file"),
        ],
    )
    def test_other_root_shapes_are_errors(self, tmp_path, content, match):
        batch = tmp_path / "b.yaml"
        batch.write_text(content, encoding="utf-8")
        with pytest.raises(ResolveError, match=match) as excinfo:
            read_batch(batch)
        assert excinfo.value.exit_code == 1

    def test_missing_file_is_an_error(self, tmp_path):
        with pytest.raises(ResolveError, match="cannot read batch file"):
            read_batch(tmp_path / "missing.yaml")

    def test_extra_root_keys_are_rejected_by_resolve_too(self, tmp_path):
        batch = _write_yaml(
            tmp_path / "b.yaml", {"type": "rfe", "items": [{"prompt": "x"}], "labels": ["a"]}
        )
        match = "exactly the keys 'type' and 'items'; unexpected key\\(s\\): labels"
        with pytest.raises(ResolveError, match=match):
            resolve(_shipped(), batch=batch, env={})
        result = _cli("resolve", "--batch", str(batch))
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.startswith("ERROR: ") and "unexpected key(s): labels" in result.stderr

    def test_resolve_reads_the_batch_through_read_batch(self):
        # Source-form pin: one parser, so the two entry scripts and the ladder cannot drift.
        source = inspect.getsource(type_registry._batch_signal)
        assert "read_batch(path)" in source
        assert "_load_yaml" not in source


# ── resolve over pre-parsed batch items (PR-3b: the batch-root consumers' call) ────


class TestResolveBatchItems:
    """The two batch-root consumers hand ``resolve`` the pair ``read_batch`` returned
    (``batch_items`` — a pipe is read once), tell it that a bare string item is an entry, not an
    id (``items_are_ids=False``), and take the type verdict only (``binding=False``)."""

    def test_batch_items_are_not_re_read(self, tmp_path):
        # ``batch`` only names the file: a path that no longer exists (a consumed pipe) is fine.
        gone = tmp_path / "gone.yaml"
        res = resolve(_shipped(), batch=gone, batch_items=(None, [{"prompt": "x"}]), env={})
        assert (res.type_name, res.rung) == ("rfe", "legacy default")
        res = resolve(_shipped(), batch=gone, batch_items=("initiative", [{"prompt": "x"}]), env={})
        assert (res.type_name, res.rung) == ("initiative", "batch type")
        assert res.line() == "TYPE RESOLVED: initiative (batch type)"

    def test_batch_items_keep_the_unknown_type_d1_and_d2_errors(self, tmp_path):
        label = tmp_path / "b.yaml"
        reg = _shipped()
        with pytest.raises(ResolveError, match=r"unknown type 'epic' \(.*b\.yaml type:\)"):
            resolve(reg, batch=label, batch_items=("epic", []), env={})
        with pytest.raises(ResolveError, match="--type rfe disagrees with .*b.yaml type: initi"):
            resolve(reg, explicit_type="rfe", batch=label, batch_items=("initiative", []), env={})
        items = [{"prompt": "x"}, {"prompt": "y", "type": "rfe"}]
        with pytest.raises(ResolveError, match=r"b\.yaml: item 1 carries a per-item 'type' key"):
            resolve(reg, batch=label, batch_items=(None, items), env={})

    def test_batch_items_need_the_label(self):
        with pytest.raises(ValueError, match="needs batch"):
            resolve(_shipped(), batch_items=(None, []), env={})

    def test_string_items_are_ids_by_default(self, tmp_path):
        # PR-3a semantics, kept for a batch of ids (the ``resolve`` CLI's ``--batch``).
        label = tmp_path / "ids.yaml"
        res = resolve(_shipped(), batch=label, batch_items=(None, ["RHOAIENG-1"]), env={})
        assert (res.type_name, res.rung) == ("initiative", "id grammar")
        with pytest.raises(ResolveError, match="no registered type owns id 'alpha'"):
            resolve(_shipped(), batch=label, batch_items=(None, ["alpha"]), env={"CI": "true"})

    def test_entry_grammar_string_items_are_not_signals(self, tmp_path):
        # items_are_ids=False — the speedrun validator's "entry N: must be a mapping" case: no
        # rung-3 signal, no D5 error headless, no conflict with --type; D2 still applies.
        reg = _shipped()
        kw = {"batch": tmp_path / "batch.yaml", "items_are_ids": False}
        items = ["just a string", {"prompt": "ok"}]
        res = resolve(reg, batch_items=(None, items), env={"CI": "true"}, **kw)
        assert (res.type_name, res.rung) == ("rfe", "legacy default")
        res = resolve(reg, batch_items=(None, ["RHAIRFE-123"]), env={}, **kw)
        assert (res.type_name, res.rung) == ("rfe", "legacy default")
        res = resolve(
            reg, explicit_type="rfe", batch_items=(None, ["RHOAIENG-123"]), env={"CI": "1"}, **kw
        )
        assert (res.type_name, res.rung) == ("rfe", "--type")
        res = resolve(reg, batch_items=("initiative", ["RHAIRFE-1"]), env={"CI": "1"}, **kw)
        assert (res.type_name, res.rung) == ("initiative", "batch type")
        with pytest.raises(ResolveError, match="per-item 'type' key"):
            resolve(reg, batch_items=(None, ["x", {"type": "rfe"}]), env={}, **kw)

    def test_binding_false_returns_the_verdict_only(self):
        env = {"JIRA_PROJECT": "KONFLUX", "RFE_CREATOR_BINDING_RFE_ISSUE_TYPE": "Story"}
        res = resolve(_shipped(), explicit_type="rfe", env=env, binding=False)
        assert res.binding is None and res.desc.name == "rfe"
        assert res.line() == "TYPE RESOLVED: rfe (--type)"
        assert res.as_dict()["binding"] is None
        # the default still computes and renders it
        assert resolve(_shipped(), explicit_type="rfe", env=env).line() == (
            "TYPE RESOLVED: rfe (--type; binding override project=KONFLUX issue_type=Story)"
        )

    @pytest.mark.parametrize(
        "env",
        [
            {"JIRA_PROJECT": "rhairfe"},
            {"JIRA_PROJECT": "bad project"},
            {"JIRA_ISSUE_TYPE": ""},
            {"RFE_CREATOR_BINDING_RFE_PROJECT": "foo bar"},
            {"RFE_CREATOR_BINDING_RFE_LOCAL_PREFIX": "X"},
            {"RFE_CREATOR_BINDING_INITIATIVE_PROJECT": "bad!"},
        ],
    )
    def test_binding_false_never_reads_a_malformed_override(self, env):
        # A caller that never applies the binding cannot be failed by an override it would not
        # have read (the batch-root consumers: main read none of these variables).
        for kwargs in ({}, {"explicit_type": "rfe"}, {"explicit_type": "initiative"}):
            res = resolve(_shipped(), env=env, binding=False, **kwargs)
            assert res.binding is None and res.type_name is not None
            assert "binding override" not in res.line()
        # the default is fail-fast for the callers that do apply it
        with pytest.raises(RegistryError, match="JIRA_PROJECT='rhairfe'"):
            resolve(_shipped(), explicit_type="rfe", env={"JIRA_PROJECT": "rhairfe"})

    def test_binding_false_leaves_the_id_rungs_on_effective_bindings(self):
        # The contract is scoped to the RESOLVED binding: ids are still matched over effective
        # bindings (an overridden write prefix decides ownership), so a valid override still
        # routes KONFLUX-1 to rfe and a malformed one still raises when ids are resolved.
        res = resolve(
            _shipped(),
            ids=["KONFLUX-1"],
            env={"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"},
            binding=False,
        )
        assert (res.type_name, res.rung, res.provisional, res.binding) == (
            "rfe",
            "id grammar",
            False,
            None,
        )
        with pytest.raises(RegistryError, match="expected an upper-case tracker project key"):
            resolve(
                _shipped(),
                ids=["RHAIRFE-1"],
                env={"RFE_CREATOR_BINDING_RFE_PROJECT": "bad key"},
                binding=False,
            )
        # without ids the same environment is never read
        res = resolve(_shipped(), env={"RFE_CREATOR_BINDING_RFE_PROJECT": "bad key"}, binding=False)
        assert (res.type_name, res.rung) == ("rfe", "legacy default")

    def test_binding_false_skips_the_workspace_refusal_with_the_binding(self):
        workspace = {"rfe": {"jira": {"project": "KONFLUX"}}}
        res = resolve(
            _shipped(), explicit_type="rfe", env={"CI": "1"}, workspace=workspace, binding=False
        )
        assert res.binding is None and res.type_name == "rfe"
        with pytest.raises(ResolveError, match="workspace file"):
            resolve(_shipped(), explicit_type="rfe", env={"CI": "1"}, workspace=workspace)


class TestParentKeyPatternEffective:
    """``Descriptor.parent_key_pattern_effective``: the ``parent_key`` join over the effective
    binding — the descriptor join byte for byte without a project override, the effective write
    prefix prepended under one, so the children of a parent fetched under the override validate
    (the task schema in artifact_utils and validate_batch_input read this form)."""

    def test_no_project_override_is_the_descriptor_join_byte_for_byte(self):
        reg = _shipped()
        for name in SHIPPED:
            desc = reg.get(name)
            assert desc.parent_key_pattern_effective({}) == desc.parent_key_pattern
            # An issue-type override is not a project override: nothing to prepend.
            issue_type_var = f"RFE_CREATOR_BINDING_{name.upper()}_ISSUE_TYPE"
            assert (
                desc.parent_key_pattern_effective({issue_type_var: "Epic"})
                == desc.parent_key_pattern
            )

    def test_a_project_override_prepends_the_effective_write_prefix(self):
        rfe = _shipped().get("rfe")
        pattern = rfe.parent_key_pattern_effective({"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"})
        assert pattern == r"^(KONFLUX-\d+|RFE-\d+|RHAIRFE-\d+)$"
        for key in ("KONFLUX-1", "RFE-001", "RHAIRFE-7"):
            assert re.fullmatch(pattern, key), key
        for key in ("RHOAIENG-1", "KONFLUX-", "konflux-1"):
            assert re.fullmatch(pattern, key) is None, key
        init = _shipped().get("initiative")
        assert (
            init.parent_key_pattern_effective({"RFE_CREATOR_BINDING_INITIATIVE_PROJECT": "KONFLUX"})
            == r"^(KONFLUX-\d+|RHAISTRAT-\d+|RHOAIENG-\d+|INIT-\d+)$"
        )

    def test_an_override_naming_a_declared_alternative_adds_nothing(self):
        rfe = _shipped().get("rfe")
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "RHAIRFE"}
        assert rfe.binding(env)["overrides"] == ["project"]
        assert rfe.parent_key_pattern_effective(env) == rfe.parent_key_pattern

    def test_none_without_patterns_and_the_registry_env_by_default(self):
        assert Descriptor("bare", {"type": "bare"}).parent_key_pattern_effective({}) is None
        empty = {"type": "e", "conventions": {"parent_key_patterns": []}}
        assert Descriptor("e", empty).parent_key_pattern_effective({}) is None
        reg = load(
            root=TYPES_ROOT, extra_roots=[], env={"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"}
        )
        assert reg.get("rfe").parent_key_pattern_effective().startswith(r"^(KONFLUX-\d+|")
        assert reg.get("initiative").parent_key_pattern_effective() == (
            reg.get("initiative").parent_key_pattern
        )

    def test_a_malformed_override_raises_like_binding(self):
        with pytest.raises(RegistryError, match="RFE_CREATOR_BINDING_RFE_PROJECT='lower'"):
            _shipped().get("rfe").parent_key_pattern_effective(
                {"RFE_CREATOR_BINDING_RFE_PROJECT": "lower"}
            )


class TestAcceptedPairs:
    """``Descriptor.accepted_pairs``: the pairs a fetched issue behind a key may show — the
    effective pair always, plus the descriptor pair for a key carrying a descriptor prefix (an
    item created before the override); ``render_pairs`` is the writers' ``binds ...`` text."""

    RFE_PAIR = ("RHAIRFE", "Feature Request")

    def test_no_override_is_the_one_descriptor_pair(self):
        rfe = _shipped().get("rfe")
        binding = rfe.binding({})
        for key in ("RHAIRFE-7", "KONFLUX-1", "RFE-001", "", None):
            assert rfe.accepted_pairs(binding, key) == [self.RFE_PAIR], key

    def test_a_descriptor_prefixed_key_also_accepts_the_descriptor_pair_under_an_override(self):
        rfe = _shipped().get("rfe")
        binding = rfe.binding({"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"})
        assert rfe.accepted_pairs(binding, "RHAIRFE-7") == [
            ("KONFLUX", "Feature Request"),
            self.RFE_PAIR,
        ]
        # The overridden write prefix and a local id carry no descriptor prefix: the effective
        # pair alone — a KONFLUX-1 Epic is refused whatever the descriptor says.
        assert rfe.accepted_pairs(binding, "KONFLUX-1") == [("KONFLUX", "Feature Request")]
        assert rfe.accepted_pairs(binding, "RFE-001") == [("KONFLUX", "Feature Request")]

    def test_an_issue_type_override_accepts_the_pre_override_pair_for_its_own_keys(self):
        rfe = _shipped().get("rfe")
        binding = rfe.binding({"RFE_CREATOR_BINDING_RFE_ISSUE_TYPE": "Epic"})
        assert rfe.accepted_pairs(binding, "RHAIRFE-7") == [("RHAIRFE", "Epic"), self.RFE_PAIR]
        assert rfe.accepted_pairs(binding, "FOO-1") == [("RHAIRFE", "Epic")]

    def test_render_pairs(self):
        import type_registry

        assert type_registry.render_pairs([self.RFE_PAIR]) == "(RHAIRFE, Feature Request)"
        assert type_registry.render_pairs([("KONFLUX", "Feature Request"), self.RFE_PAIR]) == (
            "(KONFLUX, Feature Request) or, for a pre-override key, (RHAIRFE, Feature Request)"
        )


class TestAssertNotShorthand:
    """The writers refuse a binding the bare JIRA_PROJECT / JIRA_ISSUE_TYPE shorthand contributed
    to: the artifact layer reads binding() without it. The shorthand stays a resolve-CLI verdict."""

    LINE = (
        "JIRA_PROJECT / JIRA_ISSUE_TYPE shorthand is not honoured by the artifact layer; set "
        "RFE_CREATOR_BINDING_RFE_PROJECT / _ISSUE_TYPE instead"
    )

    def test_descriptor_and_env_sourced_bindings_pass_through(self):
        import type_registry

        rfe = _shipped().get("rfe")
        for env in ({}, {"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"}):
            binding = rfe.binding(env, shorthand=True)
            assert type_registry.assert_not_shorthand("rfe", binding) is binding
        # A shorthand in the environment that the binding was computed WITHOUT is no source.
        binding = rfe.binding({"JIRA_PROJECT": "KONFLUX"})
        assert binding["source"] == "descriptor"
        assert type_registry.assert_not_shorthand("rfe", binding) is binding

    @pytest.mark.parametrize(
        "env",
        [
            {"JIRA_PROJECT": "KONFLUX"},
            {"JIRA_ISSUE_TYPE": "Story"},
            {"JIRA_PROJECT": "KONFLUX", "RFE_CREATOR_BINDING_RFE_ISSUE_TYPE": "Story"},
            {"JIRA_PROJECT": "RHAIRFE"},  # equal to the descriptor value: still shorthand-sourced
        ],
        ids=["project", "issue_type", "env+shorthand", "descriptor-value"],
    )
    def test_a_shorthand_contribution_is_refused_naming_the_typed_variables(self, env):
        import type_registry

        binding = _shipped().get("rfe").binding(env, shorthand=True)
        assert "shorthand" in binding["source"].split("+")
        with pytest.raises(RegistryError) as exc:
            type_registry.assert_not_shorthand("rfe", binding)
        assert exc.value.args[0] == self.LINE

    def test_the_type_token_is_binding_env_vars(self):
        import type_registry

        binding = _shipped().get("initiative").binding({"JIRA_PROJECT": "KONFLUX"}, shorthand=True)
        with pytest.raises(RegistryError, match="set RFE_CREATOR_BINDING_INITIATIVE_PROJECT / "):
            type_registry.assert_not_shorthand("initiative", binding)

    def test_resolve_keeps_the_shorthand_for_the_cli_verdict(self):
        import type_registry

        reg = load(root=TYPES_ROOT, extra_roots=[], env={"JIRA_PROJECT": "KONFLUX"})
        result = type_registry.resolve(reg, explicit_type="rfe")
        assert result.binding["source"] == "shorthand"
        assert result.line() == "TYPE RESOLVED: rfe (--type; binding override project=KONFLUX)"
        with pytest.raises(RegistryError, match="not honoured by the artifact layer"):
            type_registry.assert_not_shorthand(result.type_name, result.binding)


# ── launch-vars (design §4.1, §8.3; PR-5b) ─────────────────────────────────────────────────


class TestLaunchVars:
    """``launch-vars <type> <stage>`` prints the KEY=value block the generic bodies and the
    dispatcher render agent launches from: a projection of the descriptor, deterministic,
    single-line values, refused for a stage the type does not ship."""

    def test_projection_for_both_shipped_types(self):
        reg = _shipped()
        for name in reg.names():
            desc = reg.get(name)
            pairs = dict(type_registry.launch_vars(desc, "review"))
            dirs = desc.dirs()
            pipe = desc.get("pipeline")
            assert pairs["TYPE"] == name and pairs["TYPE_FLAG"] == f"--type {name}"
            assert pairs["ID_FIELD"] == desc.id_field and pairs["ID_FLAG"] == "--id"
            assert pairs["TASKS_DIR"] == dirs["tasks"]
            assert pairs["ORIGINALS_DIR"] == dirs["originals"]
            assert pairs["REVIEWS_DIR"] == dirs["reviews"]
            assert pairs["TASK_SCHEMA"] == f"{name}-task"
            assert pairs["REVIEW_SCHEMA"] == f"{name}-review"
            assert pairs["SCORER_AGENT"] == pipe["scorer_agent"]
            assert pairs["PROMPT_PATH"] == f".context/assess-rfe/{pipe['rubric']['path']}"
            assert pairs["POLL_PREFIX"] == pipe["poll_prefix"]
            assert pairs["STATE_PREFIX"] == pipe["state_prefix"]
            assert pairs["POLL_FILE_PREFIX"] == f"tmp/{name}-poll-"
            assert pairs["SCORE_FIELDS"] == ",".join(desc.score_fields)
            assert pairs["SCORE_ZERO_SET"].split() == [f"scores.{f}=0" for f in desc.score_fields]
            assert pairs["DIMENSIONS"] == ",".join(d["name"] for d in pipe["dimensions"])
            for dim in pipe["dimensions"]:
                key = dim["name"].upper()
                assert pairs[f"DIMENSION_{key}_PROMPT"] == dim["prompt"]
                assert (
                    pairs[f"DIMENSION_{key}_FILE"] == f"{dirs['reviews']}/{{ID}}-{dim['name']}.md"
                )
            assert pairs["RESPLIT_FIELD"] == pipe["resplit"]["score_field"]
            assert pairs["RESPLIT_BELOW"] == str(pipe["resplit"]["below"])
            assert pairs["NEXT_ID_FLAGS"] == (
                f"--prefix {desc.local_prefix.rstrip('-')} --dir {dirs['tasks']}"
            )
            assert pairs["INDEX_ENABLED"] == ("true" if desc.get("index.enabled") else "false")
            assert pairs["COMMENTS_COMPANION"] == (
                "true" if desc.get("companions.comments") else "false"
            )
            for value in pairs.values():
                assert "\n" not in value

    def test_type_specific_values(self):
        reg = _shipped()
        rfe = dict(type_registry.launch_vars(reg.get("rfe"), "create"))
        init = dict(type_registry.launch_vars(reg.get("initiative"), "create"))
        assert rfe["EXTRA_RULES"] == "none" and rfe["REVIEW_EXTRA_SET"] == ""
        assert init["EXTRA_RULES"] == "If `alignment` is `weak`, set `needs_attention=true`."
        assert init["REVIEW_EXTRA_SET"] == " alignment=<strong/partial/weak/not_assessed>"
        assert rfe["SIZE_SET"] == " size=<size>" and init["SIZE_SET"] == ""
        assert rfe["PARENT_FLAG"] == "" and init["PARENT_FLAG"] == "--parent"
        assert rfe["COMMENTS_FIELD"] == ',"comment"' and init["COMMENTS_FIELD"] == ""
        assert init["DIMENSION_ALIGNMENT_CONDITION"] == "parent_key startswith RHAISTRAT-"
        assert init["DIMENSION_ALIGNMENT_BLOCKING"] == "false"
        assert rfe["RUN_REPORT"] == "artifacts/auto-fix-runs/<timestamp>.yaml"
        assert init["RUN_REPORT"] == "artifacts/auto-fix-runs/initiative-run-<timestamp>.yaml"
        assert rfe["RUBRIC_EXPORT"] == "artifacts/rfe-rubric.md" and init["RUBRIC_EXPORT"] == "none"

    def test_deterministic_and_stage_scoped(self):
        reg = _shipped()
        desc = reg.get("rfe")
        assert type_registry.launch_vars(desc, "split") == type_registry.launch_vars(desc, "split")
        keys = [k for k, _ in type_registry.launch_vars(desc, "split")]
        assert keys[0] == "STAGE" and len(keys) == len(set(keys))
        with pytest.raises(type_registry.ResolveError) as exc_info:
            type_registry.launch_vars(desc, "bogus")
        assert exc_info.value.exit_code == 2
        assert "pipeline.stages" in exc_info.value.args[0]

    def test_cli_text_and_json(self):
        result = _cli("launch-vars", "rfe", "review", env=_clean_env())
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        assert lines[0] == "STAGE=review" and "TYPE_FLAG=--type rfe" in lines
        assert all("=" in line for line in lines)
        as_json = _cli("--json", "launch-vars", "initiative", "speedrun", env=_clean_env())
        assert as_json.returncode == 0, as_json.stderr
        data = json.loads(as_json.stdout)
        assert data["STAGE"] == "speedrun" and data["SCORER_AGENT"] == "initiative-scorer"
        bad = _cli("launch-vars", "rfe", "bogus", env=_clean_env())
        assert bad.returncode == 2 and "pipeline.stages" in bad.stderr
