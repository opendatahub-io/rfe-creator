#!/usr/bin/env python3
"""Pin tests: the type descriptors equal every type-keyed registry a script still carries.

design-proposals/work-item-types-unified.md §10 item 1: the descriptor becomes source of truth
BY TEST before BY IMPORT. PR-1 shipped the registry inert and pinned every hardcoded dict, literal
and prefix sniffer in scripts/ to a descriptor projection. Each PR-2 migration (§10 item 2) turns
a pinned registry into an import-time projection of scripts/type_registry.py and DELETES its pin
here — a pin of a derived value is a tautology — so what remains pins exactly the registries that
still carry their own literals. Every assertion reads <descriptor projection> == <live value>;
its message names the descriptor field (design §3.2 / §8.2) and the consuming file:line on main
at c1df503 (checklist Q25 — the citation baseline, not the worktree's current line numbers).

How to read a failure: either a script changed a pinned value (update the descriptor in the same
PR — that is the mechanic) or a descriptor drifted from the code it documents. Neither may happen
silently. When a PR-2+ script starts reading the registry, delete its pin here and move its rows
to MIGRATED.

Grandfathered projections (explicit — never "compare literally"):
  * rfe pipeline.poll_prefix / pipeline.state_prefix / snapshot.report_prefix are '' while the
    initiative values are 'initiative-' / 'initiative-' / 'initiative-run-' (design §3.2);
  * submit.TYPE_CONFIGS['rfe']['snapshot_prefix'] is '' — a sentinel for snapshot_fetch's default
    'issue-snapshot-' (Q16); snapshot.prefix itself is the non-empty string;
  * validate_batch_input.PARENT_KEY_PATTERN omits INIT- (Q14: known divergence until PR-3);
  * the four rfe rubric-path sites in skill bodies are STALE (design §10 PR-5) and are pinned as
    such so the PR-5 fix is a visible pin change.

Rows of the PR-1 pin matrix that carry no descriptor projection are listed in DEFERRED with a
reason, and rows whose registry now derives from the descriptor at import are listed in MIGRATED
with the migrating PR, so the coverage of the matrix is auditable from this file alone. Every
test carries a ``# rows:`` comment naming the matrix rows it covers.
"""

import ast
import hashlib
import inspect
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import artifact_utils  # noqa: E402
import bootstrap_snapshot  # noqa: E402
import check_autofix_complete  # noqa: E402
import check_conflicts  # noqa: E402
import check_review_progress  # noqa: E402
import compare_review_outputs  # noqa: E402
import generate_review_pdf  # noqa: E402
import generate_run_report  # noqa: E402
import jira_utils  # noqa: E402
import pipeline_state  # noqa: E402
import prep_assess  # noqa: E402
import snapshot_fetch  # noqa: E402
import split_submit  # noqa: E402
import submit  # noqa: E402
import type_registry  # noqa: E402
import validate_batch_input  # noqa: E402
import verify_phase  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# Hermetic: default root is <repo>/types (``__file__``-relative in type_registry), no drop-in roots,
# no RFE_CREATOR_BINDING_* overlay — the pins are about the DESCRIPTOR values (design §3.2.1: the
# descriptor is the default binding; the effective binding is tested in test_type_registry.py).
REG = type_registry.load(extra_roots=[], env={})
TYPES = REG.names()

# ── constants the descriptors deliberately do not carry (UNMAPPED in the matrix) ─────────────
# Skill-directory naming prefix: 'rfe.' vs 'initiative-'. It determines dispatch_skill, the prompt
# dirs and eval execution.skill; design §4.4 renames everything to rfe-* generics in PR-5.
SKILL_PREFIX = {"rfe": "rfe.", "initiative": "initiative-"}
# Interactive skills poll through a second, non-empty prefix the descriptor cannot express.
POLL_FILE_PREFIX = {"rfe": "tmp/rfe-poll-", "initiative": "tmp/initiative-poll-"}
# Design §10 PR-5: these sites still point at the pre-assess-rfe#5 rubric path.
STALE_RFE_RUBRIC_PATH = ".context/assess-rfe/scripts/agent_prompt.md"
CONTEXT_DIR = ".context/assess-rfe"  # bootstrap-assess-rfe.sh:44
ASSESS_STAGING = "tmp/rfe-assess/single"  # byte-stable staging dir (design §10 tail)
PASS_THRESHOLD = 7  # Q15: scoring machinery, a constant — not per type
POLL_PHASE_BASES = ("fetch", "assess", "review", "revise", "split")
STATE_STAGES = ("review", "split", "autofix", "speedrun")
# The reserved label keys whose value is "<label_prefix>-<suffix>" today (design §3.2 labels map).
LABEL_SUFFIX = {
    "rubric_pass": "autofix-rubric-pass",
    "ignore": "ignore",
    "split_quarantine": "split-quarantine",
    "needs_attention": "needs-attention",
    "auto_created": "auto-created",
    "auto_revised": "auto-revised",
    "split_result": "split-result",
    "split_original": "split-original",
}
SUFFIX_TO_KEY = {v: k for k, v in LABEL_SUFFIX.items()}

# ── matrix rows with no descriptor projection (row index in pin-test-matrix.json order) ──────
DEFERRED = [
    (
        52,
        "snapshot_fetch fetch field list",
        "no descriptor field; the request string ('key,description,labels,issuetype', PR1-19) is "
        "pinned by tests/test_pr1_transparent_edits.py::TestSnapshotFetchIssuetype",
    ),
    (
        88,
        "generate_run_report split outcome marker prefixes",
        "type-invariant pipeline vocabulary (split_refused:/split_submit_failed:/"
        "split_not_attempted:) shared with generate_review_pdf/error_collect; no descriptor field",
    ),
    (
        176,
        "artifact_utils._FIELD_MIGRATIONS / _migrate_fields",
        "schema-agnostic legacy migration {'revised': 'auto_revised'}; no descriptor field",
    ),
    (
        178,
        "artifact_utils.find_artifact_file (rfe-only, DEAD)",
        "DELETED in PR-2b (no callers: grep over scripts/, tests/, .claude/, eval/ hit only the "
        "definition); nothing a pin could protect",
    ),
    (
        181,
        "artifact_utils.find_removed_context_file (legacy .md, DEAD)",
        "DELETED in PR-2b (no callers)",
    ),
    (
        188,
        "frontmatter module docstring usage examples",
        "documentation only (rfe-flavoured examples); no runtime value",
    ),
    (
        196,
        ".claude/settings.json permissions.allow",
        "no descriptor projection; PR1-20 purge/add list is pinned by "
        "tests/test_pr1_transparent_edits.py::TestSettingsAllowlist",
    ),
    (198, ".codex-plugin/plugin.json", "plugin-level manifest; no per-type field"),
    (
        213,
        "Makefile lint/test targets; .github/workflows/lint.yml",
        "type-neutral wiring; pinned by tests/test_pr1_transparent_edits.py::TestLintHooks",
    ),
    (
        215,
        "AGENTS.md artifact tree / schema names / state prefixes / file naming",
        "documentation; the dir/schema facts are pinned through the scripts that own them",
    ),
    (
        223,
        "headless completion strings and return paths",
        "shared skeleton headless-return block; no descriptor field (design §10 PR-5)",
    ),
]

# ── matrix rows whose registry now derives from the descriptor at import (pins deleted) ─────
# (rows, registry, "<PR>: <projection>; <what, if anything, is still literal and pinned>")
MIGRATED = [
    (
        (62,),
        "jql_query default exclusion wrapper",
        "PR-2a: conventions.labels.{ignore,rubric_pass}",
    ),
    (
        (66,),
        "check_conflicts._TYPE_CONFIG",
        "PR-2a: dirs(bare).originals / id_field / key_prefixes[0]; PR-2b: the task scan is "
        "artifact_utils.scan_tasks(desc) (no scan_fn key); still pinned: the "
        "startswith(jira_prefix) predicate (prefix-union later)",
    ),
    (
        (67,),
        "next_rfe_id.DEFAULT_PREFIX / DEFAULT_DIR",
        "PR-2a: rfe local_prefix (dash-less) / dirs.tasks",
    ),
    (
        (69,),
        "validate_batch_input.ALLOWED_PRIORITIES",
        "PR-2a: rfe schema.task.priority.enum for both types; the task schemas derive the same "
        "enum per type since PR-2b (row 170)",
    ),
    (
        (70,),
        "validate_batch_input.{RFE,INITIATIVE}_KNOWN_FIELDS",
        "PR-2a: base set ∪ batch.extra_fields",
    ),
    (
        tuple(range(71, 82)),
        "generate_run_report.TYPE_CONFIG / SCORE_FIELDS",
        "PR-2a: descriptor projection per key; PR-2b: scan_tasks = "
        "functools.partial(artifact_utils.scan_tasks, desc=desc); still pinned: the "
        "child_parent_prefixes derivation guard (RHAISTRAT- excluded)",
    ),
    (
        (87, 102, 106, 147, 148, 153, 154, 155, 156),
        "argparse choices of the migrated scripts (+ rows 62, 66, 70)",
        "PR-2a: registry.choices(); the source form is pinned by "
        "test_migrated_argparse_choices_read_the_registry",
    ),
    (
        tuple(range(89, 101)),
        "generate_review_pdf.REPORT_CONFIG",
        "PR-2a: descriptor projection per key; PASS_THRESHOLD stays a pinned constant (Q15)",
    ),
    ((105,), "batch_summary._TYPE_CONFIG", "PR-2a: dirs view of generate_run_report.TYPE_CONFIG"),
    (
        tuple(range(125, 141)),
        "check_review_progress.PHASE_CHECKS / check_id modes",
        "PR-2b: dirs x poll_prefix x {fetch, assess (shared staging), review, revise, split} + "
        "pipeline.dimensions[].name (+ the rfe-only create row), identity.id_field in the create "
        "mode, modes by phase base; still literal and pinned: the create grandfather "
        "_CREATE_BARRIER_TYPES (row 125 residue here), the initiative row order "
        "(_LEGACY_ROW_ORDER — CLI choices text; tests/test_check_review_progress.py), the "
        "_detect_fast allowlist (141) and the choices source form (142)",
    ),
    (
        (143, 144, 145, 146),
        "verify_phase._TYPE_CONFIG / phase tables / error-stub score tail",
        "PR-2a: dirs x phases + pipeline.dimensions[].name, id_field, scores.<f>=0 over "
        "score_fields; still pinned: the fixed stub vocabulary and its schema acceptance",
    ),
    ((148,), "reassess_save._TYPE_CONFIG", "PR-2a: dirs.reviews"),
    ((149,), "check_revised._TYPE_CONFIG", "PR-2a: dirs(bare) + f'{type}-review'"),
    (
        (150, 151, 152),
        "prefix sniffers preserve_review_state / prep_assess / filter_for_revision",
        "PR-2a: registry.detect(id) or rfe (tests/test_type_registry.py::TestDetect)",
    ),
    ((153,), "collect_recommendations._review_dir", "PR-2a: dirs(bare).reviews"),
    (
        (154,),
        "error_collect._TYPE_CONFIG",
        "PR-2a: dirs; still pinned: the removed-context companion line",
    ),
    (
        (155,),
        "split_collect._TYPE_CONFIG / _set_revise defaults",
        "PR-2a: dirs.reviews + f'{type}-review'; still pinned: the split-status path line",
    ),
    (
        (156,),
        "collect_children id_field / scan",
        "PR-2a: identity.id_field; PR-2b: artifact_utils.scan_tasks(desc) — the type-name "
        "if/else is gone",
    ),
    (
        (157,),
        "check_right_sized._TYPE_CONFIG / resplit threshold",
        "PR-2a: dirs.reviews + pipeline.resplit; below == 2 stays a descriptor guard (Q3)",
    ),
    (
        (162, 163, 164, 165, 166, 167, 168, 169, 170, 171, 174, 175),
        "artifact_utils.SCHEMAS",
        "PR-2b: _task_schema / _review_schema over _TYPES — keys names() x {task, review}; "
        "identity.id_field (the field's name), local_id_pattern | key_prefixes + digits (its "
        "grammar), identity.local_id_pattern (the four local_id sites), "
        "conventions.parent_key_patterns, schema.task.priority.enum, schema.task.extra_fields "
        "(rfe size), schema.review.score_fields (scores / before_scores), "
        "schema.review.extra_fields (initiative alignment); byte-pinned by "
        "tests/test_schemas_golden.py; still pinned: the shared status / recommendation / "
        "feasibility vocabularies (172, 173), the alignment cross-field residue (175) and the "
        "frontmatter choices source form (162)",
    ),
    (
        (179, 180, 182, 183, 184, 185, 186, 187),
        "artifact_utils helpers / frontmatter._detect_schema_type",
        "PR-2b: find_artifact_file_including_archived reads the rfe descriptor; "
        "find_removed_context_yaml / find_review_file route via registry.detect(id) or rfe; "
        "scan_tasks / scan_reviews / rename_to_tracker_key / parse_child take a Descriptor (the "
        "per-type names are wrappers); rebuild_index reads the rfe id_field; "
        "frontmatter._SCHEMA_BY_DIR from dirs; still literal and pinned: _is_companion_file "
        "(177), the rfes.md index contract (186 residue) and — name-keyed until PR-3 — the "
        "rename ValueError label, rfe's slug-tolerant review lookup and parse_child's rfe "
        "markdown fallbacks (184, 185 residue; tests/test_artifact_utils.py holds their "
        "behaviour)",
    ),
]


# ── helpers ───────────────────────────────────────────────────────────────────────────────────


def pin(field, where, descriptor_value, live_value):
    """One pin: <descriptor projection> == <live value>, message names field and file:line."""
    assert descriptor_value == live_value, (
        f"{field}: descriptor projection {descriptor_value!r} != live {live_value!r} ({where})"
    )


def _ctx(t):
    desc = REG.get(t)
    d = desc.data
    pipe = d["pipeline"]
    return SimpleNamespace(
        t=t,
        desc=desc,
        d=d,
        jira=d["identity"]["jira"],
        ident=d["identity"],
        conv=d["conventions"],
        labels=d["conventions"]["labels"],
        pipe=pipe,
        schema=d["schema"],
        rep=d["reporting"],
        ev=d["eval"],
        snap=d["snapshot"],
        dirs=desc.dirs(),
        bare=desc.dirs("bare"),
        dims={x["name"]: x for x in pipe["dimensions"]},
        pp=pipe["poll_prefix"],
        sp=pipe["state_prefix"],
        wp=desc.write_prefix,
        lp=desc.local_prefix,
        id_field=desc.id_field,
        score_fields=desc.score_fields,
        task_schema=f"{t}-task",
        review_schema=f"{t}-review",
        sk=SKILL_PREFIX[t],
        sample_ids=(f"{desc.local_prefix}001", f"{desc.write_prefix}1234"),
    )


