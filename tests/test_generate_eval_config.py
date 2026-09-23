"""scripts/generate_eval_config.py — eval single-sourcing (design §4.5, PR-4).

Three groups: the template language on a synthetic skeleton, the shipped skeleton + fragments
(both committed configs are exactly a fresh render), and the drift dispositions the first
regeneration applied, pinned readably so a later skeleton edit cannot silently undo them.
"""

import copy
import json
import os
import shutil
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import generate_eval_config as gen  # noqa: E402
import type_registry  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TYPES_ROOT = REPO_ROOT / "types"
TYPES = ["rfe", "initiative"]
REG = type_registry.load(root=TYPES_ROOT, extra_roots=[], env={})
FRAGMENT_SCHEMA = json.loads(
    (TYPES_ROOT / "_schema" / "eval-fragment.schema.json").read_text(encoding="utf-8")
)


# ── helpers ───────────────────────────────────────────────────────────────────────


def _desc(name="rfe", mutate=None):
    """A Descriptor over a deep copy of the shipped rfe data (path kept, so eval/ resolves)."""
    base = REG.get(name)
    data = copy.deepcopy(base.data)
    if mutate:
        mutate(data)
    return type_registry.Descriptor(base.name, data, path=base.path)


def _fragment(name="rfe", mutate=None):
    data = gen.load_fragment(REG.get(name))
    if mutate:
        mutate(data)
    return data


REVISION_TEXT = gen.load_fragment(REG.get("rfe"))["revision"]["assessments"]


def _mini_fragment(**extra):
    """The smallest fragment MINI accepts (every key must be used), plus the given sections."""
    frag = {"schema_version": 1}
    for key, value in extra.items():
        frag.setdefault(key, {}).update(value) if isinstance(value, dict) else frag.__setitem__(
            key, value
        )
    return frag


def _mini_desc(mutate=None):
    """rfe with a one-judge threshold map so mini skeletons need one judge only."""

    def _apply(d):
        d["eval"]["thresholds"] = {"rfe_quality": {"min_mean": 3.5}}
        if mutate:
            mutate(d)

    return _desc(mutate=_apply)


MINI = """\
#@ skeleton-only line
name: ${gen.name}
entity: ${type.display.entity}
skill: ${gen.skill}
judges:
  - name: ${gen.quality_judge}
    check: |
      x = 1
      ${gen.checks.extra_rules}
      return (True, "ok")
thresholds:
  ${gen.thresholds}
"""


def _render_mini(skeleton=MINI, desc=None, fragment=None):
    return gen.render(desc or _mini_desc(), fragment or _mini_fragment(), skeleton)


def _body(text):
    """The render without the generated header."""
    return "".join(ln for ln in text.splitlines(keepends=True) if not ln.startswith("#"))


# ── the template language ─────────────────────────────────────────────────────────


