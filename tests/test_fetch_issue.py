#!/usr/bin/env python3
"""Tests for scripts/fetch_issue.py — the type-aware ``--fetch-all`` artifact writer.

design-proposals/work-item-types-unified.md §10 item 2 ("fetch_issue.py --fetch-all made
type-aware"): the task and original directories (``dirs.tasks`` / ``dirs.originals``), the
frontmatter id field (``identity.id_field``) and whether a ``<KEY>-comments.md`` companion is
produced (``companions.comments``) are read from the selected type's descriptor. ``--type`` is
additive: the rfe invocation every skill issues today (no ``--type``) is byte-identical to the
pre-registry script — the golden bytes below were captured on main c1df503 and are literals on
purpose. The priority fallback ``Major`` and ``status=Ready`` are shared pipeline conventions,
not type facts, and stay literal in the script.

Unit tests monkeypatch the two Jira reads; the last class drives the script as a subprocess
against the jira-emulator (``jira`` fixture, tests/conftest.py).
"""

import io
import json
import os
import subprocess
import sys
import textwrap
from contextlib import redirect_stdout

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import fetch_issue  # noqa: E402
import type_registry  # noqa: E402
from artifact_utils import read_frontmatter, read_frontmatter_validated  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "fetch_issue.py")
REG = type_registry.load(extra_roots=[], env={})

JIRA_ENV = {"JIRA_SERVER": "http://x", "JIRA_USER": "u", "JIRA_TOKEN": "t"}
DEFAULT_FIELDS = ["summary", "description", "priority", "labels", "status", "issuetype"]


def _clean_env(**extra):
    # Subprocess env without the registry's RFE_CREATOR_* seam: a developer's
    # RFE_CREATOR_EXTRA_TYPES would otherwise widen --type's choices in the child.
    env = {k: v for k, v in os.environ.items() if not k.startswith("RFE_CREATOR_")}
    env.update(extra)
    return env


# ── fixtures: one issue, two comments, the c1df503 golden artifacts ───────────────────────────

DESC_MD = (
    "## Problem\n\n"
    "Data scientists cannot export registered models to S3-compatible storage.\n\n"
    "- Manual copies drift from the registry\n- No audit trail\n"
)


def _paragraph(text):
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


ADF = {
    "type": "doc",
    "version": 1,
    "content": [
        {
            "type": "heading",
            "attrs": {"level": 2},
            "content": [{"type": "text", "text": "Problem"}],
        },
        _paragraph("Data scientists cannot export registered models to S3-compatible storage."),
        {
            "type": "bulletList",
            "content": [
                {
                    "type": "listItem",
                    "content": [_paragraph("Manual copies drift from the registry")],
                },
                {"type": "listItem", "content": [_paragraph("No audit trail")]},
            ],
        },
    ],
}

ISSUE = {
    "key": "RHAIRFE-1595",
    "fields": {
        "summary": "Add model registry export to S3-compatible storage",
        "description": ADF,
        "priority": {"name": "Major"},
        "labels": ["rfe-creator-autofix-rubric-pass", "customer-request"],
        "status": {"name": "New"},
        "issuetype": {"name": "Feature Request", "id": "10700"},
    },
}

COMMENTS = [
    {
        "author": {"displayName": "Jane Doe"},
        "created": "2025-01-15T10:30:00.000+0000",
        "body": {
            "type": "doc",
            "version": 1,
            "content": [_paragraph("Acme Corp asked for this twice.")],
        },
    },
    {
        "author": {"displayName": "John Roe"},
        "created": "2025-02-01T08:00:00.000+0000",
        "body": "Plain string body.",
    },
]

# Captured on main c1df503 by running _fetch_all over ISSUE/COMMENTS (no --type existed).
GOLDEN_TASK = (
    b"---\nrfe_id: RHAIRFE-1595\ntitle: Add model registry export to S3-compatible storage\n"
    b"priority: Major\nstatus: Ready\noriginal_labels:\n- rfe-creator-autofix-rubric-pass\n"
    b"- customer-request\nlocal_id: null\nsize: null\nparent_key: null\n---\n" + DESC_MD.encode()
)
GOLDEN_ORIGINAL = DESC_MD.encode()
GOLDEN_COMMENTS = (
    "# Comments: RHAIRFE-1595\n\n"
    "## Jane Doe — 2025-01-15\n\nAcme Corp asked for this twice.\n\n"
    "## John Roe — 2025-02-01\n\nPlain string body.\n\n"
).encode("utf-8")