@pytest.fixture(params=TYPES, ids=TYPES)
def ctx(request):
    return _ctx(request.param)


_TEXT = {}


def read(rel):
    """Repo-relative file text (cached)."""
    if rel not in _TEXT:
        _TEXT[rel] = (REPO_ROOT / rel).read_text(encoding="utf-8")
    return _TEXT[rel]


def skill(t, stage, sub="SKILL.md"):
    return read(f".claude/skills/{SKILL_PREFIX[t]}{stage}/{sub}")


def choices(rel, flag="--type"):
    """argparse ``choices=`` of every ``add_argument(flag, ...)`` call in a script (AST)."""
    found = []
    for node in ast.walk(ast.parse(read(rel))):
        if not (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "add_argument"):
            continue
        if not (
            node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == flag
        ):
            continue
        for kw in node.keywords:
            if kw.arg == "choices":
                try:
                    found.append(ast.literal_eval(kw.value))
                except ValueError:
                    found.append(ast.unparse(kw.value))
    return found


def fstring_suffixes(source, head):
    """Suffixes of ``f"{<head>}-<suffix>"`` literals in ``source`` (composed-label sites)."""
    return set(re.findall(r'f"\{' + re.escape(head) + r'\}-([a-z-]+)"', source))


def eval_config(ctx):
    return yaml.safe_load(read(ctx.ev["config"]))


def judges(ctx):
    return {j["name"]: j for j in eval_config(ctx)["judges"]}


def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def fm(fields, body="Body\n"):
    return "---\n" + yaml.safe_dump(fields, sort_keys=False) + "---\n" + body


# ── registry shape ────────────────────────────────────────────────────────────────────────────


class TestRegistryShape:
    # rows: 25, 46, 53, 60, 124, 142, 159, 160, 161 + the source form of 162 (62, 66, 70, 87,
    # 102, 106, 147, 148, 153-156, 162 MIGRATED)

    def test_shipped_types_in_argparse_order(self):
        assert TYPES == ["rfe", "initiative"]

    @pytest.mark.parametrize(
        "name, live",
        [
            # The type-keyed dicts still spelled out by hand; a dict derived from the registry
            # (comprehension over names()) has this property by construction and is not listed.
            ("submit.TYPE_CONFIGS", submit.TYPE_CONFIGS),
            ("split_submit.SPLIT_CONFIG", split_submit.SPLIT_CONFIG),
            ("snapshot_fetch.SNAPSHOT_CONFIG", snapshot_fetch.SNAPSHOT_CONFIG),
            ("bootstrap_snapshot.BOOTSTRAP_CONFIG", bootstrap_snapshot.BOOTSTRAP_CONFIG),
            ("pipeline_state.PIPELINE_TYPES", pipeline_state.PIPELINE_TYPES),
            ("compare_review_outputs._TYPE_CONFIG", compare_review_outputs._TYPE_CONFIG),
            ("check_autofix_complete._TYPE_CONFIG", check_autofix_complete._TYPE_CONFIG),
        ],
    )
    def test_every_type_keyed_registry_has_exactly_the_shipped_types(self, name, live):
        pin("registry names()", name, set(TYPES), set(live))

    def test_frontmatter_schema_choices_are_the_schema_keys(self):
        # scripts/frontmatter.py:229-231,:237-242,:254-259 — row 162's SCHEMAS keys are MIGRATED
        # (a comprehension over _TYPES; tests/test_schemas_golden.py holds the key order); the
        # source form of the choices is pinned so a re-introduced literal list is a visible change
        for got in choices("scripts/frontmatter.py", "schema_type") + choices(
            "scripts/frontmatter.py", "--schema-type"
        ):
            assert got == "list(SCHEMAS.keys())", got

    @pytest.mark.parametrize(
        "rel, flag",
        [
            ("scripts/submit.py", "--type"),  # :365-370
            ("scripts/split_submit.py", "--type"),  # :853-858
            ("scripts/snapshot_fetch.py", "--type"),  # :490-495
            ("scripts/bootstrap_snapshot.py", "--type"),  # :417-422
            ("scripts/pipeline_state.py", "--type"),  # :848 cmd_init
            ("scripts/compare_review_outputs.py", "--type"),  # :145
            ("scripts/cleanup_partial_split.py", "--type"),  # :27
            ("scripts/check_content_preservation.py", "--type"),  # :208
        ],
    )
    def test_argparse_type_choices_are_registry_choices(self, rel, flag):
        # rows: 25, 46, 53, 60, 124, 159, 160, 161 — the literal lists still carried
        # (check_revised.py / check_right_sized.py hand-parse --type without choices)
        got = choices(rel, flag)
        assert got, f"{rel}: no add_argument({flag!r}, choices=...) found"
        for one in got:
            pin("registry.choices()", f"{rel} {flag}", REG.choices(), one)

    @pytest.mark.parametrize(
        "rel, flag",
        [
            ("scripts/jql_query.py", "--project"),
            ("scripts/check_conflicts.py", "--type"),
            ("scripts/validate_batch_input.py", "--type"),
            ("scripts/generate_run_report.py", "--type"),
            ("scripts/generate_review_pdf.py", "--type"),
            ("scripts/batch_summary.py", "--type"),
            ("scripts/verify_phase.py", "--type"),
            ("scripts/reassess_save.py", "--type"),
            ("scripts/collect_recommendations.py", "--type"),
            ("scripts/error_collect.py", "--type"),
            ("scripts/split_collect.py", "--type"),
            ("scripts/collect_children.py", "--type"),
        ],
    )
    def test_migrated_argparse_choices_read_the_registry(self, rel, flag):
        # MIGRATED rows 62, 66, 70, 87, 102, 106, 147, 148, 153-156 — the literal list is gone;
        # the source form is pinned (as test_frontmatter_schema_choices_are_the_schema_keys pins
        # "list(SCHEMAS.keys())") so a re-introduced literal list is a visible change.
        assert choices(rel, flag) == ["_TYPES.choices()"], rel

    def test_pipeline_types_and_state_validation_share_the_choices(self):
        # scripts/pipeline_state.py:507-509 — type binds at init (design §5)
        pin(
            "registry names()",
            "pipeline_state.py:507-509",
            sorted(TYPES),
            sorted(pipeline_state.PIPELINE_TYPES),
        )


# ── submit.TYPE_CONFIGS ───────────────────────────────────────────────────────────────────────


class TestSubmitTypeConfigs:
    # rows: 1-26

    def test_binding_and_identity(self, ctx):
        c = submit.TYPE_CONFIGS[ctx.t]
        pin("identity.jira.project", "submit.py:63,:94", ctx.jira["project"], c["project"])
        pin("identity.jira.issue_type", "submit.py:64,:95", ctx.jira["issue_type"], c["issue_type"])
        pin("conventions.type_label", "submit.py:65,:96", ctx.conv["type_label"], c["type_label"])
        pin("identity.id_field", "submit.py:66,:97", ctx.id_field, c["id_field"])
        pin("identity.local_prefix", "submit.py:67,:98", ctx.lp, c["local_prefix"])
        pin("identity.jira.key_prefixes[0]", "submit.py:68,:99", ctx.wp, c["jira_prefix"])

    def test_dirs_bare_form_and_schema_names(self, ctx):
        c = submit.TYPE_CONFIGS[ctx.t]
        pin("dirs.tasks (bare)", "submit.py:69,:100", ctx.bare["tasks"], c["tasks_dir"])
        pin("dirs.reviews (bare)", "submit.py:70,:101", ctx.bare["reviews"], c["reviews_dir"])
        pin("dirs.originals (bare)", "submit.py:71,:102", ctx.bare["originals"], c["originals_dir"])
        pin("f'{type}-task'", "submit.py:72,:103", ctx.task_schema, c["task_schema"])
        pin("f'{type}-review'", "submit.py:73,:104", ctx.review_schema, c["review_schema"])

    def test_snapshot_prefix_is_a_grandfathered_sentinel_for_rfe(self, ctx):
        # Q16: '' means "use snapshot_fetch's default" (guards submit.py:672-673,:1170-1171);
        # never compare snapshot.prefix literally to this key.
        c = submit.TYPE_CONFIGS[ctx.t]
        expected = "" if ctx.t == "rfe" else ctx.snap["prefix"]
        pin(
            "snapshot.prefix ('' sentinel for rfe)",
            "submit.py:74,:105",
            expected,
            c["snapshot_prefix"],
        )
        assert ctx.snap["prefix"], "snapshot.prefix itself is never empty (design §8.2 L531)"

    def test_split_type_arg_is_the_argv_convention(self, ctx):
        # UNMAPPED argv convention (consumed submit.py:497-498): pinned as a literal.
        c = submit.TYPE_CONFIGS[ctx.t]
        pin(
            "(argv) None if rfe else type",
            "submit.py:75,:106",
            None if ctx.t == "rfe" else ctx.t,
            c["split_type_arg"],
        )

    def test_labels(self, ctx):
        c = submit.TYPE_CONFIGS[ctx.t]
        pin(
            "conventions.label_prefix",
            "submit.py:76,:107",
            ctx.conv["label_prefix"],
            c["label_prefix"],
        )
        pin(
            "conventions.labels.rubric_pass",
            "submit.py:77,:108",
            ctx.labels["rubric_pass"],
            c["rubric_pass_label"],
        )
        pin(
            "conventions.labels.feasibility",
            "submit.py:78-82,:109-113",
            ctx.labels["feasibility"],
            c["feasibility_labels"],
        )
        pin(
            "conventions.labels.feasibility (order)",
            "submit.py:78-82,:109-113",
            list(ctx.labels["feasibility"]),
            list(c["feasibility_labels"]),
        )
        pin(
            "conventions.labels.alignment",
            "submit.py:83,:114-118",
            ctx.labels.get("alignment"),
            c["alignment_labels"],
        )

    def test_published_contract_strings(self, ctx):
        c = submit.TYPE_CONFIGS[ctx.t]
        pin(
            "conventions.removed_context_preamble",
            "submit.py:84-89,:119-124",
            ctx.conv["removed_context_preamble"],
            c["removed_context_preamble"],
        )
        pin(
            "conventions.comment_prefix",
            "submit.py:90,:125",
            ctx.conv["comment_prefix"],
            c["comment_prefix"],
        )

    def test_has_index(self, ctx):
        pin(
            "index.enabled",
            "submit.py:91,:126",
            ctx.d["index"]["enabled"],
            submit.TYPE_CONFIGS[ctx.t]["has_index"],
        )

    def test_module_alias_is_the_rfe_feasibility_map(self):
        # rows: 18 — rfe-only backward-compat alias (feasibility_label_changes default :142-143)
        rfe = _ctx("rfe")
        pin(
            "labels.feasibility (rfe)",
            "submit.py:131",
            rfe.labels["feasibility"],
            submit.FEASIBILITY_LABELS,
        )
        add, stale = submit.feasibility_label_changes(
            "feasible", is_reject=False, original_labels=[]
        )
        assert (add, stale) == (rfe.labels["feasibility"]["feasible"], [])

    def test_feasibility_label_changes_uses_the_type_map(self, ctx):
        feas = ctx.labels["feasibility"]
        for verdict, label in feas.items():
            add, stale = submit.feasibility_label_changes(
                verdict,
                is_reject=False,
                original_labels=list(feas.values()),
                feasibility_labels=feas,
            )
            assert add == label
            assert set(stale) == set(feas.values()) - {label}

    def test_composed_flag_labels_in_record_split_failure(self, ctx):
        # rows: 19 — submit.py:281-283 compose from label_prefix (split_quarantine's 2nd definition)
        source = inspect.getsource(submit._record_split_failure)
        suffixes = fstring_suffixes(source, "cfg['label_prefix']")
        assert suffixes == {"needs-attention", "split-quarantine"}, suffixes
        for suffix in suffixes:
            key = SUFFIX_TO_KEY[suffix]
            pin(
                f"conventions.labels.{key}",
                "submit.py:281-283",
                ctx.labels[key],
                f"{ctx.conv['label_prefix']}-{suffix}",
            )

    def test_composed_labels_in_build_labels(self, ctx):
        # rows: 20 — submit.py:720-743 (_build_labels is a closure inside main: regex the block)
        body = (
            read("scripts/submit.py").split("def _build_labels", 1)[1].split("return labels", 1)[0]
        )
        suffixes = fstring_suffixes(body, "cfg['label_prefix']")
        assert suffixes == {"auto-created", "auto-revised", "needs-attention"}, suffixes
        for suffix in suffixes:
            key = SUFFIX_TO_KEY[suffix]
            pin(
                f"conventions.labels.{key}",
                "submit.py:723-727",
                ctx.labels[key],
                f"{ctx.conv['label_prefix']}-{suffix}",
            )
        for cfg_key in ("rubric_pass_label", "feasibility_labels", "alignment_labels"):
            assert f'cfg["{cfg_key}"]' in body, f"_build_labels no longer reads cfg[{cfg_key!r}]"

    def test_auto_approve_policy_and_transition_target(self, ctx):
        # rows: 21, 22 — submit.py:805-814 (type-invariant policy), :970-991 (transition + comment)
        source = read("scripts/submit.py")
        assert 'review_data.get("feasibility") == "feasible"' in source
        assert "feasible" == list(ctx.labels["feasibility"])[0]
        approved = ctx.jira["state_map"]["approved"]
        assert f'transition_issue(server, user, token, jira_key, "{approved}")' in source, (
            "submit.py:987"
        )
        assert f'entry.get("jira_status") == "{approved}"' in source, "submit.py:981"
        assert "f\"*{cfg['comment_prefix']}* This {type_label} has been automatically \"" in source
        assert f'"transitioned to {approved} status based on passing rubric scoring and "' in source

    def test_dry_run_sentinel_derivation(self, ctx):
        # rows: 23, 45 — submit.py:1079 uses jira_prefix, split_submit.py:689/:1036 use project;
        # both render the same value today (binding-derived <PROJECT>-DRY, design §3.2.1 d)
        pin(
            "key_prefixes[0]+'DRY' == project+'-DRY'",
            "submit.py:1079 vs split_submit.py:689",
            f"{ctx.wp}DRY",
            f"{ctx.jira['project']}-DRY",
        )
        assert 'f"{jira_prefix}DRY"' in read("scripts/submit.py")
        assert read("scripts/split_submit.py").count('f"{project}-DRY"') == 2

    def test_report_companion_path(self, ctx):
        # rows: 24 — submit.py:318-326 reads generate_run_report.TYPE_CONFIG[t]['output_prefix']
        got = submit.report_companion_path("a", "RUN", ctx.t, "-report.html")
        expected = os.path.join("a", "auto-fix-runs", f"{ctx.snap['report_prefix']}RUN-report.html")
        pin("snapshot.report_prefix", "submit.py:318-326", expected, got)

    def test_dispatch_on_id_field(self, ctx, monkeypatch):
        # rows: 26 — submit.py:154-164 forked-pair dispatcher keyed on id_field (collapses in PR-2)
        calls = []
        monkeypatch.setattr(submit, "scan_task_files", lambda a: calls.append("rfe") or [])
        monkeypatch.setattr(
            submit, "scan_initiative_task_files", lambda a: calls.append("initiative") or []
        )
        monkeypatch.setattr(submit, "rename_to_jira_key", lambda a, i, k: calls.append("rfe"))
        monkeypatch.setattr(
            submit, "rename_initiative_to_jira_key", lambda a, i, k: calls.append("initiative")
        )
        cfg = submit.TYPE_CONFIGS[ctx.t]
        submit._scan_tasks("x", cfg)
        submit._rename_to_jira("x", "A-1", "B-1", cfg)
        expected = "initiative" if ctx.id_field == "initiative_id" else "rfe"
        pin("identity.id_field discriminator", "submit.py:154-164", [expected, expected], calls)
        # :192-193/:210-211 append '--type initiative' to the report commands iff initiative
        assert read("scripts/submit.py").count('extend(["--type", "initiative"])') == 2

    def test_no_task_files_message_is_byte_stable_for_rfe(self):
        # design §10 tail: "No RFE task files found" stays byte-stable (type_label projection)
        assert 'f"Error: No {type_label} task files found."' in read("scripts/submit.py")
        assert (
            f"No {_ctx('rfe').conv['type_label']} task files found." == "No RFE task files found."
        )