class TestTemplateLanguage:
    def test_inline_namespaces_and_skeleton_comments(self):
        out = _render_mini()
        assert out.startswith("# GENERATED")
        assert "skeleton-only" not in out
        assert "name: rfe-speedrun\nentity: RFE\nskill: rfe-speedrun\n" in out
        assert "  - name: rfe_quality\n" in out

    def test_block_slot_keeps_indentation_and_blank_lines(self):
        skeleton = MINI.replace(
            "entity: ${type.display.entity}\n", "prose: |\n    ${fragment.revision.assessments}\n"
        )
        out = _render_mini(
            skeleton, fragment=_mini_fragment(revision={"assessments": REVISION_TEXT})
        )
        lines = out.splitlines()
        first = lines.index("    **Revision quality**: Did revisions genuinely improve the RFE?")
        assert lines[first + 3] == ""  # paragraph separator stays a truly blank line
        assert lines[first + 4].startswith("    **Content fidelity**")

    def test_empty_block_drops_its_line_and_collapses_double_blanks(self):
        skeleton = MINI.replace(
            "entity: ${type.display.entity}\n",
            "text: |\n  before\n\n  ${fragment.quality.extra_checks}\n\n  after\n",
        )
        empty = _mini_fragment(quality={"extra_checks": ""})
        out = _body(_render_mini(skeleton, fragment=empty))
        assert "text: |\n  before\n\n  after\n" in out
        # A non-blank neighbour: just the slot line goes.
        skeleton = MINI.replace(
            "entity: ${type.display.entity}\n",
            "text: |\n  before\n  ${fragment.quality.extra_checks}\n  after\n",
        )
        assert "text: |\n  before\n  after\n" in _body(_render_mini(skeleton, fragment=empty))

    def test_non_empty_block_between_blank_lines(self):
        skeleton = MINI.replace(
            "entity: ${type.display.entity}\n",
            "text: |\n  before\n\n  ${fragment.quality.extra_checks}\n\n  after\n",
        )
        frag = _mini_fragment(quality={"extra_checks": "middle\n"})
        out = _body(_render_mini(skeleton, fragment=frag))
        assert "text: |\n  before\n\n  middle\n\n  after\n" in out

    def test_multi_line_value_in_an_inline_slot_is_an_error(self):
        skeleton = MINI.replace(
            "entity: ${type.display.entity}\n", "x: ${fragment.revision.assessments}\n"
        )
        with pytest.raises(gen.GenerateError, match="multi-line"):
            _render_mini(skeleton, fragment=_mini_fragment(revision={"assessments": REVISION_TEXT}))

    @pytest.mark.parametrize(
        "placeholder, message",
        [
            ("${nope.x}", "unknown placeholder"),
            ("${type.display.nothing}", "descriptor has no"),
            ("${fragment.quality.nothing}", "fragment has no key"),
            ("${gen.nothing}", "unknown derived value"),
        ],
    )
    def test_unresolvable_placeholders(self, placeholder, message):
        skeleton = MINI.replace("entity: ${type.display.entity}\n", f"x: {placeholder}\n")
        with pytest.raises(gen.GenerateError, match=message):
            _render_mini(skeleton)

    def test_malformed_placeholder_left_behind_is_an_error(self):
        skeleton = MINI.replace("entity: ${type.display.entity}\n", "x: ${Type.Display}\n")
        with pytest.raises(gen.GenerateError, match="unresolved placeholder"):
            _render_mini(skeleton)

    def test_unused_fragment_key_is_an_error(self):
        frag = _mini_fragment(quality={"criteria": "- a"})
        with pytest.raises(gen.GenerateError, match="not used by the skeleton: quality.criteria"):
            _render_mini(fragment=frag)

    def test_shipped_skeleton_consumes_every_fragment_key(self):
        for t in TYPES:
            gen.render_type(REG.get(t))  # no "not used" error

    def test_flatten_rejects_lists(self):
        with pytest.raises(gen.GenerateError, match="is a list"):
            gen.flatten({"a": {"b": ["x"]}})

    def test_rendered_yaml_must_parse(self):
        skeleton = MINI.replace("entity: ${type.display.entity}\n", "bad: [unclosed\n")
        with pytest.raises(gen.GenerateError, match="not valid YAML"):
            _render_mini(skeleton)

    def test_check_bodies_must_compile(self):
        skeleton = MINI.replace("      x = 1\n", "      x = = 1\n")
        with pytest.raises(gen.GenerateError, match="check does not compile"):
            _render_mini(skeleton)

    def test_duplicate_judge_names_are_an_error(self):
        skeleton = MINI.replace("thresholds:", "  - name: ${gen.quality_judge}\nthresholds:")
        with pytest.raises(gen.GenerateError, match="defined twice"):
            _render_mini(skeleton)

    def test_thresholds_must_name_defined_judges(self):
        desc = _mini_desc(lambda d: d["eval"]["thresholds"].__setitem__("ghost", {"min_mean": 1}))
        with pytest.raises(gen.GenerateError, match="ghost"):
            _render_mini(desc=desc)

    def test_threshold_notes_render_as_comments_above_the_entry(self):
        frag = _mini_fragment(
            threshold_notes={"rfe_quality": "first line\nsecond line\n\nfourth\n"}
        )
        out = _body(_render_mini(fragment=frag))
        assert (
            "thresholds:\n  # rfe_quality: first line\n  # second line\n  #\n  # fourth\n"
            "  rfe_quality:\n    min_mean: 3.5\n"
        ) in out

    def test_threshold_notes_for_an_unknown_judge_are_an_error(self):
        frag = _mini_fragment(threshold_notes={"ghost": "x"})
        with pytest.raises(gen.GenerateError, match="threshold_notes for judges without"):
            _render_mini(fragment=frag)

    def test_threshold_values_keep_their_number_form(self):
        desc = _mini_desc(
            lambda d: d["eval"]["thresholds"].__setitem__(
                "rfe_quality", {"min_mean": 4, "min_pass_rate": 0.9}
            )
        )
        out = _body(_render_mini(desc=desc))
        assert "  rfe_quality:\n    min_mean: 4\n    min_pass_rate: 0.9\n" in out


