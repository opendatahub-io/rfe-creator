#!/usr/bin/env python3
"""Tests for scripts/check_conflicts.py — registry-derived _TYPE_CONFIG and the conflict scan."""

import os
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import artifact_utils  # noqa: E402
import check_conflicts  # noqa: E402
import type_registry  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "check_conflicts.py")
REG = type_registry.load(extra_roots=[], env={})

JIRA_ENV = {"JIRA_SERVER": "https://jira.example.com", "JIRA_USER": "u", "JIRA_TOKEN": "t"}

# (tasks dir, originals dir, id field, sample local id, sample jira id) per type — the literal
# layout every artifact on disk follows today.
LAYOUT = {
    "rfe": ("rfe-tasks", "rfe-originals", "rfe_id", "RFE-001", "RHAIRFE-1595"),
    "initiative": (
        "initiatives",
        "initiative-originals",
        "initiative_id",
        "INIT-001",
        "RHOAIENG-12345",
    ),
}


def _task(artifacts, type_name, item_id, status="Draft"):
    tasks_dir, _, id_field, _, _ = LAYOUT[type_name]
    path = artifacts / tasks_dir / f"{item_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\n{id_field}: {item_id}\ntitle: T\npriority: Major\nstatus: {status}\n---\n\nbody\n"
    )
    return path


def _original(artifacts, type_name, item_id):
    _, originals_dir, _, _, _ = LAYOUT[type_name]
    path = artifacts / originals_dir / f"{item_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {item_id}: T\n\nbody\n")
    return path


def _dropin_root(tmp_path):
    """A minimal third type under a drop-in root (registry env seam, dev/test only)."""
    root = tmp_path / "types"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "type.yaml").write_text(
        textwrap.dedent(
            """\
            schema_version: 1
            type: docs
            identity:
              tracker: jira
              jira: {project: DOCS, issue_type: Task, key_prefixes: ["DOCS-"]}
              local_prefix: "DOC-"
              id_field: doc_id
            dirs: {tasks: artifacts/doc-tasks, originals: artifacts/doc-originals}
            """
        )
    )
    return str(root)