# ── split_submit.SPLIT_CONFIG ─────────────────────────────────────────────────────────────────


class TestSplitSubmitConfig:
    # rows: 27-46

    def test_binding_conventions_display(self, ctx):
        s = split_submit.SPLIT_CONFIG[ctx.t]
        pin("identity.jira.project", "split_submit.py:111,:129", ctx.jira["project"], s["project"])
        pin(
            "identity.jira.issue_type",
            "split_submit.py:112,:130",
            ctx.jira["issue_type"],
            s["issue_type"],
        )
        pin(
            "conventions.comment_prefix",
            "split_submit.py:113,:131",
            ctx.conv["comment_prefix"],
            s["comment_marker"],
        )
        pin(
            "conventions.label_prefix",
            "split_submit.py:114,:132",
            ctx.conv["label_prefix"],
            s["label_prefix"],
        )
        pin(
            "display.entity",
            "split_submit.py:115,:133",
            ctx.d["display"]["entity"],
            s["entity_name"],
        )
        pin(
            "display.entity_plural",
            "split_submit.py:116,:134",
            ctx.d["display"]["entity_plural"],
            s["entity_name_plural"],
        )
        pin("identity.id_field", "split_submit.py:117,:135", ctx.id_field, s["id_field"])
        pin(
            "dirs.reviews (bare)", "split_submit.py:118,:136", ctx.bare["reviews"], s["reviews_dir"]
        )
        pin("f'{type}-review'", "split_submit.py:119,:137", ctx.review_schema, s["review_schema"])
        pin(
            "dirs.originals (bare)",
            "split_submit.py:120,:138",
            ctx.bare["originals"],
            s["originals_dir"],
        )
        pin(
            "index.enabled",
            "split_submit.py:125,:145",
            ctx.d["index"]["enabled"],
            s["do_rebuild_index"],
        )
        pin(
            "conventions.labels.alignment",
            "split_submit.py:126,:146-150",
            ctx.labels.get("alignment"),
            s["alignment_labels"],
        )

    def test_forked_callables_selected_by_id_field(self, ctx, tmp_path):
        # rows: 34 — split_submit.py:121-124,:139-144; find_review_fn pinned by rendered path
        s = split_submit.SPLIT_CONFIG[ctx.t]
        if ctx.id_field == "rfe_id":
            expected = (
                artifact_utils.scan_task_files,
                artifact_utils.rename_to_jira_key,
                artifact_utils.parse_child_artifact,
            )
        else:
            expected = (
                artifact_utils.scan_initiative_task_files,
                artifact_utils.rename_initiative_to_jira_key,
                artifact_utils.parse_child_initiative,
            )
        assert (s["scan_fn"], s["rename_fn"], s["parse_child_fn"]) == expected
        for ident in ctx.sample_ids:
            review = write(
                tmp_path / ctx.bare["reviews"] / f"{ident}-review.md", fm({ctx.id_field: ident})
            )
            pin(
                "dirs.reviews",
                "split_submit.py:124,:142-144",
                str(review),
                s["find_review_fn"](str(tmp_path), ident),
            )

    def test_feasibility_labels_second_definition(self, ctx):
        # rows: 13, 37 — split_submit.py:161-166 composes what submit.py:78-82 spells out
        pin(
            "conventions.labels.feasibility",
            "split_submit.py:161-166",
            ctx.labels["feasibility"],
            split_submit._feasibility_labels(ctx.conv["label_prefix"]),
        )
        assert (
            split_submit._feasibility_labels(ctx.conv["label_prefix"])
            == submit.TYPE_CONFIGS[ctx.t]["feasibility_labels"]
        )

    def test_split_child_marker_template_renders_like_the_code(self, ctx):
        # rows: 38 — split_submit.py:225-253; Q12: the template lower-cases {parent}/{child_id},
        # {fingerprint} = sha256(cleaned)[:12] (:230)
        lp = ctx.conv["label_prefix"]
        pin(
            "conventions.labels.split_child_marker",
            "split_submit.py:253",
            f"{lp}-split-child-{{parent}}-{{child_id}}-{{fingerprint}}",
            ctx.labels["split_child_marker"],
        )
        fp = hashlib.sha256(b"x").hexdigest()[:12]
        parent, child = f"{ctx.wp}1234", f"{ctx.lp}001"
        rendered = ctx.labels["split_child_marker"].format(
            parent=parent.lower(), child_id=child.lower(), fingerprint=fp
        )
        pin(
            "split_child_marker rendered",
            "split_submit.py:253",
            rendered,
            split_submit._child_marker_label(lp, parent, child, fp),
        )
        assert len(fp) == 12 and "hexdigest()[:12]" in inspect.getsource(
            split_submit._child_fingerprint
        )

    def test_phase2_and_phase3_composed_labels(self, ctx):
        # rows: 39, 40, 41 — split_submit.py:636-668 child labels, :807/:813 parent label,
        # :514-518 inheritance filter strips f"{label_prefix}-"
        source = read("scripts/split_submit.py")
        suffixes = fstring_suffixes(source, "label_prefix")
        expected = {
            "auto-created",
            "split-result",
            "auto-revised",
            "needs-attention",
            "autofix-rubric-pass",
            "split-original",
        }
        feas = {"feasibility-pass", "feasibility-fail", "feasibility-unknown"}  # :161-166
        assert suffixes == expected | feas, suffixes
        assert {f"{ctx.conv['label_prefix']}-{x}" for x in feas} == set(
            ctx.labels["feasibility"].values()
        )
        for suffix in suffixes - feas:
            pin(
                f"conventions.labels.{SUFFIX_TO_KEY[suffix]}",
                "split_submit.py:636-668,:807-813",
                ctx.labels[SUFFIX_TO_KEY[suffix]],
                f"{ctx.conv['label_prefix']}-{suffix}",
            )
        assert 'if not label.startswith(f"{label_prefix}-")' in source, "split_submit.py:514-518"
        for key, value in ctx.desc.labels.items():
            assert value.startswith(ctx.conv["label_prefix"] + "-"), (
                f"labels.{key}={value!r} escapes the inheritance filter"
            )

    def test_all_reserved_labels_are_prefix_composed(self, ctx):
        # rows: 19, 20, 39, 40 — every reserved key equals "<label_prefix>-<suffix>" today
        for key, suffix in LABEL_SUFFIX.items():
            pin(
                f"conventions.labels.{key}",
                "submit.py/split_submit.py composed sites",
                f"{ctx.conv['label_prefix']}-{suffix}",
                ctx.labels[key],
            )

    def test_split_link_type_literal_sites(self, ctx):
        # rows: 42 — six literal sites :210,:410,:497,:610,:685,:716; emulator seed conftest.py:96
        link = ctx.jira["split_link_type"]
        assert link == "Work item split"
        pin(
            "identity.jira.split_link_type (6 sites)",
            "split_submit.py:210-716",
            6,
            read("scripts/split_submit.py").count(link),
        )
        assert f'"name": "{link}"' in read("tests/conftest.py")

    def test_close_superseded_state_map(self, ctx):
        # rows: 43 — split_submit.py:816-839 (:820 target matched case-insensitively, :837
        # resolution)
        close = ctx.jira["state_map"]["close_superseded"]
        source = read("scripts/split_submit.py")
        assert f'.lower() == "{close["transition"].lower()}"' in source, "split_submit.py:820"
        assert f'fields={{"resolution": {{"name": "{close["resolution"]}"}}}}' in source, (
            "split_submit.py:837"
        )
        assert (
            f"Would transition {{parent_key}} to {close['transition']} "
            f"(resolution: {close['resolution']})" in source
        )

    def test_durable_store_comment_grammar_round_trips(self, ctx):
        # rows: 44 — split_submit.py:353-389 (reader regexes), :550,:614-617,:721-731,:769-776
        # (writer templates); a published Jira-comment contract parameterised only by
        # comment_prefix + display.entity/entity_plural; must stay byte-stable
        source = read("scripts/split_submit.py")
        readers = [
            r'rf"{marker} Split child (\S+) \(\d+ of \d+\):"',
            r'rf"{marker} Split child (\d+) of (\d+):"',
            r'rf"{marker} Created as (\S+) for (\S+), linked to parent"',
            r'rf"{marker} Created as (\S+),.*\(ref: child (\d+) of (\d+)\)"',
        ]
        writers = [
            'f"{comment_marker} Split child {child_id} ({idx} of {total}): {title}"',
            'f"{comment_marker} Created as {child_key} for {child_id}, "',
            'f"linked to parent. ({idx} of {total})"',
            "f\" This {config['entity_name']} has been split\"",
            'f" into {total} child {entity_plural}:"',
            'f"*{comment_marker}* This {entity_name} has been flagged for human review:"',
        ]
        for literal in readers + writers:
            assert literal in source, f"split_submit comment grammar changed: {literal}"
        marker = re.escape(ctx.conv["comment_prefix"])
        child, key = f"{ctx.lp}001", f"{ctx.wp}9"
        header = f"{ctx.conv['comment_prefix']} Split child {child} (1 of 3): Title"
        confirm = (
            f"{ctx.conv['comment_prefix']} Created as {key} for {child}, linked to parent. (1 of 3)"
        )
        assert re.search(rf"{marker} Split child (\S+) \(\d+ of \d+\):", header).group(1) == child
        m = re.search(rf"{marker} Created as (\S+) for (\S+), linked to parent", confirm)
        assert (m.group(1), m.group(2)) == (key, child)


# ── snapshot_fetch / bootstrap_snapshot / jql_query / jira_utils ─────────────────────────────


class TestSnapshotAndBootstrap:
    # rows: 47-51, 53-61

    def test_snapshot_config(self, ctx):
        sc = snapshot_fetch.SNAPSHOT_CONFIG[ctx.t]
        pin(
            "conventions.labels.ignore",
            "snapshot_fetch.py:58,:63",
            ctx.labels["ignore"],
            sc["ignore_label"],
        )
        pin(
            "conventions.labels.split_quarantine",
            "snapshot_fetch.py:59,:64",
            ctx.labels["split_quarantine"],
            sc["quarantine_label"],
        )
        pin(
            "snapshot.prefix", "snapshot_fetch.py:60,:65", ctx.snap["prefix"], sc["snapshot_prefix"]
        )

    def test_default_prefix_kwargs_are_the_rfe_prefix(self):
        # rows: 50 — snapshot_fetch.py:142,:163,:280-282; a third type must always pass prefix
        rfe = _ctx("rfe")
        for fn in (
            snapshot_fetch.find_previous_snapshot,
            snapshot_fetch.load_snapshot_from_dir,
            snapshot_fetch.update_snapshot_hashes,
        ):
            pin(
                "snapshot.prefix (rfe default)",
                f"snapshot_fetch.{fn.__name__}",
                rfe.snap["prefix"],
                inspect.signature(fn).parameters["prefix"].default,
            )

    def test_hard_filter_jql_wrapper_in_both_scripts(self, ctx):
        # rows: 51, 57 — snapshot_fetch.py:368-379 and its verbatim duplicate
        # bootstrap_snapshot.py:456-461,:585
        excluded = f"{ctx.labels['ignore']}, {ctx.labels['split_quarantine']}"
        sc = snapshot_fetch.SNAPSHOT_CONFIG[ctx.t]
        pin(
            "labels.ignore + labels.split_quarantine",
            "snapshot_fetch.py:375",
            excluded,
            f"{sc['ignore_label']}, {sc['quarantine_label']}",
        )
        snap_src, boot_src = (
            read("scripts/snapshot_fetch.py"),
            read("scripts/bootstrap_snapshot.py"),
        )
        assert "excluded = f\"{config['ignore_label']}, {config['quarantine_label']}\"" in snap_src
        assert (
            "excluded = f\"{snap_config['ignore_label']}, {snap_config['quarantine_label']}\""
            in boot_src
        )
        for source in (snap_src, boot_src):
            assert 'f"({args.jql}) AND statusCategory != Done "' in source
            assert 'f"AND (labels not in ({excluded}) OR labels is EMPTY)"' in source
        assert "updated_jql = f'{jql} AND updated >= \"{run_jql_ts}\"'" in boot_src

    def test_bootstrap_config_matches_run_report_writer(self, ctx):
        # rows: 54, 55 — bootstrap_snapshot.py:45-50 reads what generate_run_report.py writes;
        # the writer's TYPE_CONFIG is registry-derived since PR-2a (MIGRATED rows 71-81), so the
        # reader is pinned to the descriptor directly (tests/test_report_roundtrip.py keeps the
        # real-CLI round trip; PR1-10)
        bc = bootstrap_snapshot.BOOTSTRAP_CONFIG[ctx.t]
        pin(
            "snapshot.report_prefix",
            "bootstrap_snapshot.py:45,:49",
            ctx.snap["report_prefix"],
            bc["report_prefix"],
        )
        pin(
            "reporting.item_key",
            "bootstrap_snapshot.py:46,:50",
            ctx.rep["item_key"],
            bc["item_key"],
        )

    def test_run_report_reader_path_is_report_prefix_plus_run(self, ctx, tmp_path):
        # rows: 58 — bootstrap_snapshot.py:76-79 path derivation
        run = "20260818-120000"
        path = os.path.join(
            str(tmp_path), run, "auto-fix-runs", f"{ctx.snap['report_prefix']}{run}.yaml"
        )
        write(
            path,
            yaml.safe_dump(
                {ctx.rep["item_key"]: [{"id": f"{ctx.wp}1", "recommendation": "submit"}]}
            ),
        )
        ids, report = bootstrap_snapshot._load_run_report(
            str(tmp_path), run, config=bootstrap_snapshot.BOOTSTRAP_CONFIG[ctx.t]
        )
        pin(
            "snapshot.report_prefix + reporting.item_key",
            "bootstrap_snapshot.py:76-79",
            {f"{ctx.wp}1"},
            ids,
        )
        assert report is not None

    def test_run_dir_snapshot_probe_is_hardcoded_to_the_rfe_prefix(self, tmp_path):
        # rows: 56 — bootstrap_snapshot.py:175-178 literal 'issue-snapshot-' (rfe-only; latent
        # initiative bug — PR-2 reads snapshot.prefix, which makes this pin change visibly)
        assert 'name.startswith("issue-snapshot-")' in read("scripts/bootstrap_snapshot.py")
        pin(
            "snapshot.prefix (rfe)",
            "bootstrap_snapshot.py:176",
            _ctx("rfe").snap["prefix"],
            "issue-snapshot-",
        )
        for t in TYPES:
            run = f"run-{t}"
            write(
                tmp_path / run / "auto-fix-runs" / f"{_ctx(t).snap['prefix']}x.yaml", "issues: {}\n"
            )
            assert bootstrap_snapshot._run_dir_has_snapshots(str(tmp_path), run) is (t == "rfe")

    def test_snapshot_output_filename(self, ctx):
        # rows: 59 — bootstrap_snapshot.py:676-677; snapshot_fetch.py:456
        assert 'f"{snapshot_prefix}{tip_name}.yaml"' in read("scripts/bootstrap_snapshot.py")
        assert 'snapshot_prefix = snap_config["snapshot_prefix"]' in read(
            "scripts/bootstrap_snapshot.py"
        )
        assert 'f"{prefix}{ts}.yaml"' in read("scripts/snapshot_fetch.py")
        assert ctx.snap["prefix"].endswith("-") and "-snapshot-" in ctx.snap["prefix"]

    def test_done_status_heuristic_recognises_the_superseded_close_target(self, ctx):
        # rows: 61 — bootstrap_snapshot.py:294-313 is type-invariant; the one binding fact it must
        # keep honouring is that a Closed/Obsolete split parent counts as done
        pin(
            "state_map.close_superseded.transition (lower)",
            "bootstrap_snapshot.py:294-313",
            True,
            ctx.jira["state_map"]["close_superseded"]["transition"].lower()
            in bootstrap_snapshot._DONE_STATUS_PATTERNS,
        )


