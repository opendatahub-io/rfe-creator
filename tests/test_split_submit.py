#!/usr/bin/env python3
"""Tests for scripts/split_submit.py — guardrails and ADF output."""

import os
import subprocess
import sys
import urllib.error
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from jira_utils import create_issue
from split_submit import SubmissionState, build_split_summary_adf, discover_state

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "split_submit.py")


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
        [sys.executable, SCRIPT, parent_key, "--dry-run", "--artifacts-dir", artifacts_dir],
        capture_output=True,
        text=True,
        env=env,
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
            _write(f"{art_dir}/rfe-tasks/RFE-{i:03d}.md", CHILD_TASK.format(num=i))

        result = _run_split_submit(art_dir)
        assert result.returncode == 2
        assert "Refusing to submit" in result.stderr
        assert "requires human review" in result.stderr
        assert "7 leaf children" in result.stderr

    def test_accepts_at_limit(self, art_dir):
        """Exactly MAX_LEAF_CHILDREN → proceeds (no exit code 2)."""
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", PARENT_TASK)
        for i in range(1, 7):  # 6 children = limit
            _write(f"{art_dir}/rfe-tasks/RFE-{i:03d}.md", CHILD_TASK.format(num=i))

        result = _run_split_submit(art_dir)
        # Should not exit with code 2 (may fail for other reasons
        # in dry-run without Jira creds, but NOT the cap)
        assert result.returncode != 2
        assert "Refusing to submit" not in result.stderr

    def test_accepts_under_limit(self, art_dir):
        """Fewer than MAX_LEAF_CHILDREN → proceeds."""
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", PARENT_TASK)
        for i in range(1, 4):  # 3 children
            _write(f"{art_dir}/rfe-tasks/RFE-{i:03d}.md", CHILD_TASK.format(num=i))

        result = _run_split_submit(art_dir)
        assert result.returncode != 2
        assert "Refusing to submit" not in result.stderr


class TestSplitSummaryAdf:
    def test_produces_inline_cards(self):
        """Summary ADF uses inlineCard nodes for child keys."""
        state = SubmissionState()
        state.phase2_done = {1: "RHAIRFE-100", 2: "RHAIRFE-101"}
        children = [
            ("RFE-001", "First child", "Major", "/fake/path1"),
            ("RFE-002", "Second child", "Major", "/fake/path2"),
        ]
        adf = build_split_summary_adf("https://jira.example.com", children, state, 2)

        # Top-level structure
        assert adf["type"] == "doc"
        content = adf["content"]
        assert len(content) == 3  # paragraph, bulletList, paragraph
        assert content[1]["type"] == "bulletList"

        # Correct number of list items
        items = content[1]["content"]
        assert len(items) == 2

        # Each item has inlineCard with correct URL
        for i, item in enumerate(items):
            para = item["content"][0]
            inline_card = para["content"][0]
            assert inline_card["type"] == "inlineCard"
            expected_key = state.phase2_done[i + 1]
            assert inline_card["attrs"]["url"] == f"https://jira.example.com/browse/{expected_key}"

    def test_strips_trailing_slash(self):
        """Trailing slash on server URL does not produce double slash."""
        state = SubmissionState()
        state.phase2_done = {1: "RHAIRFE-100"}
        children = [("RFE-001", "Child", "Major", "/fake/path")]
        adf = build_split_summary_adf("https://jira.example.com/", children, state, 1)

        item = adf["content"][1]["content"][0]
        url = item["content"][0]["content"][0]["attrs"]["url"]
        assert "//" not in url.replace("https://", "")


