#!/usr/bin/env python3
"""Tests for scripts/validate_types.py — the descriptor contract enforcer (design §3.3).

Gate 1 (always): JSON Schema, repo-relative references exist, score_fields, the
verify_phase error stub, cross-type invariants on EFFECTIVE bindings (§3.2.1).
Gate 2 (--with-deps): rubric path and scorer agent inside the assess checkout.
Gate 3 (--verify --type T): gate 1 for one type, one-line diagnosis (unwired in PR-1).

Mutation tests copy the shipped descriptors into a tmp root and drive the Python
API (``validate_all``); repo-relative references still resolve against this checkout
via ``repo_root``. The CLI is exercised by ``python3 scripts/validate_types.py`` from
the repo root and by ``main()`` in-process where cwd matters (--assess-dir default).

Also here: the paper epic descriptor (Q18: schema-valid, never registered) and the
design's "extra types" CI test (a drop-in third type discovered by the registry AND
passing every gate-1 check).
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import type_registry  # noqa: E402
import validate_types  # noqa: E402
import verify_phase  # noqa: E402
from validate_types import (  # noqa: E402
    Finding,
    MissingDependencyError,
    Report,
    build_error_stub,
    validate_all,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TYPES_ROOT = REPO_ROOT / "types"
SCHEMA_PATH = TYPES_ROOT / "_schema" / "type.schema.json"
SCRIPT = "scripts/validate_types.py"
EPIC_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "types" / "epic" / "type.yaml"
FIXTURES_TYPES_ROOT = EPIC_FIXTURE.parent.parent
STRAT_INPUTS_FIXTURE = FIXTURES_TYPES_ROOT / "strategy-inputs.yaml"
# A live repo file to stand in for an embedded (repo: self) rubric in D3 tests.
SELF_RUBRIC_PATH = ".claude/skills/rfe.review/prompts/review-agent.md"
SHIPPED = ("rfe", "initiative")
OK_LINE = "OK: 2 type(s) valid: rfe, initiative\n"

RFE_RUBRIC = "skills/assess-rfe/scripts/agent_prompt.md"
INITIATIVE_RUBRIC = "skills/assess-initiative/scripts/agent_prompt.md"


# ── helpers ──────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_binding_env(monkeypatch):
    """In-process main() reads os.environ: keep the developer's seams out."""
    for key in list(os.environ):
        if key.startswith("RFE_CREATOR_"):
            monkeypatch.delenv(key)


