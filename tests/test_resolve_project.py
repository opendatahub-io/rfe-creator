"""Tests for resolve_project.py."""

import os
import subprocess
import sys

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "resolve_project.py")


class TestResolveProject:
    def test_both_set(self):
        env = {k: v for k, v in os.environ.items()}
        env["JIRA_PROJECT"] = "MYPROJ"
        env["JIRA_ISSUE_TYPE"] = "Bug"
        result = subprocess.run(
            [sys.executable, SCRIPT],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "JIRA_PROJECT=MYPROJ" in result.stdout
        assert "JIRA_ISSUE_TYPE=Bug" in result.stdout

    def test_project_only_defaults_issue_type(self):
        env = {k: v for k, v in os.environ.items() if k != "JIRA_ISSUE_TYPE"}
        env["JIRA_PROJECT"] = "RHAIRFE"
        result = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, env=env)
        assert result.returncode == 0
        assert "JIRA_PROJECT=RHAIRFE" in result.stdout
        assert "JIRA_ISSUE_TYPE=Feature Request" in result.stdout

    def test_missing_project_exits_nonzero(self):
        env = {k: v for k, v in os.environ.items() if k not in ("JIRA_PROJECT", "JIRA_ISSUE_TYPE")}
        result = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, env=env)
        assert result.returncode == 1
        assert "JIRA_PROJECT" in result.stderr