class TestJqlAndJiraUtils:
    # rows: 63, 64 (62 MIGRATED — tests/test_jql_query.py holds the wrapper byte-identical and
    # design §3.6 invariant 4: split_quarantine is never excluded)

    def test_strip_metadata_heading_regex_is_a_known_divergence(self, ctx):
        # rows: 63 — jira_utils.py:736 literal union (RFE-|RHAIRFE-|STRAT-|RHAISTRAT-) != the
        # shipped prefix union; design §3.2.1 'rendered from the registry prefix union' is PR-2+
        literal = r"^#\s+(RFE-\d+|RHAIRFE-\d+|STRAT-\d+|RHAISTRAT-\d+):"
        assert literal in read("scripts/jira_utils.py")
        union = sorted(p for t in TYPES for p in (_ctx(t).lp, *_ctx(t).jira["key_prefixes"]))
        assert sorted(re.findall(r"\(?([A-Z]+-)", literal.split("(", 1)[1])) != union
        for ident in ctx.sample_ids:
            stripped = jira_utils.strip_metadata(f"# {ident}: Title\nbody\n")
            heading_dropped = not stripped.startswith("# ")
            assert heading_dropped is (ctx.t == "rfe"), f"{ident}: {stripped!r}"

    def test_create_issue_sends_project_key_and_issuetype_by_name(self, ctx, monkeypatch):
        # rows: 64 — jira_utils.py:172-179; callers submit.py:1096-1107, split_submit.py:696-709
        seen = {}

        def fake(server, path, user, token, body=None, method="POST"):
            seen.update(path=path, body=body)
            return {"key": f"{ctx.wp}1"}

        monkeypatch.setattr(jira_utils, "api_call_with_retry", fake)
        key = jira_utils.create_issue(
            "s", "u", "t", ctx.jira["project"], ctx.jira["issue_type"], "T", {}, "Major"
        )
        assert key == f"{ctx.wp}1" and seen["path"] == "/issue"
        pin(
            "identity.jira.project",
            "jira_utils.py:174",
            {"key": ctx.jira["project"]},
            seen["body"]["fields"]["project"],
        )
        pin(
            "identity.jira.issue_type (by name)",
            "jira_utils.py:175",
            {"name": ctx.jira["issue_type"]},
            seen["body"]["fields"]["issuetype"],
        )
        assert re.search(
            r'create_issue\(\s*server,\s*user,\s*token,\s*cfg\["project"\],\s*cfg\["issue_type"\],',
            read("scripts/submit.py"),
        )
        assert re.search(
            r"create_issue\(\s*server,\s*user,\s*token,\s*project,\s*issue_type,",
            read("scripts/split_submit.py"),
        )


# ── fetch_issue, check_conflicts, next_rfe_id, validate_batch_input, small _TYPE_CONFIG dicts ─


class TestSmallRegistries:
    # rows: 65, 66 (residue), 68, 107, 154, 155, 157-161 (residues) — 66, 67, 69, 70, 105,
    # 148-157, 170 MIGRATED as listed above

    def test_fetch_issue_is_rfe_only(self):
        # rows: 65 — fetch_issue.py:59-60,:71,:83,:98,:101,:122,:224 (no --type)
        rfe = _ctx("rfe")
        source = read("scripts/fetch_issue.py")
        assert f'os.path.join(artifacts_dir, "{rfe.bare["tasks"]}")' in source
        assert f'os.path.join(artifacts_dir, "{rfe.bare["originals"]}")' in source
        assert f'f"{rfe.id_field}={{issue_key}}"' in source
        assert ('f"{issue_key}-comments.md"' in source) is rfe.d["companions"]["comments"]
        assert "--type" not in source

    def test_check_conflicts_write_prefix_predicate(self, ctx):
        # rows: 66 — the dict is MIGRATED and the task scan is artifact_utils.scan_tasks(desc)
        # since PR-2b (no scan_fn entry); still literal in check_conflicts.py: the
        # startswith(jira_prefix) predicate — key_prefixes[0] only, prefix-union in a later
        # PR-2 step
        assert "scan_fn" not in check_conflicts._TYPE_CONFIG[ctx.t]
        assert 'item_id.startswith(tc["jira_prefix"])' in read("scripts/check_conflicts.py")

    def test_batch_parent_key_pattern_known_divergence(self):
        # rows: 68 — Q14: validate_batch_input.py:36 omits INIT- while the initiative task schema
        # (derived from conventions.parent_key_patterns since PR-2b, row 169 MIGRATED) accepts
        # it; reconciled in PR-3
        init = _ctx("initiative")
        registry_pattern = "^(" + "|".join(init.conv["parent_key_patterns"]) + ")$"
        assert validate_batch_input.PARENT_KEY_PATTERN.pattern == r"^(RHAISTRAT-\d+|RHOAIENG-\d+)$"
        assert validate_batch_input.PARENT_KEY_PATTERN.pattern != registry_pattern
        local_parent = f"{init.lp}001"
        assert validate_batch_input.PARENT_KEY_PATTERN.match(local_parent) is None
        assert re.match(registry_pattern, local_parent)
        assert re.match(
            artifact_utils.SCHEMAS["initiative-task"]["parent_key"]["pattern"], local_parent
        )

    def test_batch_summary_literals(self, ctx):
        # rows: 107 — batch_summary.py:87,:97-111 literals (row 105's dirs dict is MIGRATED)
        source = read("scripts/batch_summary.py")
        assert f'scores.get("{ctx.pipe["resplit"]["score_field"]}")' in source
        assert f'f"{{score}}/{2 * len(ctx.score_fields)}"' in source, (
            "denominator == 2 * len(score_fields)"
        )
        for key in ctx.labels["feasibility"]:
            assert key in artifact_utils.SCHEMAS[ctx.review_schema]["feasibility"]["enum"]

    def test_assess_staging_dir_is_shared(self):
        # rows: 150-152 are MIGRATED (detect()); what stays type-neutral and byte-stable is the
        # assess staging dir prep_assess writes and verify_phase / PHASE_CHECKS read (design §10)
        assert prep_assess.SINGLE_DIR == ASSESS_STAGING
        assert verify_phase.ASSESS_STAGING == ASSESS_STAGING
        assert check_review_progress.ASSESS_STAGING == ASSESS_STAGING

    def test_error_collect_removed_context_companion(self, ctx):
        # rows: 154 — error_collect.py:216 companion (companions.removed_context); the dirs dict
        # is MIGRATED
        assert ctx.d["companions"]["removed_context"] is True
        assert "rc = f\"{tc['tasks_dir']}/{rfe_id}-removed-context.yaml\"" in read(
            "scripts/error_collect.py"
        )

    def test_split_collect_status_path(self):
        # rows: 155 — split_collect.py:60 status path; the dirs dict and the _set_revise rfe
        # defaults are MIGRATED
        assert "status_path = f\"{tc['reviews_dir']}/{pid}-split-status.yaml\"" in read(
            "scripts/split_collect.py"
        )

    def test_resplit_threshold_is_two_for_both_types(self, ctx):
        # rows: 157 — check_right_sized.py reads pipeline.resplit since PR-2a (MIGRATED), which
        # inverts the Q3 note: setting the initiative descriptor to below: 1 now IS the behaviour
        # change, so it must stay a visible pin change rather than a silent descriptor edit
        resplit = ctx.pipe["resplit"]
        assert resplit["below"] == 2, "Q3: initiative below:1 is a behaviour change"
        assert resplit["score_field"] in ctx.score_fields

    def test_check_autofix_complete_encodes_state_prefix(self, ctx):
        # rows: 158 — check_autofix_complete.py:16-25 (the only dict encoding state_prefix)
        expected = {
            "ids_file": f"tmp/{ctx.sp}speedrun-all-ids.txt",
            "reviews_dir": ctx.dirs["reviews"],
        }
        pin(
            "pipeline.state_prefix + dirs.reviews",
            "check_autofix_complete.py:16-25",
            expected,
            check_autofix_complete._TYPE_CONFIG[ctx.t],
        )

    def test_compare_review_outputs(self, ctx):
        # rows: 159 — compare_review_outputs.py:16-29
        expected = {
            "reviews_dir": ctx.bare["reviews"],
            "originals_dir": ctx.bare["originals"],
            "tasks_dir": ctx.bare["tasks"],
            "score_sub_fields": ctx.score_fields,
        }
        pin(
            "dirs (bare) + schema.review.score_fields",
            "compare_review_outputs.py:16-29",
            expected,
            compare_review_outputs._TYPE_CONFIG[ctx.t],
        )

    def test_cleanup_partial_split_inline_branch(self, ctx):
        # rows: 160 — cleanup_partial_split.py:36-50,:92-97 (inline restatement)
        source = read("scripts/cleanup_partial_split.py")
        assert f'os.path.join(ARTIFACTS_DIR, "{ctx.bare["tasks"]}")' in source
        assert f'os.path.join(ARTIFACTS_DIR, "{ctx.bare["reviews"]}")' in source
        assert f'id_field = "{ctx.id_field}"' in source
        assert f'schema_type = "{ctx.task_schema}"' in source
        if ctx.t == "initiative":
            assert (
                f'os.path.join(ARTIFACTS_DIR, "{ctx.bare["tasks"]}", f"{{parent_id}}.md")' in source
            )
        else:
            assert "find_artifact_file_including_archived(ARTIFACTS_DIR, parent_id)" in source

    def test_check_content_preservation_branch(self, ctx):
        # rows: 161 — check_content_preservation.py:219-224
        source = read("scripts/check_content_preservation.py")
        assert f'os.path.join("artifacts", "{ctx.bare["originals"]}")' in source
        assert f'os.path.join("artifacts", "{ctx.bare["tasks"]}")' in source


# ── generate_run_report.TYPE_CONFIG ───────────────────────────────────────────────────────────


class TestRunReportConfig:
    # rows: 82-86 (71-81, 87 MIGRATED)

    def test_child_parent_prefixes_guard(self, ctx):
        # rows: 71-81 are MIGRATED (the scanner is artifact_utils.scan_tasks bound to the
        # descriptor since PR-2b); still pinned: the derivation guard that child_parent_prefixes
        # is (local_prefix, *key_prefixes) and NOT conventions.parent_key_patterns (RHAISTRAT-
        # must stay excluded, :104)
        g = generate_run_report.TYPE_CONFIG[ctx.t]
        assert "RHAISTRAT-" not in g["child_parent_prefixes"]

    def test_predicates_and_rfe_only_keys_in_source(self, ctx):
        # rows: 82, 83, 84, 85, 86 —
        # generate_run_report.py:203-208,:212-213,:268-271,:352-354,:376-377
        source = read("scripts/generate_run_report.py")
        assert re.search(
            r'if entry_type == "rfe":\s+review_path = find_review_file\(artifacts_dir, item_id\)'
            r'\s+else:\s+review_path = os\.path\.join\(reviews_dir, f"\{item_id\}-review\.md"\)',
            source,
        )
        assert 'is_tracker_id = item_id.startswith(config["tracker_prefix"])' in source
        assert 'is_local_id = item_id.startswith(config["local_prefix"])' in source
        assert '"intermediary" if is_local_id and task_status == "Archived" else "leaf"' in source
        assert re.search(r'if entry_type == "rfe":\s+results\["retried"\]', source), (
            ":352-354 rfe-only"
        )
        assert re.search(r'if entry_type == "rfe":\s+report\["batch_size"\]', source), (
            ":376-377 rfe-only"
        )
        assert "Archived" in artifact_utils.SCHEMAS[ctx.task_schema]["status"]["enum"]


# ── generate_review_pdf.REPORT_CONFIG ─────────────────────────────────────────────────────────