def _issue_for(key):
    """ISSUE re-keyed; an initiative key carries no priority (exercises the Major fallback)."""
    fields = dict(ISSUE["fields"])
    if key.startswith(REG.get("initiative").write_prefix):
        fields["priority"] = None
        fields["labels"] = []
        fields["issuetype"] = {"name": "Initiative", "id": "10103"}
    return {"key": key, "fields": fields}


@pytest.fixture
def fake_jira(monkeypatch):
    """Monkeypatch the two Jira reads and record every call; cwd is the repo root because
    _fetch_all shells out to scripts/frontmatter.py by relative path."""
    calls = {"issue": [], "comments": []}

    def fake_get_issue(server, user, token, key, fields=None):
        calls["issue"].append((key, list(fields) if fields else fields))
        return _issue_for(key)

    def fake_get_comments(server, user, token, key):
        calls["comments"].append(key)
        return COMMENTS

    monkeypatch.setattr(fetch_issue, "get_issue", fake_get_issue)
    monkeypatch.setattr(fetch_issue, "get_comments", fake_get_comments)
    for var, value in JIRA_ENV.items():
        monkeypatch.setenv(var, value)
    monkeypatch.chdir(REPO_ROOT)
    return calls


def _fetch_all(key, artifacts, **kwargs):
    """Run _fetch_all in-process; returns (rc, stdout)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fetch_issue._fetch_all(key, str(artifacts), "http://x", "u", "t", **kwargs)
    return rc, buf.getvalue()


def _main(monkeypatch, *argv):
    """Run main() in-process; returns (exit code, stdout). --fetch-all always sys.exit()s;
    the JSON modes return normally, which is exit 0."""
    monkeypatch.setattr(sys, "argv", ["fetch_issue.py", *argv])
    buf = io.StringIO()
    code = 0
    with redirect_stdout(buf):
        try:
            fetch_issue.main()
        except SystemExit as exc:
            code = exc.code
    return code, buf.getvalue()


def _tree(root):
    """{relative path: bytes} for every file under root."""
    out = {}
    for dirpath, _, files in os.walk(root):
        for name in files:
            path = os.path.join(dirpath, name)
            with open(path, "rb") as f:
                out[os.path.relpath(path, root)] = f.read()
    return out


# ── the rfe invocation is unchanged ──────────────────────────────────────────────────────────


class TestRfeInvocationIsByteIdentical:
    def test_default_type_writes_the_c1df503_golden_bytes(self, tmp_path, fake_jira):
        artifacts = tmp_path / "artifacts"
        rc, out = _fetch_all("RHAIRFE-1595", artifacts)
        assert rc == 0
        task = artifacts / "rfe-tasks" / "RHAIRFE-1595.md"
        original = artifacts / "rfe-originals" / "RHAIRFE-1595.md"
        comments = artifacts / "rfe-tasks" / "RHAIRFE-1595-comments.md"
        assert task.read_bytes() == GOLDEN_TASK
        assert original.read_bytes() == GOLDEN_ORIGINAL
        assert comments.read_bytes() == GOLDEN_COMMENTS
        assert _tree(artifacts).keys() == {
            "rfe-tasks/RHAIRFE-1595.md",
            "rfe-originals/RHAIRFE-1595.md",
            "rfe-tasks/RHAIRFE-1595-comments.md",
        }
        assert out == f"OK: wrote {task}, {original}, {comments}\n"
        # The request lists are PR-1's (issuetype requested, nothing written reads it) and the
        # comments are fetched exactly once, after the issue.
        assert fake_jira["issue"] == [("RHAIRFE-1595", DEFAULT_FIELDS)]
        assert fake_jira["comments"] == ["RHAIRFE-1595"]

    def test_explicit_type_rfe_equals_no_type(self, tmp_path, monkeypatch, fake_jira):
        implicit = tmp_path / "implicit"
        explicit = tmp_path / "explicit"
        code_a, out_a = _main(monkeypatch, "RHAIRFE-1595", "--fetch-all", str(implicit))
        code_b, out_b = _main(
            monkeypatch, "RHAIRFE-1595", "--fetch-all", str(explicit), "--type", "rfe"
        )
        assert code_a == code_b == 0
        assert _tree(implicit) == _tree(explicit)
        assert out_a.replace(str(implicit), "<dir>") == out_b.replace(str(explicit), "<dir>")
        assert _tree(implicit)["rfe-tasks/RHAIRFE-1595.md"] == GOLDEN_TASK

    def test_fetch_all_signature_is_backward_compatible(self, tmp_path, fake_jira):
        # Positional five-argument callers (tests/test_pr1_transparent_edits.py) still work and
        # still mean rfe.
        rc = fetch_issue._fetch_all("RHAIRFE-1595", str(tmp_path), "http://x", "u", "t")
        assert rc == 0
        assert (tmp_path / "rfe-tasks" / "RHAIRFE-1595.md").read_bytes() == GOLDEN_TASK


# ── the initiative layout ────────────────────────────────────────────────────────────────────


class TestInitiativeLayout:
    def test_writes_initiative_dirs_id_field_and_no_comments(self, tmp_path, fake_jira):
        artifacts = tmp_path / "artifacts"
        rc, out = _fetch_all("RHOAIENG-12345", artifacts, type_name="initiative")
        assert rc == 0
        task = artifacts / "initiatives" / "RHOAIENG-12345.md"
        original = artifacts / "initiative-originals" / "RHOAIENG-12345.md"
        assert _tree(artifacts).keys() == {
            "initiatives/RHOAIENG-12345.md",
            "initiative-originals/RHOAIENG-12345.md",
        }
        assert out == f"OK: wrote {task}, {original}\n"
        # companions.comments is false for initiative: no companion AND no comment request.
        assert fake_jira["comments"] == []
        assert fake_jira["issue"] == [("RHOAIENG-12345", DEFAULT_FIELDS)]

        data, body = read_frontmatter_validated(str(task), "initiative-task")
        assert data["initiative_id"] == "RHOAIENG-12345"
        assert "rfe_id" not in data
        assert data["title"] == ISSUE["fields"]["summary"]
        assert data["priority"] == "Major"  # fallback: the issue carries no priority
        assert data["status"] == "Ready"
        assert data["original_labels"] is None  # no labels -> null, as for rfe
        assert body == DESC_MD
        assert original.read_bytes() == GOLDEN_ORIGINAL

    def test_frontmatter_bytes(self, tmp_path, fake_jira):
        # The initiative-task schema's field order and defaults (no `size`, parent_key null),
        # written by scripts/frontmatter.py exactly as the initiative fetch agent hand-built it.
        rc, _ = _fetch_all("RHOAIENG-12345", tmp_path, type_name="initiative")
        assert rc == 0
        assert (tmp_path / "initiatives" / "RHOAIENG-12345.md").read_bytes() == (
            b"---\ninitiative_id: RHOAIENG-12345\n"
            b"title: Add model registry export to S3-compatible storage\n"
            b"priority: Major\nstatus: Ready\noriginal_labels: null\nlocal_id: null\n"
            b"parent_key: null\n---\n" + DESC_MD.encode()
        )

    def test_cli_type_initiative(self, tmp_path, monkeypatch, fake_jira):
        code, out = _main(
            monkeypatch, "RHOAIENG-12345", "--fetch-all", str(tmp_path), "--type", "initiative"
        )
        assert code == 0
        assert _tree(tmp_path).keys() == {
            "initiatives/RHOAIENG-12345.md",
            "initiative-originals/RHOAIENG-12345.md",
        }
        assert out.startswith("OK: wrote ")
        assert "comments" not in out


# ── every registered type: the layout is the descriptor projection ───────────────────────────


class TestLayoutIsTheDescriptorProjection:
    @pytest.mark.parametrize("type_name", REG.names())
    def test_dirs_id_field_and_companion_follow_the_descriptor(
        self, tmp_path, fake_jira, type_name
    ):
        desc = REG.get(type_name)
        key = f"{desc.write_prefix}42"
        bare = desc.dirs(form="bare")
        rc, out = _fetch_all(key, tmp_path, type_name=type_name)
        assert rc == 0

        task = tmp_path / bare["tasks"] / f"{key}.md"
        original = tmp_path / bare["originals"] / f"{key}.md"
        comments = tmp_path / bare["tasks"] / f"{key}-comments.md"
        expected = {task, original}
        if desc.get("companions.comments"):
            expected.add(comments)
        assert {tmp_path / rel for rel in _tree(tmp_path)} == expected
        assert (
            out
            == "OK: wrote "
            + ", ".join(str(p) for p in (task, original, comments)[: len(expected)])
            + "\n"
        )

        data, _ = read_frontmatter(str(task))
        assert list(data)[0] == desc.id_field
        assert data[desc.id_field] == key
        assert (fake_jira["comments"] == [key]) is bool(desc.get("companions.comments"))


def _dropin_root(tmp_path):
    """A third type under a drop-in root (registry env seam, dev/test only) that declares the
    facts artifact_utils needs to build its task schema, its own dirs and a comments companion.
    """
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
              local_id_pattern: "DOC-\\\\d+"
              id_field: doc_id
            dirs:
              tasks: artifacts/doc-tasks
              originals: artifacts/doc-originals
              reviews: artifacts/doc-reviews
            companions: {comments: true, removed_context: false}
            conventions:
              parent_key_patterns: ["DOCS-\\\\d+"]
            schema:
              task: {priority: {enum: [Blocker, Critical, Major, Normal, Minor, Undefined]}}
              review: {score_fields: [what, why]}
            """
        )
    )
    return str(root)


