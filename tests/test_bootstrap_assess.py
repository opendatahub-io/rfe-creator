#!/usr/bin/env python3
"""Tests for scripts/bootstrap-assess-rfe.sh --type validation.

Bootstrap is the only gate between "the plugin checkout is complete" and an
agent phase that can never finish. A checkout missing the initiative rubric or
the initiative-scorer agent used to exit 0 here, and the failure surfaced much
later as wait-for-wave returning exit 3 forever with nothing to diagnose.
"""

import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from pipeline_state import PIPELINE_TYPES  # noqa: E402

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPT = os.path.join(REPO_ROOT, "scripts", "bootstrap-assess-rfe.sh")
SKILLS_DIR = os.path.join(REPO_ROOT, ".claude", "skills")

RFE_RUBRIC = "skills/assess-rfe/scripts/agent_prompt.md"
INITIATIVE_RUBRIC = "skills/assess-initiative/scripts/agent_prompt.md"


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("# stub\n")


@pytest.fixture
def fake_checkout(tmp_path):
    """A working dir with a pre-existing .context/assess-rfe.

    The directory being present sends the script down the `git pull` branch,
    which fails on a non-repo and is swallowed by its `|| echo WARN` — so these
    tests never touch the network.
    """
    ctx = tmp_path / ".context" / "assess-rfe"
    os.makedirs(ctx)
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield ctx
    os.chdir(orig)


def _run(*args):
    env = {k: v for k, v in os.environ.items() if k not in ("ASSESS_RFE_REF", "RFE_SKIP_BOOTSTRAP")}
    result = subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True, env=env)
    return result.stdout, result.stderr, result.returncode


def _add_rfe_assets(ctx):
    _touch(str(ctx / RFE_RUBRIC))


def _add_initiative_assets(ctx, rubric=True, agent=True):
    if rubric:
        _touch(str(ctx / INITIATIVE_RUBRIC))
    if agent:
        _touch(str(ctx / "agents" / "initiative-scorer.md"))


class TestTypeValidation:
    def test_rfe_default_passes_without_initiative_assets(self, fake_checkout):
        """The RFE path must not start failing over an asset it never uses."""
        _add_rfe_assets(fake_checkout)

        _, stderr, rc = _run()
        assert rc == 0, stderr

    def test_initiative_fails_when_rubric_missing(self, fake_checkout):
        """The exact upstream state today: RFE rubric present, initiative absent."""
        _add_rfe_assets(fake_checkout)

        _, stderr, rc = _run("--type", "initiative")
        assert rc == 1
        assert "assess-initiative" in stderr
        assert "ASSESS_RFE_REPO" in stderr

    def test_initiative_fails_when_scorer_agent_missing(self, fake_checkout):
        """Rubric alone is not enough — the assess agent needs the subagent type."""
        _add_rfe_assets(fake_checkout)
        _add_initiative_assets(fake_checkout, agent=False)

        _, stderr, rc = _run("--type", "initiative")
        assert rc == 1
        assert "initiative-scorer" in stderr

    def test_initiative_passes_with_full_checkout(self, fake_checkout):
        _add_rfe_assets(fake_checkout)
        _add_initiative_assets(fake_checkout)

        _, stderr, rc = _run("--type", "initiative")
        assert rc == 0, stderr

    def test_equals_form_accepted(self, fake_checkout):
        _add_rfe_assets(fake_checkout)

        _, stderr, rc = _run("--type=initiative")
        assert rc == 1, stderr
        assert "assess-initiative" in stderr

    def test_unknown_type_rejected(self, fake_checkout):
        _add_rfe_assets(fake_checkout)

        _, stderr, rc = _run("--type", "epic")
        assert rc == 2
        assert "expected rfe or initiative" in stderr

    def test_unknown_argument_rejected(self, fake_checkout):
        _add_rfe_assets(fake_checkout)

        _, stderr, rc = _run("--initiative")
        assert rc == 2
        assert "unknown argument" in stderr

    def test_skip_bootstrap_still_short_circuits(self, fake_checkout):
        """RFE_SKIP_BOOTSTRAP wins over validation — offline runs stay possible."""
        env = {**os.environ, "RFE_SKIP_BOOTSTRAP": "1"}
        result = subprocess.run(
            ["bash", SCRIPT, "--type", "initiative"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0


class TestPathsMatchPipelineRegistry:
    """The shell script restates paths PIPELINE_TYPES already owns.

    Duplication is fine, silent divergence is not: if the rubric moves in the
    registry but not here, bootstrap validates a path nothing reads.
    """

    def _script_text(self):
        with open(SCRIPT) as f:
            return f.read()

    def test_rfe_rubric_matches_registry(self):
        assert PIPELINE_TYPES["rfe"]["rubric_path"].endswith(RFE_RUBRIC)
        assert RFE_RUBRIC in self._script_text()

    def test_initiative_rubric_matches_registry(self):
        assert PIPELINE_TYPES["initiative"]["rubric_path"].endswith(INITIATIVE_RUBRIC)
        assert INITIATIVE_RUBRIC in self._script_text()

    def test_scorer_agent_filename_matches_registry(self):
        expected = PIPELINE_TYPES["initiative"]["scorer_type"] + ".md"
        assert f'INITIATIVE_AGENT="{expected}"' in self._script_text()


class TestCallersDeclareType:
    """An initiative skill that forgets the flag silently loses the gate."""

    def _bootstrap_lines(self, path):
        with open(path) as f:
            return [ln for ln in f if re.search(r"bootstrap-assess-rfe\.sh", ln)]

    def _skill_files(self):
        for dirpath, _, filenames in os.walk(SKILLS_DIR):
            for name in filenames:
                if name.endswith(".md"):
                    yield os.path.join(dirpath, name)

    def test_initiative_skills_pass_type_initiative(self):
        missing = []
        for path in self._skill_files():
            if "initiative-" not in path:
                continue
            for line in self._bootstrap_lines(path):
                if "--type initiative" not in line:
                    missing.append(f"{os.path.relpath(path, REPO_ROOT)}: {line.strip()}")
        detail = "\n".join(missing)
        assert not missing, f"initiative skills invoking bootstrap without --type:\n{detail}"

    def test_at_least_one_initiative_caller_exists(self):
        """Guards the filter above from passing vacuously."""
        found = [
            path
            for path in self._skill_files()
            if "initiative-" in path and self._bootstrap_lines(path)
        ]
        assert len(found) >= 3

    def test_pipeline_setup_phase_is_type_aware(self):
        """The pipeline is where a missed gate becomes an unbounded wait-for-wave spin."""
        from pipeline_state import _build_phase_config

        for ptype in PIPELINE_TYPES:
            setup = _build_phase_config(ptype)["SETUP"]
            # SETUP runs its bootstrap steps as a concurrent "commands" list.
            commands = setup.get("commands") or [setup["command"]]
            assert any(f"bootstrap-assess-rfe.sh --type {ptype}" in c for c in commands)