def _read_yaml(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    return path


def _copy_types(dest, names=SHIPPED, with_schema=True):
    dest.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copytree(TYPES_ROOT / name, dest / name)
    if with_schema:
        shutil.copytree(TYPES_ROOT / "_schema", dest / "_schema")
    return dest


def _mutate(root, name, fn):
    """Load <root>/<name>/type.yaml, apply fn(data) in place, write it back."""
    path = root / name / "type.yaml"
    data = _read_yaml(path)
    fn(data)
    _write_yaml(path, data)
    return path


def _validate(root, **kw):
    kw.setdefault("extra_roots", [])
    kw.setdefault("env", {})
    kw.setdefault("repo_root", REPO_ROOT)
    return validate_all(root=root, **kw)


def _messages(report, type_=None):
    return [f.message for f in report.findings if type_ is None or f.type == type_]


def _find(report, stem, type_=None):
    """The findings whose message contains ``stem`` (optionally under one type)."""
    return [f for f in report.findings if stem in f.message and (type_ is None or f.type == type_)]


def _assert_finding(report, stem, type_=None):
    hits = _find(report, stem, type_)
    assert hits, (
        f"no finding containing {stem!r}"
        + (f" under type {type_!r}" if type_ else "")
        + f"; got: {report.lines()}"
    )
    assert not report.ok
    return hits


def _clean_env(**extra):
    """Developer seams and the headless/CI markers (which gate RFE_CREATOR_EXTRA_TYPES)
    stay out of subprocess tests."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("RFE_CREATOR_") and k not in type_registry.HEADLESS_MARKER_VARS
    }
    env.update(extra)
    return env


def _cli(*args, env=None):
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_clean_env(**(env or {})),
    )


@pytest.fixture
def types_copy(tmp_path):
    return _copy_types(tmp_path / "types")


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# stub\n")


def _fake_assess_checkout(base, rfe=True, initiative_rubric=True, initiative_agent=True):
    """A .context/assess-rfe-shaped tree (pattern of tests/test_bootstrap_assess.py)."""
    ctx = base / ".context" / "assess-rfe"
    ctx.mkdir(parents=True, exist_ok=True)
    if rfe:
        _touch(ctx / RFE_RUBRIC)
        _touch(ctx / "agents" / "rfe-scorer.md")
    if initiative_rubric:
        _touch(ctx / INITIATIVE_RUBRIC)
    if initiative_agent:
        _touch(ctx / "agents" / "initiative-scorer.md")
    return ctx


def _third_type(root, name="docs"):
    """A complete drop-in descriptor derived from rfe with a disjoint identity.

    Every cross-type invariant is satisfied: its own (project, issue_type), local
    prefix/pattern/id field, dirs, poll/state/report prefixes and snapshot prefix.
    Prompt/eval references stay rfe's live files, which exist in this checkout.
    """
    data = _read_yaml(TYPES_ROOT / "rfe" / "type.yaml")
    data["type"] = name
    data["display"] = {"entity": "Doc", "entity_plural": "Docs"}
    data["identity"] = {
        "tracker": "jira",
        "jira": {
            "project": "RHAIDOCS",
            "issue_type": "Documentation Request",
            "key_prefixes": ["RHAIDOCS-"],
        },
        "local_prefix": "DOC-",
        "local_id_pattern": r"^DOC-\d+$",
        "id_field": "doc_id",
    }
    data["dirs"] = {
        "tasks": "artifacts/doc-tasks",
        "originals": "artifacts/doc-originals",
        "reviews": "artifacts/doc-reviews",
    }
    conv = data["conventions"]
    conv["type_label"] = "Doc"
    conv["label_prefix"] = "docs-creator"
    conv["labels"] = {
        key: value.replace("rfe-creator", "docs-creator")
        if isinstance(value, str)
        else {k: v.replace("rfe-creator", "docs-creator") for k, v in value.items()}
        for key, value in conv["labels"].items()
    }
    conv["comment_prefix"] = "[Docs Creator]"
    conv["query_default"] = 'project = RHAIDOCS AND issuetype = "Documentation Request"'
    conv["parent_key_patterns"] = [r"DOC-\d+", r"RHAIDOCS-\d+"]
    data["pipeline"]["poll_prefix"] = "docs-"
    data["pipeline"]["state_prefix"] = "docs-"
    data["pipeline"]["rubric"]["export"] = "artifacts/docs-rubric.md"
    data["snapshot"] = {"prefix": "docs-snapshot-", "report_prefix": "docs-run-"}
    data["reporting"]["item_key"] = "per_doc"
    data["eval"]["mlflow_experiment"] = "docs-speedrun-eval"
    _write_yaml(root / name / "type.yaml", data)
    return root


# ── shipped descriptors ──────────────────────────────────────────────────────────


class TestShipped:
    def test_python_api_ok(self):
        report = _validate(TYPES_ROOT)
        assert report.ok, report.lines()
        assert report.types == ["rfe", "initiative"]
        assert report.findings == []

    def test_cli_exit_0_and_ok_line(self):
        result = _cli()
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == OK_LINE
        assert result.stderr == ""

    def test_cli_json_shape_ok(self):
        result = _cli("--json")
        assert result.returncode == 0
        assert json.loads(result.stdout) == {
            "ok": True,
            "types": ["rfe", "initiative"],
            "errors": [],
        }

    def test_default_root_is_the_shipped_types_dir(self):
        assert validate_all(extra_roots=[], env={}).ok

    def test_report_to_dict_and_lines_shape(self):
        report = Report(
            ["rfe"], [Finding("rfe", "boom"), Finding("*", "cross", 1, frozenset({"rfe"}))]
        )
        assert not report.ok
        assert report.lines() == ["ERROR rfe: boom", "ERROR *: cross"]
        assert report.to_dict() == {
            "ok": False,
            "types": ["rfe"],
            "errors": [{"type": "rfe", "message": "boom"}, {"type": "*", "message": "cross"}],
        }

    def test_cli_json_on_failure_lists_errors(self, types_copy):
        _mutate(types_copy, "initiative", lambda d: d["pipeline"].__setitem__("poll_prefix", ""))
        result = _cli("--root", str(types_copy), "--json")
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert payload["types"] == ["rfe", "initiative"]
        assert payload["errors"] == [
            {
                "type": "*",
                "message": "pipeline.poll_prefix is empty for type 'initiative' (an empty "
                "prefix is grandfathered for type 'rfe' only)",
            }
        ]

    def test_cli_text_on_failure_prints_error_lines_and_summary(self, types_copy):
        _mutate(types_copy, "initiative", lambda d: d["pipeline"].__setitem__("poll_prefix", ""))
        result = _cli("--root", str(types_copy))
        assert result.returncode == 1
        assert result.stdout.startswith(
            "ERROR *: pipeline.poll_prefix is empty for type 'initiative'"
        )
        assert "validate_types: 1 finding(s) across 2 type(s)" in result.stderr


# ── gate 1: JSON Schema ──────────────────────────────────────────────────────────


class TestSchemaGate:
    def test_shipped_schema_is_a_valid_draft_2020_12_schema(self):
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            schema = json.load(fh)
        jsonschema.Draft202012Validator.check_schema(schema)
        assert schema["$schema"].startswith("https://json-schema.org/draft/2020-12")

    def test_unknown_top_level_key(self, types_copy):
        _mutate(types_copy, "rfe", lambda d: d.__setitem__("bogus", 1))
        report = _validate(types_copy)
        hits = _assert_finding(report, "schema: $: Additional properties are not allowed", "rfe")
        assert "'bogus' was unexpected" in hits[0].message

    def test_missing_required_block(self, types_copy):
        _mutate(types_copy, "initiative", lambda d: d.pop("reporting"))
        _assert_finding(_validate(types_copy), "'reporting' is a required property", "initiative")

    def test_schema_version_is_pinned_to_1(self, types_copy):
        _mutate(types_copy, "rfe", lambda d: d.__setitem__("schema_version", 2))
        _assert_finding(_validate(types_copy), "schema: $.schema_version: 1 was expected", "rfe")

    def test_tmp_root_without_schema_falls_back_to_the_shipped_one(self, tmp_path):
        root = _copy_types(tmp_path / "types", with_schema=False)
        assert _validate(root).ok
        _mutate(root, "rfe", lambda d: d.__setitem__("schema_version", 2))
        assert validate_types.find_schema_file(root) == SCHEMA_PATH
        _assert_finding(_validate(root), "schema_version: 1 was expected", "rfe")

    def test_root_schema_takes_precedence(self, types_copy):
        assert (
            validate_types.find_schema_file(types_copy)
            == types_copy / "_schema" / "type.schema.json"
        )

    def test_invalid_schema_file_is_a_registry_level_finding(self, types_copy):
        (types_copy / "_schema" / "type.schema.json").write_text(json.dumps({"type": "nope"}))
        report = _validate(types_copy)
        hits = _assert_finding(report, "is not a valid JSON Schema", "*")
        assert len(hits) == 1

    def test_both_binding_blocks_violate_one_of(self, types_copy):
        def both(d):
            d["identity"]["github"] = {"repo": "acme/x", "kind": "issue", "alias_prefix": "GH-"}

        _mutate(types_copy, "rfe", both)
        hits = _assert_finding(_validate(types_copy), "schema: $.identity: violates oneOf", "rfe")
        assert "closest sub-error" in hits[0].message

    def test_type_field_mismatch_is_a_load_failure(self, tmp_path):
        root = tmp_path / "types"
        shutil.copytree(TYPES_ROOT / "rfe", root / "feature")
        report = _validate(root)
        assert report.types == []
        hits = _assert_finding(report, "cannot load type registry", "*")
        assert "does not match its directory name 'feature'" in hits[0].message

    def test_duplicate_type_across_roots_is_a_load_failure(self, tmp_path):
        extra = _copy_types(tmp_path / "extra", names=("rfe",), with_schema=False)
        report = _validate(TYPES_ROOT, extra_roots=[extra])
        _assert_finding(report, "cannot load type registry: duplicate type 'rfe'", "*")

    def test_root_without_descriptors_is_a_finding_not_a_vacuous_pass(self, tmp_path):
        """make lint / lint.yml depend on the exit code: a mis-set --root or a checkout
        without types/ must fail, never print 'OK: 0 type(s) valid:'."""
        root = tmp_path / "types"
        shutil.copytree(TYPES_ROOT / "_schema", root / "_schema")
        report = _validate(root)
        assert report.ok is False
        assert report.types == []
        assert report.lines() == [
            f"ERROR *: no type descriptors found under {root} (expected <root>/<name>/type.yaml)"
        ]
        result = _cli("--root", str(root))
        assert result.returncode == 1
        assert result.stdout.startswith("ERROR *: no type descriptors found under")
        empty = tmp_path / "empty"
        empty.mkdir()
        assert _cli("--root", str(empty)).returncode == 1

    def test_empty_key_prefixes_is_rejected(self, types_copy):
        """PR1-03: the write prefix is key_prefixes[0]; an empty list has none."""
        _mutate(types_copy, "rfe", lambda d: d["identity"]["jira"].__setitem__("key_prefixes", []))
        _assert_finding(
            _validate(types_copy),
            "schema: $.identity.jira.key_prefixes: [] should be non-empty",
            "rfe",
        )

    def test_missing_local_id_pattern_is_rejected(self, types_copy):
        """PR1-03: local_id_pattern is the authoritative local-id grammar, never optional."""
        _mutate(types_copy, "rfe", lambda d: d["identity"].pop("local_id_pattern"))
        _assert_finding(
            _validate(types_copy),
            "schema: $.identity: 'local_id_pattern' is a required property",
            "rfe",
        )

    def test_github_binding_sample_validates_and_passes_gate_1(self, tmp_path):
        """PR1-12: the design §8.6 GitHub-backed 'docs request' sample (completed with the
        two identity fields §8.6 elides) is schema-valid and passes every gate-1 rule."""
        extra = _third_type(tmp_path / "extra")

        def to_github(d):
            d["identity"] = {
                "tracker": "github",
                "github": {
                    "repo": "opendatahub-io/model-registry",
                    "kind": {"type_label": "type: docs-request"},
                    "alias_prefix": "MRDOC-",
                    "labels_defined": [{"name": "docs-request-ignore", "color": "ededed"}],
                    "state_map": {
                        "approved": "docs-request-approved",
                        "close_superseded": "not_planned",
                    },
                    "priority": "omit",
                },
                "local_prefix": "DOC-",
                "local_id_pattern": r"^DOC-\d+$",
                "id_field": "doc_id",
            }
            d["conventions"]["query_default"] = (
                'repo:opendatahub-io/model-registry is:issue label:"type: docs-request"'
            )

        _mutate(extra, "docs", to_github)
        report = _validate(TYPES_ROOT, extra_roots=[extra])
        assert report.ok, report.lines()
        binding = (
            type_registry.load(root=TYPES_ROOT, extra_roots=[extra], env={}).get("docs").binding()
        )
        assert binding["tracker"] == "github"
        assert binding["key_prefixes"] == ["MRDOC-"]  # alias_prefix is the single read/write prefix
        assert binding["priority"] == "omit"

    def test_context_exists_dimension_and_extra_scores_are_accepted(self, types_copy):
        """PR1-18: PR #100's JTBD dimension shape — condition {context_exists}, setup,
        skip_stub — and schema.review.extra_scores are schema-valid and pass gate 1."""

        def add_jtbd(d):
            prompt = d["pipeline"]["dimensions"][0]["prompt"]  # a live file: gate 1 checks it
            d["pipeline"]["dimensions"].append(
                {
                    "name": "jtbd",
                    "prompt": prompt,
                    "blocking": False,
                    "condition": {"context_exists": ".context/jtbd-registry/index.yaml"},
                    "setup": "scripts/fetch-jtbd-registry.sh",
                    "skip_stub": {"result": "skipped", "reason": "no JTBD registry"},
                }
            )
            d["schema"]["review"]["extra_scores"] = ["jtbd_alignment"]

        _mutate(types_copy, "rfe", add_jtbd)
        report = _validate(types_copy)
        assert report.ok, report.lines()

    def test_missing_root_is_a_load_failure(self, tmp_path):
        report = _validate(tmp_path / "nope")
        _assert_finding(report, "cannot load type registry: type root not found", "*")


# ── gate 1: per-type checks ──────────────────────────────────────────────────────


class TestPerTypeGate:
    def test_missing_prompt_file(self, types_copy):
        _mutate(
            types_copy,
            "rfe",
            lambda d: d["pipeline"]["prompts"].__setitem__(
                "review_rules", ".claude/skills/nope.md"
            ),
        )
        hits = _assert_finding(
            _validate(types_copy), "pipeline.prompts.review_rules: file not found:", "rfe"
        )
        assert ".claude/skills/nope.md" in hits[0].message
        assert f"(relative to {REPO_ROOT})" in hits[0].message

    def test_missing_dimension_prompt(self, types_copy):
        _mutate(
            types_copy,
            "initiative",
            lambda d: d["pipeline"]["dimensions"][1].__setitem__("prompt", "no/such/SKILL.md"),
        )
        _assert_finding(
            _validate(types_copy), "pipeline.dimensions[1].prompt: file not found:", "initiative"
        )

    def test_missing_eval_dataset_and_config(self, types_copy):
        def bogus(d):
            d["eval"]["dataset"] = "eval/nope/cases"
            d["eval"]["config"] = "eval-nope.yaml"

        _mutate(types_copy, "rfe", bogus)
        report = _validate(types_copy)
        _assert_finding(report, "eval.dataset: path not found: eval/nope/cases", "rfe")
        _assert_finding(report, "eval.config: file not found: eval-nope.yaml", "rfe")

    def test_references_resolve_against_repo_root_not_cwd(self, types_copy, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # no .claude/, eval.yaml, ... here
        assert _validate(types_copy).ok
        assert _validate(types_copy, repo_root=REPO_ROOT).ok
        assert not _validate(types_copy, repo_root=tmp_path).ok

    def test_rubric_export_is_not_required_to_exist(self, types_copy):
        _mutate(
            types_copy,
            "rfe",
            lambda d: d["pipeline"]["rubric"].__setitem__("export", "artifacts/never-written.md"),
        )
        assert _validate(types_copy).ok

    def test_rubric_path_is_gate_2_not_gate_1(self, types_copy):
        _mutate(
            types_copy,
            "rfe",
            lambda d: d["pipeline"]["rubric"].__setitem__("path", "skills/nope/agent_prompt.md"),
        )
        assert _validate(types_copy).ok  # Q24: checked only under --with-deps

    def test_empty_score_fields(self, types_copy):
        _mutate(
            types_copy,
            "initiative",
            lambda d: d["schema"]["review"].__setitem__("score_fields", []),
        )
        report = _validate(types_copy)
        _assert_finding(report, "schema.review.score_fields must be a non-empty list", "initiative")
        _assert_finding(report, "schema: $.schema.review.score_fields: [] should be non-empty")

    @pytest.mark.parametrize(
        "ref, shown",
        [("e27d7", "'e27d7'"), ("E27D7AC", "'E27D7AC'"), (1234567, "1234567"), (None, "None")],
    )
    def test_bad_rubric_ref(self, types_copy, ref, shown):
        _mutate(types_copy, "rfe", lambda d: d["pipeline"]["rubric"].__setitem__("ref", ref))
        hits = _assert_finding(
            _validate(types_copy),
            "pipeline.rubric.ref must be a 7-40 character lowercase hex commit SHA, got " + shown,
            "rfe",
        )
        assert len(hits) == 1

    def test_full_length_sha_is_accepted(self, types_copy):
        _mutate(types_copy, "rfe", lambda d: d["pipeline"]["rubric"].__setitem__("ref", "a" * 40))
        assert _validate(types_copy).ok

    def test_rubric_ref_regex_is_q9(self):
        assert validate_types.RUBRIC_REF_RE.pattern == r"^[0-9a-f]{7,40}$"
        assert validate_types.RUBRIC_VERSION_RE.pattern == r"^[0-9a-f]{7,64}$"

    def test_rubric_ref_with_a_trailing_newline_is_rejected(self, types_copy):
        """`$` alone tolerates a trailing newline (and so does the schema pattern);
        the lint full-matches."""
        _mutate(
            types_copy, "rfe", lambda d: d["pipeline"]["rubric"].__setitem__("ref", "e27d7ac\n")
        )
        report = _validate(types_copy)
        hits = _assert_finding(report, "pipeline.rubric.ref must be a 7-40 character", "rfe")
        assert len(hits) == 1 and len(report.findings) == 1, report.lines()

    def _self_rubric(self, version="1c2be0522738", path=SELF_RUBRIC_PATH):
        def mutate(d):
            d["pipeline"]["rubric"] = {"repo": "self", "path": path, "rubric_version": version}

        return mutate

    def test_self_rubric_is_accepted(self, types_copy):
        """D3 (design §11): an embedded rubric is pinned by rubric_version, not ref — the
        schema's second oneOf branch and the lint must agree (the epic fixture's shape)."""
        _mutate(types_copy, "rfe", self._self_rubric())
        report = _validate(types_copy)
        assert report.ok, report.lines()

    def test_self_rubric_accepts_a_full_sha256(self, types_copy):
        _mutate(types_copy, "rfe", self._self_rubric(version="a" * 64))
        assert _validate(types_copy).ok

    @pytest.mark.parametrize(
        "version, shown",
        [
            ("abc", "'abc'"),
            ("ABCDEF1234", "'ABCDEF1234'"),
            (None, "None"),
            ("g" * 12, "'gggggggggggg'"),
        ],
    )
    def test_self_rubric_bad_version(self, types_copy, version, shown):
        _mutate(types_copy, "rfe", self._self_rubric(version=version))
        report = _validate(types_copy)
        hits = _assert_finding(
            report,
            "pipeline.rubric.rubric_version must be a 7-64 character lowercase hex content hash "
            "when pipeline.rubric.repo is 'self' (D3), got " + shown,
            "rfe",
        )
        assert len(hits) == 1
        assert not _find(report, "pipeline.rubric.ref must be")  # never the wrong branch

    def test_self_rubric_ignores_the_ref_rule(self, types_copy):
        """A self rubric carries no ref; the schema forbids one (additionalProperties)
        and the lint must not demand it."""
        _mutate(types_copy, "rfe", self._self_rubric())
        assert not _find(_validate(types_copy), "pipeline.rubric.ref must be")

    @pytest.mark.parametrize("kind", ["strategy", "decomposition"])
    def test_registered_descriptor_must_be_of_the_work_item_kind(self, types_copy, kind):
        """Design §3.2: the schema enum admits the sibling kinds (so the epic fixture
        validates schema-only) but the v1 engine has a phase table for work-item only."""
        _mutate(types_copy, "rfe", lambda d: d.__setitem__("kind", kind))
        report = _validate(types_copy)
        hits = _assert_finding(
            report,
            f"kind {kind!r} cannot be registered: the v1 engine runs the 'work-item' kind only",
            "rfe",
        )
        assert len(hits) == 1
        assert _messages(report, "initiative") == []

    def test_kind_defaults_to_work_item_when_absent(self, types_copy):
        _mutate(types_copy, "rfe", lambda d: d.pop("kind"))
        assert _validate(types_copy).ok

    def test_unknown_kind_is_a_schema_finding_only(self, types_copy):
        _mutate(types_copy, "rfe", lambda d: d.__setitem__("kind", "Work-Item"))
        report = _validate(types_copy)
        _assert_finding(report, "schema: $.kind:", "rfe")
        assert not _find(report, "cannot be registered")

    def test_executable_code_under_a_descriptor_root_is_a_finding(self, types_copy):
        """PR1-21 / design §3.5.1: descriptor roots are data only. Suffix-based (.py/.sh/
        .bash/.zsh) plus shebang files, so a git checkout's mode bits never matter."""
        (types_copy / "rfe" / "helper.py").write_text("print('no')\n")
        (types_copy / "_schema" / "gen.sh").write_text("echo no\n")
        (types_copy / "hook").write_text("#!/usr/bin/env bash\necho no\n")
        (types_copy / "notes.txt").write_text("#not a shebang\n")
        report = _validate(types_copy)
        hits = _assert_finding(report, "executable code under the descriptor root", "*")
        flagged = sorted(str(Path(h.message.split(": ", 1)[1].split(" (")[0]).name) for h in hits)
        assert flagged == ["gen.sh", "helper.py", "hook"]
        assert not _find(report, "notes.txt")
        # --verify keeps only the finding under the verified type's own directory
        rfe_only = _validate(types_copy, only="rfe")
        assert [h.message for h in _find(rfe_only, "executable code")] == [
            h.message for h in hits if "helper.py" in h.message
        ]
        assert not _find(_validate(types_copy, only="initiative"), "executable code")

    def test_shipped_roots_are_data_only(self):
        reg = type_registry.load(root=TYPES_ROOT, extra_roots=[], env={})
        assert validate_types.data_only_findings(reg) == []
        fixtures = type_registry.load(root=FIXTURES_TYPES_ROOT, extra_roots=[], env={})
        assert validate_types.data_only_findings(fixtures) == []

    def test_unanchored_local_id_pattern(self, types_copy):
        _mutate(
            types_copy, "rfe", lambda d: d["identity"].__setitem__("local_id_pattern", r"RFE-\d+")
        )
        report = _validate(types_copy)
        _assert_finding(report, "must be anchored with ^ and $", "rfe")
        _assert_finding(report, "schema: $.identity.local_id_pattern", "rfe")

    def test_invalid_local_id_regex(self, types_copy):
        _mutate(
            types_copy,
            "rfe",
            lambda d: d["identity"].__setitem__("local_id_pattern", r"^RFE-(\d+$"),
        )
        hits = _assert_finding(_validate(types_copy), "is not a valid regex:", "rfe")
        assert len(hits) == 1

    def test_alignment_labels_without_alignment_dimension(self, types_copy):
        labels = {"strong": "rfe-creator-alignment-strong", "partial": "x", "weak": "y"}
        _mutate(
            types_copy, "rfe", lambda d: d["conventions"]["labels"].__setitem__("alignment", labels)
        )
        _assert_finding(
            _validate(types_copy),
            "conventions.labels.alignment is declared but no pipeline.dimensions[] entry is "
            "named 'alignment'",
            "rfe",
        )

    def test_alignment_dimension_without_labels_is_not_flagged(self, types_copy):
        # One direction only (design §3.2 text): labels require a dimension, not vice versa.
        _mutate(types_copy, "initiative", lambda d: d["conventions"]["labels"].pop("alignment"))
        assert _validate(types_copy).ok

    def test_error_stub_missing_score_field(self, types_copy):
        fields = ["what", "why", "open_to_how", "right_sized"]  # scope dropped
        _mutate(
            types_copy,
            "initiative",
            lambda d: d["schema"]["review"].__setitem__("score_fields", fields),
        )
        hits = _assert_finding(
            _validate(types_copy),
            "review error stub (verify_phase.py:105-127) is rejected by "
            "artifact_utils.SCHEMAS['initiative-review']: Missing required field: scores.scope",
            "initiative",
        )
        assert len(hits) == 1

    @pytest.mark.parametrize("name", SHIPPED)
    def test_error_stub_mirrors_verify_phase(self, name):
        """The stub the validator checks is exactly what verify_phase writes."""
        desc = type_registry.load(root=TYPES_ROOT, extra_roots=[], env={}).get(name)
        tc = verify_phase._TYPE_CONFIG[name]
        stub = build_error_stub(desc)
        assert stub[tc["id_field"]].startswith(desc.local_prefix)
        assert stub["error"] == "assess_failed"
        assert stub["needs_attention_reason"] == "Agent failed: assess_failed"
        for key, value in validate_types.ERROR_STUB_CONSTANTS.items():
            assert stub[key] == value
        assert [f"scores.{f}=0" for f in desc.score_fields] == tc["score_fields"]
        assert stub["scores"] == {f: 0 for f in desc.score_fields}
        assert tc["review_schema"] == f"{name}-review"
        assert tc["reviews_dir"] == desc.dirs()["reviews"]
        assert tc["id_field"] == desc.id_field

    def test_error_stub_check_skipped_without_artifact_utils(self):
        desc = type_registry.load(root=TYPES_ROOT, extra_roots=[], env={}).get("rfe")
        assert validate_types.error_stub_messages(desc, None) == []

    def test_error_stub_check_skipped_for_types_without_a_review_schema(self, tmp_path):
        root = _third_type(tmp_path / "extra")
        desc = type_registry.load(root=root, extra_roots=[], env={}).get("docs")
        import artifact_utils

        assert "docs-review" not in artifact_utils.SCHEMAS
        assert validate_types.error_stub_messages(desc, artifact_utils) == []