class TestDropInType:
    def test_a_third_type_writes_its_own_layout(self, tmp_path, monkeypatch, fake_jira):
        # Type-aware means every registered type, not an rfe/initiative fork: a drop-in with
        # companions.comments true gets its dirs, its id field AND the comments companion.
        root = _dropin_root(tmp_path)
        # The frontmatter.py subprocess must see the same registry (allowlisted for CI runs).
        monkeypatch.setenv("RFE_CREATOR_EXTRA_TYPES", root)
        monkeypatch.setenv("RFE_CREATOR_EXTRA_TYPES_ALLOWLIST", root)
        monkeypatch.setattr(fetch_issue, "_TYPES", type_registry.load(extra_roots=[root]))

        artifacts = tmp_path / "artifacts"
        rc, out = _fetch_all("DOCS-7", artifacts, type_name="docs")
        assert rc == 0
        assert _tree(artifacts).keys() == {
            "doc-tasks/DOCS-7.md",
            "doc-originals/DOCS-7.md",
            "doc-tasks/DOCS-7-comments.md",
        }
        data, _ = read_frontmatter(str(artifacts / "doc-tasks" / "DOCS-7.md"))
        assert data["doc_id"] == "DOCS-7"
        assert (artifacts / "doc-tasks" / "DOCS-7-comments.md").read_bytes() == (
            GOLDEN_COMMENTS.replace(b"RHAIRFE-1595", b"DOCS-7")
        )
        assert fake_jira["comments"] == ["DOCS-7"]

    def test_help_offers_the_drop_in(self, tmp_path):
        root = _dropin_root(tmp_path)
        env = _clean_env(RFE_CREATOR_EXTRA_TYPES=root, RFE_CREATOR_EXTRA_TYPES_ALLOWLIST=root)
        result = subprocess.run(
            [sys.executable, SCRIPT, "--help"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 0
        assert "--type {rfe,docs,initiative}" in result.stdout


# ── CLI surface ──────────────────────────────────────────────────────────────────────────────


class TestCli:
    def test_help_offers_the_registry_choices_and_keeps_the_fetch_all_text(self):
        result = subprocess.run(
            [sys.executable, SCRIPT, "--help"], capture_output=True, text=True, env=_clean_env()
        )
        assert result.returncode == 0
        assert "--type {" + ",".join(REG.choices()) + "}" in result.stdout
        assert "--type {rfe,initiative}" in result.stdout
        # The pre-registry --fetch-all sentence is byte-identical (argparse re-wraps it). Its
        # "(rfe-tasks, rfe-originals, comments)" wording is deliberately stale for
        # --type initiative: PR-2c keeps --help byte-identical apart from the new --type block.
        # The follow-up that makes the help type-neutral may reword it and update this pin;
        # the pin is not a requirement to keep the sentence.
        assert (
            "Fetch issue and write all artifact files (rfe-tasks,\n"
            "                        rfe-originals, comments) to the given directory."
        ) in result.stdout
        assert "(default: rfe)" in result.stdout

    def test_source_keeps_the_pre_registry_help_and_the_shared_conventions(self):
        with open(SCRIPT, encoding="utf-8") as f:
            source = f.read()
        # The "(rfe-tasks, rfe-originals, comments)" sentence is deliberately stale for
        # --type initiative (kept so --help changes only by the --type block in PR-2c); the
        # follow-up that makes the help type-neutral may reword it and update this pin.
        assert (
            'help="Fetch issue and write all artifact files "\n'
            '        "(rfe-tasks, rfe-originals, comments) to "\n'
            '        "the given directory.",'
        ) in source
        # Shared pipeline conventions, deliberately literal (not descriptor facts).
        assert '"status=Ready",' in source
        assert 'else "Major"' in source
        assert "choices=_TYPES.choices()" in source

    def test_unknown_type_is_a_usage_error(self, tmp_path):
        result = subprocess.run(
            [sys.executable, SCRIPT, "RHAIRFE-1", "--fetch-all", str(tmp_path), "--type", "epic"],
            capture_output=True,
            text=True,
            env=_clean_env(**JIRA_ENV),
        )
        assert result.returncode == 2
        assert "invalid choice: 'epic'" in result.stderr
        assert _tree(tmp_path) == {}

    def test_missing_credentials_exit_2_for_every_type(self, tmp_path, monkeypatch):
        for var in JIRA_ENV:
            monkeypatch.delenv(var, raising=False)
        for type_name in REG.names():
            code, out = _main(monkeypatch, "X-1", "--fetch-all", str(tmp_path), "--type", type_name)
            assert code == 2
            assert out == ""
        assert _tree(tmp_path) == {}

    def test_type_is_ignored_by_the_fields_mode(self, monkeypatch, fake_jira):
        # Only --fetch-all reads --type; the JSON modes are type-neutral and unchanged.
        code, out = _main(
            monkeypatch, "RHAIRFE-1595", "--fields", "summary,status", "--type", "initiative"
        )
        assert code == 0
        assert json.loads(out) == {
            "key": "RHAIRFE-1595",
            "fields": {"summary": ISSUE["fields"]["summary"], "status": {"name": "New"}},
        }
        assert fake_jira["issue"] == [("RHAIRFE-1595", ["summary", "status"])]


# ── end to end against the jira-emulator ─────────────────────────────────────────────────────


class TestFetchAllAgainstTheEmulator:
    @pytest.fixture
    def env(self, jira):
        return _clean_env(JIRA_SERVER=jira.url, JIRA_USER="admin", JIRA_TOKEN="admin")

    def _run(self, env, *args):
        return subprocess.run(
            [sys.executable, SCRIPT, *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=REPO_ROOT,
        )

    def test_rfe_writes_task_original_and_comments(self, tmp_path, jira, env):
        jira.request(
            "POST",
            "/api/admin/import",
            {
                "issues": [
                    {
                        "key": "RHAIRFE-1",
                        "summary": "Export models",
                        "project": "RHAIRFE",
                        "issue_type": "Feature Request",
                        "description": "Body text.",
                        "labels": ["customer-request"],
                        "priority": "Critical",
                    }
                ]
            },
        )
        jira.request(
            "POST",
            "/rest/api/3/issue/RHAIRFE-1/comment",
            {"body": {"type": "doc", "version": 1, "content": [_paragraph("Acme asked twice.")]}},
        )
        artifacts = tmp_path / "artifacts"
        result = self._run(env, "RHAIRFE-1", "--fetch-all", str(artifacts))
        assert result.returncode == 0, result.stderr
        task = artifacts / "rfe-tasks" / "RHAIRFE-1.md"
        original = artifacts / "rfe-originals" / "RHAIRFE-1.md"
        comments = artifacts / "rfe-tasks" / "RHAIRFE-1-comments.md"
        assert result.stdout == f"OK: wrote {task}, {original}, {comments}\n"

        data, body = read_frontmatter_validated(str(task), "rfe-task")
        assert data["rfe_id"] == "RHAIRFE-1"
        assert data["title"] == "Export models"
        assert data["priority"] == "Critical"
        assert data["status"] == "Ready"
        assert data["original_labels"] == ["customer-request"]
        assert body == "Body text.\n"
        assert original.read_text(encoding="utf-8") == "Body text.\n"
        text = comments.read_text(encoding="utf-8")
        assert text.startswith("# Comments: RHAIRFE-1\n\n## Admin User — ")
        assert text.endswith("\n\nAcme asked twice.\n\n")

    def test_initiative_writes_only_task_and_original(self, tmp_path, jira, env):
        jira.create("RHOAIENG-1", "Serve models at the edge", "Init body.", issue_type="Initiative")
        artifacts = tmp_path / "artifacts"
        result = self._run(env, "RHOAIENG-1", "--fetch-all", str(artifacts), "--type", "initiative")
        assert result.returncode == 0, result.stderr
        task = artifacts / "initiatives" / "RHOAIENG-1.md"
        original = artifacts / "initiative-originals" / "RHOAIENG-1.md"
        assert result.stdout == f"OK: wrote {task}, {original}\n"
        assert _tree(artifacts).keys() == {
            "initiatives/RHOAIENG-1.md",
            "initiative-originals/RHOAIENG-1.md",
        }

        data, body = read_frontmatter_validated(str(task), "initiative-task")
        assert data["initiative_id"] == "RHOAIENG-1"
        assert data["title"] == "Serve models at the edge"
        assert data["priority"] == "Major"  # the emulator leaves priority unset
        assert data["status"] == "Ready"
        assert data["original_labels"] is None
        assert body == "Init body.\n"
        assert original.read_text(encoding="utf-8") == "Init body.\n"
