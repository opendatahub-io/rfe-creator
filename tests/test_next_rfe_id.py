#!/usr/bin/env python3
"""Tests for scripts/next_rfe_id.py — atomic ID allocation with registry-derived rfe defaults.

PR-3b: ``--from-batch`` accepts the ``{type, items}`` mapping form (prefix and directory from the
type's descriptor, explicit flags that disagree are a conflict) through the registry's one parser
and one ladder; the legacy list form is byte-identical to main (ids only on stdout, nothing on
stderr) whatever the environment or the file's channel: read once, string items counted rather
than read as ids, the binding never evaluated.
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import next_rfe_id  # noqa: E402
import type_registry  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "next_rfe_id.py")


def _clean_env():
    """No drop-in roots, no headless/CI markers, no shorthand binding (a JIRA_PROJECT would add
    a binding-override clause to the resolve line)."""
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("RFE_CREATOR_")
        and k not in type_registry.HEADLESS_MARKER_VARS
        and k not in {"JIRA_PROJECT", "JIRA_ISSUE_TYPE"}
    }


def _run(cwd, *args, env=None, stdin=None):
    return subprocess.run(
        ["python3", SCRIPT, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env={**_clean_env(), **(env or {})},
        input=stdin,
    )


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
        assert next_rfe_id.type_defaults(rfe) == ("RFE", "artifacts/rfe-tasks")

    def test_type_defaults_for_the_initiative_descriptor(self):
        init = type_registry.load(extra_roots=[], env={}).get("initiative")
        assert next_rfe_id.type_defaults(init) == ("INIT", "artifacts/initiatives")

    def test_help_shows_the_defaults(self, tmp_path):
        # The argparse defaults are None since PR-3b (an explicit flag must be distinguishable
        # from the default); the help text still renders the rfe values.
        result = _run(tmp_path, "--help")
        assert result.returncode == 0
        assert "ID prefix (default: RFE)" in result.stdout
        assert "Tasks directory (default: artifacts/rfe-tasks)" in result.stdout


class TestAllocation:
    def test_allocates_sequential_ids_and_placeholders_in_the_default_dir(self, tmp_path):
        result = _run(tmp_path, "2")
        assert result.returncode == 0, result.stderr
        assert result.stdout == "RFE-001\nRFE-002\n"
        assert result.stderr == ""
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

    def test_legacy_list_prints_ids_only_and_nothing_on_stderr(self, tmp_path):
        # The legacy default is not a signal: no resolve line, no other output (PR-3 D3).
        batch = tmp_path / "batch.yaml"
        batch.write_text("- prompt: a\n  priority: Major\n- prompt: b\n")
        result = _run(tmp_path, "--from-batch", str(batch))
        assert result.returncode == 0, result.stderr
        assert result.stdout == "RFE-001\nRFE-002\n"
        assert result.stderr == ""

    def test_initiative_flags_with_a_legacy_list_do_not_conflict(self, tmp_path):
        # The `rfe-speedrun --type initiative` invocation (NEXT_ID_FLAGS renders --prefix INIT
        # --dir artifacts/initiatives) with a list-form file. The legacy default is not a
        # signal, so nothing to disagree with.
        batch = tmp_path / "batch.yaml"
        batch.write_text("- prompt: a\n  parent_key: RHAISTRAT-1\n")
        result = _run(
            tmp_path,
            "--prefix",
            "INIT",
            "--dir",
            "artifacts/initiatives",
            "--from-batch",
            str(batch),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "INIT-001\n"
        assert result.stderr == ""
        assert (tmp_path / "artifacts" / "initiatives" / "INIT-001.md").exists()
        assert not (tmp_path / "artifacts" / "rfe-tasks").exists()

    def test_non_list_batch_is_a_usage_error(self, tmp_path):
        batch = tmp_path / "batch.yaml"
        batch.write_text("prompt: a\n")
        result = _run(tmp_path, "--from-batch", str(batch))
        assert result.returncode == 2
        assert result.stdout == ""
        assert "expected a list of items (legacy form) or a mapping with 'type' and 'items'" in (
            result.stderr
        )
        assert not (tmp_path / "artifacts").exists()

    def test_invalid_yaml_is_a_usage_error(self, tmp_path):
        batch = tmp_path / "batch.yaml"
        batch.write_text("- [unclosed\n")
        result = _run(tmp_path, "--from-batch", str(batch))
        assert result.returncode == 2
        assert "invalid YAML" in result.stderr
        assert not (tmp_path / "artifacts").exists()

    def test_empty_batch_is_rejected(self, tmp_path):
        batch = tmp_path / "batch.yaml"
        batch.write_text("[]\n")
        result = _run(tmp_path, "--from-batch", str(batch))
        assert result.returncode == 2
        assert "Count must be >= 1" in result.stderr

    @pytest.mark.parametrize(
        "content",
        ["- prompt: a\n  type: rfe\n", "type: rfe\nitems:\n- prompt: a\n  type: rfe\n"],
        ids=["list-form", "mapping-form"],
    )
    def test_per_item_type_exits_two_with_the_split_message(self, tmp_path, content):
        # PR-3 D2, in both forms.
        batch = tmp_path / "batch.yaml"
        batch.write_text(content)
        result = _run(tmp_path, "--from-batch", str(batch))
        assert result.returncode == 2
        assert result.stdout == ""
        assert "per-item 'type' key" in result.stderr
        assert "split the batch by type" in result.stderr
        assert not (tmp_path / "artifacts").exists()


class TestLegacyListNeutrality:
    """The list form allocates exactly as main did whatever the environment or the channel."""

    def test_a_pipe_is_read_once(self, tmp_path):
        result = _run(tmp_path, "--from-batch", "/dev/stdin", stdin="- prompt: a\n- prompt: b\n")
        assert result.returncode == 0, result.stderr
        assert result.stdout == "RFE-001\nRFE-002\n"
        assert result.stderr == ""
        assert (tmp_path / "artifacts" / "rfe-tasks" / "RFE-002.md").exists()

    def test_a_piped_mapping_form(self, tmp_path):
        result = _run(
            tmp_path, "--from-batch", "/dev/stdin", stdin="type: initiative\nitems:\n- prompt: a\n"
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "INIT-001\n"
        assert result.stderr == "TYPE RESOLVED: initiative (batch type)\n"

    @pytest.mark.parametrize(
        "env",
        [
            {"JIRA_PROJECT": "rhairfe"},
            {"JIRA_PROJECT": "bad project"},
            {"RFE_CREATOR_BINDING_RFE_PROJECT": "foo bar"},
            {"RFE_CREATOR_BINDING_RFE_LOCAL_PREFIX": "X"},
            {"RFE_CREATOR_BINDING_RFE_LOCAL_PREFIX": "x-"},
        ],
        ids=lambda e: "=".join(*e.items()),
    )
    @pytest.mark.parametrize(
        "flags, expected",
        [([], "RFE-001\n"), (["--prefix", "INIT", "--dir", "artifacts/initiatives"], "INIT-001\n")],
        ids=["rfe", "initiative-flags"],
    )
    def test_a_malformed_binding_override_is_not_read(self, tmp_path, env, flags, expected):
        # main never read these variables and neither does the verdict-only resolve
        # (binding=False): no traceback, no exit 2 — ids as before.
        batch = tmp_path / "batch.yaml"
        batch.write_text("- prompt: a\n")
        result = _run(tmp_path, *flags, "--from-batch", str(batch), env=env)
        assert result.returncode == 0, result.stderr
        assert result.stdout == expected
        assert result.stderr == ""

    def test_a_registry_error_is_exit_two_not_a_traceback(self, tmp_path, monkeypatch, capsys):
        # The docstring's contract: every RegistryError the ladder raises inside load_batch is
        # exit 2 with the message on stderr and nothing on stdout. (A registry that fails to
        # LOAD — an invalid drop-in descriptor — fails at import, as it does in every registry
        # consumer; that is the shared convention, not this script's contract.)
        assert issubclass(type_registry.ResolveError, type_registry.RegistryError)
        batch = tmp_path / "batch.yaml"
        batch.write_text("- prompt: a\n")

        def boom(*args, **kwargs):
            raise type_registry.RegistryError("registry says no")

        monkeypatch.setattr(next_rfe_id.type_registry, "resolve", boom)
        with pytest.raises(SystemExit) as exc_info:
            next_rfe_id.load_batch(str(batch))
        assert exc_info.value.code == 2
        out, err = capsys.readouterr()
        assert out == ""
        assert err.strip() == "registry says no"

    @pytest.mark.parametrize("env", [{}, {"CI": "true"}, {"RFE_CREATOR_HEADLESS": "1"}])
    def test_string_items_are_counted_not_read_as_ids(self, tmp_path, env):
        # main allocated one id per item whatever its shape; a headless run must not turn a
        # string item into a D5 error here (the validator, run first, reports malformed entries).
        batch = tmp_path / "batch.yaml"
        batch.write_text("- alpha\n- beta\n")
        result = _run(tmp_path, "--from-batch", str(batch), env=env)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "RFE-001\nRFE-002\n"
        assert result.stderr == ""
        batch.write_text("- RHOAIENG-123\n- prompt: ok\n")
        result = _run(tmp_path, "--from-batch", str(batch), env=env)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "RFE-003\nRFE-004\n"
        assert result.stderr == ""


class TestFromBatchMappingForm:
    def test_mapping_form_allocates_in_the_types_dir_with_its_prefix(self, tmp_path):
        batch = tmp_path / "batch.yaml"
        batch.write_text("type: initiative\nitems:\n- prompt: a\n- prompt: b\n")
        result = _run(tmp_path, "--from-batch", str(batch))
        assert result.returncode == 0, result.stderr
        assert result.stdout == "INIT-001\nINIT-002\n"
        assert result.stderr == "TYPE RESOLVED: initiative (batch type)\n"
        assert (tmp_path / "artifacts" / "initiatives" / "INIT-002.md").exists()
        assert not (tmp_path / "artifacts" / "rfe-tasks").exists()

    def test_mapping_form_rfe(self, tmp_path):
        batch = tmp_path / "batch.yaml"
        batch.write_text("type: rfe\nitems:\n- prompt: a\n")
        result = _run(tmp_path, "--from-batch", str(batch))
        assert result.returncode == 0, result.stderr
        assert result.stdout == "RFE-001\n"
        assert result.stderr == "TYPE RESOLVED: rfe (batch type)\n"
        assert (tmp_path / "artifacts" / "rfe-tasks" / "RFE-001.md").exists()

    def test_mapping_form_continues_after_the_highest_existing_id(self, tmp_path):
        _write(tmp_path / "artifacts" / "initiatives" / "INIT-004.md", "")
        batch = tmp_path / "batch.yaml"
        batch.write_text("type: initiative\nitems:\n- prompt: a\n")
        result = _run(tmp_path, "--from-batch", str(batch))
        assert result.stdout == "INIT-005\n"

    @pytest.mark.parametrize(
        "flags",
        [
            ["--prefix", "INIT"],
            ["--dir", "artifacts/initiatives"],
            ["--prefix", "INIT", "--dir", "artifacts/initiatives"],
            ["--prefix", "INIT", "--dir", "artifacts/initiatives/"],
        ],
    )
    def test_agreeing_explicit_flags_are_fine(self, tmp_path, flags):
        batch = tmp_path / "batch.yaml"
        batch.write_text("type: initiative\nitems:\n- prompt: a\n")
        result = _run(tmp_path, *flags, "--from-batch", str(batch))
        assert result.returncode == 0, result.stderr
        assert result.stdout == "INIT-001\n"
        assert (tmp_path / "artifacts" / "initiatives" / "INIT-001.md").exists()

    @pytest.mark.parametrize(
        "flags, needle",
        [
            (["--prefix", "RFE"], "--prefix RFE (the initiative prefix is INIT)"),
            (
                ["--dir", "artifacts/rfe-tasks"],
                "--dir artifacts/rfe-tasks (the initiative tasks dir is artifacts/initiatives)",
            ),
            (["--prefix", "INIT", "--dir", "artifacts/rfe-tasks"], "--dir artifacts/rfe-tasks"),
        ],
    )
    def test_disagreeing_explicit_flags_are_a_conflict(self, tmp_path, flags, needle):
        # PR-3 D1 applied to the allocator: the mapping's type and the flags are both explicit.
        batch = tmp_path / "batch.yaml"
        batch.write_text("type: initiative\nitems:\n- prompt: a\n")
        result = _run(tmp_path, *flags, "--from-batch", str(batch))
        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr.startswith(f"{batch}: type: initiative disagrees with ")
        assert needle in result.stderr
        assert "D1" in result.stderr
        assert not (tmp_path / "artifacts").exists()

    def test_unknown_mapping_type_exits_two_with_the_registered_list(self, tmp_path):
        batch = tmp_path / "batch.yaml"
        batch.write_text("type: epic\nitems:\n- prompt: a\n")
        result = _run(tmp_path, "--from-batch", str(batch))
        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr == (
            f"unknown type 'epic' ({batch} type:); registered types: rfe, initiative\n"
        )
        assert not (tmp_path / "artifacts").exists()

    @pytest.mark.parametrize(
        "content, needle",
        [
            ("type: initiative\n", "expected a list of items (legacy form)"),
            ("type: initiative\nitems: 3\n", "'items' must be a list, got int"),
            (
                "type: initiative\nitems: [{prompt: a}]\nlabels: []\n",
                "exactly the keys 'type' and 'items'; unexpected key(s): labels",
            ),
        ],
    )
    def test_malformed_mapping_roots_are_usage_errors(self, tmp_path, content, needle):
        batch = tmp_path / "batch.yaml"
        batch.write_text(content)
        result = _run(tmp_path, "--from-batch", str(batch))
        assert result.returncode == 2
        assert result.stdout == ""
        assert needle in result.stderr
        assert not (tmp_path / "artifacts").exists()

    def test_empty_items_is_rejected(self, tmp_path):
        batch = tmp_path / "batch.yaml"
        batch.write_text("type: initiative\nitems: []\n")
        result = _run(tmp_path, "--from-batch", str(batch))
        assert result.returncode == 2
        assert "Count must be >= 1" in result.stderr
        assert not (tmp_path / "artifacts").exists()


class TestUsageErrors:
    def test_missing_count_and_batch_is_a_usage_error(self, tmp_path):
        result = _run(tmp_path)
        assert result.returncode == 2
        assert "either count or --from-batch is required" in result.stderr

    def test_zero_count_is_rejected(self, tmp_path):
        result = _run(tmp_path, "0")
        assert result.returncode == 2
        assert "Count must be >= 1" in result.stderr
