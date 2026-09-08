#!/usr/bin/env python3
"""Pin the PR-1 "transparent" edits to existing files.

PR-1 (work-item-types-unified.md §10 item 1) adds the type registry without
changing any production binding, label, prompt, artifact byte or skill
behaviour. The edits below touch files that production DOES read, so each one
carries a test proving the observable output is unchanged:

  1. artifact_utils.SCHEMAS gains optional `type`/`tracker_ref` WITHOUT a
     default — frontmatter written by every path stays byte-identical (Q4).
  2. `issuetype` joins the fetch field lists — request-only widening; the
     artifacts fetch_issue writes and the snapshot entries snapshot_fetch
     persists are unchanged, so no snapshot item is re-marked CHANGED (Q23).
  3. .claude/settings.json drops dead allowlist entries and adds the three
     registry entries in relative form only (Q20 of the checklist).
  4. rfe-creator.update-deps removes everything bootstrap-assess-rfe.sh can
     install, and .gitignore hides the same set (PR1-22).
  5. make lint / lint.yml run the two descriptor lints before pytest, and the
     three agent-facing docs point at types/README.md and the scripts.

The golden byte strings were captured on main c1df503 before the schema edit;
they are literals on purpose so a later "harmless" default would fail here.
"""

import io
import json
import os
import re
import subprocess
import sys
from contextlib import redirect_stdout

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import fetch_issue  # noqa: E402
import snapshot_fetch  # noqa: E402
from artifact_utils import (  # noqa: E402
    SCHEMAS,
    apply_defaults,
    get_schema_yaml,
    update_frontmatter,
    validate,
    write_frontmatter,
)
from pipeline_state import PIPELINE_TYPES  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _read(rel):
    with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ── 1. Base-schema additions ──────────────────────────────────────────────────

NEW_FIELDS = ("type", "tracker_ref")

BODY = "## Summary\n\nBody text with a horizontal rule below.\n\n---\n\nMore body.\n"

# (schema, artifact path under artifacts/, initial data, update applied second)
SAMPLES = {
    "rfe-task": (
        "rfe-tasks/RHAIRFE-1595.md",
        {
            "rfe_id": "RHAIRFE-1595",
            "title": "Add model registry export to S3-compatible storage",
            "priority": "Major",
            "status": "Ready",
            "original_labels": ["rfe-creator-autofix-rubric-pass"],
        },
        {"status": "Submitted"},
    ),
    "rfe-review": (
        "rfe-reviews/RHAIRFE-1595-review.md",
        {
            "rfe_id": "RHAIRFE-1595",
            "score": 8,
            "pass": True,
            "recommendation": "submit",
            "feasibility": "feasible",
            "auto_revised": True,
            "needs_attention": False,
            "scores": {"what": 2, "why": 2, "open_to_how": 2, "not_a_task": 2, "right_sized": 0},
            "before_score": 5,
        },
        {"needs_attention": False},
    ),
    "initiative-task": (
        "initiatives/RHOAIENG-12345.md",
        {
            "initiative_id": "RHOAIENG-12345",
            "title": "Unify model serving telemetry across RHOAI components",
            "priority": "Normal",
            "status": "Ready",
            "parent_key": "RHAISTRAT-42",
        },
        {"status": "Submitted"},
    ),
    "initiative-review": (
        "initiative-reviews/RHOAIENG-12345-review.md",
        {
            "initiative_id": "RHOAIENG-12345",
            "score": 7,
            "pass": True,
            "recommendation": "submit",
            "feasibility": "feasible",
            "auto_revised": False,
            "needs_attention": True,
            "needs_attention_reason": "Alignment weak: parent outcome scope differs",
            "scores": {"what": 2, "why": 1, "scope": 1, "open_to_how": 2, "right_sized": 1},
            "alignment": "weak",
        },
        {"needs_attention": True},
    ),
}