# ── derived values ────────────────────────────────────────────────────────────────


class TestDerivedValues:
    def test_identity_projections(self):
        frag = _fragment()
        d = gen.derived_values(REG.get("rfe"), gen.flatten(frag))
        assert d["name"] == "rfe-speedrun"
        assert d["batch_pattern"] == "RFE-{n:03d}"
        assert d["write_prefix"] == "RHAIRFE-"
        assert d["score_fields_slash"] == "what/why/open_to_how/not_a_task/right_sized"
        assert d["score_fields_py"] == "['what', 'why', 'open_to_how', 'not_a_task', 'right_sized']"
        assert d["criteria_count"] == "5"
        assert d["quality_judge"] == "rfe_quality"
        assert d["pairwise_prompt_file"] == "types/rfe/eval/pairwise-judge.md"
        assert (
            d["run_report_entry_fields"] == "id, recommendation, revision_cycles, needs_attention"
        )
        assert d["annotations_extra"] == ""
        assert d["review_extra_fields"] == ""
        assert d["checks.extra_enum_fields"] == ""
        assert d["checks.extra_rules"] == ""
        assert d["checks.extra_rules_desc"] == ""
        assert d["arch.not_relevant_pattern_def"].startswith("not_relevant_pattern = None")
        assert set(d) == set(gen.DERIVED)

    def test_initiative_extra_fields_rules_and_annotations(self):
        frag = _fragment("initiative")
        d = gen.derived_values(REG.get("initiative"), gen.flatten(frag))
        assert d["annotations_extra"] == (
            "- expected_alignment (nullable string: strong/partial/weak/not_assessed)"
        )
        assert d["review_extra_fields"] == "alignment (strong/partial/weak/not_assessed),"
        assert d["checks.extra_enum_fields"].split("\n") == [
            "alignment = fm.get('alignment')",
            "valid_alignments = ['strong', 'partial', 'weak', 'not_assessed']",
            "if alignment and alignment not in valid_alignments:",
            "    errors.append(f\"{fname}: invalid alignment '{alignment}'\")",
        ]
        assert d["checks.extra_rules"].split("\n") == [
            "if fm.get('alignment', '') == 'weak' and not fm.get('needs_attention'):",
            '    errors.append(f"{fname}: alignment=weak but needs_attention=false")',
        ]
        assert (
            d["checks.extra_rules_desc"]
            == "Type rule: alignment=weak requires needs_attention=true."
        )
        assert d["run_report_entry_fields"] == (
            "id, recommendation, revision_cycles, alignment, feasibility, needs_attention"
        )
        assert d["arch.not_relevant_pattern_def"].startswith("not_relevant_pattern = re.compile(")

    def test_generated_code_quotes_descriptor_values(self):
        # CodeRabbit on #189: field names and rule values never reach the check code raw.
        def mutate(d):
            d["schema"]["review"]["extra_fields"] = {
                "risk-level": {"type": "string", "enum": ["low", "it's high"]},
                "class": {"type": "string", "enum": ["a"]},
                "owner": {"type": "string", "enum": ["x"]},
                "it's": {"type": "string", "enum": ["y"]},
            }
            d["schema"]["review"]["extra_rules"] = [
                {"when": {"field": "risk-level", "equals": "it's high"}, "then": "needs_attention"}
            ]

        d = gen.derived_values(_desc(mutate=mutate), gen.flatten(_fragment()))
        checks = d["checks.extra_enum_fields"].split("\n")
        assert checks[0] == "_extra_field_0 = fm.get('risk-level')"
        assert checks[1] == "_allowed_0 = ['low', \"it's high\"]"
        # A hyphenated name is plain text: it may sit in the message verbatim.
        assert checks[3] == "    errors.append(f\"{fname}: invalid risk-level '{_extra_field_0}'\")"
        assert checks[4] == "_extra_field_1 = fm.get('class')"  # keyword: not a local name
        assert checks[8] == "owner = fm.get('owner')" and checks[9] == "valid_owners = ['x']"
        # A quote in the name: the message falls back to a literal outside the f-string.
        assert checks[12] == '_extra_field_3 = fm.get("it\'s")'
        assert (
            checks[15]
            == '    errors.append(f"{fname}: invalid " + "it\'s" + f" \'{_extra_field_3}\'")'
        )
        rules = d["checks.extra_rules"].split("\n")
        assert (
            rules[0]
            == "if fm.get('risk-level', '') == \"it's high\" and not fm.get('needs_attention'):"
        )
        assert (
            rules[1]
            == '    errors.append(f"{fname}: " + "risk-level=it\'s high but needs_attention=false")'
        )
        body = "def _check():\n    fm = {}\n    errors = []\n    fname = 'f'\n" + "".join(
            f"    {ln}\n" for ln in checks + rules
        )
        compile(body, "<generated>", "exec")

    def test_descriptor_values_spliced_into_code_are_guarded(self):
        desc = _mini_desc(lambda d: d["display"].__setitem__("entity", 'R"FE'))
        with pytest.raises(gen.GenerateError, match="contains a quote"):
            _render_mini(desc=desc)

    def test_invalid_not_relevant_regex_is_an_error(self):
        frag = _fragment("initiative")
        frag["architecture_context"]["not_relevant_pattern"] = "("
        with pytest.raises(gen.GenerateError, match="not a valid regex"):
            gen.derived_values(REG.get("initiative"), gen.flatten(frag))

    def test_malformed_extra_rule_is_an_error(self):
        desc = _desc(
            mutate=lambda d: d["schema"]["review"].__setitem__("extra_rules", [{"when": {}}])
        )
        with pytest.raises(gen.GenerateError, match="extra_rules\\[0\\]"):
            gen.derived_values(desc, gen.flatten(_fragment()))

    def test_missing_pairwise_prompt_is_an_error(self, tmp_path):
        root = tmp_path / "types"
        shutil.copytree(TYPES_ROOT / "rfe", root / "rfe")
        (root / "rfe" / "eval" / "pairwise-judge.md").unlink()
        desc = type_registry.Descriptor("rfe", REG.get("rfe").data, path=root / "rfe" / "type.yaml")
        with pytest.raises(gen.GenerateError, match="pairwise judge prompt missing"):
            gen.render_type(desc)