# ── gate 1: cross-type invariants on effective bindings ──────────────────────────


class TestCrossTypeGate:
    def test_duplicate_binding_via_descriptor(self, types_copy):
        _mutate(
            types_copy,
            "initiative",
            lambda d: d["identity"]["jira"].update(project="RHAIRFE", issue_type="Feature Request"),
        )
        report = _validate(types_copy)
        hits = _assert_finding(
            report,
            "duplicate effective binding (jira, RHAIRFE, Feature Request) shared by types: "
            "initiative, rfe",
            "*",
        )
        assert hits[0].types == frozenset({"rfe", "initiative"})
        assert report.lines() == [
            "ERROR *: duplicate effective binding (jira, RHAIRFE, Feature Request) shared by "
            "types: initiative, rfe"
        ]

    def test_duplicate_binding_via_env_override_is_effective(self, types_copy):
        env = {
            "RFE_CREATOR_BINDING_INITIATIVE_PROJECT": "RHAIRFE",
            "RFE_CREATOR_BINDING_INITIATIVE_ISSUE_TYPE": "Feature Request",
        }
        assert _validate(types_copy).ok
        _assert_finding(
            _validate(types_copy, env=env),
            "duplicate effective binding (jira, RHAIRFE, Feature Request)",
            "*",
        )

    def test_same_project_different_issue_type_is_fine(self, types_copy):
        env = {"RFE_CREATOR_BINDING_INITIATIVE_PROJECT": "RHAIRFE"}
        assert _validate(types_copy, env=env).ok

    def test_duplicate_local_prefix(self, types_copy):
        _mutate(
            types_copy, "initiative", lambda d: d["identity"].__setitem__("local_prefix", "RFE-")
        )
        _assert_finding(
            _validate(types_copy),
            "duplicate identity.local_prefix 'RFE-' shared by types: initiative, rfe",
            "*",
        )

    def test_duplicate_local_prefix_via_env_override(self, types_copy):
        env = {"RFE_CREATOR_BINDING_INITIATIVE_LOCAL_PREFIX": "RFE-"}
        _assert_finding(
            _validate(types_copy, env=env), "duplicate identity.local_prefix 'RFE-'", "*"
        )

    def test_duplicate_local_id_pattern(self, types_copy):
        _mutate(
            types_copy,
            "initiative",
            lambda d: d["identity"].__setitem__("local_id_pattern", r"^RFE-\d+$"),
        )
        _assert_finding(
            _validate(types_copy),
            "duplicate identity.local_id_pattern '^RFE-\\\\d+$' shared by types: initiative, rfe",
            "*",
        )

    def test_duplicate_id_field(self, types_copy):
        _mutate(types_copy, "initiative", lambda d: d["identity"].__setitem__("id_field", "rfe_id"))
        _assert_finding(
            _validate(types_copy),
            "duplicate identity.id_field 'rfe_id' shared by types: initiative, rfe",
            "*",
        )

    @pytest.mark.parametrize(
        "dotted, setter",
        [
            ("pipeline.poll_prefix", lambda d: d["pipeline"].__setitem__("poll_prefix", "")),
            ("pipeline.state_prefix", lambda d: d["pipeline"].__setitem__("state_prefix", "")),
            ("snapshot.report_prefix", lambda d: d["snapshot"].__setitem__("report_prefix", "")),
        ],
    )
    def test_empty_prefix_is_grandfathered_for_rfe_only(self, types_copy, dotted, setter):
        _mutate(types_copy, "initiative", setter)
        report = _validate(types_copy)
        hits = _assert_finding(
            report,
            f"{dotted} is empty for type 'initiative' (an empty prefix is grandfathered for "
            "type 'rfe' only)",
            "*",
        )
        assert hits[0].types == frozenset({"initiative"})
        assert len(report.findings) == 1

    def test_rfe_empty_prefixes_stay_grandfathered(self):
        desc = type_registry.load(root=TYPES_ROOT, extra_roots=[], env={}).get("rfe")
        assert desc.get("pipeline.poll_prefix") == ""
        assert desc.get("pipeline.state_prefix") == ""
        assert desc.get("snapshot.report_prefix") == ""
        assert validate_types.GRANDFATHERED_EMPTY_PREFIX_TYPE == "rfe"
        assert _validate(TYPES_ROOT).ok

    def test_duplicate_non_empty_poll_prefix(self, types_copy):
        _mutate(
            types_copy, "rfe", lambda d: d["pipeline"].__setitem__("poll_prefix", "initiative-")
        )
        _assert_finding(
            _validate(types_copy),
            "duplicate pipeline.poll_prefix 'initiative-' shared by types: initiative, rfe",
            "*",
        )

    def test_duplicate_report_prefix(self, types_copy):
        _mutate(
            types_copy,
            "rfe",
            lambda d: d["snapshot"].__setitem__("report_prefix", "initiative-run-"),
        )
        _assert_finding(
            _validate(types_copy), "duplicate snapshot.report_prefix 'initiative-run-'", "*"
        )

    @pytest.mark.parametrize("prefix", ["issue-snapshot-initiative-", "issue-"])
    def test_snapshot_prefix_collision_both_directions(self, types_copy, prefix):
        _mutate(types_copy, "initiative", lambda d: d["snapshot"].__setitem__("prefix", prefix))
        hits = _assert_finding(_validate(types_copy), "snapshot.prefix collision:", "*")
        assert hits[0].types == frozenset({"rfe", "initiative"})
        assert "one is a prefix of the other" in hits[0].message

    def test_empty_snapshot_prefix(self, types_copy):
        _mutate(types_copy, "initiative", lambda d: d["snapshot"].__setitem__("prefix", ""))
        report = _validate(types_copy)
        _assert_finding(report, "snapshot.prefix is empty for type 'initiative'", "*")
        _assert_finding(report, "schema: $.snapshot.prefix", "initiative")

    def test_local_prefix_stem_equal_to_own_project_key(self, types_copy):
        _mutate(
            types_copy,
            "initiative",
            lambda d: d["identity"].__setitem__("local_prefix", "RHOAIENG-"),
        )
        hits = _assert_finding(
            _validate(types_copy),
            "identity.local_prefix 'RHOAIENG-' of type 'initiative' has the same stem as the "
            "effective project key 'RHOAIENG' of type 'initiative'",
            "*",
        )
        assert hits[0].types == frozenset({"initiative"})

    def test_local_prefix_stem_equal_to_another_effective_project_key(self, types_copy):
        env = {"RFE_CREATOR_BINDING_INITIATIVE_LOCAL_PREFIX": "RHAIRFE-"}
        hits = _assert_finding(
            _validate(types_copy, env=env),
            "identity.local_prefix 'RHAIRFE-' of type 'initiative' has the same stem as the "
            "effective project key 'RHAIRFE' of type 'rfe'",
            "*",
        )
        assert hits[0].types == frozenset({"rfe", "initiative"})

    def test_project_override_moving_onto_a_local_prefix_stem(self, types_copy):
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "INIT"}
        _assert_finding(
            _validate(types_copy, env=env),
            "identity.local_prefix 'INIT-' of type 'initiative' has the same stem as the "
            "effective project key 'INIT' of type 'rfe'",
            "*",
        )

    def test_invalid_override_value_is_a_finding_not_a_crash(self, types_copy):
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "acme"}
        report = _validate(types_copy, env=env)
        hits = _assert_finding(report, "effective binding cannot be computed:", "rfe")
        assert "RFE_CREATOR_BINDING_RFE_PROJECT='acme'" in hits[0].message
        assert len(report.findings) == 1

    def test_valid_override_without_collision_passes(self, types_copy):
        env = {
            "RFE_CREATOR_BINDING_RFE_PROJECT": "ACME",
            "RFE_CREATOR_BINDING_RFE_ISSUE_TYPE": "Story",
            "RFE_CREATOR_BINDING_RFE_LOCAL_PREFIX": "REQ-",
        }
        assert _validate(types_copy, env=env).ok

    def test_cli_reads_overrides_from_the_environment(self, types_copy):
        env = {"RFE_CREATOR_BINDING_INITIATIVE_LOCAL_PREFIX": "RHAIRFE-"}
        result = _cli("--root", str(types_copy), env=env)
        assert result.returncode == 1
        assert "has the same stem as the effective project key 'RHAIRFE'" in result.stdout

    def test_cross_type_findings_are_reported_under_star(self, types_copy):
        _mutate(types_copy, "initiative", lambda d: d["identity"].__setitem__("id_field", "rfe_id"))
        report = _validate(types_copy)
        cross = [f for f in report.findings if f.type == "*"]
        assert cross and all(f.types for f in cross)
        assert all(f.gate == 1 for f in report.findings)