# Captured on main c1df503 (before `type`/`tracker_ref` existed) by running
# write_frontmatter + update_frontmatter over SAMPLES with BODY.
GOLDEN = {
    "rfe-task": (
        b"---\nrfe_id: RHAIRFE-1595\n"
        b"title: Add model registry export to S3-compatible storage\n"
        b"priority: Major\nstatus: Submitted\noriginal_labels:\n"
        b"- rfe-creator-autofix-rubric-pass\nlocal_id: null\nsize: null\nparent_key: null\n"
        b"---\n" + BODY.encode()
    ),
    "rfe-review": (
        b"---\nrfe_id: RHAIRFE-1595\nscore: 8\npass: true\nrecommendation: submit\n"
        b"feasibility: feasible\nauto_revised: true\nneeds_attention: false\nscores:\n"
        b"  what: 2\n  why: 2\n  open_to_how: 2\n  not_a_task: 2\n  right_sized: 0\n"
        b"before_score: 5\nlocal_id: null\nerror: null\nneeds_attention_reason: null\n"
        b"before_scores: null\n---\n" + BODY.encode()
    ),
    "initiative-task": (
        b"---\ninitiative_id: RHOAIENG-12345\n"
        b"title: Unify model serving telemetry across RHOAI components\n"
        b"priority: Normal\nstatus: Submitted\nparent_key: RHAISTRAT-42\nlocal_id: null\n"
        b"original_labels: null\n---\n" + BODY.encode()
    ),
    "initiative-review": (
        b"---\ninitiative_id: RHOAIENG-12345\nscore: 7\npass: true\nrecommendation: submit\n"
        b"feasibility: feasible\nauto_revised: false\nneeds_attention: true\n"
        b"needs_attention_reason: 'Alignment weak: parent outcome scope differs'\nscores:\n"
        b"  what: 2\n  why: 1\n  scope: 1\n  open_to_how: 2\n  right_sized: 1\n"
        b"alignment: weak\nlocal_id: null\nerror: null\nbefore_score: null\n"
        b"before_scores: null\n---\n" + BODY.encode()
    ),
}

_NEW_FIELD_LINE = re.compile(rb"^(type|tracker_ref):", re.MULTILINE)


def _materialize(tmp_path, schema):
    rel, data, update = SAMPLES[schema]
    path = tmp_path / "artifacts" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(BODY, encoding="utf-8")
    write_frontmatter(str(path), dict(data), schema)
    update_frontmatter(str(path), dict(update), schema)
    return path


class TestBaseSchemaAdditions:
    @pytest.mark.parametrize("schema", sorted(SCHEMAS))
    def test_declared_optional_string_without_default(self, schema):
        for field in NEW_FIELDS:
            spec = SCHEMAS[schema][field]
            assert spec["type"] == "string"
            assert spec.get("required", False) is False
            # The load-bearing property: apply_defaults keys on the presence of
            # `default`, so its absence is what keeps artifacts byte-stable.
            assert "default" not in spec, f"{schema}.{field} must not carry a default (Q4)"

    @pytest.mark.parametrize("schema", sorted(SCHEMAS))
    def test_apply_defaults_never_materializes_them(self, schema):
        data = {}
        apply_defaults(data, schema)
        assert "local_id" in data  # control: defaults ARE applied for fields that have one
        for field in NEW_FIELDS:
            assert field not in data

    @pytest.mark.parametrize("schema", sorted(SCHEMAS))
    def test_accepted_when_set_and_typed(self, schema):
        _, data, _ = SAMPLES[schema]
        record = dict(data, type=schema.split("-")[0], tracker_ref="RHAIRFE-1595")
        apply_defaults(record, schema)
        assert validate(record, schema) == []
        bad = dict(data, type=5, tracker_ref=["x"])
        apply_defaults(bad, schema)
        errors = validate(bad, schema)
        assert any(e.startswith("type:") for e in errors)
        assert any(e.startswith("tracker_ref:") for e in errors)

    @pytest.mark.parametrize("schema", sorted(SCHEMAS))
    def test_schema_yaml_lists_them_as_optional(self, schema):
        shown = yaml.safe_load(get_schema_yaml(schema))
        for field in NEW_FIELDS:
            assert field in shown["optional"]
            assert field not in shown["required"]
            assert "default" not in shown["optional"][field]

    @pytest.mark.parametrize("schema", sorted(SCHEMAS))
    def test_api_writers_are_byte_stable(self, tmp_path, schema):
        """write_frontmatter + update_frontmatter output == pre-change golden bytes."""
        out = _materialize(tmp_path, schema).read_bytes()
        assert not _NEW_FIELD_LINE.search(out)
        assert out == GOLDEN[schema]

    @pytest.mark.parametrize("schema", sorted(SCHEMAS))
    def test_frontmatter_cli_set_is_byte_stable(self, tmp_path, schema):
        """`python3 scripts/frontmatter.py set` (the path every skill uses) is unchanged."""
        rel, data, update = SAMPLES[schema]
        path = tmp_path / "artifacts" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(BODY, encoding="utf-8")
        write_frontmatter(str(path), dict(data), schema)
        (field, value), *_ = update.items()
        result = subprocess.run(
            [sys.executable, "scripts/frontmatter.py", "set", str(path), f"{field}={value}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        out = path.read_bytes()
        assert not _NEW_FIELD_LINE.search(out)
        assert out == GOLDEN[schema]

    def test_no_production_writer_sets_them_yet(self):
        """PR-3 writes `type:`; until then no script or skill stamps either field."""
        hits = []
        for root in ("scripts", ".claude/skills"):
            for dirpath, dirnames, filenames in os.walk(os.path.join(REPO_ROOT, root)):
                # Vendored assess-rfe skills are runtime-installed, not ours.
                dirnames[:] = [
                    d
                    for d in dirnames
                    if d not in ("assess-rfe", "assess-initiative", "export-rubric")
                ]
                for name in filenames:
                    if not name.endswith((".py", ".md")):
                        continue
                    with open(os.path.join(dirpath, name), encoding="utf-8") as f:
                        for line in f:
                            if (
                                re.search(r"(?<![\w-])(type|tracker_ref)=", line)
                                and "frontmatter.py" in line
                            ):
                                hits.append(f"{root}/{name}: {line.strip()}")
        assert hits == []


# ── 2. issuetype in the fetch field lists ─────────────────────────────────────

ADF = {
    "type": "doc",
    "version": 1,
    "content": [
        {
            "type": "heading",
            "attrs": {"level": 2},
            "content": [{"type": "text", "text": "Problem"}],
        },
        {
            "type": "paragraph",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Data scientists cannot export registered models to S3-compatible storage."
                    ),
                }
            ],
        },
        {
            "type": "bulletList",
            "content": [
                {
                    "type": "listItem",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "Manual copies drift from the registry"}
                            ],
                        }
                    ],
                },
                {
                    "type": "listItem",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "No audit trail"}],
                        }
                    ],
                },
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
        # Present in the response from now on; must leave every artifact untouched.
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
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Acme Corp asked for this twice."}],
                }
            ],
        },
    },
    {
        "author": {"displayName": "John Roe"},
        "created": "2025-02-01T08:00:00.000+0000",
        "body": "Plain string body.",
    },
]