class TestReviewPdfConfig:
    # rows: 101, 103, 104 (89-100, 102 MIGRATED)

    def test_pass_threshold_is_a_constant(self, ctx):
        # rows: 89-100 are MIGRATED; Q15: the passing total is scoring machinery — one module
        # constant for every type — and the '/10' denominators are 2 * len(score_fields)
        pin(
            "PASS_THRESHOLD constant (Q15)",
            "generate_review_pdf.PASS_THRESHOLD",
            PASS_THRESHOLD,
            generate_review_pdf.PASS_THRESHOLD,
        )
        assert generate_review_pdf.REPORT_CONFIG[ctx.t]["pass_threshold"] == PASS_THRESHOLD
        assert 2 * len(ctx.score_fields) == 10, "'/10' denominators == 2 * len(score_fields)"

    def test_parse_before_scores_default_map_is_rfe(self, ctx):
        # rows: 101 — generate_review_pdf.py:119-123 rfe default in a shared helper
        rfe = _ctx("rfe")
        assert 'name_map = REPORT_CONFIG["rfe"]["before_score_name_map"]' in read(
            "scripts/generate_review_pdf.py"
        )
        alias, key = next(iter(ctx.rep["before_score_name_map"].items()))
        with_map = generate_review_pdf.parse_before_scores(
            f"{alias} (0->1)", {key: 1}, ctx.rep["before_score_name_map"]
        )
        assert with_map[key] == 0
        by_default = generate_review_pdf.parse_before_scores(f"{alias} (0->1)", {key: 1})
        assert by_default[key] == (0 if alias in rfe.rep["before_score_name_map"] else 1)

    def test_split_child_and_vocabulary_sites(self, ctx):
        # rows: 103, 104 — generate_review_pdf.py:443-449, :1517-1535
        source = read("scripts/generate_review_pdf.py")
        assert re.search(
            r'parent_key\.startswith\(\s*\(config\["local_prefix"\], '
            r'config\["jira_prefix"\]\)\s*\)',
            source,
        )
        assert (
            'task_fm.get("status") == "Archived" and review_fm.get("recommendation") == "split"'
            in source
        )
        for verdict in ctx.labels["feasibility"]:
            assert f'if f == "{verdict}":' in source, f"feasibility_text lacks {verdict}"
        if "alignment" in ctx.dims:
            for verdict in artifact_utils.SCHEMAS[ctx.review_schema]["alignment"]["enum"]:
                assert f'if a == "{verdict}":' in source, f"alignment_text lacks {verdict}"
            assert (
                ctx.dims["alignment"]["skip_stub"]["result"]
                in artifact_utils.SCHEMAS[ctx.review_schema]["alignment"]["enum"]
            )
        assert 'has_alignment = "alignment" in config["extra_fields"]' in source


# ── pipeline_state.PIPELINE_TYPES and _build_phase_config ─────────────────────────────────────


class TestPipelineTypes:
    # rows: 108-124

    def test_pipeline_types(self, ctx):
        p = pipeline_state.PIPELINE_TYPES[ctx.t]
        prompts = ctx.pipe["prompts"]
        pin(
            "dirname(pipeline.prompts.review_rules)",
            "pipeline_state.py:98,:110",
            os.path.dirname(prompts["review_rules"]),
            p["review_prompts"],
        )
        pin(
            "dirname(pipeline.prompts.revise_rules)",
            "pipeline_state.py:98,:110",
            os.path.dirname(prompts["revise_rules"]),
            p["review_prompts"],
        )
        pin(
            "pipeline.prompts.split_rules",
            "pipeline_state.py:99,:111",
            prompts["split_rules"],
            p["split_prompt"],
        )
        pin(
            "pipeline.scorer_agent",
            "pipeline_state.py:100,:112",
            ctx.pipe["scorer_agent"],
            p["scorer_type"],
        )
        pin(
            "CONTEXT_DIR + pipeline.rubric.path",
            "pipeline_state.py:101,:113",
            f"{CONTEXT_DIR}/{ctx.pipe['rubric']['path']}",
            p["rubric_path"],
        )
        pin(
            "dimensions[feasibility].prompt",
            "pipeline_state.py:102,:114",
            ctx.dims["feasibility"]["prompt"],
            p["feasibility_skill"],
        )
        pin(
            "dimensions[alignment].prompt or None",
            "pipeline_state.py:103,:115",
            ctx.dims["alignment"]["prompt"] if "alignment" in ctx.dims else None,
            p["alignment_skill"],
        )
        pin("dirs.tasks", "pipeline_state.py:104,:116", ctx.dirs["tasks"], p["tasks_dir"])
        pin("dirs.reviews", "pipeline_state.py:105,:117", ctx.dirs["reviews"], p["reviews_dir"])
        pin(
            "(UNMAPPED) skill dir prefix + 'auto-fix'",
            "pipeline_state.py:106,:118",
            f".claude/skills/{ctx.sk}auto-fix/SKILL.md",
            p["dispatch_skill"],
        )
        pin("pipeline.poll_prefix", "pipeline_state.py:107,:119", ctx.pp, p["poll_prefix"])

    def test_setup_commands_from_context_sources(self, ctx):
        # rows: 117 — pipeline_state.py:225-235 (hardcoded list; context_sources is descriptive)
        cfg = pipeline_state._build_phase_config(ctx.t)
        expected = [
            f"bash {cs['bootstrap']} {' '.join(cs.get('args', []))}".rstrip()
            for cs in ctx.pipe["context_sources"]
        ]
        pin(
            "pipeline.context_sources[]",
            "pipeline_state.py:231-234",
            expected,
            cfg["SETUP"]["commands"],
        )
        assert expected == [
            f"bash scripts/bootstrap-assess-rfe.sh --type {ctx.t}",
            "bash scripts/fetch-architecture-context.sh",
        ]

    def test_assess_family(self, ctx):
        # rows: 110, 118 — pipeline_state.py:194-211,:236-245
        # (+REASSESS_ASSESS/SPLIT_ASSESS/SPLIT_REASSESS)
        cfg = pipeline_state._build_phase_config(ctx.t)
        review_dir = os.path.dirname(ctx.pipe["prompts"]["review_rules"])
        parallel = [
            {
                "prompt": d["prompt"],
                "poll_phase": f"{ctx.pp}{d['name']}",
                "vars": {"ID": "{ID}"},
                **({"condition": "has_rhaistrat_parent"} if "condition" in d else {}),
            }
            for d in ctx.pipe["dimensions"]
        ]
        for phase in ("ASSESS", "REASSESS_ASSESS", "SPLIT_ASSESS", "SPLIT_REASSESS"):
            pin(
                "pipeline.scorer_agent",
                f"pipeline_state {phase}.subagent_type",
                ctx.pipe["scorer_agent"],
                cfg[phase]["subagent_type"],
            )
            pin(
                "poll_prefix + 'assess'",
                f"pipeline_state {phase}.poll_phase",
                f"{ctx.pp}assess",
                cfg[phase]["poll_phase"],
            )
            pin(
                "review_prompts/assess-agent.md",
                f"pipeline_state {phase}.prompt",
                f"{review_dir}/assess-agent.md",
                cfg[phase]["prompt"],
            )
            pin(
                "CONTEXT_DIR + rubric.path",
                f"pipeline_state {phase}.vars.PROMPT_PATH",
                f"{CONTEXT_DIR}/{ctx.pipe['rubric']['path']}",
                cfg[phase]["vars"]["PROMPT_PATH"],
            )
            assert cfg[phase]["pre_script"] == "python3 scripts/prep_assess.py {ID}"
        for phase in ("ASSESS", "SPLIT_ASSESS"):
            pin(
                "pipeline.dimensions[] -> parallel",
                f"pipeline_state {phase}.parallel",
                parallel,
                cfg[phase]["parallel"],
            )
            assert cfg[phase]["parallel_timeout"] == 300
        assert cfg["FETCH"]["prompt"] == f"{review_dir}/fetch-agent.md"

    def test_review_revise_split_prompts_vars_and_type_flags(self, ctx):
        # rows: 119 — pipeline_state.py:246-420
        cfg = pipeline_state._build_phase_config(ctx.t)
        prompts, rv = ctx.pipe["prompts"], ctx.dirs["reviews"]
        for phase in ("REVIEW", "REASSESS_REVIEW", "SPLIT_REVIEW", "SPLIT_RE_REVIEW"):
            pin(
                "pipeline.prompts.review_rules",
                f"pipeline_state {phase}.prompt",
                prompts["review_rules"],
                cfg[phase]["prompt"],
            )
            pin(
                "dirs.reviews",
                f"pipeline_state {phase}.vars.FEASIBILITY_PATH",
                f"{rv}/{{ID}}-feasibility.md",
                cfg[phase]["vars"]["FEASIBILITY_PATH"],
            )
            assert ("ALIGNMENT_PATH" in cfg[phase]["vars"]) is ("alignment" in ctx.dims)
            if "alignment" in ctx.dims:
                pin(
                    "dirs.reviews",
                    f"pipeline_state {phase}.vars.ALIGNMENT_PATH",
                    f"{rv}/{{ID}}-alignment.md",
                    cfg[phase]["vars"]["ALIGNMENT_PATH"],
                )
            assert cfg[phase]["vars"]["ASSESS_PATH"] == f"{ASSESS_STAGING}/{{ID}}.result.md"
        for phase in ("REVISE", "REASSESS_REVISE", "SPLIT_REVISE"):
            pin(
                "pipeline.prompts.revise_rules",
                f"pipeline_state {phase}.prompt",
                prompts["revise_rules"],
                cfg[phase]["prompt"],
            )
        pin(
            "pipeline.prompts.split_rules",
            "pipeline_state SPLIT.prompt",
            prompts["split_rules"],
            cfg["SPLIT"]["prompt"],
        )
        pin(
            "dirs.tasks/dirs.reviews",
            "pipeline_state SPLIT.vars",
            {
                "ID": "{ID}",
                "TASK_FILE": f"{ctx.dirs['tasks']}/{{ID}}.md",
                "REVIEW_FILE": f"{rv}/{{ID}}-review.md",
            },
            cfg["SPLIT"]["vars"],
        )
        # every poll_phase is f"{poll_prefix}{fetch|assess|review|revise|split}"
        for name, phase in cfg.items():
            if "poll_phase" not in phase:
                continue
            base = next(
                (b for b in ("review", "revise", "assess") if name.endswith(b.upper())),
                name.lower(),
            )
            pin(
                "poll_prefix + phase base",
                f"pipeline_state {name}.poll_phase",
                f"{ctx.pp}{base}",
                phase["poll_phase"],
            )
            if "post_verify" in phase:
                assert phase["post_verify"].startswith(
                    f"python3 scripts/verify_phase.py --type {ctx.t} --phase {base} "
                )
        for name in ("FIXUP", "REASSESS_FIXUP", "SPLIT_FIXUP"):
            assert (
                cfg[name]["command"] == f"python3 scripts/check_revised.py --batch --type {ctx.t}"
            )
        for name, script in (
            ("REASSESS_SAVE", "reassess_save"),
            ("SPLIT_COLLECT", "split_collect"),
            ("ERROR_COLLECT", "error_collect"),
        ):
            assert cfg[name]["command"] == f"python3 scripts/{script}.py --type {ctx.t}"
        assert f"python3 scripts/generate_run_report.py --type {ctx.t}" in cfg["REPORT"]["command"]

    def test_alignment_condition_and_skip_stub(self, ctx, tmp_path, monkeypatch):
        # rows: 120, 121 — pipeline_state.py:127-151 (:144 predicate), :154-163 stub writer
        monkeypatch.chdir(tmp_path)
        if "alignment" not in ctx.dims:
            assert (
                ctx.t == "rfe"
                and pipeline_state._has_rhaistrat_parent("X", {"type": ctx.t}) is False
            )
            return
        cond = ctx.dims["alignment"]["condition"]
        pin(
            "dimensions[alignment].condition",
            "pipeline_state.py:143-144",
            {"frontmatter_field": "parent_key", "prefix": "RHAISTRAT-"},
            cond,
        )
        assert 'if condition == "has_rhaistrat_parent":' in read("scripts/pipeline_state.py")
        write(
            Path(ctx.dirs["tasks"]) / "A.md", fm({cond["frontmatter_field"]: f"{cond['prefix']}1"})
        )
        write(Path(ctx.dirs["tasks"]) / "B.md", fm({cond["frontmatter_field"]: f"{ctx.wp}1"}))
        assert pipeline_state._has_rhaistrat_parent("A", {"type": ctx.t}) is True
        assert pipeline_state._has_rhaistrat_parent("B", {"type": ctx.t}) is False
        assert pipeline_state._has_rhaistrat_parent("A", {"type": "rfe"}) is False
        pipeline_state._write_poll_stub(f"{ctx.pp}alignment", "A")
        stub_path = check_review_progress.PHASE_CHECKS[f"{ctx.pp}alignment"]("A")
        pin(
            "dirs.reviews + dimension name",
            "pipeline_state.py:158",
            f"{ctx.dirs['reviews']}/A-alignment.md",
            stub_path,
        )
        stub = yaml.safe_load(Path(stub_path).read_text().split("---")[1])
        pin(
            "dimensions[alignment].skip_stub",
            "pipeline_state.py:161-162",
            ctx.dims["alignment"]["skip_stub"],
            stub,
        )

    def test_save_originals_derives_the_originals_dir(self, ctx, tmp_path, monkeypatch):
        # rows: 122 — pipeline_state.py:552-570 (:562-563 str.replace derivation; PR-2 reads
        # dirs.originals)
        monkeypatch.chdir(tmp_path)
        ident = ctx.sample_ids[0]
        write(Path(ctx.dirs["tasks"]) / f"{ident}.md", fm({ctx.id_field: ident}))
        pipeline_state._save_originals([ident], ctx.t)
        pin(
            "dirs.originals",
            "pipeline_state.py:562-563",
            True,
            (Path(ctx.dirs["originals"]) / f"{ident}.md").exists(),
        )
        derived = (
            ctx.dirs["tasks"]
            .replace("rfe-tasks", "rfe-originals")
            .replace("initiatives", "initiative-originals")
        )
        pin(
            "dirs.originals (str.replace)",
            "pipeline_state.py:562-563",
            ctx.dirs["originals"],
            derived,
        )

    def test_valid_id_floor_accepts_both_grammars(self, ctx):
        # rows: 123 — pipeline_state.py:465,:482 type-neutral floor; registry owns grammar in PR-2
        assert pipeline_state._VALID_ID_RE.pattern == r"[A-Z][A-Z0-9]*-[0-9]+"
        for ident in ctx.sample_ids:
            assert pipeline_state._VALID_ID_RE.fullmatch(ident), ident
        assert re.fullmatch(ctx.ident["local_id_pattern"], ctx.sample_ids[0])


# ── check_review_progress.PHASE_CHECKS ────────────────────────────────────────────────────────