# ── gate 2: --with-deps ──────────────────────────────────────────────────────────


class TestWithDeps:
    def test_complete_fake_checkout_passes(self, tmp_path):
        ctx = _fake_assess_checkout(tmp_path)
        report = _validate(TYPES_ROOT, with_deps=True, assess_dir=ctx)
        assert report.ok, report.lines()
        result = _cli("--with-deps", "--assess-dir", str(ctx))
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == OK_LINE

    def test_missing_initiative_rubric(self, tmp_path):
        ctx = _fake_assess_checkout(tmp_path, initiative_rubric=False)
        report = _validate(TYPES_ROOT, with_deps=True, assess_dir=ctx)
        hits = _assert_finding(
            report,
            f"with-deps: pipeline.rubric.path '{INITIATIVE_RUBRIC}' not found under {ctx}",
            "initiative",
        )
        assert hits[0].gate == 2
        assert len(report.findings) == 1

    def test_missing_scorer_agent(self, tmp_path):
        ctx = _fake_assess_checkout(tmp_path, initiative_agent=False)
        report = _validate(TYPES_ROOT, with_deps=True, assess_dir=ctx)
        hits = _assert_finding(
            report,
            f"with-deps: scorer agent file {ctx / 'agents' / 'initiative-scorer.md'} not found "
            "(pipeline.scorer_agent='initiative-scorer')",
            "initiative",
        )
        assert hits[0].gate == 2

    def test_self_rubric_path_resolves_against_the_repo_root(self, tmp_path):
        """D3: an embedded rubric is not in the assess checkout; gate 2 looks for it in
        this repo (the scorer-agent check is unchanged)."""
        ctx = _fake_assess_checkout(tmp_path)
        root = _copy_types(tmp_path / "types")

        def self_rubric(path):
            def mutate(d):
                d["pipeline"]["rubric"] = {
                    "repo": "self",
                    "path": path,
                    "rubric_version": "abcdef1",
                }

            return mutate

        _mutate(root, "rfe", self_rubric(SELF_RUBRIC_PATH))
        report = _validate(root, with_deps=True, assess_dir=ctx)
        assert report.ok, report.lines()

        _mutate(root, "rfe", self_rubric("types/rfe/rubric/agent_prompt.md"))
        report = _validate(root, with_deps=True, assess_dir=ctx)
        hits = _assert_finding(
            report,
            "with-deps: pipeline.rubric.path 'types/rfe/rubric/agent_prompt.md' not found",
            "rfe",
        )
        assert len(hits) == 1 and hits[0].gate == 2
        assert f"under {REPO_ROOT} (rubric.repo is 'self')" in hits[0].message

    def test_missing_checkout(self, tmp_path):
        missing = tmp_path / "nowhere"
        report = _validate(TYPES_ROOT, with_deps=True, assess_dir=missing)
        hits = _assert_finding(report, f"with-deps: assess checkout not found at {missing}", "*")
        assert hits[0].gate == 2
        assert len(report.findings) == 1

    def test_gate_2_is_opt_in(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)  # no .context/assess-rfe here
        assert validate_types.main([]) == 0
        assert capsys.readouterr().out == OK_LINE
        assert validate_types.main(["--with-deps"]) == 1
        out = capsys.readouterr().out
        assert "with-deps: assess checkout not found at .context/assess-rfe" in out

    def test_default_assess_dir_is_cwd_relative(self, tmp_path, monkeypatch, capsys):
        _fake_assess_checkout(tmp_path)
        monkeypatch.chdir(tmp_path)
        assert validate_types.main(["--with-deps"]) == 0
        assert capsys.readouterr().out == OK_LINE
        assert validate_types.DEFAULT_ASSESS_DIR == Path(".context") / "assess-rfe"

    def test_assess_dir_without_with_deps_is_a_usage_error(self, tmp_path):
        result = _cli("--assess-dir", str(tmp_path))
        assert result.returncode == 2
        assert "--assess-dir is only meaningful with --with-deps" in result.stderr

    def test_verify_keeps_only_gate_2_findings_for_that_type(self, tmp_path):
        ctx = _fake_assess_checkout(tmp_path, initiative_rubric=False, initiative_agent=False)
        assert _validate(TYPES_ROOT, with_deps=True, assess_dir=ctx, only="rfe").ok
        report = _validate(TYPES_ROOT, with_deps=True, assess_dir=ctx, only="initiative")
        assert len(report.findings) == 2