DESC_MD = (
    "## Problem\n\n"
    "Data scientists cannot export registered models to S3-compatible storage.\n\n"
    "- Manual copies drift from the registry\n- No audit trail\n"
)

# Captured on main c1df503 by running _fetch_all over ISSUE/COMMENTS.
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

DEFAULT_FIELDS = ["summary", "description", "priority", "labels", "status", "issuetype"]
# The explicit list the initiative fetch agent passes
# (.claude/skills/initiative-review/prompts/fetch-agent.md:10) — untouched by PR-1.
INITIATIVE_AGENT_FIELDS = "summary,description,priority,labels,status"


@pytest.fixture
def fake_jira(monkeypatch):
    captured = {}

    def fake_get_issue(server, user, token, key, fields=None):
        captured["fields"] = list(fields) if fields else fields
        return ISSUE

    monkeypatch.setattr(fetch_issue, "get_issue", fake_get_issue)
    monkeypatch.setattr(fetch_issue, "get_comments", lambda *a: COMMENTS)
    monkeypatch.setenv("JIRA_SERVER", "http://x")
    monkeypatch.setenv("JIRA_USER", "u")
    monkeypatch.setenv("JIRA_TOKEN", "t")
    return captured


class TestFetchIssueIssuetype:
    def test_fetch_all_requests_issuetype_and_writes_identical_artifacts(
        self, tmp_path, monkeypatch, fake_jira
    ):
        # _fetch_all shells out to scripts/frontmatter.py by relative path.
        monkeypatch.chdir(REPO_ROOT)
        artifacts = tmp_path / "artifacts"
        with redirect_stdout(io.StringIO()):
            rc = fetch_issue._fetch_all("RHAIRFE-1595", str(artifacts), "http://x", "u", "t")
        assert rc == 0
        assert fake_jira["fields"] == DEFAULT_FIELDS
        assert (artifacts / "rfe-tasks" / "RHAIRFE-1595.md").read_bytes() == GOLDEN_TASK
        assert (artifacts / "rfe-originals" / "RHAIRFE-1595.md").read_bytes() == GOLDEN_ORIGINAL
        assert (
            artifacts / "rfe-tasks" / "RHAIRFE-1595-comments.md"
        ).read_bytes() == GOLDEN_COMMENTS

    def test_default_fields_request_issuetype(self, monkeypatch, fake_jira):
        monkeypatch.setattr(sys, "argv", ["fetch_issue.py", "RHAIRFE-1595"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            fetch_issue.main()
        assert fake_jira["fields"] == DEFAULT_FIELDS
        out = json.loads(buf.getvalue())
        # Request-only widening: the default JSON echoes what was requested
        # (no caller omits --fields today — Q23).
        assert list(out["fields"]) == DEFAULT_FIELDS
        assert out["fields"]["issuetype"] == {"name": "Feature Request", "id": "10700"}

    def test_explicit_fields_output_unchanged(self, monkeypatch, fake_jira):
        """The initiative fetch agent's exact invocation still emits exactly its 5 fields."""
        monkeypatch.setattr(
            sys,
            "argv",
            ["fetch_issue.py", "RHAIRFE-1595", "--fields", INITIATIVE_AGENT_FIELDS, "--markdown"],
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            fetch_issue.main()
        assert fake_jira["fields"] == INITIATIVE_AGENT_FIELDS.split(",")
        out = json.loads(buf.getvalue())
        assert list(out["fields"]) == INITIATIVE_AGENT_FIELDS.split(",")
        assert "issuetype" not in out["fields"]
        assert out["fields"]["description"] == DESC_MD.rstrip("\n")

    def test_help_text_matches_default(self):
        src = _read("scripts/fetch_issue.py")
        assert '"summary,description,priority,labels,status,issuetype"' in src
        assert "labels,status,issuetype). " in src


class TestSnapshotFetchIssuetype:
    """docs/snapshot-incremental-fetch.md invariants 3 and 7: the change fingerprint
    is compute_content_hash(description) and nothing else, so widening the request
    must not re-mark a single processed item as CHANGED."""

    def _fetch(self, monkeypatch, with_issuetype):
        captured = {}

        def fake_paginated(server, user, token, jql, fields):
            captured["fields"] = fields
            fields_out = {"description": ADF, "labels": ["customer-request"]}
            if with_issuetype:
                fields_out["issuetype"] = {"name": "Feature Request"}
            yield {"key": "RHAIRFE-1595", "fields": fields_out}

        monkeypatch.setattr(snapshot_fetch, "_fetch_paginated", fake_paginated)
        issues = snapshot_fetch.fetch_all_issues("s", "u", "t", "project = RHAIRFE")
        return captured, issues

    def test_requests_issuetype(self, monkeypatch):
        captured, _ = self._fetch(monkeypatch, with_issuetype=True)
        assert captured["fields"] == "key,description,labels,issuetype"

    def test_entry_shape_and_hash_unchanged(self, monkeypatch):
        _, with_it = self._fetch(monkeypatch, with_issuetype=True)
        _, without_it = self._fetch(monkeypatch, with_issuetype=False)
        assert with_it == without_it
        assert set(with_it["RHAIRFE-1595"]) == {"content_hash", "labels"}
        assert with_it["RHAIRFE-1595"]["content_hash"] == snapshot_fetch.compute_content_hash(ADF)

    def test_previous_snapshot_is_not_remarked_changed(self, monkeypatch):
        _, current = self._fetch(monkeypatch, with_issuetype=True)
        previous = {
            "issues": {
                "RHAIRFE-1595": {
                    "hash": snapshot_fetch.compute_content_hash(ADF),
                    "processed": True,
                }
            }
        }
        changed, new = snapshot_fetch.diff_snapshots(current, previous)
        assert changed == []
        assert new == []


# ── 3. .claude/settings.json allowlist ────────────────────────────────────────

NEW_ALLOW = [
    "Bash(python3 scripts/type_registry.py *)",
    "Bash(python3 scripts/validate_types.py *)",
    "Bash(python3 scripts/generate_eval_config.py *)",
]
STALE_ALLOW = [
    # assess-rfe moved its scripts under skills/<skill>/scripts (assess-rfe#5);
    # the vendored SKILL.md files invoke them via ${CLAUDE_SKILL_DIR}/scripts/.
    "Bash(python3 .context/assess-rfe/scripts/check_progress.py *)",
    "Bash(python3 .context/assess-rfe/scripts/parse_results.py *)",
    "Bash(python3 .context/assess-rfe/scripts/summarize_run.py *)",
    "Bash(python3 .context/assess-rfe/scripts/fetch_single.py *)",
    "Bash(python3 .context/assess-rfe/scripts/export_rubric.py *)",
    # Never existed; batch_summary.py --type initiative is the real command.
    "Bash(python3 scripts/initiative_batch_summary.py *)",
]
# Allowlisted ahead of the script (design §10 item 4 ships it in PR-4).
FORWARD_DECLARED = {"scripts/generate_eval_config.py"}


class TestSettingsAllowlist:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.settings = json.loads(_read(".claude/settings.json"))
        self.allow = self.settings["permissions"]["allow"]

    def test_new_entries_present_once(self):
        for entry in NEW_ALLOW:
            assert self.allow.count(entry) == 1, entry

    def test_stale_entries_removed(self):
        for entry in STALE_ALLOW:
            assert entry not in self.allow, entry
        assert not any(".context/assess-rfe/scripts/" in e for e in self.allow)

    def test_relative_form_only(self):
        """AGENTS.md: the allowlist matches command text literally, so absolute
        forms are never allowlisted (they would also never match)."""
        for entry in self.allow:
            assert not re.match(r"Bash\((?:python3|bash) /", entry), entry

    def test_every_allowlisted_repo_script_exists(self):
        missing = []
        for entry in self.allow:
            m = re.match(r"Bash\((?:python3|bash) (scripts/[^ )]+)", entry)
            if m and m.group(1) not in FORWARD_DECLARED:
                if not os.path.exists(os.path.join(REPO_ROOT, m.group(1))):
                    missing.append(m.group(1))
        assert missing == []

    def test_no_duplicates(self):
        assert len(self.allow) == len(set(self.allow))

    def test_hooks_and_directories_untouched(self):
        assert self.settings["hooks"] == {
            "SessionStart": [
                {
                    "matcher": "compact",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 scripts/pipeline_state.py post-compact-hook",
                        }
                    ],
                }
            ]
        }
        assert self.settings["permissions"]["additionalDirectories"] == [
            "/tmp/rfe-assess",
            ".context/assess-rfe",
        ]


# ── 4. update-deps removal list and .gitignore ────────────────────────────────


def _bootstrap_installs():
    """Everything scripts/bootstrap-assess-rfe.sh can install, derived from the
    script itself plus PIPELINE_TYPES (rubric skill dirs and scorer agents)."""
    script = _read("scripts/bootstrap-assess-rfe.sh")
    # The copy loops are what make the list "everything": every skills/*/ dir
    # in the checkout and every agents/*.md file, not a fixed subset.
    assert 'for skill_dir in "$CONTEXT_DIR"/skills/*/' in script
    assert 'cp "$CONTEXT_DIR"/agents/*.md .claude/agents/' in script

    skills = set(re.findall(r"skills/([a-z0-9-]+)/scripts/", script))  # incl. export-rubric
    agents = set(re.findall(r'_AGENT="([a-z0-9-]+\.md)"', script))
    for cfg in PIPELINE_TYPES.values():
        m = re.match(r"\.context/assess-rfe/skills/([^/]+)/", cfg["rubric_path"])
        skills.add(m.group(1))
        agents.add(f"{cfg['scorer_type']}.md")
    assert {"assess-rfe", "assess-initiative", "export-rubric"} <= skills
    assert {"rfe-scorer.md", "initiative-scorer.md"} <= agents
    return {
        "context": ".context/assess-rfe",
        "skills": {f".claude/skills/{s}" for s in skills},
        "agents": {f".claude/agents/{a}" for a in agents},
    }


def _update_deps_rm_targets():
    skill = _read(".claude/skills/rfe-creator.update-deps/SKILL.md")
    block = re.search(r"### 1\. Update assess-rfe.*?```bash\n(.*?)```", skill, re.DOTALL).group(1)
    joined = block.replace("\\\n", " ")
    lines = [ln.strip() for ln in joined.splitlines() if ln.strip()]
    assert lines[-1] == "bash scripts/bootstrap-assess-rfe.sh", lines
    assert len(lines) == 2, "step 1 is exactly: one rm, then the bootstrap"
    tokens = lines[0].split()
    assert tokens[:2] == ["rm", "-rf"]
    return set(tokens[2:])


class TestUpdateDepsCoverage:
    def test_rm_list_equals_everything_bootstrap_installs(self):
        installs = _bootstrap_installs()
        expected = {installs["context"]} | installs["skills"] | installs["agents"]
        assert _update_deps_rm_targets() == expected

    def test_rest_of_skill_unchanged(self):
        skill = _read(".claude/skills/rfe-creator.update-deps/SKILL.md")
        assert (
            "rm -rf .context/architecture-context\nbash scripts/fetch-architecture-context.sh"
            in skill
        )
        assert "### 3. Report" in skill
        assert "user-invocable: true" in skill
        assert "disable-model-invocation: true" in skill

    def test_gitignore_hides_the_same_set(self):
        installs = _bootstrap_installs()
        entries = {
            ln.strip()
            for ln in _read(".gitignore").splitlines()
            if ln.strip() and not ln.startswith("#")
        }
        assert ".context/" in entries  # covers .context/assess-rfe
        for skill_dir in installs["skills"]:
            assert f"{skill_dir}/" in entries, skill_dir
        for agent in installs["agents"]:
            assert agent in entries, agent


# ── 5. Lint hooks and docs ────────────────────────────────────────────────────

LINT_STEPS = ["python3 scripts/validate_types.py", "python3 scripts/lint_prefix_predicates.py"]


class TestLintHooks:
    def test_makefile_runs_both_before_pytest(self):
        makefile = _read("Makefile")
        lint_body = makefile.split("\nlint:", 1)[1].split("\n.PHONY", 1)[0]
        positions = [lint_body.index(f"@{step}") for step in LINT_STEPS]
        assert positions == sorted(positions)
        assert positions[-1] < lint_body.index("$(MAKE) test")
        # Relative invocation only — the headless allowlist is literal.
        for step in LINT_STEPS:
            assert f"@{step}\n" in lint_body

    def test_workflow_runs_both_after_ruff_format_before_tests(self):
        wf = yaml.safe_load(_read(".github/workflows/lint.yml"))
        steps = wf["jobs"]["lint"]["steps"]
        runs = [s.get("run") for s in steps]
        i_fmt = runs.index("ruff format --check --diff .")
        i_val = runs.index(LINT_STEPS[0])
        i_pfx = runs.index(LINT_STEPS[1])
        i_test = runs.index('python3 -m pytest tests/ -v -k "not integration"')
        assert i_fmt < i_val < i_pfx < i_test
        # jsonschema (validate_types' only non-stdlib dependency) is installed
        # by the requirements-dev step that precedes ruff.
        assert any("requirements-dev.txt" in (r or "") for r in runs[:i_fmt])


class TestDocs:
    @pytest.mark.parametrize("doc", ["AGENTS.md", "CLAUDE.md", "README.md"])
    def test_points_at_types_readme_and_scripts(self, doc):
        text = _read(doc)
        assert "types/README.md" in text
        assert "python3 scripts/type_registry.py" in text
        assert "python3 scripts/validate_types.py" in text
        # No absolute-path invocation slipped into the docs agents copy from.
        assert not re.search(r"python3 /\S*scripts/(type_registry|validate_types)\.py", text)

    def test_claude_md_mirrors_agents_md(self):
        assert _read("CLAUDE.md") == _read("AGENTS.md")

    def test_linked_targets_exist(self):
        """The docs point at files other PR-1 owners ship; guard against dead links."""
        for rel in (
            "types/README.md",
            "docs/type-provider-guide.md",
            "types/_schema/type.schema.json",
            "scripts/type_registry.py",
            "scripts/validate_types.py",
            "scripts/lint_prefix_predicates.py",
            "tests/data/prefix_predicate_baseline.json",
        ):
            assert os.path.exists(os.path.join(REPO_ROOT, rel)), rel

    def test_readme_command_list_unchanged(self):
        """PR1-31: the README command lists are part of the byte-stable surface."""
        quick_start = _read("README.md").split("## Quick Start", 1)[1].split("```", 2)[1]
        commands = re.findall(r"^/[\w.-]+", quick_start, re.MULTILINE)
        assert commands == [
            "/rfe.create",
            "/rfe.review",
            "/rfe.split",
            "/rfe.submit",
            "/rfe.speedrun",
            "/rfe.auto-fix",
            "/rfe.review",
            "/rfe.split",
            "/rfe.speedrun",
            "/rfe.speedrun",
            "/rfe.speedrun",
            "/rfe.auto-fix",
            "/rfe.auto-fix",
            "/initiative-create",
            "/initiative-review",
            "/initiative-split",
            "/initiative-submit",
            "/initiative-speedrun",
            "/initiative-auto-fix",
            "/rfe-creator.update-deps",
        ]