# ── fragment loading ──────────────────────────────────────────────────────────────


class TestFragmentLoading:
    def test_shipped_fragments_validate_against_the_schema(self):
        jsonschema.Draft202012Validator.check_schema(FRAGMENT_SCHEMA)
        for t in TYPES:
            jsonschema.validate(gen.load_fragment(REG.get(t)), FRAGMENT_SCHEMA)

    def test_missing_fragment(self, tmp_path):
        desc = type_registry.Descriptor(
            "rfe", REG.get("rfe").data, path=tmp_path / "rfe" / "type.yaml"
        )
        with pytest.raises(gen.GenerateError, match="eval fragment missing"):
            gen.load_fragment(desc)

    def test_fragment_must_be_a_mapping_with_schema_version_1(self, tmp_path):
        eval_dir = tmp_path / "rfe" / "eval"
        eval_dir.mkdir(parents=True)
        desc = type_registry.Descriptor(
            "rfe", REG.get("rfe").data, path=tmp_path / "rfe" / "type.yaml"
        )
        (eval_dir / "fragment.yaml").write_text("- a list\n")
        with pytest.raises(gen.GenerateError, match="must be a mapping"):
            gen.load_fragment(desc)
        (eval_dir / "fragment.yaml").write_text("schema_version: 2\n")
        with pytest.raises(gen.GenerateError, match="schema_version must be 1"):
            gen.load_fragment(desc)
        (eval_dir / "fragment.yaml").write_text("a: [\n")
        with pytest.raises(gen.GenerateError, match="not valid YAML"):
            gen.load_fragment(desc)

    def test_descriptor_without_a_path_cannot_locate_eval(self):
        desc = type_registry.Descriptor("rfe", REG.get("rfe").data)
        with pytest.raises(gen.GenerateError, match="has no path"):
            gen.fragment_path(desc)