# ── gate 3: --verify --type ──────────────────────────────────────────────────────


class TestVerify:
    def test_unknown_type(self):
        result = _cli("--verify", "--type", "nope")
        assert result.returncode == 1
        assert result.stdout == (
            "VERIFY FAILED: type 'nope' — unknown type 'nope' (available: rfe, initiative)\n"
        )

    def test_verify_requires_type(self):
        result = _cli("--verify")
        assert result.returncode == 2
        assert "--verify requires --type T" in result.stderr

    def test_type_requires_verify(self):
        result = _cli("--type", "rfe")
        assert result.returncode == 2
        assert "--type is only meaningful with --verify" in result.stderr

    @pytest.mark.parametrize("name", SHIPPED)
    def test_shipped_types_verify_ok(self, name):
        result = _cli("--verify", "--type", name)
        assert result.returncode == 0, result.stdout
        assert result.stdout == f"VERIFY OK: type {name!r} passes gate 1\n"

    def test_verify_isolates_per_type_findings(self, types_copy):
        _mutate(types_copy, "rfe", lambda d: d["pipeline"]["rubric"].__setitem__("ref", "short"))
        ok = _cli("--root", str(types_copy), "--verify", "--type", "initiative")
        assert ok.returncode == 0, ok.stdout
        bad = _cli("--root", str(types_copy), "--verify", "--type", "rfe")
        assert bad.returncode == 1
        assert bad.stdout.startswith("VERIFY FAILED: type 'rfe' — ")
        assert "pipeline.rubric.ref must be a 7-40 character lowercase hex commit SHA" in bad.stdout
        assert bad.stdout.count("\n") == 1  # one-line diagnosis

    def test_verify_keeps_cross_type_findings_involving_the_type(self, types_copy):
        _mutate(
            types_copy,
            "initiative",
            lambda d: d["identity"]["jira"].update(project="RHAIRFE", issue_type="Feature Request"),
        )
        for name in SHIPPED:
            report = _validate(types_copy, only=name)
            assert report.types == [name]
            assert [f.type for f in report.findings] == ["*"]
            assert "duplicate effective binding" in report.findings[0].message

    def test_verify_drops_cross_type_findings_not_involving_the_type(self, tmp_path):
        root = _copy_types(tmp_path / "types")
        _third_type(root, "dup")
        _mutate(root, "dup", lambda d: d["identity"].__setitem__("local_prefix", "RFE-"))
        assert _validate(root, only="initiative").ok
        assert not _validate(root, only="dup").ok
        assert not _validate(root, only="rfe").ok

    def test_verify_json(self, types_copy):
        result = _cli("--root", str(types_copy), "--verify", "--type", "rfe", "--json")
        assert result.returncode == 0
        assert json.loads(result.stdout) == {"ok": True, "types": ["rfe"], "errors": []}

    def test_multiple_findings_are_joined_with_pipes(self, types_copy, capsys):
        def two(d):
            d["pipeline"]["rubric"]["ref"] = "short"
            d["schema"]["review"]["score_fields"] = []

        _mutate(types_copy, "initiative", two)
        assert (
            validate_types.main(["--root", str(types_copy), "--verify", "--type", "initiative"])
            == 1
        )
        out = capsys.readouterr().out
        assert out.startswith("VERIFY FAILED: type 'initiative' — ")
        assert " | " in out


