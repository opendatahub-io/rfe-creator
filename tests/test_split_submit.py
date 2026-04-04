#!/usr/bin/env python3
"""Tests for scripts/split_submit.py — max children guardrail."""
import os
import subprocess
import sys

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                      "split_submit.py")


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


PARENT_TASK = """\
---
rfe_id: RHAIRFE-1000
title: Parent RFE
priority: Major
status: Archived
---

## Problem Statement

Original parent content.
"""

CHILD_TASK = """\
---
rfe_id: RFE-{num:03d}
title: Child RFE {num}
priority: Major
status: Ready
parent_key: RHAIRFE-1000
---

## Problem Statement

Child {num} content.
"""


def _run_split_submit(artifacts_dir, parent_key="RHAIRFE-1000"):
    """Run split_submit.py --dry-run and return result."""
    env = {
        **os.environ,
        "JIRA_SERVER": "",
        "JIRA_USER": "",
        "JIRA_TOKEN": "",
    }
    return subprocess.run(
        [sys.executable, SCRIPT, parent_key, "--dry-run",
         "--artifacts-dir", artifacts_dir],
        capture_output=True, text=True, env=env,
    )


@pytest.fixture
def art_dir(tmp_path):
    """Create a minimal artifacts directory."""
    for d in ["rfe-tasks", "rfe-reviews"]:
        os.makedirs(tmp_path / d)
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield str(tmp_path)
    os.chdir(orig)


class TestMaxLeafChildren:
    def test_exits_code_2_when_over_limit(self, art_dir):
        """More than MAX_LEAF_CHILDREN → exit code 2."""
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", PARENT_TASK)
        for i in range(1, 8):  # 7 children > 6 limit
            _write(f"{art_dir}/rfe-tasks/RFE-{i:03d}.md",
                   CHILD_TASK.format(num=i))

        result = _run_split_submit(art_dir)
        assert result.returncode == 2
        assert "Refusing to submit" in result.stderr
        assert "requires human review" in result.stderr
        assert "7 leaf children" in result.stderr

    def test_accepts_at_limit(self, art_dir):
        """Exactly MAX_LEAF_CHILDREN → proceeds (no exit code 2)."""
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", PARENT_TASK)
        for i in range(1, 7):  # 6 children = limit
            _write(f"{art_dir}/rfe-tasks/RFE-{i:03d}.md",
                   CHILD_TASK.format(num=i))

        result = _run_split_submit(art_dir)
        # Should not exit with code 2 (may fail for other reasons
        # in dry-run without Jira creds, but NOT the cap)
        assert result.returncode != 2
        assert "Refusing to submit" not in result.stderr

    def test_accepts_under_limit(self, art_dir):
        """Fewer than MAX_LEAF_CHILDREN → proceeds."""
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", PARENT_TASK)
        for i in range(1, 4):  # 3 children
            _write(f"{art_dir}/rfe-tasks/RFE-{i:03d}.md",
                   CHILD_TASK.format(num=i))

        result = _run_split_submit(art_dir)
        assert result.returncode != 2
        assert "Refusing to submit" not in result.stderr