class TestTypeConfigDerivesFromTheRegistry:
    def test_values_are_the_pre_registry_literals(self):
        # Behaviour-neutral migration: same keys, same order, same values.
        plain = {
            t: {k: v for k, v in c.items() if k != "scan_fn"}
            for t, c in check_conflicts._TYPE_CONFIG.items()
        }
        assert plain == {
            "rfe": {
                "originals_dir": "rfe-originals",
                "id_field": "rfe_id",
                "jira_prefix": "RHAIRFE-",
            },
            "initiative": {
                "originals_dir": "initiative-originals",
                "id_field": "initiative_id",
                "jira_prefix": "RHOAIENG-",
            },
        }
        assert list(check_conflicts._TYPE_CONFIG) == ["rfe", "initiative"]
        for config in check_conflicts._TYPE_CONFIG.values():
            assert list(config) == ["originals_dir", "scan_fn", "id_field", "jira_prefix"]

    def test_scan_fns_are_the_forked_pair(self):
        assert check_conflicts._TYPE_CONFIG["rfe"]["scan_fn"] is artifact_utils.scan_task_files
        assert (
            check_conflicts._TYPE_CONFIG["initiative"]["scan_fn"]
            is artifact_utils.scan_initiative_task_files
        )

    @pytest.mark.parametrize("type_name", REG.names())
    def test_projection_from_the_descriptor(self, type_name):
        desc = REG.get(type_name)
        tc = check_conflicts._TYPE_CONFIG[type_name]
        assert tc["originals_dir"] == desc.dirs(form="bare")["originals"]
        assert tc["id_field"] == desc.id_field
        assert tc["jira_prefix"] == desc.key_prefixes[0] == desc.write_prefix

    def test_type_choices_are_the_registry_choices(self):
        result = subprocess.run(["python3", SCRIPT, "--help"], capture_output=True, text=True)
        assert result.returncode == 0
        assert "--type {rfe,initiative}" in result.stdout

    def test_a_drop_in_type_is_offered_but_refused_without_a_scanner(self, tmp_path):
        root = _dropin_root(tmp_path)
        env = {
            **os.environ,
            **JIRA_ENV,
            "RFE_CREATOR_EXTRA_TYPES": root,
            "RFE_CREATOR_EXTRA_TYPES_ALLOWLIST": root,
        }
        result = subprocess.run(
            ["python3", SCRIPT, "--help"], capture_output=True, text=True, env=env
        )
        assert "--type {rfe,docs,initiative}" in result.stdout
        result = subprocess.run(
            ["python3", SCRIPT, "--type", "docs", "--artifacts-dir", str(tmp_path)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 2
        assert "no task scanner is registered for type 'docs'" in result.stderr


class TestMain:
    @pytest.fixture
    def jira(self, monkeypatch):
        for var, value in JIRA_ENV.items():
            monkeypatch.setenv(var, value)

    def _main(self, monkeypatch, *args):
        monkeypatch.setattr(sys, "argv", ["check_conflicts.py", *args])
        with pytest.raises(SystemExit) as excinfo:
            check_conflicts.main()
        return excinfo.value.code

    def test_missing_env_exits_two(self, monkeypatch, capsys, tmp_path):
        for var in JIRA_ENV:
            monkeypatch.delenv(var, raising=False)
        code = self._main(monkeypatch, "--artifacts-dir", str(tmp_path))
        assert code == 2
        assert "JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars required" in capsys.readouterr().err

    @pytest.mark.parametrize("type_name", ["rfe", "initiative"])
    def test_no_jira_sourced_items(self, jira, monkeypatch, capsys, tmp_path, type_name):
        _, _, _, local_id, jira_id = LAYOUT[type_name]
        _task(tmp_path, type_name, local_id)  # local id: never checked
        _task(tmp_path, type_name, jira_id)  # jira id without an original: skipped
        monkeypatch.setattr(
            check_conflicts,
            "check_description_conflict",
            lambda *a, **k: pytest.fail("no fetch expected"),
        )
        code = self._main(monkeypatch, "--type", type_name, "--artifacts-dir", str(tmp_path))
        assert code == 0
        assert capsys.readouterr().out == "CONFLICT_COUNT=0\nOK: no Jira-sourced items to check\n"

    @pytest.mark.parametrize("type_name", ["rfe", "initiative"])
    def test_conflicts_are_reported_per_item(self, jira, monkeypatch, capsys, tmp_path, type_name):
        _, _, _, _, jira_id = LAYOUT[type_name]
        prefix = jira_id.rsplit("-", 1)[0] + "-"
        same, changed, archived, gone = (f"{prefix}{n}" for n in (9001, 9002, 9003, 9005))
        for item in (same, changed, gone):
            _task(tmp_path, type_name, item)
            _original(tmp_path, type_name, item)
        _task(tmp_path, type_name, archived, status="Archived")
        _original(tmp_path, type_name, archived)

        seen = []

        def fake_check(server, user, token, key, original_path):
            seen.append((key, os.path.basename(os.path.dirname(original_path))))
            if key == gone:
                raise RuntimeError("HTTP Error 404: Not Found")
            return key == changed, {"description": "x"}

        monkeypatch.setattr(check_conflicts, "check_description_conflict", fake_check)
        code = self._main(monkeypatch, "--type", type_name, "--artifacts-dir", str(tmp_path))
        out = capsys.readouterr()
        assert code == 1
        assert out.out == (
            f"CONFLICT_COUNT=1\nCONFLICT: {changed} — modified in Jira since last fetch\n"
        )
        assert out.err == f"Warning: could not fetch {gone}: HTTP Error 404: Not Found\n"
        originals_dir = LAYOUT[type_name][1]
        assert seen == [(same, originals_dir), (changed, originals_dir), (gone, originals_dir)]

    def test_no_conflicts_exits_zero(self, jira, monkeypatch, capsys, tmp_path):
        _task(tmp_path, "rfe", "RHAIRFE-1")
        _original(tmp_path, "rfe", "RHAIRFE-1")
        monkeypatch.setattr(
            check_conflicts, "check_description_conflict", lambda *a, **k: (False, {})
        )
        code = self._main(monkeypatch, "--artifacts-dir", str(tmp_path))
        assert code == 0
        assert capsys.readouterr().out == "CONFLICT_COUNT=0\nOK: no conflicts detected\n"

    def test_type_selects_the_tree(self, jira, monkeypatch, capsys, tmp_path):
        # An initiative tree scanned as rfe (the default) has nothing to check, and vice versa.
        _task(tmp_path, "initiative", "RHOAIENG-1")
        _original(tmp_path, "initiative", "RHOAIENG-1")
        monkeypatch.setattr(
            check_conflicts,
            "check_description_conflict",
            lambda *a, **k: pytest.fail("no fetch expected"),
        )
        code = self._main(monkeypatch, "--artifacts-dir", str(tmp_path))
        assert code == 0
        assert "no Jira-sourced items" in capsys.readouterr().out