# ── the shipped configs ───────────────────────────────────────────────────────────


class TestShippedConfigs:
    @pytest.mark.parametrize("t", TYPES)
    def test_committed_config_is_a_fresh_render(self, t):
        path, rendered, committed, diff = gen.compare(REG.get(t))
        assert path == REPO_ROOT / REG.get(t).get("eval.config")
        assert committed == rendered, diff

    @pytest.mark.parametrize("t", TYPES)
    def test_header_names_the_sources(self, t):
        head = (
            (REPO_ROOT / REG.get(t).get("eval.config")).read_text(encoding="utf-8").splitlines()[:3]
        )
        assert head[0].startswith("# GENERATED — do not edit by hand")
        assert gen.SKELETON_RELPATH in head[1] and f"types/{t}/eval/fragment.yaml" in head[1]
        assert f"types/{t}/type.yaml" in head[2]

    def test_thresholds_are_the_descriptor_map_verbatim(self):
        for t in TYPES:
            config = yaml.safe_load((REPO_ROOT / REG.get(t).get("eval.config")).read_text())
            assert config["thresholds"] == REG.get(t).get("eval.thresholds")

    def test_shared_judges_are_byte_identical_across_types(self):
        configs = {
            t: {
                j["name"]: j
                for j in yaml.safe_load((REPO_ROOT / REG.get(t).get("eval.config")).read_text())[
                    "judges"
                ]
            }
            for t in TYPES
        }
        for name in ("revision_flag_consistency", "revision_coverage"):
            assert configs["rfe"][name] == configs["initiative"][name], name

    def test_check_mode_on_the_repo_passes(self, capsys):
        assert gen.main(["--check"]) == 0
        out = capsys.readouterr().out
        assert (
            "rfe: eval.yaml up to date" in out
            and "initiative: eval-initiative.yaml up to date" in out
        )

    def test_write_then_check_over_a_fake_repo_root(self, tmp_path, capsys):
        repo = tmp_path / "repo"
        (repo / "eval" / "config").mkdir(parents=True)
        shutil.copy(REPO_ROOT / gen.SKELETON_RELPATH, repo / gen.SKELETON_RELPATH)
        assert gen.main(["--check", "--repo-root", str(repo)]) == 1
        err = capsys.readouterr().err
        assert "eval.yaml is out of date" in err and "2 config(s) out of date" in err
        assert gen.main(["--repo-root", str(repo)]) == 0
        assert (repo / "eval.yaml").is_file() and (repo / "eval-initiative.yaml").is_file()
        assert gen.main(["--check", "--repo-root", str(repo)]) == 0
        # --type limits the work; --stdout prints the render and writes nothing.
        (repo / "eval.yaml").unlink()
        capsys.readouterr()
        assert gen.main(["--type", "rfe", "--stdout", "--repo-root", str(repo)]) == 0
        printed = capsys.readouterr().out
        assert not (repo / "eval.yaml").exists()
        assert printed.startswith("# GENERATED")
        assert gen.main(["--type", "nope", "--repo-root", str(repo)]) == 2

    def test_stdout_render_equals_compare(self, capsys):
        assert gen.main(["--type", "initiative", "--stdout"]) == 0
        assert capsys.readouterr().out == gen.compare(REG.get("initiative"))[1]


# ── first-regeneration dispositions (design §4.5) ─────────────────────────────────