class TestCreateIssueReporter:
    @patch("jira_utils.api_call_with_retry")
    def test_reporter_included_when_provided(self, mock_api):
        mock_api.return_value = {"key": "RHAIRFE-999"}
        create_issue(
            "https://jira.example.com",
            "user",
            "token",
            "RHAIRFE",
            "Feature Request",
            "Test summary",
            {"type": "doc", "version": 1, "content": []},
            "Major",
            reporter_account_id="abc123",
        )
        call_body = mock_api.call_args[1].get("body") or mock_api.call_args[0][4]
        assert call_body["fields"]["reporter"] == {"accountId": "abc123"}

    @patch("jira_utils.api_call_with_retry")
    def test_reporter_omitted_when_not_provided(self, mock_api):
        mock_api.return_value = {"key": "RHAIRFE-999"}
        create_issue(
            "https://jira.example.com",
            "user",
            "token",
            "RHAIRFE",
            "Feature Request",
            "Test summary",
            {"type": "doc", "version": 1, "content": []},
            "Major",
        )
        call_body = mock_api.call_args[1].get("body") or mock_api.call_args[0][4]
        assert "reporter" not in call_body["fields"]

    @patch("jira_utils.api_call_with_retry")
    def test_reporter_stripped_of_whitespace(self, mock_api):
        """Leading/trailing whitespace on reporter_account_id is stripped."""
        mock_api.return_value = {"key": "RHAIRFE-999"}
        create_issue(
            "https://jira.example.com",
            "user",
            "token",
            "RHAIRFE",
            "Feature Request",
            "Test summary",
            {"type": "doc", "version": 1, "content": []},
            "Major",
            reporter_account_id="  abc123  ",
        )
        call_body = mock_api.call_args[1].get("body") or mock_api.call_args[0][4]
        assert call_body["fields"]["reporter"] == {"accountId": "abc123"}

    @patch("jira_utils.api_call_with_retry")
    def test_reporter_omitted_when_blank(self, mock_api):
        """Blank/whitespace-only reporter_account_id is treated as absent."""
        mock_api.return_value = {"key": "RHAIRFE-999"}
        create_issue(
            "https://jira.example.com",
            "user",
            "token",
            "RHAIRFE",
            "Feature Request",
            "Test summary",
            {"type": "doc", "version": 1, "content": []},
            "Major",
            reporter_account_id="   ",
        )
        call_body = mock_api.call_args[1].get("body") or mock_api.call_args[0][4]
        assert "reporter" not in call_body["fields"]

    @patch("jira_utils.api_call_with_retry")
    def test_reporter_fallback_on_403(self, mock_api):
        """If Jira rejects reporter with 403, retry without reporter field."""
        error_response = urllib.error.HTTPError(
            url="https://jira.example.com/rest/api/3/issue",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=None,
        )
        mock_api.side_effect = [error_response, {"key": "RHAIRFE-999"}]
        key = create_issue(
            "https://jira.example.com",
            "user",
            "token",
            "RHAIRFE",
            "Feature Request",
            "Test summary",
            {"type": "doc", "version": 1, "content": []},
            "Major",
            reporter_account_id="abc123",
        )
        assert key == "RHAIRFE-999"
        assert mock_api.call_count == 2
        # Second call should not have reporter in fields
        retry_body = mock_api.call_args_list[1][1].get("body") or mock_api.call_args_list[1][0][4]
        assert "reporter" not in retry_body["fields"]

    @patch("jira_utils.api_call_with_retry")
    def test_reporter_fallback_on_400(self, mock_api):
        """If Jira rejects reporter with 400, retry without reporter field."""
        error_response = urllib.error.HTTPError(
            url="https://jira.example.com/rest/api/3/issue",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=None,
        )
        mock_api.side_effect = [error_response, {"key": "RHAIRFE-999"}]
        key = create_issue(
            "https://jira.example.com",
            "user",
            "token",
            "RHAIRFE",
            "Feature Request",
            "Test summary",
            {"type": "doc", "version": 1, "content": []},
            "Major",
            reporter_account_id="abc123",
        )
        assert key == "RHAIRFE-999"
        assert mock_api.call_count == 2

    @patch("jira_utils.api_call_with_retry")
    def test_non_reporter_error_not_caught(self, mock_api):
        """Non-400/403 errors are re-raised, not silently retried."""
        error_response = urllib.error.HTTPError(
            url="https://jira.example.com/rest/api/3/issue",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=None,
        )
        mock_api.side_effect = error_response
        with pytest.raises(urllib.error.HTTPError):
            create_issue(
                "https://jira.example.com",
                "user",
                "token",
                "RHAIRFE",
                "Feature Request",
                "Test summary",
                {"type": "doc", "version": 1, "content": []},
                "Major",
                reporter_account_id="abc123",
            )


class TestDiscoverStateReporter:
    @patch("split_submit.get_issue")
    @patch("split_submit.get_comments")
    def test_captures_parent_reporter(self, mock_comments, mock_issue):
        mock_comments.return_value = []
        mock_issue.return_value = {
            "fields": {
                "issuelinks": [],
                "status": {"statusCategory": {"key": "new"}},
                "components": [],
                "labels": [],
                "parent": None,
                "reporter": {
                    "accountId": "reporter-123",
                    "displayName": "Jane Doe",
                },
            }
        }
        state = discover_state("https://jira.example.com", "u", "t", "RHAIRFE-1000", [])
        assert state.parent_reporter_id == "reporter-123"

    @patch("split_submit.get_issue")
    @patch("split_submit.get_comments")
    def test_handles_missing_reporter(self, mock_comments, mock_issue):
        mock_comments.return_value = []
        mock_issue.return_value = {
            "fields": {
                "issuelinks": [],
                "status": {"statusCategory": {"key": "new"}},
                "components": [],
                "labels": [],
                "parent": None,
            }
        }
        state = discover_state("https://jira.example.com", "u", "t", "RHAIRFE-1000", [])
        assert state.parent_reporter_id is None
