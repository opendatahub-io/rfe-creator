#!/usr/bin/env python3
"""Tests for scripts/prep_assess.py — task-dir routing through ``TypeRegistry.detect()`` and
the staging copy.

This is the behavioural half of the prefix-sniff pin PR-2a retired from
tests/test_type_registry_pins.py: a sample id must land in the ``dirs.tasks`` of the type
that owns it, and anything no type owns keeps the rfe default of the sniff it replaced.
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import prep_assess  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "prep_assess.py")

TASK_BODY = "---\n{id_field}: {item_id}\ntitle: T\n---\n\nBody.\n"


def _run(cwd, *args):
    env = {k: v for k, v in os.environ.items() if k != "RFE_CREATOR_EXTRA_TYPES"}
    return subprocess.run(
        ["python3", SCRIPT, *args], cwd=str(cwd), capture_output=True, text=True, env=env
    )


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestTaskDirRouting:
    @pytest.mark.parametrize(
        "item_id, expected",
        [
            ("RHAIRFE-1", "artifacts/rfe-tasks"),
            ("RFE-001", "artifacts/rfe-tasks"),
            ("INIT-001", "artifacts/initiatives"),
            ("RHOAIENG-1", "artifacts/initiatives"),
            ("INIT-x", "artifacts/initiatives"),  # malformed local id: the local_prefix rung
            ("RHAISTRAT-1", "artifacts/rfe-tasks"),  # a peer pipeline's key: rfe default
            ("", "artifacts/rfe-tasks"),
            ("init-001", "artifacts/rfe-tasks"),  # case-sensitive, like the sniff it replaced
        ],
    )
    def test_task_dir_for(self, item_id, expected):
        assert prep_assess._task_dir_for(item_id) == expected

    def test_routing_is_the_descriptor_dirs_value(self):
        reg = prep_assess._TYPES
        assert prep_assess._task_dir_for("INIT-001") == reg.get("initiative").dirs()["tasks"]
        assert prep_assess._task_dir_for("RHAIRFE-1") == reg.get("rfe").dirs()["tasks"]

    def test_staging_dir_is_type_neutral(self):
        assert prep_assess.SINGLE_DIR == "tmp/rfe-assess/single"


class TestStagingCopy:
    def test_initiative_id_copies_from_the_initiatives_dir(self, tmp_path):
        body = TASK_BODY.format(id_field="initiative_id", item_id="INIT-003")
        _write(tmp_path / "artifacts" / "initiatives" / "INIT-003.md", body)
        r = _run(tmp_path, "INIT-003")
        assert r.returncode == 0, r.stdout + r.stderr
        assert r.stdout == "FILE=tmp/rfe-assess/single/INIT-003.md\n"
        assert (tmp_path / "tmp" / "rfe-assess" / "single" / "INIT-003.md").read_text() == body

    def test_rfe_id_copies_from_the_rfe_tasks_dir(self, tmp_path):
        body = TASK_BODY.format(id_field="rfe_id", item_id="RHAIRFE-1")
        _write(tmp_path / "artifacts" / "rfe-tasks" / "RHAIRFE-1.md", body)
        r = _run(tmp_path, "RHAIRFE-1")
        assert r.returncode == 0, r.stdout + r.stderr
        assert r.stdout == "FILE=tmp/rfe-assess/single/RHAIRFE-1.md\n"

    def test_unowned_id_is_looked_up_under_rfe_tasks(self, tmp_path):
        r = _run(tmp_path, "RHAISTRAT-1")
        assert r.returncode == 1
        assert "Task file not found: artifacts/rfe-tasks/RHAISTRAT-1.md" in r.stderr