class TestDispositions:
    """What the regeneration changed on purpose. Each row is a reviewable decision, pinned so a
    later skeleton edit cannot undo it silently."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.cfg = {
            t: yaml.safe_load((REPO_ROOT / REG.get(t).get("eval.config")).read_text())
            for t in TYPES
        }
        self.judges = {t: {j["name"]: j for j in self.cfg[t]["judges"]} for t in TYPES}

    def test_pass_logic_escape_removed_from_the_initiative_config(self):
        # dd57da6 tolerated any pass/total inconsistency once needs_attention was set; no producer
        # rule ever justified it (weak alignment sets needs_attention, never pass).
        for t in TYPES:
            check = self.judges[t]["frontmatter_valid"]["check"]
            assert "if fm.get('pass') != expected_pass:\n            errors.append(" in check
            assert "fm.get('needs_attention') and fm.get('pass')" not in check

    def test_missing_pass_field_is_an_error_not_a_crash(self):
        # CodeRabbit on #189: fm['pass'] raised KeyError once the missing-field error was
        # already recorded; fm.get('pass') lets the check return its findings.
        for t in TYPES:
            check = self.judges[t]["frontmatter_valid"]["check"]
            assert "pass={fm.get('pass')} inconsistent" in check and "fm['pass']" not in check

    def test_rm_artifacts_check_and_phase_markers_shared(self):
        for t in TYPES:
            check = self.judges[t]["pipeline_flow"]["check"]
            assert f'"rm {REG.get(t).dirs()["tasks"]}/" in stdout' in check
            assert '"AUTOFIX" in stdout or "Batch " in stdout' in check
            assert "unexpected file deletions" in self.judges[t]["pipeline_flow"]["description"]

    def test_not_relevant_carve_out_is_a_fragment_switch(self):
        rfe, init = (self.judges[t]["architecture_context_used"]["check"] for t in TYPES)
        assert "not_relevant_pattern = None" in rfe
        assert "not_relevant_pattern = re.compile(" in init
        assert "architecture context (was |is )?(not |un)relevant" in init
        assert self.cfg["rfe"]["thresholds"]["architecture_context_used"] == {"min_pass_rate": 1.0}
        assert self.cfg["initiative"]["thresholds"]["architecture_context_used"] == {
            "min_pass_rate": 0.85
        }

    def test_run_report_exists_excludes_any_snapshot_file(self):
        for t in TYPES:
            check = self.judges[t]["run_report_exists"]["check"]
            assert '"-snapshot-" not in os.path.basename(k)' in check
            assert "issue-snapshot" not in check

    def test_dead_html_output_dropped_and_companion_documented(self):
        for t in TYPES:
            paths = [o["path"] for o in self.cfg[t]["outputs"]]
            assert not any(p.endswith("review-report.html") for p in paths), paths
            runs = next(o for o in self.cfg[t]["outputs"] if o["path"] == "artifacts/auto-fix-runs")
            prefix = REG.get(t).get("snapshot.report_prefix")
            assert f"{prefix}YYYYMMDD-HHMMSS-report.html" in runs["schema"]
            assert "retried/retry_successes" in runs["schema"] and "batch_size" in runs["schema"]

    def test_revision_quality_points_at_review_fields_that_exist(self):
        for t in TYPES:
            prompt = self.judges[t]["revision_quality"]["prompt"]
            assert "`before_score`/`score`" in prompt and "revision_cycles" not in prompt

    def test_split_status_and_score_tolerance_documented_for_both(self):
        for t in TYPES:
            assert "{ID}-split-status.yaml" in self.cfg[t]["outputs"][1]["schema"]
            assert "score_tolerance (int, default 1)" in self.cfg[t]["dataset"]["schema"]

    def test_pr4b_judge_tightenings(self):
        # Replayed over six runs (3 rfe, 3 initiative, 127 cases): the two tightenings
        # changed no verdict; the placeholder normalisation removed exactly the two
        # "None (first pass)." false positives of #190's first calibration run.
        for t in TYPES:
            flow = self.judges[t]["pipeline_flow"]["check"]
            assert "if len(phases_found) < 3:" in flow and "< 2" not in flow
            arch = self.judges[t]["architecture_context_used"]["check"]
            assert "elif transcripts:" in arch and "no writer transcript among" in arch
            cov = self.judges[t]["revision_coverage"]["check"]
            assert "EMPTY_HISTORY_PREFIX = re.compile(" in cov

    def test_pairwise_prompts_live_with_their_type(self):
        for t in TYPES:
            assert self.judges[t]["pairwise"]["prompt_file"] == f"types/{t}/eval/pairwise-judge.md"
        assert not (REPO_ROOT / "eval" / "config" / "pairwise-judge.md").exists()