# ── jsonschema is a lazy dev dependency (Q5) ─────────────────────────────────────


class TestMissingJsonschema:
    def test_api_raises_missing_dependency_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "jsonschema", None)
        with pytest.raises(MissingDependencyError, match="pip install -r requirements-dev.txt"):
            _validate(TYPES_ROOT)

    def test_cli_exits_2_on_stderr(self, monkeypatch, capsys):
        monkeypatch.setitem(sys.modules, "jsonschema", None)
        assert validate_types.main([]) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith(
            "ERROR *: jsonschema is required by scripts/validate_types.py"
        )

    def test_registry_does_not_need_jsonschema(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "jsonschema", None)
        assert type_registry.load(root=TYPES_ROOT, extra_roots=[], env={}).names() == [
            "rfe",
            "initiative",
        ]

    def test_jsonschema_is_listed_in_requirements_dev(self):
        text = (REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
        assert re.search(r"^jsonschema\b", text, re.MULTILINE)


# ── the paper epic descriptor (Q18) ──────────────────────────────────────────────


@pytest.fixture(scope="module")
def epic():
    """The paper epic descriptor, parsed once per module (a class-scoped fixture defined
    as an instance method is deprecated in pytest 9 and an error in pytest 10)."""
    return _read_yaml(EPIC_FIXTURE)


class TestEpicFixture:
    def test_validates_against_the_shipped_schema(self, epic):
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            schema = json.load(fh)
        errors = list(jsonschema.Draft202012Validator(schema).iter_errors(epic))
        assert errors == [], [e.message for e in errors]

    def test_schema_messages_helper_agrees(self, epic):
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            schema = json.load(fh)
        desc = type_registry.Descriptor("epic", epic, path=EPIC_FIXTURE)
        assert validate_types.schema_messages(desc, schema) == []

    def test_is_not_registered(self):
        reg = type_registry.load(root=TYPES_ROOT, extra_roots=[], env={})
        assert "epic" not in reg.names()
        assert not (TYPES_ROOT / "epic").exists()

    def test_loads_from_the_fixtures_root(self):
        reg = type_registry.load(root=FIXTURES_TYPES_ROOT, extra_roots=[], env={})
        assert reg.names() == ["epic"]
        binding = reg.get("epic").binding()
        assert (binding["project"], binding["issue_type"]) == ("RHAI", "Epic")
        assert binding["key_prefixes"] == ["RHAI-"]

    def test_composite_local_id_matches_epic_only(self, epic):
        reg = type_registry.load(root=TYPES_ROOT, extra_roots=[], env={})
        pattern = epic["identity"]["local_id_pattern"]
        assert re.fullmatch(pattern, "RHAISTRAT-1234-E001")
        assert re.fullmatch(pattern, "RHAISTRAT-1234-BRANCH-A-E002")
        for name in SHIPPED:
            assert re.fullmatch(reg.get(name).local_id_pattern, "RHAISTRAT-1234-E001") is None
        assert re.fullmatch(pattern, "RFE-001") is None
        assert re.fullmatch(pattern, "INIT-001") is None

    def test_gate_1_refuses_to_register_it(self):
        """Schema-valid, never runnable here: the fixtures root as a registry trips the
        kind rule (and its epic-creator-relative paths), which is exactly Q18's point."""
        report = validate_all(root=FIXTURES_TYPES_ROOT, extra_roots=[], env={}, repo_root=REPO_ROOT)
        assert not report.ok
        _assert_finding(report, "kind 'decomposition' cannot be registered", "epic")
        assert not _find(report, "pipeline.rubric.ref must be")  # D3 branch, not a ref

    def test_reserved_vocabulary_is_exercised(self, epic):
        assert epic["kind"] == "decomposition"
        assert epic["snapshot"] == {"mode": "none", "processed_gate": "remote_children"}
        assert epic["pipeline"]["rubric"]["repo"] == "self"
        assert epic["inputs"][0]["from_type"] == "strategy"
        assert epic["schema"]["task"]["priority"]["map_to_tracker"] == {
            "P0": "Critical",
            "P1": "Major",
            "P2": "Minor",
        }


# ── the design's "extra types" CI test ───────────────────────────────────────────


class TestExtraTypes:
    def test_third_type_is_discovered_and_passes_gate_1(self, tmp_path):
        extra = _third_type(tmp_path / "extra")
        reg = type_registry.load(root=TYPES_ROOT, extra_roots=[extra], env={})
        assert reg.names() == ["rfe", "docs", "initiative"]
        report = _validate(TYPES_ROOT, extra_roots=[extra])
        assert report.ok, report.lines()
        assert report.types == ["rfe", "docs", "initiative"]

    def test_third_type_via_env_variable(self, tmp_path):
        extra = _third_type(tmp_path / "extra")
        env = {"RFE_CREATOR_EXTRA_TYPES": str(extra)}
        assert type_registry.load(root=TYPES_ROOT, env=env).names() == ["rfe", "docs", "initiative"]
        report = validate_all(root=TYPES_ROOT, env=env, repo_root=REPO_ROOT)
        assert report.ok, report.lines()

    def test_cli_with_env_variable(self, tmp_path):
        extra = _third_type(tmp_path / "extra")
        result = _cli(env={"RFE_CREATOR_EXTRA_TYPES": str(extra)})
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == "OK: 3 type(s) valid: rfe, docs, initiative\n"

    def test_cli_with_extra_roots_option(self, tmp_path):
        extra = _third_type(tmp_path / "extra")
        result = _cli("--extra-roots", str(extra), "--json")
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["types"] == ["rfe", "docs", "initiative"]

    def test_third_type_verify(self, tmp_path):
        extra = _third_type(tmp_path / "extra")
        result = _cli("--extra-roots", str(extra), "--verify", "--type", "docs")
        assert result.returncode == 0, result.stdout
        assert result.stdout == "VERIFY OK: type 'docs' passes gate 1\n"

    def test_cli_extra_roots_grammar_matches_the_registry_cli(self, tmp_path):
        """A trailing separator is not an extra (cwd) root, and "" is an explicit empty
        list that switches the env seam off — the same parse_extra_roots grammar."""
        extra = _third_type(tmp_path / "extra")
        trailing = _cli("--extra-roots", str(extra) + os.pathsep, "--json")
        assert trailing.returncode == 0, trailing.stdout + trailing.stderr
        assert json.loads(trailing.stdout)["types"] == ["rfe", "docs", "initiative"]
        explicit_empty = _cli(
            "--extra-roots", "", "--json", env={"RFE_CREATOR_EXTRA_TYPES": str(extra)}
        )
        assert explicit_empty.returncode == 0, explicit_empty.stdout + explicit_empty.stderr
        assert json.loads(explicit_empty.stdout)["types"] == ["rfe", "initiative"]

    def test_headless_gate_applies_to_the_validator(self, tmp_path):
        """PR1-05: under a CI marker the env seam is honoured only for allowlisted roots."""
        extra = _third_type(tmp_path / "extra")
        gated = validate_all(
            root=TYPES_ROOT,
            env={"RFE_CREATOR_EXTRA_TYPES": str(extra), "CI": "true"},
            repo_root=REPO_ROOT,
        )
        assert gated.ok and gated.types == ["rfe", "initiative"]
        allowed = validate_all(
            root=TYPES_ROOT,
            env={
                "RFE_CREATOR_EXTRA_TYPES": str(extra),
                "CI": "true",
                "RFE_CREATOR_EXTRA_TYPES_ALLOWLIST": str(extra),
            },
            repo_root=REPO_ROOT,
        )
        assert allowed.ok and allowed.types == ["rfe", "docs", "initiative"]
        cli = _cli(env={"RFE_CREATOR_EXTRA_TYPES": str(extra), "GITHUB_ACTIONS": "true"})
        assert cli.returncode == 0, cli.stdout + cli.stderr
        assert cli.stdout == OK_LINE
        assert "ignoring RFE_CREATOR_EXTRA_TYPES" in cli.stderr

    def test_drop_in_passes_the_same_gates(self, tmp_path):
        """A drop-in that merely renames rfe collides on every cross-type invariant."""
        extra = tmp_path / "extra"
        shutil.copytree(TYPES_ROOT / "rfe", extra / "clone")
        _mutate(extra, "clone", lambda d: d.__setitem__("type", "clone"))
        report = _validate(TYPES_ROOT, extra_roots=[extra])
        assert not report.ok
        _assert_finding(report, "duplicate effective binding (jira, RHAIRFE, Feature Request)", "*")
        _assert_finding(report, "duplicate identity.local_prefix 'RFE-'", "*")
        _assert_finding(report, "duplicate identity.id_field 'rfe_id'", "*")
        _assert_finding(report, "snapshot.prefix collision:", "*")
        _assert_finding(report, "pipeline.poll_prefix is empty for type 'clone'", "*")
        _assert_finding(report, "snapshot.report_prefix is empty for type 'clone'", "*")

    def test_drop_in_schema_findings_are_reported_under_its_name(self, tmp_path):
        extra = _third_type(tmp_path / "extra")
        _mutate(extra, "docs", lambda d: d.pop("eval"))
        report = _validate(TYPES_ROOT, extra_roots=[extra])
        _assert_finding(report, "'eval' is a required property", "docs")
        assert _messages(report, "rfe") == []
        assert _messages(report, "initiative") == []


# ── the reserved inputs: block against strat-creator's real gate (PR1-15, R1) ─────────

_LABEL_REF_RE = re.compile(r"^\$\{types\.([a-z][a-z0-9-]*)\.labels\.([a-z][a-z0-9_.]*)\}$")


@pytest.fixture(scope="module")
def strat_inputs():
    return _read_yaml(STRAT_INPUTS_FIXTURE)


class TestStratInputs:
    """design §3.7 / §11 R1: the reserved `inputs:` vocabulary must be able to DESCRIBE
    strat-creator's rfe -> strategy intake gate (config/pipeline-settings.yaml@e6b5bf0)
    without an evaluator in rfe-creator. The fixture is data under tests/fixtures/ (not a
    registered type); its `strat_creator:` block carries the verbatim source values."""

    def test_fixture_is_the_strat_creator_gate(self, strat_inputs):
        (entry,) = strat_inputs["inputs"]
        src = strat_inputs["strat_creator"]
        assert entry["from_type"] == "rfe"
        assert entry["relation"] == {
            "kind": "clones",
            "link_type": "Cloners",
            "direction": "derived_is_inward",
        }
        assert entry["source_ref_field"] == "source_rfe"
        gate = entry["gate"]
        assert gate["labels_any"] == ["${types.rfe.labels.rubric_pass}", "tech-reviewed"]
        assert gate["labels_all"] == []
        assert gate["statuses_not"] == src["excluded_statuses"] == ["Closed", "Resolved", "Draft"]
        assert gate["any_of"][0] == {"labels_any": src["required_labels"]}
        assert gate["any_of"][1] == {
            "fields": {"customfield_10855": {"name_in": src["target_versions"]}}
        }
        assert len(src["target_versions"]) == 24
        assert entry["body_source"] == {
            "attachment": "{key}-strategy.md",
            "fallback": "description",
        }
        assert entry["skip_if"]["labels_any"] == src["skip_labels"]
        assert entry["skip_if"]["statuses"] == src["excluded_strat_statuses"]
        assert entry["skip_if"]["single_open_unlabeled_override"] is True

    def test_validates_against_the_shipped_input_schema(self, strat_inputs):
        """The block validates against $defs/input verbatim (a fresh root schema reusing
        the shipped $defs, so every $ref resolves exactly as it does for a descriptor)."""
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            shipped = json.load(fh)
        wrapper = {
            "$schema": shipped["$schema"],
            "$defs": shipped["$defs"],
            "type": "array",
            "minItems": 1,
            "items": {"$ref": "#/$defs/input"},
        }
        errors = list(jsonschema.Draft202012Validator(wrapper).iter_errors(strat_inputs["inputs"]))
        assert errors == [], [e.message for e in errors]

    def test_a_descriptor_carrying_it_passes_gate_1(self, tmp_path, strat_inputs):
        extra = _third_type(tmp_path / "extra")
        _mutate(extra, "docs", lambda d: d.__setitem__("inputs", strat_inputs["inputs"]))
        report = _validate(TYPES_ROOT, extra_roots=[extra])
        assert report.ok, report.lines()

    def test_rubric_pass_reference_resolves_to_the_published_label(self, strat_inputs):
        """R7: strat-creator's quality_labels literal IS rfe's exported rubric_pass label;
        the ${types.rfe.labels.rubric_pass} reference form resolves to it through the registry."""
        reg = type_registry.load(root=TYPES_ROOT, extra_roots=[], env={})
        (entry,) = strat_inputs["inputs"]
        resolved = []
        for token in entry["gate"]["labels_any"]:
            m = _LABEL_REF_RE.match(token)
            resolved.append(reg.get(m.group(1)).labels[m.group(2)] if m else token)
        assert resolved == strat_inputs["strat_creator"]["quality_labels"]
        assert resolved[0] == "rfe-creator-autofix-rubric-pass"

    def test_recorded_gaps_name_the_two_no_home_facts(self, strat_inputs):
        gaps = " ".join(strat_inputs["not_expressible_at_v1"])
        assert "excluded_labels" in gaps and "strat-creator-processing" in gaps
        assert "customfield_10855" in gaps and "Target Version" in gaps

    def test_no_script_reads_the_reserved_keys(self):
        """PR1-15: rfe-creator ships NO gate evaluator — nothing in scripts/ reads kind,
        inputs or produces from a descriptor (the registry and the validator excepted)."""
        pattern = re.compile(r"""(get\(|\[)["'](kind|inputs|produces)["']""")
        offenders = []
        for path in sorted((REPO_ROOT / "scripts").glob("*.py")):
            if path.name in ("type_registry.py", "validate_types.py"):
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line) and "descriptor" in line.lower():
                    offenders.append(f"{path.name}:{lineno}: {line.strip()}")
        assert offenders == []