def _expected_phase_rows(ctx):
    rows = {
        f"{ctx.pp}fetch": f"{ctx.dirs['tasks']}/X.md",
        f"{ctx.pp}assess": f"{ASSESS_STAGING}/X.result.md",
    }
    for base in ("review", "revise"):
        rows[f"{ctx.pp}{base}"] = f"{ctx.dirs['reviews']}/X-review.md"
    rows[f"{ctx.pp}split"] = f"{ctx.dirs['reviews']}/X-split-status.yaml"
    for d in ctx.pipe["dimensions"]:
        rows[f"{ctx.pp}{d['name']}"] = f"{ctx.dirs['reviews']}/X-{d['name']}.md"
    if ctx.t == "rfe":
        rows["create"] = f"{ctx.dirs['tasks']}/X.md"  # #148: rfe-only create barrier
    return rows


class TestPhaseChecks:
    # rows: 125 (residue), 141, 142 (125-140 MIGRATED: PHASE_CHECKS and the check_id modes derive
    # from dirs x poll_prefix x pipeline.dimensions[].name since PR-2b;
    # tests/test_check_review_progress.py::TestDerivedFromRegistry holds the projection, today's
    # 14-key order and the four modes)

    def test_create_barrier_is_the_rfe_only_grandfather(self):
        # rows: 125 (residue) — check_review_progress._CREATE_BARRIER_TYPES: the PR #148 create
        # barrier is still a name-keyed literal (no descriptor fact says which pipeline polls one),
        # so PR-5's generic speedrun body inheriting it for every type is a visible pin change
        # here, not a silent descriptor edit
        assert check_review_progress._CREATE_BARRIER_TYPES == ("rfe",)
        assert "create" in check_review_progress.PHASE_CHECKS
        assert "initiative-create" not in check_review_progress.PHASE_CHECKS
        assert len(check_review_progress.PHASE_CHECKS) == 14

    def test_detect_fast_config_allowlist(self):
        # rows: 141 — check_review_progress.py:134-141; each ==
        # f"tmp/{state_prefix}{stage}-config.yaml";
        # tmp/initiative-speedrun-config.yaml is MISSING today (live drift; pin the current tuple)
        fn = next(
            n
            for n in ast.walk(ast.parse(read("scripts/check_review_progress.py")))
            if isinstance(n, ast.FunctionDef) and n.name == "_detect_fast"
        )
        loop = next(n for n in ast.walk(fn) if isinstance(n, ast.For))
        live = tuple(ast.literal_eval(loop.iter))
        expected = tuple(
            f"tmp/{_ctx(t).sp}{stage}-config.yaml" for t in TYPES for stage in STATE_STAGES
        )
        assert live == tuple(x for x in expected if x != "tmp/initiative-speedrun-config.yaml")
        assert "tmp/initiative-speedrun-config.yaml" not in live

    def test_phase_flag_choices_are_the_table_keys(self):
        # rows: 142 — check_review_progress.py:157,:163
        for flag in ("--phase", "--also-phase"):
            assert choices("scripts/check_review_progress.py", flag) == [
                "list(PHASE_CHECKS.keys())"
            ]


# ── verify_phase ──────────────────────────────────────────────────────────────────────────────


