#!/usr/bin/env python3
"""Tests for scripts/fetch_issue.py — the type-aware ``--fetch-all`` artifact writer.

design-proposals/work-item-types-unified.md §10 item 2 ("fetch_issue.py --fetch-all made
type-aware"): the task and original directories (``dirs.tasks`` / ``dirs.originals``), the
frontmatter id field (``identity.id_field``) and whether a ``<KEY>-comments.md`` companion is
produced (``companions.comments``) are read from the selected type's descriptor. ``--type`` is
additive: the rfe invocation every skill issues today (no ``--type``) writes the pre-registry
bytes captured on main c1df503 (literals on purpose) plus, since PR-3c (design §5
self-describing artifacts), the two appended frontmatter lines ``type: <type>`` and
``tracker_ref: <KEY>`` — a fetched task is a NEW artifact, and D7 appends, never reorders.
The priority fallback ``Major`` and ``status=Ready`` are shared pipeline conventions, not type
facts, and stay literal in the script.

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
# --fetch-all also requests the project witness of the post-fetch verification (PR-3c, D9);
# the JSON modes keep DEFAULT_FIELDS.
FETCH_ALL_FIELDS = DEFAULT_FIELDS + ["project"]


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
# PR-3c (design §5 self-describing artifacts, D7): the fetched task is a NEW artifact, so the
# golden gains exactly two lines, `type: rfe` and `tracker_ref: RHAIRFE-1595`, appended after
# the pre-3c fields (the schema defaults still trail them); nothing else moved.
STAMP_LINES = b"type: rfe\ntracker_ref: RHAIRFE-1595\n"
GOLDEN_TASK = (
    b"---\nrfe_id: RHAIRFE-1595\ntitle: Add model registry export to S3-compatible storage\n"
    b"priority: Major\nstatus: Ready\noriginal_labels:\n- rfe-creator-autofix-rubric-pass\n"
    b"- customer-request\n"
    + STAMP_LINES
    + b"local_id: null\nsize: null\nparent_key: null\n---\n"
    + DESC_MD.encode()
)
GOLDEN_ORIGINAL = DESC_MD.encode()
GOLDEN_COMMENTS = (
    "# Comments: RHAIRFE-1595\n\n"
    "## Jane Doe — 2025-01-15\n\nAcme Corp asked for this twice.\n\n"
    "## John Roe — 2025-02-01\n\nPlain string body.\n\n"
).encode("utf-8")


def _issue_for(key):
    """ISSUE re-keyed to ``key`` and living where the key says: ``project.key`` is the key stem
    and ``issuetype.name`` the issue type of the registered type whose prefix the key carries
    (fetch_issue's live registry, so a drop-in counts) — the pair the post-fetch verification
    compares with the effective binding (PR-3c, D9). A key no type owns keeps ISSUE's Feature
    Request. An initiative key carries no priority (exercises the Major fallback)."""
    fields = dict(ISSUE["fields"])
    stem = key.split("-")[0]
    fields["project"] = {"key": stem, "id": "10001", "name": stem}
    desc = fetch_issue._TYPES.detect(key)
    if desc is not None:
        fields["issuetype"] = {"name": desc.binding(env={})["issue_type"], "id": "1"}
    if key.startswith(REG.get("initiative").write_prefix):
        fields["priority"] = None
        fields["labels"] = []
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
        # The request list is PR-1's plus the PR-3c project witness (both requested for the
        # post-fetch verification only; nothing written reads them) and the comments are
        # fetched exactly once, after the issue.
        assert fake_jira["issue"] == [("RHAIRFE-1595", FETCH_ALL_FIELDS)]
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
        assert fake_jira["issue"] == [("RHOAIENG-12345", FETCH_ALL_FIELDS)]

        data, body = read_frontmatter_validated(str(task), "initiative-task")
        assert data["initiative_id"] == "RHOAIENG-12345"
        assert "rfe_id" not in data
        assert data["title"] == ISSUE["fields"]["summary"]
        assert data["priority"] == "Major"  # fallback: the issue carries no priority
        assert data["status"] == "Ready"
        assert data["original_labels"] is None  # no labels -> null, as for rfe
        assert (data["type"], data["tracker_ref"]) == ("initiative", "RHOAIENG-12345")
        assert body == DESC_MD
        assert original.read_bytes() == GOLDEN_ORIGINAL

    def test_frontmatter_bytes(self, tmp_path, fake_jira):
        # The initiative-task schema's field order and defaults (no `size`, parent_key null),
        # written by scripts/frontmatter.py exactly as the pre-D10 initiative fetch agent
        # hand-built them from `--fields ... --markdown` JSON; since PR-3c the agent runs
        # `--fetch-all artifacts --type initiative`, so this writer is the only one.
        rc, _ = _fetch_all("RHOAIENG-12345", tmp_path, type_name="initiative")
        assert rc == 0
        assert (tmp_path / "initiatives" / "RHOAIENG-12345.md").read_bytes() == (
            b"---\ninitiative_id: RHOAIENG-12345\n"
            b"title: Add model registry export to S3-compatible storage\n"
            b"priority: Major\nstatus: Ready\noriginal_labels: null\n"
            b"type: initiative\ntracker_ref: RHOAIENG-12345\n"
            b"local_id: null\nparent_key: null\n---\n" + DESC_MD.encode()
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
        # The self-describing pair follows the pre-3c fields directly (before the schema
        # defaults frontmatter.py appends): type name, then the key the task was fetched from.
        keys = list(data)
        assert keys[keys.index("original_labels") + 1 :][:2] == ["type", "tracker_ref"]
        assert (data["type"], data["tracker_ref"]) == (type_name, key)
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
        assert (data["type"], data["tracker_ref"]) == ("docs", "DOCS-7")
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


# ── post-fetch verification (design §5 self-describing artifacts, PR-3c D9) ─────────────────

RFE_BINDS = "(RHAIRFE, Feature Request)"


class TestPostFetchVerification:
    """The fetched ``(project.key, issuetype.name)`` pair must equal the resolved type's EFFECTIVE
    ``(project, issue_type)`` before anything is written. A mismatch writes no task, original or
    comments file (not even the directories), prints one stderr line naming the key, the fetched
    pair, the expected pair and the resolved type — plus ``re-run with --type <t>`` when exactly
    one registered type owns the fetched pair — and returns 1; a response without a witness
    cannot be verified and fails the same way (fail closed)."""

    @staticmethod
    def _serve(monkeypatch, issue):
        monkeypatch.setattr(fetch_issue, "get_issue", lambda *a, **kw: issue)

    def test_mismatch_writes_nothing_and_hints_the_owning_type(self, tmp_path, fake_jira, capsys):
        artifacts = tmp_path / "artifacts"
        # The legacy default (rfe) over an Initiative: the pair is owned by exactly one type.
        rc, out = _fetch_all("RHOAIENG-12345", artifacts)
        assert (rc, out) == (1, "")
        assert capsys.readouterr().err == (
            "Error: RHOAIENG-12345 is (RHOAIENG, Initiative) in Jira but the resolved type rfe "
            f"binds {RFE_BINDS}; nothing written - re-run with --type initiative\n"
        )
        assert not artifacts.exists()
        assert fake_jira["comments"] == []

    def test_wrong_explicit_type_hints_the_right_one(self, tmp_path, fake_jira, capsys):
        rc, _ = _fetch_all("RHAIRFE-1595", tmp_path / "artifacts", type_name="initiative")
        assert rc == 1
        assert capsys.readouterr().err == (
            "Error: RHAIRFE-1595 is (RHAIRFE, Feature Request) in Jira but the resolved type "
            "initiative binds (RHOAIENG, Initiative); nothing written - re-run with --type rfe\n"
        )
        assert not (tmp_path / "artifacts").exists()

    def test_no_hint_when_no_type_owns_the_pair(self, tmp_path, fake_jira, monkeypatch, capsys):
        issue = _issue_for("RHAIRFE-1595")
        issue["fields"]["issuetype"] = {"name": "Bug", "id": "1"}
        self._serve(monkeypatch, issue)
        rc, _ = _fetch_all("RHAIRFE-1595", tmp_path / "artifacts")
        assert rc == 1
        err = capsys.readouterr().err
        assert err == (
            "Error: RHAIRFE-1595 is (RHAIRFE, Bug) in Jira but the resolved type rfe binds "
            f"{RFE_BINDS}; nothing written\n"
        )
        assert "re-run with --type" not in err
        assert not (tmp_path / "artifacts").exists()

    @pytest.mark.parametrize(
        "dropped, named",
        [
            (("project",), "project"),
            (("issuetype",), "issuetype"),
            (("project", "issuetype"), "project or issuetype"),
        ],
    )
    def test_missing_witness_fails_closed(
        self, tmp_path, fake_jira, monkeypatch, capsys, dropped, named
    ):
        issue = _issue_for("RHAIRFE-1595")
        for name in dropped:
            del issue["fields"][name]
        self._serve(monkeypatch, issue)
        rc, out = _fetch_all("RHAIRFE-1595", tmp_path / "artifacts")
        assert (rc, out) == (1, "")
        assert capsys.readouterr().err == (
            f"Error: cannot verify RHAIRFE-1595 against the resolved type rfe binding {RFE_BINDS}: "
            f"the fetched issue has no {named} field; nothing written\n"
        )
        assert not (tmp_path / "artifacts").exists()

    @pytest.mark.parametrize("value", [None, {}, {"id": "10001"}, "RHAIRFE"])
    def test_a_project_without_a_key_is_missing(
        self, tmp_path, fake_jira, monkeypatch, capsys, value
    ):
        issue = _issue_for("RHAIRFE-1595")
        issue["fields"]["project"] = value
        self._serve(monkeypatch, issue)
        rc, _ = _fetch_all("RHAIRFE-1595", tmp_path / "artifacts")
        assert rc == 1
        assert "the fetched issue has no project field" in capsys.readouterr().err

    def test_verification_uses_the_effective_binding(self):
        # The pure check over explicit environments: a foreign-project Feature Request is refused
        # under the descriptor binding (no hint: no type owns KONFLUX) and accepted once rfe is
        # bound to that project; the hint follows the effective bindings too.
        rfe = REG.get("rfe")
        fields = _issue_for("KONFLUX-1")["fields"]
        assert fields["issuetype"]["name"] == "Feature Request"
        assert fetch_issue.verify_binding("KONFLUX-1", fields, "rfe", rfe.binding(env={}), {}) == (
            "Error: KONFLUX-1 is (KONFLUX, Feature Request) in Jira but the resolved type rfe "
            f"binds {RFE_BINDS}; nothing written"
        )
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"}
        assert fetch_issue.verify_binding("KONFLUX-1", fields, "rfe", rfe.binding(env), env) is None
        home = _issue_for("RHAIRFE-1")["fields"]
        assert fetch_issue.verify_binding("RHAIRFE-1", home, "rfe", rfe.binding(env), env) == (
            "Error: RHAIRFE-1 is (RHAIRFE, Feature Request) in Jira but the resolved type rfe "
            "binds (KONFLUX, Feature Request); nothing written"
        )
        # An issue-type override is honoured the same way, and the artifact path is open to it
        # (the key grammar is unchanged): see the emulator suite below.
        env = {"RFE_CREATOR_BINDING_RFE_ISSUE_TYPE": "Epic"}
        epic = _issue_for("RHAIRFE-1")["fields"] | {"issuetype": {"name": "Epic"}}
        assert fetch_issue.verify_binding("RHAIRFE-1", epic, "rfe", rfe.binding(env), env) is None

    def test_the_resolved_type_is_never_the_hint(self, tmp_path, monkeypatch, fake_jira, capsys):
        # Under the bare JIRA_PROJECT shorthand rfe resolves to (RHOAIENG, Feature Request); a
        # fetched (RHAIRFE, Feature Request) is rfe's own DESCRIPTOR pair, but the shorthand
        # follows the resolved type, so re-running with --type rfe would fail the same way. The
        # owners scan therefore skips the resolved type: the refusal carries no hint at all
        # rather than "re-run with --type rfe".
        env = {"JIRA_PROJECT": "RHOAIENG"}
        rfe = REG.get("rfe")
        binding = rfe.binding(env, shorthand=True)
        assert (binding["project"], binding["issue_type"]) == ("RHOAIENG", "Feature Request")
        fields = _issue_for("RHAIRFE-1595")["fields"]
        message = fetch_issue.verify_binding("RHAIRFE-1595", fields, "rfe", binding, env)
        assert message == (
            "Error: RHAIRFE-1595 is (RHAIRFE, Feature Request) in Jira but the resolved type rfe "
            "binds (RHOAIENG, Feature Request); nothing written"
        )
        assert "re-run with --type" not in message
        # Through main: resolve applies the shorthand to the legacy default (no D3 line), the
        # ownership check passes (initiative binds Initiative, not Feature Request) and the
        # post-fetch refusal is exactly the line above.
        monkeypatch.setenv("JIRA_PROJECT", "RHOAIENG")
        code, out = _main(monkeypatch, "RHAIRFE-1595", "--fetch-all", str(tmp_path / "a"))
        assert (code, out) == (1, "")
        assert capsys.readouterr().err == message + "\n"
        assert not (tmp_path / "a").exists()
        # Another type that owns the fetched pair is still offered under the same shorthand.
        rc, _ = _fetch_all("RHOAIENG-12345", tmp_path / "b")
        assert rc == 1
        assert capsys.readouterr().err == (
            "Error: RHOAIENG-12345 is (RHOAIENG, Initiative) in Jira but the resolved type rfe "
            "binds (RHOAIENG, Feature Request); nothing written - re-run with --type initiative\n"
        )
        assert not (tmp_path / "b").exists()

    def test_direct_callers_verify_against_the_types_own_effective_binding(
        self, tmp_path, fake_jira, monkeypatch, capsys
    ):
        # _fetch_all without a binding (the positional callers) resolves the type's effective
        # binding itself — the same pair main() would pass.
        monkeypatch.setenv("RFE_CREATOR_BINDING_RFE_ISSUE_TYPE", "Epic")
        rc, _ = _fetch_all("RHAIRFE-1595", tmp_path / "artifacts")
        assert rc == 1
        assert capsys.readouterr().err == (
            "Error: RHAIRFE-1595 is (RHAIRFE, Feature Request) in Jira but the resolved type rfe "
            "binds (RHAIRFE, Epic); nothing written\n"
        )

    def test_a_malformed_override_for_another_type_drops_the_hint_not_the_refusal(
        self, tmp_path, monkeypatch, fake_jira, capsys
    ):
        # The re-run hint scans every type's effective binding; for a direct caller of
        # _fetch_all (main refuses such an environment up front, see the emulator suite) a
        # malformed variable for a type OTHER than the resolved one must not turn the refusal
        # into a traceback: the one-line refusal stands, without the hint.
        monkeypatch.setenv("RFE_CREATOR_BINDING_INITIATIVE_PROJECT", "lower")
        artifacts = tmp_path / "artifacts"
        rc, out = _fetch_all("RHOAIENG-12345", artifacts)
        assert (rc, out) == (1, "")
        assert capsys.readouterr().err == (
            "Error: RHOAIENG-12345 is (RHOAIENG, Initiative) in Jira but the resolved type rfe "
            f"binds {RFE_BINDS}; nothing written\n"
        )
        assert not artifacts.exists()
        # The match path never read that variable and is unaffected.
        rc, out = _fetch_all("RHAIRFE-1595", artifacts)
        assert rc == 0 and out.startswith("OK: wrote ")

    def test_an_override_selecting_another_types_pair_is_refused_before_the_fetch(
        self, tmp_path, monkeypatch, fake_jira, capsys
    ):
        # §3.2.1 g: rfe re-bound to the initiative pair would pass the post-fetch check for an
        # Initiative and start the rfe-layout write of it; the ownership assert refuses the
        # override first — no fetch, no directory, exit 1 — and names the type to pass.
        monkeypatch.setenv("RFE_CREATOR_BINDING_RFE_PROJECT", "RHOAIENG")
        monkeypatch.setenv("RFE_CREATOR_BINDING_RFE_ISSUE_TYPE", "Initiative")
        code, out = _main(monkeypatch, "RHOAIENG-12345", "--fetch-all", str(tmp_path / "a"))
        assert (code, out) == (1, "")
        err = capsys.readouterr().err
        assert err.startswith(
            "Error: rfe: effective binding ('jira', 'RHOAIENG', 'Initiative') (source: env) is "
            "the binding registered for type 'initiative'; a tracker binding must be owned by "
            "exactly one type"
        )
        assert err.rstrip("\n").endswith("pass --type initiative")
        assert "Traceback" not in err
        assert fake_jira["issue"] == []
        assert not (tmp_path / "a").exists()
        # With an explicit --type the D3 line (which names the override) precedes the refusal.
        code, _ = _main(
            monkeypatch, "RHOAIENG-12345", "--fetch-all", str(tmp_path / "b"), "--type", "rfe"
        )
        assert code == 1
        err = capsys.readouterr().err
        assert err.startswith(
            "TYPE RESOLVED: rfe (--type; binding override project=RHOAIENG "
            "issue_type=Initiative)\nError: rfe: effective binding"
        )
        assert fake_jira["issue"] == []

    def test_resolve_line_only_for_an_explicit_type(self, tmp_path, monkeypatch, fake_jira, capsys):
        # D3: the legacy default stays silent (the production invocation is byte-identical on
        # stderr too); an explicit --type is rung 1 and prints the line, never on stdout.
        code, out = _main(monkeypatch, "RHAIRFE-1595", "--fetch-all", str(tmp_path / "a"))
        assert code == 0 and out.startswith("OK: wrote ")
        assert capsys.readouterr().err == ""
        code, out = _main(
            monkeypatch, "RHAIRFE-1595", "--fetch-all", str(tmp_path / "b"), "--type", "rfe"
        )
        assert code == 0 and out.startswith("OK: wrote ")
        assert capsys.readouterr().err == "TYPE RESOLVED: rfe (--type)\n"
        assert "TYPE RESOLVED" not in out

    def test_cli_mismatch_exits_1_after_the_resolve_line(
        self, tmp_path, monkeypatch, fake_jira, capsys
    ):
        code, out = _main(
            monkeypatch, "RHAIRFE-1595", "--fetch-all", str(tmp_path), "--type", "initiative"
        )
        assert (code, out) == (1, "")
        assert capsys.readouterr().err == (
            "TYPE RESOLVED: initiative (--type)\n"
            "Error: RHAIRFE-1595 is (RHAIRFE, Feature Request) in Jira but the resolved type "
            "initiative binds (RHOAIENG, Initiative); nothing written - re-run with --type rfe\n"
        )
        assert _tree(tmp_path) == {}

    def test_type_default_is_none_so_the_ladder_decides(self):
        with open(SCRIPT, encoding="utf-8") as f:
            source = f.read()
        assert 'default="rfe"' not in source
        assert "type_registry.resolve(_TYPES, explicit_type=args.type, env=os.environ)" in source
        assert "if resolution.rung != type_registry.LEGACY_DEFAULT_RUNG:" in source
        assert fetch_issue.FETCH_ALL_FIELDS == FETCH_ALL_FIELDS


# ── a failed frontmatter step leaves nothing behind ──────────────────────────────────────────


class TestFrontmatterFailureWritesNothing:
    """When the verification passed but ``scripts/frontmatter.py set`` fails, the body-only task
    file just written is removed again before ``_fetch_all`` returns 1: the fetch barrier
    accepts a task file that merely exists, so leaving it would pass a frontmatter-less artifact
    downstream instead of firing the ``fetch_failed`` stub path. Nothing else was written yet —
    no original, no comments companion (the comments are not even requested)."""

    @pytest.mark.parametrize(
        "key, kwargs, task_rel, original_rel",
        [
            ("RHAIRFE-1595", {}, "rfe-tasks/RHAIRFE-1595.md", "rfe-originals/RHAIRFE-1595.md"),
            (
                "RHOAIENG-12345",
                {"type_name": "initiative"},
                "initiatives/RHOAIENG-12345.md",
                "initiative-originals/RHOAIENG-12345.md",
            ),
        ],
    )
    def test_task_file_is_removed_and_no_original_is_written(
        self, tmp_path, fake_jira, monkeypatch, capsys, key, kwargs, task_rel, original_rel
    ):
        calls = []

        def failing_run(cmd, *args, **run_kwargs):
            calls.append(list(cmd))
            # The task file exists at this point — the failure is the frontmatter step's own.
            assert os.path.exists(cmd[3]), cmd
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="schema says no\n")

        monkeypatch.setattr(fetch_issue.subprocess, "run", failing_run)
        artifacts = tmp_path / "artifacts"
        rc, out = _fetch_all(key, artifacts, **kwargs)
        assert (rc, out) == (1, "")
        assert capsys.readouterr().err == "Error setting frontmatter: schema says no\n"
        assert len(calls) == 1 and calls[0][1:3] == ["scripts/frontmatter.py", "set"]
        # The issue was fetched and verified (the write started) ...
        assert fake_jira["issue"] == [(key, FETCH_ALL_FIELDS)]
        # ... but no file survives: not the task file, no original, no comments companion.
        assert not (artifacts / task_rel).exists()
        assert not (artifacts / original_rel).exists()
        assert _tree(artifacts) == {}
        assert fake_jira["comments"] == []


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
        assert (data["type"], data["tracker_ref"]) == ("rfe", "RHAIRFE-1")
        assert list(data)[5:7] == ["type", "tracker_ref"]
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
        assert (data["type"], data["tracker_ref"]) == ("initiative", "RHOAIENG-1")
        assert body == "Init body.\n"
        assert original.read_text(encoding="utf-8") == "Init body.\n"

    # ── post-fetch verification (PR-3c D9): the emulator serves project and issuetype ──────

    def test_initiative_under_the_rfe_default_is_refused_then_fetched_with_the_hint(
        self, tmp_path, jira, env
    ):
        jira.create("RHOAIENG-2", "Serve models at the edge", "Init body.", issue_type="Initiative")
        artifacts = tmp_path / "artifacts"
        result = self._run(env, "RHOAIENG-2", "--fetch-all", str(artifacts))
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == (
            "Error: RHOAIENG-2 is (RHOAIENG, Initiative) in Jira but the resolved type rfe "
            f"binds {RFE_BINDS}; nothing written - re-run with --type initiative\n"
        )
        assert not artifacts.exists()
        # The hint is right: the same key under --type initiative is written (its project
        # matches the initiative binding), after the D3 line.
        result = self._run(env, "RHOAIENG-2", "--fetch-all", str(artifacts), "--type", "initiative")
        assert result.returncode == 0, result.stderr
        assert result.stderr == "TYPE RESOLVED: initiative (--type)\n"
        assert _tree(artifacts).keys() == {
            "initiatives/RHOAIENG-2.md",
            "initiative-originals/RHOAIENG-2.md",
        }
        data, _ = read_frontmatter_validated(
            str(artifacts / "initiatives" / "RHOAIENG-2.md"), "initiative-task"
        )
        assert (data["type"], data["tracker_ref"]) == ("initiative", "RHOAIENG-2")

    def test_feature_request_under_type_initiative_is_refused_and_the_hint_names_rfe(
        self, tmp_path, jira, env
    ):
        jira.create("RHAIRFE-3", "Export models", "Body text.")
        artifacts = tmp_path / "artifacts"
        result = self._run(env, "RHAIRFE-3", "--fetch-all", str(artifacts), "--type", "initiative")
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == (
            "TYPE RESOLVED: initiative (--type)\n"
            "Error: RHAIRFE-3 is (RHAIRFE, Feature Request) in Jira but the resolved type "
            "initiative binds (RHOAIENG, Initiative); nothing written - re-run with --type rfe\n"
        )
        assert not artifacts.exists()

    def test_pair_owned_by_no_type_is_refused_without_a_hint(self, tmp_path, jira, env):
        jira.create("RHAIRFE-4", "Not a feature request", "Body text.", issue_type="Bug")
        artifacts = tmp_path / "artifacts"
        result = self._run(env, "RHAIRFE-4", "--fetch-all", str(artifacts))
        assert result.returncode == 1
        assert result.stderr == (
            "Error: RHAIRFE-4 is (RHAIRFE, Bug) in Jira but the resolved type rfe binds "
            f"{RFE_BINDS}; nothing written\n"
        )
        assert not artifacts.exists()

    def test_foreign_project_is_refused_unless_the_binding_is_overridden(self, tmp_path, jira, env):
        # A Feature Request in another project (the emulator creates the project from the key).
        jira.create("KONFLUX-1", "Export models", "Body text.")
        artifacts = tmp_path / "artifacts"
        result = self._run(env, "KONFLUX-1", "--fetch-all", str(artifacts))
        assert result.returncode == 1
        assert result.stderr == (
            "Error: KONFLUX-1 is (KONFLUX, Feature Request) in Jira but the resolved type rfe "
            f"binds {RFE_BINDS}; nothing written\n"
        )
        assert not artifacts.exists()
        # The verification compares with the EFFECTIVE binding: with rfe bound to KONFLUX the
        # same issue passes it and the write starts. It then stops at frontmatter.py, whose id
        # grammar is still the descriptor's (the overridden-project artifact suite is PR-3c-iii;
        # flip this tail to returncode 0 and a written task there).
        result = self._run(
            {**env, "RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"},
            "KONFLUX-1",
            "--fetch-all",
            str(artifacts),
        )
        assert "Error: KONFLUX-1 is" not in result.stderr
        assert "cannot verify" not in result.stderr
        assert result.returncode == 1 and "Error setting frontmatter" in result.stderr
        # The failed write leaves no body-only task file (and no original) for the fetch
        # barrier to accept.
        assert not (artifacts / "rfe-tasks" / "KONFLUX-1.md").exists()
        assert not (artifacts / "rfe-originals" / "KONFLUX-1.md").exists()

    def test_malformed_override_for_the_other_type_is_one_line_never_a_traceback(
        self, tmp_path, jira, env
    ):
        # Through the CLI the ownership check (§3.2.1 g) computes every registered type's
        # effective binding before the fetch, so a malformed RFE_CREATOR_BINDING_* variable
        # for ANY type — not only the resolved one — is refused up front with one line naming
        # the variable, on the mismatch path and the match path alike: never a traceback, never
        # a write. (An in-process caller of _fetch_all that bypasses main gets the plain
        # post-fetch refusal without the hint: TestPostFetchVerification.)
        malformed = {**env, "RFE_CREATOR_BINDING_INITIATIVE_PROJECT": "lower"}
        line = (
            "Error: RFE_CREATOR_BINDING_INITIATIVE_PROJECT='lower': expected an upper-case "
            "tracker project key\n"
        )
        jira.create("RHOAIENG-2", "Serve models at the edge", "Init body.", issue_type="Initiative")
        jira.create("RHAIRFE-3", "Export models", "Body text.")
        artifacts = tmp_path / "artifacts"
        for key in ("RHOAIENG-2", "RHAIRFE-3"):
            result = self._run(malformed, key, "--fetch-all", str(artifacts))
            assert (result.returncode, result.stdout, result.stderr) == (1, "", line), key
            assert not artifacts.exists()
        # Without the typo both keys behave as the tests above pin (match / refusal).
        assert self._run(env, "RHAIRFE-3", "--fetch-all", str(artifacts)).returncode == 0

    def test_rfe_bound_to_the_initiative_pair_is_refused_as_an_ownership_violation(
        self, tmp_path, jira, env
    ):
        # §3.2.1 g through the CLI: the override is refused before the fetch, so an Initiative
        # can never be written into the rfe layout by re-binding rfe to its pair.
        jira.create("RHOAIENG-2", "Serve models at the edge", "Init body.", issue_type="Initiative")
        artifacts = tmp_path / "artifacts"
        result = self._run(
            {
                **env,
                "RFE_CREATOR_BINDING_RFE_PROJECT": "RHOAIENG",
                "RFE_CREATOR_BINDING_RFE_ISSUE_TYPE": "Initiative",
            },
            "RHOAIENG-2",
            "--fetch-all",
            str(artifacts),
        )
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr.startswith(
            "Error: rfe: effective binding ('jira', 'RHOAIENG', 'Initiative') (source: env) is "
            "the binding registered for type 'initiative'; a tracker binding must be owned by "
            "exactly one type"
        )
        assert result.stderr.count("\n") == 1 and "Traceback" not in result.stderr
        assert not artifacts.exists()

    def test_overridden_issue_type_is_accepted_end_to_end(self, tmp_path, jira, env):
        jira.create("RHAIRFE-5", "An epic-shaped request", "Epic body.", issue_type="Epic")
        artifacts = tmp_path / "artifacts"
        result = self._run(env, "RHAIRFE-5", "--fetch-all", str(artifacts))
        assert result.returncode == 1
        assert result.stderr == (
            "Error: RHAIRFE-5 is (RHAIRFE, Epic) in Jira but the resolved type rfe binds "
            f"{RFE_BINDS}; nothing written\n"
        )
        assert not artifacts.exists()
        result = self._run(
            {**env, "RFE_CREATOR_BINDING_RFE_ISSUE_TYPE": "Epic"},
            "RHAIRFE-5",
            "--fetch-all",
            str(artifacts),
        )
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""  # legacy default rung: silent, override or not (D3)
        task = artifacts / "rfe-tasks" / "RHAIRFE-5.md"
        assert result.stdout.startswith(f"OK: wrote {task}, ")
        data, body = read_frontmatter_validated(str(task), "rfe-task")
        assert (data["rfe_id"], data["type"], data["tracker_ref"]) == (
            "RHAIRFE-5",
            "rfe",
            "RHAIRFE-5",
        )
        assert body == "Epic body.\n"
