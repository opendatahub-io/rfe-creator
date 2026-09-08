#!/usr/bin/env python3
"""Tests for scripts/frontmatter.py — the registry-derived schema path table and the CLI."""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import frontmatter  # noqa: E402
from artifact_utils import SCHEMAS, get_schema_yaml  # noqa: E402

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


class TestDetectSchemaType:
    def test_table_is_the_hand_written_order(self):
        """Registry order, reviews before tasks per type — the order the literal `if`
        chain tested in (rfe-reviews, rfe-tasks, initiative-reviews, initiatives)."""
        assert frontmatter._SCHEMA_BY_DIR == [
            ("rfe-reviews/", "rfe-review"),
            ("rfe-tasks/", "rfe-task"),
            ("initiative-reviews/", "initiative-review"),
            ("initiatives/", "initiative-task"),
        ]

    @pytest.mark.parametrize(
        "path, expected",
        [
            ("artifacts/rfe-tasks/RFE-001.md", "rfe-task"),
            ("rfe-tasks/RFE-001.md", "rfe-task"),
            ("/abs/work/artifacts/rfe-tasks/RHAIRFE-1595.md", "rfe-task"),
            ("artifacts/rfe-reviews/RFE-001-review.md", "rfe-review"),
            ("rfe-reviews/RHAIRFE-1595-review.md", "rfe-review"),
            ("artifacts/initiatives/INIT-001.md", "initiative-task"),
            ("initiatives/RHOAIENG-1.md", "initiative-task"),
            ("artifacts/initiative-reviews/INIT-001-review.md", "initiative-review"),
            ("/abs/artifacts/initiative-reviews/RHOAIENG-1-review.md", "initiative-review"),
            # Not a task/review dir: originals, staging, anything else.
            ("artifacts/rfe-originals/RHAIRFE-1595.md", None),
            ("artifacts/initiative-originals/RHOAIENG-1.md", None),
            ("tmp/rfe-assess/single/RFE-001.md", None),
            ("RFE-001.md", None),
        ],
    )
    def test_detects_from_dirs(self, path, expected):
        assert frontmatter._detect_schema_type(path) == expected

    def test_every_schema_is_reachable_from_its_dir(self):
        detected = {schema for _, schema in frontmatter._SCHEMA_BY_DIR}
        assert detected == set(SCHEMAS)


class TestSchemaCommand:
    @pytest.mark.parametrize("name", list(SCHEMAS))
    def test_prints_get_schema_yaml(self, name):
        result = subprocess.run(
            [sys.executable, "scripts/frontmatter.py", "schema", name],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == get_schema_yaml(name) + "\n"
        assert result.stderr == ""

    def test_choices_are_the_schema_keys_in_order(self):
        result = subprocess.run(
            [sys.executable, "scripts/frontmatter.py", "schema", "bogus"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        positions = [result.stderr.find(f"'{name}'") for name in SCHEMAS]
        assert all(p >= 0 for p in positions), result.stderr
        assert positions == sorted(positions), result.stderr