class TestVerifyPhase:
    # rows: 146 (143-145, 147 MIGRATED — both phase tables derive from the same descriptors since
    # PR-2b; tests/test_verify_phase.py::TestAgreesWithProgressChecker holds them equal per type)

    def test_error_stub_command_and_schema_acceptance(self, ctx, tmp_path, monkeypatch):
        # rows: 146 — verify_phase.py:105-127; the score tail is registry-derived since PR-2a;
        # pinned here: the fixed stub vocabulary, and that the review schema artifact_utils
        # derives from the same descriptor accepts the stub shape (validate_types gate 1 restates
        # this; design §3.3)
        monkeypatch.chdir(tmp_path)
        captured = []
        real_run = subprocess.run  # verify_phase shares the subprocess module object
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: captured.append(cmd))
        ident = ctx.sample_ids[1]
        ids = write(tmp_path / "ids.txt", f"{ident}\n")
        verify_phase.verify("review", str(ids), ctx.t)
        review_path = f"{ctx.dirs['reviews']}/{ident}-review.md"
        expected = [
            "python3",
            "scripts/frontmatter.py",
            "set",
            review_path,
            f"{ctx.id_field}={ident}",
            "error=review_failed",
            "score=0",
            "pass=false",
            "recommendation=revise",
            "feasibility=feasible",
            "auto_revised=false",
            "needs_attention=true",
            "needs_attention_reason=Agent failed: review_failed",
        ] + [f"scores.{f}=0" for f in ctx.score_fields]
        pin("id_field + score_fields error stub", "verify_phase.py:105-127", expected, captured[0])
        real_run(
            [sys.executable, str(REPO_ROOT / "scripts" / "frontmatter.py"), *captured[0][2:]],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        data, _ = artifact_utils.read_frontmatter_validated(
            str(tmp_path / review_path), ctx.review_schema
        )
        assert (
            data[ctx.id_field] == ident and data["score"] == 0 and data["needs_attention"] is True
        )
        assert data["scores"] == {f: 0 for f in ctx.score_fields}
        assert ids.read_text() == ""


# ── artifact_utils.SCHEMAS and helpers (residues) ─────────────────────────────────────────────


class TestArtifactSchemas:
    # rows: 172, 173, 175 (residue) (162-171, 174, 175 MIGRATED: SCHEMAS derives from the
    # descriptors and tests/test_schemas_golden.py pins the derived dicts byte for byte)

    def test_shared_enums(self, ctx):
        # rows: 172, 173 — artifact_utils._STATUS_ENUM / _RECOMMENDATION_ENUM / _FEASIBILITY_ENUM:
        # the task/review vocabularies shared by every type stay module constants (UNMAPPED);
        # the feasibility enum is deliberately NOT derived from conventions.labels.feasibility,
        # so the label map's key order is pinned equal to it
        task, review = (
            artifact_utils.SCHEMAS[ctx.task_schema],
            artifact_utils.SCHEMAS[ctx.review_schema],
        )
        assert task["status"]["enum"] == ["Draft", "Ready", "Submitted", "Archived"], (
            "shared constant"
        )
        assert review["recommendation"]["enum"] == [
            "submit",
            "revise",
            "split",
            "reject",
            "autorevise_reject",
        ]
        pin(
            "list(conventions.labels.feasibility)",
            "artifact_utils._FEASIBILITY_ENUM",
            list(ctx.labels["feasibility"]),
            review["feasibility"]["enum"],
        )

    def test_alignment_vocabulary_agrees_across_descriptor_fields(self, ctx):
        # rows: 175 (residue) — the review schema's alignment enum (schema.review.extra_fields,
        # derived) must admit every conventions.labels.alignment key (submit's alignment_labels)
        # and the dimension's skip_stub.result (what pipeline_state._write_poll_stub writes when
        # there is no RHAISTRAT parent); validate_types does not cross-check these fields
        review = artifact_utils.SCHEMAS[ctx.review_schema]
        if "alignment" not in ctx.labels:
            assert "alignment" not in review
            return
        enum = review["alignment"]["enum"]
        assert set(ctx.labels["alignment"]) <= set(enum)
        assert ctx.dims["alignment"]["skip_stub"]["result"] in enum
        assert review["alignment"]["default"] in enum


class TestArtifactHelpers:
    # rows: 177, 184, 185, 186 (residue) (179, 180, 182-187 MIGRATED: the generics take a
    # Descriptor and the lookups route via registry.detect(); tests/test_artifact_utils.py and
    # tests/test_frontmatter.py hold the projections)

    def test_companion_suffixes_are_type_agnostic(self, ctx):
        # rows: 177 — artifact_utils._is_companion_file; companions.comments=false (initiative)
        # must NOT remove -comments.md from the predicate nor from the rename generic's suffix
        # map (rename_to_tracker_key renames a -comments.md for every type)
        for suffix in ("-comments.md", "-removed-context.md", "-removed-context.yaml"):
            assert artifact_utils._is_companion_file(f"{ctx.sample_ids[0]}{suffix}")
        assert not artifact_utils._is_companion_file(f"{ctx.sample_ids[0]}.md")
        assert 'filename.endswith("-comments.md")' in inspect.getsource(
            artifact_utils.rename_to_tracker_key
        )
        assert ctx.d["companions"]["removed_context"] is True

    def test_rebuild_index_is_the_rfe_index(self):
        # rows: 186 (residue) — rebuild_index runs iff index.enabled (callers pinned via
        # submit.has_index / split_submit.do_rebuild_index) and reads the rfe id_field from the
        # registry since PR-2b; the index contract itself (file name, heading, columns) has no
        # descriptor field and stays literal
        rfe = _ctx("rfe")
        source = inspect.getsource(artifact_utils.rebuild_index)
        assert rfe.d["index"]["enabled"] and not _ctx("initiative").d["index"]["enabled"]
        for literal in (
            '"rfes.md"',
            '"# RFE Summary"',
            '"| ID | Title | Priority | Size | Score | Rec | Status |"',
        ):
            assert literal in source, literal
        assert "scan_task_files(" in source and "scan_review_files(" in source
        assert "Size" in source and "size" in rfe.schema["task"]["extra_fields"]

    def test_name_keyed_residues_until_pr3(self):
        # rows: 184, 185 (residue) — three artifact_utils projections are keyed on the type NAME
        # because no descriptor fact expresses them (the same kind of grandfather as
        # check_review_progress._CREATE_BARRIER_TYPES): the rename ValueError label, rfe's
        # slug-tolerant review lookup and parse_child's rfe markdown fallbacks. PR-3's
        # resolution ladder generalises or retires them, so lifting one is a visible pin change
        # here, not a silent edit; tests/test_artifact_utils.py holds their behaviour
        rfe, init = REG.get("rfe"), REG.get("initiative")
        assert artifact_utils._rename_error_label(rfe) == "rename_to_jira_key"
        assert artifact_utils._rename_error_label(init) == "rename_initiative_to_jira_key"
        for fn in (
            artifact_utils._rename_error_label,
            artifact_utils._review_to_rename,
            artifact_utils.parse_child,
        ):
            assert 'desc.name == "rfe"' in inspect.getsource(fn), fn.__name__


# ── skill layer (grep pins until PR-5 lifts the bodies) ───────────────────────────────────────


class TestSkillLayer:
    # rows: 189-195, 216-222, 224-232

    def test_stage_skills_and_prompt_files_exist(self, ctx):
        # rows: 189, 190 — .claude/skills/{rfe.*,initiative-*}; prompts.* point at live files (Q1)
        assert ctx.pipe["stages"] == ["create", "review", "submit", "split", "auto-fix", "speedrun"]
        for stage in ctx.pipe["stages"]:
            assert (REPO_ROOT / f".claude/skills/{ctx.sk}{stage}/SKILL.md").is_file(), stage
        for key, path in ctx.pipe["prompts"].items():
            assert (REPO_ROOT / path).is_file(), f"pipeline.prompts.{key}"
        for d in ctx.pipe["dimensions"]:
            assert (REPO_ROOT / d["prompt"]).is_file(), f"dimensions[{d['name']}].prompt"
        assert (REPO_ROOT / f".claude/skills/{ctx.sk}create/{ctx.t}-template.md").is_file()

    def test_scorer_literal_sites(self, ctx):
        # rows: 191 — rfe.review/SKILL.md:82,:224; assess-agent.md:12; auto-fix:122; speedrun:37
        # (+twins)
        scorer = ctx.pipe["scorer_agent"]
        other = next(_ctx(o).pipe["scorer_agent"] for o in TYPES if o != ctx.t)
        assert skill(ctx.t, "review").count(f"subagent_type: {scorer}") == 2
        assert f"subagent_type: {scorer}" in skill(ctx.t, "review", "prompts/assess-agent.md")
        assert f"subagent_type: {scorer}" in skill(ctx.t, "auto-fix")
        assert f"`{scorer}`" in skill(ctx.t, "speedrun")
        for stage in ("review", "auto-fix", "speedrun"):
            assert other not in skill(ctx.t, stage), (
                f"{ctx.t} {stage} names the other type's scorer"
            )

    def test_rubric_path_sites(self, ctx):
        # rows: 192 — initiative sites == CONTEXT_DIR + rubric.path; the four rfe sites are STALE
        # (design §10 PR-5) and are pinned as such so the fix is a visible pin change
        live = f"{CONTEXT_DIR}/{ctx.pipe['rubric']['path']}"
        sites = [
            (skill(ctx.t, "review"), 2),
            (skill(ctx.t, "auto-fix"), 1),
            (skill(ctx.t, "split", "prompts/split-agent.md"), 1),
        ]
        if ctx.t == "initiative":
            for text, n in sites:
                assert text.count(live) == n
        else:
            assert STALE_RFE_RUBRIC_PATH != live
            for text, n in sites:
                assert text.count(STALE_RFE_RUBRIC_PATH) == n and live not in text

    def test_bootstrap_script_text(self, ctx):
        # rows: 193, 194 — bootstrap-assess-rfe.sh:31-37 case arms, :44-52 rubric source, :95-100,
        # :103
        sh = read("scripts/bootstrap-assess-rfe.sh")
        assert f"  {' | '.join(TYPES)}) ;;" in sh, "case arm == registry choices"
        assert f"(expected {' or '.join(TYPES)})" in sh
        rubric = ctx.pipe["rubric"]
        assert f"ASSESS_RFE_REPO:-{rubric['repo']}" in sh
        var = "RUBRIC_FILE" if ctx.t == "rfe" else "INITIATIVE_RUBRIC"
        assert f'{var}="$CONTEXT_DIR/{rubric["path"]}"' in sh
        assert f'CONTEXT_DIR="{CONTEXT_DIR}"' in sh
        if ctx.t == "initiative":
            assert f'INITIATIVE_AGENT="{ctx.pipe["scorer_agent"]}.md"' in sh
        assert rubric["ref"] not in sh, "Q9: rubric.ref is documentary — the bootstrap pins no ref"
        assert re.fullmatch(r"[0-9a-f]{7,40}", rubric["ref"])
        assert "skills/export-rubric/scripts/export_rubric.py" in sh
        if rubric["export"] is None:
            assert "initiative-rubric" not in sh
        else:
            assert rubric["export"] == "artifacts/rfe-rubric.md"
            assert os.path.basename(rubric["export"]) in read("AGENTS.md")
            assert rubric["export"] in skill(ctx.t, "create")

    def test_architecture_context_source_is_type_neutral(self, ctx):
        # rows: 195 — fetch-architecture-context.sh:5-28; SETUP command 2; rfe.review:63 /
        # initiative-review:63
        cs = ctx.pipe["context_sources"][1]
        assert cs["bootstrap"] == "scripts/fetch-architecture-context.sh" and "args" not in cs
        assert "--type" not in read(cs["bootstrap"])
        assert f"bash {cs['bootstrap']}" in skill(ctx.t, "review")

    def test_tmp_state_and_poll_file_names(self, ctx):
        # rows: 216 — config/ids files are f"tmp/{state_prefix}{stage}-..."; interactive poll files
        # use
        # a second prefix the descriptor cannot express (UNMAPPED, pinned as POLL_FILE_PREFIX)
        texts = "\n".join(skill(ctx.t, st) for st in ("review", "split", "speedrun", "auto-fix"))
        configs = set(re.findall(r"tmp/[a-z-]*-config\.yaml", texts))
        ids = set(re.findall(r"tmp/[a-z-]*-all-ids\.txt", texts))
        allowed_cfg = {f"tmp/{ctx.sp}{st}-config.yaml" for st in STATE_STAGES}
        allowed_ids = {f"tmp/{ctx.sp}{st}-all-ids.txt" for st in STATE_STAGES} | {
            "tmp/pipeline-all-ids.txt"
        }
        pin(
            "pipeline.state_prefix + stage",
            "skills tmp/*-config.yaml",
            set(),
            configs - allowed_cfg,
        )
        pin("pipeline.state_prefix + stage", "skills tmp/*-all-ids.txt", set(), ids - allowed_ids)
        polls = set(re.findall(r"tmp/[a-z]+-poll-[a-z-]+", texts))
        assert polls and all(p.startswith(POLL_FILE_PREFIX[ctx.t]) for p in polls), polls

    def test_phase_barrier_names(self, ctx):
        # rows: 217 — every --phase literal == f"{poll_prefix}{phase}" and is a PHASE_CHECKS key;
        # no initiative-create barrier (asymmetry kept visible)
        texts = "\n".join(skill(ctx.t, st) for st in ("review", "split", "speedrun"))
        used = set(re.findall(r"--phase ([a-z-]+)", texts))
        expected = set(_expected_phase_rows(ctx))
        assert used and used <= expected, used - expected
        assert ("create" in used) is (ctx.t == "rfe")
        assert "--phase initiative-create" not in texts

    def test_score_field_lists_in_skill_stubs(self, ctx):
        # rows: 218 — rfe.review/SKILL.md:53; review-agent.md:52-56,:62-64 (+ initiative twins
        # :53,:134)
        texts = [skill(ctx.t, "review"), skill(ctx.t, "review", "prompts/review-agent.md")]
        lines = [ln for text in texts for ln in text.splitlines() if "scores." in ln]
        assert lines
        for line in lines:
            named = set(re.findall(r"(?<![\w.])(?:before_)?scores\.(\w+)=", line))
            if named:
                pin(
                    "schema.review.score_fields",
                    "skill frontmatter-set stubs",
                    set(ctx.score_fields),
                    named,
                )
        assert f"{ctx.id_field}=<ID> score=0" in texts[0]
        assert re.search(r"needs_attention=true (scores\.\w+=0 ){5}", texts[0])

    def test_schema_literals_and_rebuild_index_calls(self, ctx):
        # rows: 219 — rfe.create:81; fetch-agent:13; review-agent:17; initiative-create:58;
        # review-agent:20;
        # rebuild-index invoked iff index.enabled
        assert f"frontmatter.py schema {ctx.task_schema}" in skill(ctx.t, "create")
        assert f"frontmatter.py schema {ctx.review_schema}" in skill(
            ctx.t, "review", "prompts/review-agent.md"
        )
        texts = "\n".join(skill(ctx.t, st) for st in ctx.pipe["stages"])
        pin(
            "index.enabled",
            "skills rebuild-index calls",
            ctx.d["index"]["enabled"],
            "frontmatter.py rebuild-index" in texts,
        )

    def test_next_rfe_id_invocations(self, ctx):
        # rows: 220 — rfe.create:73; rfe.speedrun:79; split-agent:105 (rfe defaults); initiative
        # twins
        # pass --prefix INIT --dir artifacts/initiatives
        texts = [
            skill(ctx.t, "create"),
            skill(ctx.t, "speedrun"),
            skill(ctx.t, "split", "prompts/split-agent.md"),
        ]
        calls = [ln for text in texts for ln in text.splitlines() if "scripts/next_rfe_id.py" in ln]
        assert len(calls) == 3, calls
        flags = f"--prefix {ctx.lp.rstrip('-')} --dir {ctx.dirs['tasks']}"
        for call in calls:
            if ctx.t == "rfe":
                assert "--prefix" not in call and "--dir" not in call, call
            else:
                assert flags in call, call

    def test_submit_skill_label_table(self, ctx):
        # rows: 221 — rfe.submit/SKILL.md:46-56; initiative-submit:45-58 (tables omit ignore,
        # split_quarantine and the split_child_marker template — documentation gap, pinned as is)
        documented = set(re.findall(r"^\| `([a-z-]+)` \|", skill(ctx.t, "submit"), re.M))
        keys = (
            "auto_created",
            "auto_revised",
            "split_original",
            "split_result",
            "needs_attention",
            "rubric_pass",
        )
        expected = (
            {ctx.labels[k] for k in keys}
            | set(ctx.labels["feasibility"].values())
            | set(ctx.labels.get("alignment", {}).values())
        )
        pin(
            "conventions.labels (documented subset)",
            f"{ctx.sk}submit/SKILL.md label table",
            expected,
            documented,
        )
        assert (
            ctx.labels["ignore"] not in documented
            and ctx.labels["split_quarantine"] not in documented
        )

    def test_type_flag_pass_through(self, ctx):
        # rows: 222 — every '--type X' literal in the type's skills names this type; rfe twins omit
        # the flag
        texts = "\n".join(skill(ctx.t, st) for st in ctx.pipe["stages"]) + skill(
            ctx.t, "split", "prompts/split-agent.md"
        )
        flags = re.findall(r"--type ([a-z]+)", texts)
        if ctx.t == "rfe":
            assert flags == []
        else:
            assert flags and set(flags) == {ctx.d["type"]}

    def test_run_report_filename_literals(self, ctx):
        # rows: 224 — rfe.speedrun:215-217; initiative-speedrun:213; eval.yaml:130,:135;
        # eval-initiative.yaml:130
        rp = ctx.snap["report_prefix"]
        assert f"artifacts/auto-fix-runs/{rp}<timestamp>.yaml" in skill(ctx.t, "speedrun")
        outputs = {o["path"]: o for o in eval_config(ctx)["outputs"]}
        assert f"{rp}YYYYMMDD-HHMMSS.yaml" in outputs["artifacts/auto-fix-runs"]["schema"]
        html = f"artifacts/{generate_review_pdf.REPORT_CONFIG[ctx.t]['default_output']}"
        assert (html in outputs) is (ctx.t == "rfe")  # UNMAPPED shape difference, pinned as is
        assert ("<timestamp>-report.html" in skill(ctx.t, "speedrun")) is (ctx.t == "rfe")

    def test_alignment_dimension_skill_text(self, ctx):
        # rows: 225 — initiative-review/SKILL.md:94-102,:117-129;
        # strategic-alignment-review/SKILL.md:25-73
        if "alignment" not in ctx.dims:
            assert "alignment" not in skill(ctx.t, "review").lower()
            return
        dim = ctx.dims["alignment"]
        review, prompt = skill(ctx.t, "review"), read(dim["prompt"])
        assert f"matches `{dim['condition']['prefix']}*`" in review
        assert "informational, not blocking" in review and dim["blocking"] is False
        assert f"**Alignment**: {dim['skip_stub']['result']}" in prompt
        pin(
            "conventions.labels.alignment keys",
            f"{dim['prompt']} verdicts",
            set(ctx.labels["alignment"]),
            set(re.findall(r"^- \*\*(\w+)\*\*:", prompt, re.M)),
        )

    def test_fetch_agent_companions(self, ctx):
        # rows: 226 — rfe.review/prompts/fetch-agent.md:14,:16,:26;
        # initiative-review/prompts/fetch-agent.md:10,:24
        text = skill(ctx.t, "review", "prompts/fetch-agent.md")
        pin(
            "companions.comments",
            "fetch-agent.md",
            ctx.d["companions"]["comments"],
            f"{ctx.dirs['tasks']}/{{KEY}}-comments.md" in text,
        )
        assert ("-comments.md" in text) is ctx.d["companions"]["comments"]
        assert f"{ctx.id_field}={{KEY}}" in text

    def test_resplit_rules_in_split_skills(self, ctx):
        # rows: 227 — rfe.split/SKILL.md:115 uses resplit {right_sized, 2}; initiative-split:115
        # re-splits
        # on recommendation=split (skill layer diverges — pinned as is, design §1)
        text = skill(ctx.t, "split")
        resplit = ctx.pipe["resplit"]
        if ctx.t == "rfe":
            assert f"below {resplit['below']}/2 on `scores.{resplit['score_field']}`" in text
        else:
            assert "recommendation=split" in text and f"scores.{resplit['score_field']}" not in text

    def test_id_grammar_prose(self, ctx):
        # rows: 228 — review:15, split:14, speedrun:18,:30
        grammar = f"({ctx.wp}NNNN or {ctx.lp}NNN)"
        assert grammar in skill(ctx.t, "review") and grammar in skill(ctx.t, "split")
        assert skill(ctx.t, "speedrun").count(f"({ctx.wp}NNNN)") == 2

    def test_create_skill_rubric_and_size_guide(self, ctx):
        # rows: 229 — rfe.create/SKILL.md:21-34,:61; initiative-create has no rubric step
        text = skill(ctx.t, "create")
        export = ctx.pipe["rubric"]["export"]
        if export is None:
            assert "rubric" not in text.lower()
        else:
            assert export in text
            sizes = re.findall(r"\b([A-Z]{1,2}) \(\d+-?\d*\+?\)", text)
            pin(
                "schema.task.extra_fields.size.enum",
                f"{ctx.sk}create/SKILL.md:61",
                ctx.schema["task"]["extra_fields"]["size"]["enum"],
                sizes,
            )

    def test_feasibility_dimension_io(self, ctx):
        # rows: 231 — rfe-feasibility-review/SKILL.md:13,:56 and the initiative twin
        text = read(ctx.dims["feasibility"]["prompt"])
        assert f"`{ctx.dirs['reviews']}/{{ID}}-feasibility.md`" in text
        assert f"`{ctx.dirs['tasks']}/{{ID}}.md`" in text
        assert ("-comments.md" in text) is ctx.d["companions"]["comments"]
        assert f"{ctx.dirs['reviews']}/X-feasibility.md" == check_review_progress.PHASE_CHECKS[
            f"{ctx.pp}feasibility"
        ]("X")

    def test_speedrun_batch_format_and_barrier(self, ctx):
        # rows: 232 — rfe.speedrun/SKILL.md:48-106; initiative-speedrun/SKILL.md:49-91
        text = skill(ctx.t, "speedrun")
        block = re.search(r"\*\*Mode A \(Batch YAML\)\*\*.*?```yaml\n(.*?)```", text, re.S).group(1)
        known = {"prompt", "priority", "labels", "clarifying_context"} | set(
            ctx.d["batch"]["extra_fields"]
        )
        used = {k for entry in yaml.safe_load(block) for k in entry}
        assert used <= known, used - known
        assert set(ctx.d["batch"]["extra_fields"]) <= used
        flag = " --type initiative" if ctx.t == "initiative" else ""
        assert f"validate_batch_input.py <input_file>{flag} --strict" in text
        assert ("--phase create" in text) is (
            "create" in check_review_progress.PHASE_CHECKS and ctx.t == "rfe"
        )


# ── eval configs ──────────────────────────────────────────────────────────────────────────────


class TestEvalConfigs:
    # rows: 199-212

    def test_top_level(self, ctx):
        # rows: 199 — eval.yaml:1-36 / eval-initiative.yaml:1-36
        ev = eval_config(ctx)
        pin("eval.dataset", f"{ctx.ev['config']}:36", ctx.ev["dataset"], ev["dataset"]["path"])
        pin(
            "eval.mlflow_experiment",
            f"{ctx.ev['config']}:33",
            ctx.ev["mlflow_experiment"],
            ev["mlflow"]["experiment"],
        )
        pin("eval.timeout", f"{ctx.ev['config']}:9", ctx.ev["timeout"], ev["execution"]["timeout"])
        pin(
            "(UNMAPPED) skill dir prefix + speedrun",
            f"{ctx.ev['config']}:7",
            f"{ctx.sk}speedrun",
            ev["execution"]["skill"],
        )
        assert (REPO_ROOT / ctx.ev["dataset"]).is_dir()

    def test_dataset_annotations_prose(self, ctx):
        # rows: 200 — eval.yaml:37-57; eval-initiative.yaml:37-58 (prose, not machine-parsed)
        prose = eval_config(ctx)["dataset"]["schema"]
        assert "/".join(ctx.score_fields) in re.sub(r"\s+", "", prose)
        for extra in ctx.ev["annotations_extra"]:
            assert f"- {extra} (" in prose, extra
        assert ("expected_alignment" in prose) is ("alignment" in ctx.dims)
        assert ("score_tolerance" in prose) is (ctx.t == "rfe")  # rfe-only, no consumer

    def test_outputs(self, ctx):
        # rows: 201 — eval.yaml:86-139; eval-initiative.yaml:87-134
        outputs = eval_config(ctx)["outputs"]
        pin(
            "dirs.tasks/reviews/originals",
            f"{ctx.ev['config']} outputs[0-2].path",
            [ctx.dirs["tasks"], ctx.dirs["reviews"], ctx.dirs["originals"]],
            [o["path"] for o in outputs[:3]],
        )
        for o in outputs:
            if o["batch_pattern"] != "*":
                pin(
                    "identity.local_prefix + {n:03d}",
                    f"{ctx.ev['config']} batch_pattern",
                    f"{ctx.lp}{{n:03d}}",
                    o["batch_pattern"],
                )
        runs = next(o for o in outputs if o["path"] == "artifacts/auto-fix-runs")
        assert (
            ctx.rep["item_key"] in runs["schema"]
            and f"{ctx.snap['report_prefix']}YYYYMMDD-HHMMSS.yaml" in runs["schema"]
        )
        assert ("{ID}-alignment.md" in outputs[1]["schema"]) is ("alignment" in ctx.dims)
        assert ("{ID}-split-status.yaml" in outputs[1]["schema"]) is (ctx.t == "rfe")

    def test_files_exist_judge(self, ctx):
        # rows: 202 — eval.yaml:150-162; eval-initiative.yaml:145-157
        check = judges(ctx)["files_exist"]["check"]
        assert f'k.startswith("{ctx.dirs["tasks"]}/")' in check
        assert f'k.startswith("{ctx.dirs["reviews"]}/")' in check
        assert ctx.ev["thresholds"]["files_exist"] == {"min_pass_rate": 1.0}

    def test_frontmatter_valid_judge(self, ctx):
        # rows: 203 — eval.yaml:164-212; eval-initiative.yaml:159-212
        check = judges(ctx)["frontmatter_valid"]["check"]
        assert (
            f"for req in ['{ctx.id_field}', 'score', 'pass', 'recommendation', 'scores']:" in check
        )
        assert f"criteria = {ctx.score_fields!r}" in check
        assert (
            f"valid_recs = {artifact_utils.SCHEMAS[ctx.review_schema]['recommendation']['enum']!r}"
            in check
        )
        if "alignment" in ctx.dims:
            assert (
                "valid_alignments = "
                f"{artifact_utils.SCHEMAS[ctx.review_schema]['alignment']['enum']!r}" in check
            )
        assert f"total >= {PASS_THRESHOLD}" in check

    def test_run_report_exists_judge(self, ctx):
        # rows: 204 — eval.yaml:214-240; the snapshot exclusion literal is the rfe prefix in BOTH
        # files
        # (initiative copy does not exclude initiative-snapshot- — latent drift, pinned visibly)
        check = judges(ctx)["run_report_exists"]["check"]
        assert f"for req in ['run_id', 'started', 'completed', '{ctx.rep['item_key']}']:" in check
        rfe_stem = _ctx("rfe").snap["prefix"].rstrip("-")
        assert f'"{rfe_stem}" not in k' in check
        assert "initiative-snapshot" not in check

    def test_recommendation_consistency_extra_rule(self, ctx):
        # rows: 205 — eval-initiative.yaml:277-278 == schema.review.extra_rules[0] (Q22: unpinned as
        # a
        # descriptor value, but the judge line is derived from it here)
        check = judges(ctx)["recommendation_consistency"]["check"]
        rules = ctx.schema["review"]["extra_rules"]
        for rule in rules:
            when, then = rule["when"], rule["then"]
            line = f"if {when['field']} == '{when['equals']}' and not fm.get('{then}'):"
            assert line in check, line
        assert ("alignment" in check) is bool(rules)

    def test_pipeline_flow_judge(self, ctx):
        # rows: 206 — eval.yaml:641 rm-check literal (absent in the initiative copy); phase markers
        # use skill names
        check = judges(ctx)["pipeline_flow"]["check"]
        assert (f'"rm {ctx.dirs["tasks"]}/" in stdout' in check) is (ctx.t == "rfe")
        assert f'"{ctx.sk}create"' in check
        if ctx.t == "initiative":
            assert f'"{ctx.sk}auto-fix"' in check

    def test_architecture_context_judge(self, ctx):
        # rows: 207 — eval.yaml:654-750; eval-initiative.yaml:649-756 (not_relevant carve-out
        # initiative-only)
        check = judges(ctx)["architecture_context_used"]["check"]
        assert ("not_relevant" in check) is (ctx.t == "initiative")
        assert ctx.ev["thresholds"]["architecture_context_used"]["min_pass_rate"] == (
            1.0 if ctx.t == "rfe" else 0.85
        )

    def test_quality_judges(self, ctx):
        # rows: 208 — eval.yaml:754-837; eval-initiative.yaml:760-864
        js = judges(ctx)
        name = f"{ctx.t}_quality"
        assert name in js and "revision_quality" in js
        assert ctx.ev["thresholds"][name] == {"min_mean": 3.5} and ctx.ev["thresholds"][
            "revision_quality"
        ] == {"min_mean": 3.5}
        assert "scores." + "/".join(ctx.score_fields) in js[name]["prompt"]
        for key in ("originals", "tasks", "reviews"):
            assert f"{ctx.dirs[key]}/" in js["revision_quality"]["prompt"], key

    def test_pairwise_prompt_file_exists(self, ctx):
        # rows: 209 — no descriptor field; literals eval/config/{,initiative-}pairwise-judge.md
        assert (REPO_ROOT / judges(ctx)["pairwise"]["prompt_file"]).is_file()

    def test_thresholds_verbatim(self, ctx):
        # rows: 210 — eval.yaml:845-878; eval-initiative.yaml:872-904 (AUTHORITATIVE from PR-4,
        # design §4.5)
        pin(
            "eval.thresholds",
            f"{ctx.ev['config']} thresholds",
            ctx.ev["thresholds"],
            eval_config(ctx)["thresholds"],
        )
        assert len(ctx.ev["thresholds"]) == 10
        assert ctx.ev["thresholds"]["revision_coverage"] == {"min_pass_rate": 0.92}

    def test_shared_two_type_judges(self, ctx):
        # rows: 211 — revision_flag_consistency / revision_coverage carry two-type literal unions
        js = judges(ctx)
        item_keys = "(" + ", ".join(f'"{_ctx(t).rep["item_key"]}"' for t in TYPES) + ")"
        id_fields = " or ".join(f'fm.get("{_ctx(t).id_field}")' for t in TYPES)
        for name in ("revision_flag_consistency", "revision_coverage"):
            check = js[name]["check"]
            assert f"for key in {item_keys}:" in check, name
            assert f"item_id = {id_fields}" in check, name
            assert '"-snapshot-" in os.path.basename(k)' in check, name
        for t in TYPES:
            assert "-snapshot-" in _ctx(t).snap["prefix"]

    def test_workflow_eval_config_options(self):
        # rows: 212 — .github/workflows/rfe-creator-eval.yml:25-35
        wf = yaml.safe_load(read(".github/workflows/rfe-creator-eval.yml"))
        options = wf[True]["workflow_dispatch"]["inputs"]["eval_config"]["options"]
        pin(
            "[eval.config for t in types]",
            "rfe-creator-eval.yml:25-35",
            [_ctx(t).ev["config"] for t in TYPES],
            options,
        )


# ── manifests and docs that restate descriptor values ────────────────────────────────────────


class TestManifestsAndDocs:
    # rows: 197, 214, 230

    def test_ambient_results_map(self):
        # rows: 197 — .ambient/ambient.json:4-13
        rfe, init = _ctx("rfe"), _ctx("initiative")
        expected = {
            "RFE Index": "artifacts/rfes.md",
            "RFE Tasks": f"{rfe.dirs['tasks']}/*.md",
            "RFE Reviews": f"{rfe.dirs['reviews']}/*.md",
            "Initiative Tasks": f"{init.dirs['tasks']}/*.md",
            "Initiative Reviews": f"{init.dirs['reviews']}/*.md",
        }
        pin(
            "dirs + index.enabled",
            ".ambient/ambient.json:4-13",
            expected,
            yaml.safe_load(read(".ambient/ambient.json"))["results"],
        )
        assert rfe.d["index"]["enabled"]

    def test_agents_md_jira_field_mappings(self, ctx):
        # rows: 214 — AGENTS.md:119-133 (prose; 'Initiative (id: 10103)' is unused by code)
        text = read("AGENTS.md")
        assert f"- **Project**: `{ctx.jira['project']}`" in text
        assert f"- **Issue Type**: `{ctx.jira['issue_type']}`" in text
        assert (
            "- **Priority values** (use these exactly): "
            + ", ".join(ctx.schema["task"]["priority"]["enum"])
            in text
        )
        if ctx.t == "initiative":
            assert f"`scripts/submit.py --type {ctx.t}`" in text

    def test_update_deps_and_gitignore_cover_the_vendored_set(self):
        # rows: 230 — rfe-creator.update-deps/SKILL.md; .gitignore vendored list (PR1-22)
        expected = {CONTEXT_DIR, ".claude/skills/export-rubric"}
        for t in TYPES:
            c = _ctx(t)
            expected.add(f".claude/skills/{Path(c.pipe['rubric']['path']).parts[1]}")
            expected.add(f".claude/agents/{c.pipe['scorer_agent']}.md")
        text = read(".claude/skills/rfe-creator.update-deps/SKILL.md")
        rm_block = re.search(
            r"rm -rf \.context/assess-rfe(.*?)\nbash scripts/bootstrap-assess-rfe\.sh", text, re.S
        ).group(0)
        removed = set(re.findall(r"(\.(?:context|claude)/\S+)", rm_block))
        pin(
            "rubric.path skill dirs + scorer agents",
            "rfe-creator.update-deps/SKILL.md",
            expected,
            removed,
        )
        ignored = {ln.rstrip("/") for ln in read(".gitignore").splitlines()}
        assert expected - {CONTEXT_DIR} <= ignored, expected - ignored


# ── published cross-pipeline contract strings (R7, PR1-29) ────────────────────────────────────

# These change only with a downstream migration note (strat-creator string-matches them).
PUBLISHED_CONTRACTS = {
    "rfe": {
        "rubric_pass": "rfe-creator-autofix-rubric-pass",
        "comment_prefix": "[RFE Creator]",
        "removed_context_preamble": (
            "*[RFE Creator]* The following technical implementation details were removed from the "
            "RFE description during review. This content is better suited for a RHAISTRAT and is "
            "preserved here for reference:"
        ),
    },
    "initiative": {
        "rubric_pass": "initiative-autofix-rubric-pass",
        "comment_prefix": "[Initiative Creator]",
        "removed_context_preamble": (
            "*[Initiative Creator]* The following technical implementation details were removed "
            "from the Initiative description during review. This content may be useful as strategy "
            "context and is preserved here for reference:"
        ),
    },
}


class TestPublishedContracts:
    # rows: 12, 15, 16 (literal pins)

    def test_literals(self, ctx):
        expected = PUBLISHED_CONTRACTS[ctx.t]
        pin(
            "conventions.labels.rubric_pass (R7)",
            "submit.py:77,:108",
            expected["rubric_pass"],
            ctx.labels["rubric_pass"],
        )
        pin(
            "conventions.comment_prefix (R7)",
            "submit.py:90,:125",
            expected["comment_prefix"],
            ctx.conv["comment_prefix"],
        )
        pin(
            "conventions.removed_context_preamble (R7)",
            "submit.py:84-89,:119-124",
            expected["removed_context_preamble"],
            ctx.conv["removed_context_preamble"],
        )
        assert ctx.conv["removed_context_preamble"].startswith(f"*{ctx.conv['comment_prefix']}* ")


# ── grandfathered projections, spelled out ────────────────────────────────────────────────────


class TestGrandfathered:
    # rows: 9, 49, 50, 54, 74, 94, 116, 158

    def test_rfe_empty_prefixes_and_initiative_non_empty(self):
        rfe, init = _ctx("rfe"), _ctx("initiative")
        assert (rfe.pp, rfe.sp, rfe.snap["report_prefix"]) == ("", "", "")
        assert (init.pp, init.sp, init.snap["report_prefix"]) == (
            "initiative-",
            "initiative-",
            "initiative-run-",
        )
        assert (
            rfe.snap["prefix"] == "issue-snapshot-"
            and init.snap["prefix"] == "initiative-snapshot-"
        )
        assert submit.TYPE_CONFIGS["rfe"]["snapshot_prefix"] == "" != rfe.snap["prefix"]
        assert submit.TYPE_CONFIGS["initiative"]["snapshot_prefix"] == init.snap["prefix"]


# ── the acceptance test of the contract: rfe <-> initiative diff == extension points (PR1-32) ─

EXTENSION_POINTS = [
    r"^type$",
    r"^display\.",
    r"^identity\.jira\.(project|issue_type|key_prefixes)",
    r"^identity\.(local_prefix|local_id_pattern|id_field)$",
    r"^dirs\.",
    r"^index\.enabled$",
    r"^companions\.comments$",
    r"^batch\.extra_fields",
    r"^snapshot\.(prefix|report_prefix)$",
    r"^conventions\.(type_label|label_prefix|comment_prefix|removed_context_preamble|query_default|parent_key_patterns)",
    r"^conventions\.labels\.",
    r"^schema\.task\.extra_fields",
    r"^schema\.review\.(score_fields|extra_fields|extra_rules)",
    r"^pipeline\.(poll_prefix|state_prefix|scorer_agent)$",
    r"^pipeline\.prompts\.",
    r"^pipeline\.dimensions",
    r"^pipeline\.rubric\.(ref|path|export)$",
    r"^pipeline\.context_sources\.\d+\.args",
    r"^reporting\.(item_key|criterion_labels|criterion_short_labels|before_score_name_map|pdf\.extra_fields|run_report\.extra_entry_fields)",
    r"^eval\.",
]
SHARED_MACHINERY = [
    "schema_version",
    "kind",
    "identity.tracker",
    "identity.jira.split_link_type",
    "identity.jira.state_map",
    "companions.removed_context",
    "schema.task.priority",
    "pipeline.stages",
    "pipeline.resplit",
    "pipeline.rubric.repo",
]


def _flatten(node, prefix=""):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            out.update(_flatten(v, f"{prefix}{k}."))
        return out
    if isinstance(node, list) and node and all(isinstance(x, (dict, list)) for x in node):
        out = {}
        for i, v in enumerate(node):
            out.update(_flatten(v, f"{prefix}{i}."))
        return out
    return {prefix.rstrip("."): node}


class TestExtensionPointDiff:
    # PR1-32 / design §3.4: every leaf that differs or exists on one side only is an extension point

    def test_every_difference_is_an_extension_point(self):
        rfe, init = _flatten(_ctx("rfe").d), _flatten(_ctx("initiative").d)
        differing = {k for k in rfe.keys() & init.keys() if rfe[k] != init[k]} | (
            rfe.keys() ^ init.keys()
        )
        stray = sorted(k for k in differing if not any(re.search(p, k) for p in EXTENSION_POINTS))
        assert not stray, (
            f"leaves differing outside the extension points (types/README.md table): {stray}"
        )

    def test_shared_machinery_is_identical(self):
        rfe, init = _ctx("rfe").d, _ctx("initiative").d
        for dotted in SHARED_MACHINERY:
            pin(
                dotted,
                "types/README.md 'shared machinery'",
                _ctx("rfe").desc.get(dotted),
                _ctx("initiative").desc.get(dotted),
            )
        for i, cs in enumerate(rfe["pipeline"]["context_sources"]):
            twin = init["pipeline"]["context_sources"][i]
            assert {k: v for k, v in cs.items() if k != "args"} == {
                k: v for k, v in twin.items() if k != "args"
            }


# ── DEFERRED bookkeeping ──────────────────────────────────────────────────────────────────────


class TestDeferredList:
    def test_entries_are_well_formed_and_unique(self):
        rows = [row for row, _, _ in DEFERRED]
        assert rows == sorted(rows) and len(set(rows)) == len(rows)
        assert all(reason for _, _, reason in DEFERRED)

    def test_migrated_entries_are_well_formed_and_disjoint_from_deferred(self):
        deferred = {row for row, _, _ in DEFERRED}
        firsts = []
        for rows, registry, note in MIGRATED:
            assert rows and list(rows) == sorted(rows), registry
            assert all(isinstance(row, int) for row in rows), registry
            assert registry and note.startswith("PR-2"), registry
            assert not deferred & set(rows), (registry, deferred & set(rows))
            firsts.append(rows[0])
        assert firsts == sorted(firsts)
