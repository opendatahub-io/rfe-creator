#!/usr/bin/env python3
"""Tests for scripts/jql_query.py — the default exclusion wrapper and paginated key listing."""

import os
import subprocess
import sys
import textwrap
import urllib.parse

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import jql_query  # noqa: E402
import type_registry  # noqa: E402

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
SCRIPT = os.path.join(SCRIPTS_DIR, "jql_query.py")
REG = type_registry.load(extra_roots=[], env={})

SAMPLE = "project = RHAIRFE AND status = New"


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
            conventions:
              labels: {ignore: docs-ignore, rubric_pass: docs-rubric-pass}
            """
        )
    )
    return str(root)


class TestDefaultExclusions:
    def test_wrapper_text_is_byte_identical_to_the_pre_registry_literals(self):
        # The two branches jql_query.py carried before reading the registry, verbatim.
        assert jql_query.wrap_jql(SAMPLE, "rfe") == (
            "(project = RHAIRFE AND status = New) AND statusCategory != Done"
            " AND (labels not in (rfe-creator-ignore,"
            " rfe-creator-autofix-rubric-pass) OR labels is EMPTY)"
        )
        assert jql_query.wrap_jql(SAMPLE, "initiative") == (
            "(project = RHAIRFE AND status = New) AND statusCategory != Done"
            " AND (labels not in (initiative-ignore,"
            " initiative-autofix-rubric-pass) OR labels is EMPTY)"
        )

    @pytest.mark.parametrize("type_name", REG.names())
    def test_labels_are_the_descriptors_ignore_and_rubric_pass(self, type_name):
        labels = REG.get(type_name).labels
        clause = jql_query.default_exclusions(type_name)
        assert clause == (
            " AND statusCategory != Done"
            f" AND (labels not in ({labels['ignore']}, {labels['rubric_pass']})"
            " OR labels is EMPTY)"
        )
        # design §3.6 invariant 4: quarantined items stay queryable
        assert labels["split_quarantine"] not in clause

    def test_wrap_parenthesises_the_caller_query(self):
        assert jql_query.wrap_jql("a OR b", "rfe").startswith("(a OR b) AND statusCategory")

    def test_unknown_project_is_a_key_error(self):
        with pytest.raises(KeyError, match="unknown type 'bogus'"):
            jql_query.wrap_jql(SAMPLE, "bogus")

    def test_a_drop_in_type_gets_its_own_wrapper(self, tmp_path):
        root = _dropin_root(tmp_path)
        env = {
            **os.environ,
            "RFE_CREATOR_EXTRA_TYPES": root,
            "RFE_CREATOR_EXTRA_TYPES_ALLOWLIST": root,
            "PYTHONPATH": SCRIPTS_DIR,
        }
        code = "import jql_query; print(jql_query.wrap_jql('x = 1', 'docs'))"
        result = subprocess.run(
            ["python3", "-c", code], capture_output=True, text=True, env=env, check=True
        )
        assert result.stdout.strip() == (
            "(x = 1) AND statusCategory != Done"
            " AND (labels not in (docs-ignore, docs-rubric-pass) OR labels is EMPTY)"
        )
        result = subprocess.run(
            ["python3", SCRIPT, "--help"], capture_output=True, text=True, env=env, check=True
        )
        assert "{rfe,docs,initiative}" in result.stdout


class TestCli:
    def test_project_choices_are_the_registry_choices(self):
        result = subprocess.run(["python3", SCRIPT, "--help"], capture_output=True, text=True)
        assert result.returncode == 0
        assert "{rfe,initiative}" in result.stdout

    def test_unknown_project_is_an_argparse_error(self):
        result = subprocess.run(
            ["python3", SCRIPT, "--project", "bogus", SAMPLE], capture_output=True, text=True
        )
        assert result.returncode == 2
        assert "invalid choice: 'bogus'" in result.stderr

    def test_missing_env_exits_one(self, monkeypatch, capsys):
        for var in ("JIRA_SERVER", "JIRA_USER", "JIRA_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(sys, "argv", ["jql_query.py", SAMPLE])
        with pytest.raises(SystemExit) as excinfo:
            jql_query.main()
        assert excinfo.value.code == 1
        assert "JIRA_SERVER, JIRA_USER, and JIRA_TOKEN must be set" in capsys.readouterr().err


class TestSearch:
    @pytest.fixture
    def fake_api(self, monkeypatch):
        calls = []
        pages = {}

        def api(server, path, user, token, body=None, method=None):
            calls.append(path)
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            token_value = query.get("nextPageToken", [None])[0]
            return pages[token_value]

        monkeypatch.setattr(jql_query, "api_call_with_retry", api)
        monkeypatch.setenv("JIRA_SERVER", "https://jira.example.com")
        monkeypatch.setenv("JIRA_USER", "u@example.com")
        monkeypatch.setenv("JIRA_TOKEN", "tok")
        return calls, pages

    def test_main_sends_the_wrapped_query_and_lists_keys(self, fake_api, monkeypatch, capsys):
        calls, pages = fake_api
        pages[None] = {"issues": [{"key": "RHOAIENG-1"}, {"key": "RHOAIENG-2"}], "isLast": True}
        monkeypatch.setattr(sys, "argv", ["jql_query.py", "--project", "initiative", SAMPLE])
        jql_query.main()
        out = capsys.readouterr()
        wrapped = jql_query.wrap_jql(SAMPLE, "initiative")
        assert out.err == f"JQL={wrapped}\n"
        assert out.out == "TOTAL=2\nRHOAIENG-1\nRHOAIENG-2\n"
        sent = urllib.parse.parse_qs(urllib.parse.urlsplit(calls[0]).query)
        assert sent["jql"] == [wrapped]
        assert sent["fields"] == ["key"]

    def test_default_project_is_rfe(self, fake_api, monkeypatch, capsys):
        calls, pages = fake_api
        pages[None] = {"issues": [], "isLast": True}
        monkeypatch.setattr(sys, "argv", ["jql_query.py", SAMPLE])
        jql_query.main()
        out = capsys.readouterr()
        assert out.err == f"JQL={jql_query.wrap_jql(SAMPLE, 'rfe')}\n"
        assert out.out == "TOTAL=0\n"

    def test_pagination_follows_next_page_token_and_honours_limit(self, fake_api, capsys):
        calls, pages = fake_api
        pages[None] = {
            "issues": [{"key": "A-1"}, {"key": "A-2"}],
            "isLast": False,
            "nextPageToken": "t2",
        }
        pages["t2"] = {"issues": [{"key": "A-3"}], "isLast": True}
        jql_query.search_issues("s", "u", "t", "q")
        assert capsys.readouterr().out == "TOTAL=3\nA-1\nA-2\nA-3\n"
        assert len(calls) == 2 and "nextPageToken=t2" in calls[1]

        calls.clear()
        jql_query.search_issues("s", "u", "t", "q", limit=1)
        assert capsys.readouterr().out == "TOTAL=1\nA-1\n"
        assert len(calls) == 1
