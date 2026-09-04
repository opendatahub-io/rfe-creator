#!/usr/bin/env python3
"""Tests for scripts/artifact_utils.py — schema validation, frontmatter I/O, migration."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from artifact_utils import (
    SCHEMAS,
    ValidationError,
    _body_without_frontmatter,
    _looks_like_frontmatter_block,
    _migrate_fields,
    apply_defaults,
    read_frontmatter,
    read_frontmatter_validated,
    rename_initiative_to_jira_key,
    rename_to_jira_key,
    update_frontmatter,
    validate,
    write_frontmatter,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_dir(tmp_path):
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(orig)


def _write(path, content):
    """Write a file, creating parent dirs."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


VALID_REVIEW_FM = {
    "rfe_id": "RHAIRFE-1234",
    "score": 8,
    "pass": True,
    "recommendation": "submit",
    "feasibility": "feasible",
    "auto_revised": False,
    "needs_attention": False,
    "scores": {
        "what": 2,
        "why": 2,
        "open_to_how": 2,
        "not_a_task": 2,
        "right_sized": 0,
    },
}

# A hand-written block whose free-text value carries an unquoted colon — the
# shape seen in eval runs, where review agents wrote frontmatter themselves
# instead of leaving it to scripts/frontmatter.py.
CORRUPT_REVIEW = (
    "---\n"
    "rfe_id: RFE-015\n"
    "needs_attention_reason: Blocked: needs architecture input\n"
    "---\n"
    "## Assessor Feedback\n\nKeep this body.\n"
)

# An empty block written before any fields are stamped, followed by a body that
# contains a markdown horizontal rule — the shape review agents produce when they
# write the body first. The rule is what the old regex mistook for the closing
# delimiter, handing the score table to yaml.safe_load.
EMPTY_BLOCK_REVIEW = (
    "---\n"
    "---\n"
    "### RFE-015 Review\n\n"
    "| Criterion | Score |\n"
    "| --- | --- |\n"
    "| WHAT | 2/2 |\n\n"
    "---\n\n"
    "## Assessor Feedback\n\nKeep this body.\n"
)


# ── Schema & Validation ──────────────────────────────────────────────────────


class TestSchemas:
    def test_rfe_review_schema_has_auto_revised(self):
        assert "auto_revised" in SCHEMAS["rfe-review"]
        assert "revised" not in SCHEMAS["rfe-review"]

    def test_rfe_review_auto_revised_is_bool(self):
        spec = SCHEMAS["rfe-review"]["auto_revised"]
        assert spec["type"] == "bool"
        assert spec["required"] is True
        assert spec["default"] is False


class TestValidate:
    def test_valid_review_data(self):
        errors = validate(VALID_REVIEW_FM, "rfe-review")
        assert errors == []

    def test_unknown_field_rejected(self):
        data = {**VALID_REVIEW_FM, "bogus": "value"}
        errors = validate(data, "rfe-review")
        assert any("Unknown field: bogus" in e for e in errors)

    def test_old_revised_field_rejected(self):
        data = {**VALID_REVIEW_FM}
        data.pop("auto_revised")
        data["revised"] = False
        errors = validate(data, "rfe-review")
        assert any("revised" in e for e in errors)

    def test_missing_required_field(self):
        data = {**VALID_REVIEW_FM}
        data.pop("rfe_id")
        errors = validate(data, "rfe-review")
        assert any("rfe_id" in e for e in errors)

    def test_invalid_enum_value(self):
        data = {**VALID_REVIEW_FM, "recommendation": "banana"}
        errors = validate(data, "rfe-review")
        assert any("banana" in e for e in errors)

    def test_wrong_type(self):
        data = {**VALID_REVIEW_FM, "score": "eight"}
        errors = validate(data, "rfe-review")
        assert any("expected int" in e for e in errors)

    def test_unknown_schema_type(self):
        with pytest.raises(ValueError, match="Unknown schema type"):
            validate({}, "nonexistent")


class TestApplyDefaults:
    def test_auto_revised_defaults_to_false(self):
        data = {**VALID_REVIEW_FM}
        data.pop("auto_revised")
        apply_defaults(data, "rfe-review")
        assert data["auto_revised"] is False

    def test_existing_value_not_overwritten(self):
        data = {**VALID_REVIEW_FM, "auto_revised": True}
        apply_defaults(data, "rfe-review")
        assert data["auto_revised"] is True


# ── Field Migration ───────────────────────────────────────────────────────────


class TestMigrateFields:
    def test_revised_renamed_to_auto_revised(self):
        data = {"revised": True, "other": "value"}
        _migrate_fields(data)
        assert data["auto_revised"] is True
        assert "revised" not in data

    def test_no_overwrite_if_both_present(self):
        data = {"revised": False, "auto_revised": True}
        _migrate_fields(data)
        assert data["auto_revised"] is True
        assert "revised" in data  # not removed when new key exists

    def test_noop_when_no_old_field(self):
        data = {"auto_revised": False}
        _migrate_fields(data)
        assert data["auto_revised"] is False

    def test_noop_on_empty_dict(self):
        data = {}
        _migrate_fields(data)
        assert data == {}


# ── read_frontmatter ──────────────────────────────────────────────────────────


class TestReadFrontmatter:
    def test_reads_yaml_and_body(self, tmp_dir):
        _write("test.md", "---\ntitle: Hello\n---\nBody here.\n")
        data, body = read_frontmatter("test.md")
        assert data["title"] == "Hello"
        assert "Body here." in body

    def test_no_frontmatter(self, tmp_dir):
        _write("test.md", "Just a plain file.\n")
        data, body = read_frontmatter("test.md")
        assert data == {}
        assert "Just a plain file." in body

    def test_migrates_revised_on_read(self, tmp_dir):
        _write("test.md", "---\nrevised: true\n---\nBody.\n")
        data, body = read_frontmatter("test.md")
        assert data.get("auto_revised") is True
        assert "revised" not in data

    def test_does_not_overwrite_auto_revised(self, tmp_dir):
        _write("test.md", "---\nauto_revised: true\n---\nBody.\n")
        data, _ = read_frontmatter("test.md")
        assert data["auto_revised"] is True

    def test_unparseable_raises_validation_error(self, tmp_dir):
        _write("review.md", CORRUPT_REVIEW)
        with pytest.raises(ValidationError):
            read_frontmatter("review.md")

    def test_error_locates_the_offending_line(self, tmp_dir):
        _write("review.md", CORRUPT_REVIEW)
        with pytest.raises(ValidationError) as exc_info:
            read_frontmatter("review.md")
        message = str(exc_info.value)
        assert "review.md" in message
        assert "line 2" in message
        assert "needs architecture input" in message

    def test_error_is_a_single_line(self, tmp_dir):
        """A raw PyYAML traceback is unreadable once agent logs truncate it."""
        _write("review.md", CORRUPT_REVIEW)
        with pytest.raises(ValidationError) as exc_info:
            read_frontmatter("review.md")
        assert "\n" not in str(exc_info.value)

    def test_empty_block_followed_by_horizontal_rule(self, tmp_dir):
        """An empty block plus a later `---` must not swallow the body as YAML.

        This is the crash a review agent hits when it writes the review body
        before the frontmatter: the score table's `---` separator became the
        closing delimiter and yaml.safe_load raised ScannerError.
        """
        _write("review.md", EMPTY_BLOCK_REVIEW)
        data, body = read_frontmatter("review.md")
        assert data == {}
        assert "### RFE-015 Review" in body
        assert "| WHAT | 2/2 |" in body
        # The empty block is consumed, so a later write replaces rather than
        # duplicates it.
        assert not body.startswith("---")

    def test_empty_block_with_no_later_rule(self, tmp_dir):
        _write("review.md", "---\n---\nBody only.\n")
        data, body = read_frontmatter("review.md")
        assert data == {}
        assert body == "Body only.\n"

    def test_body_horizontal_rule_preserved(self, tmp_dir):
        _write("test.md", "---\ntitle: Hello\n---\nIntro.\n\n---\n\nMore.\n")
        data, body = read_frontmatter("test.md")
        assert data["title"] == "Hello"
        assert body == "Intro.\n\n---\n\nMore.\n"

    def test_scalar_block_is_not_frontmatter(self, tmp_dir):
        _write("test.md", "---\njust a string\n---\nBody.\n")
        data, body = read_frontmatter("test.md")
        assert data == {}
        assert body.startswith("---")

    def test_crlf_line_endings(self, tmp_dir):
        _write("test.md", "---\r\ntitle: Hello\r\n---\r\nBody.\r\n")
        data, body = read_frontmatter("test.md")
        assert data["title"] == "Hello"
        assert "Body." in body

    def test_regex_handles_raw_crlf(self):
        """The `\\r?` in _FRONTMATTER_RE is what makes CRLF work — exercise it.

        read_frontmatter opens files in universal-newline mode, which strips
        every `\\r` before the content reaches the regex, so a file round-trip
        cannot prove the `\\r?` branch fires. Match the regex against a raw CRLF
        string to guard that robustness directly.
        """
        from artifact_utils import _FRONTMATTER_RE

        raw = "---\r\ntitle: Hello\r\n---\r\nBody.\r\n"
        match = _FRONTMATTER_RE.match(raw)
        assert match is not None
        assert match.group(1) == "title: Hello\r\n"
        assert raw[match.end() :] == "Body.\r\n"


# ── read_frontmatter_validated ────────────────────────────────────────────────


class TestReadFrontmatterValidated:
    def test_valid_file(self, tmp_dir):
        fm = "\n".join(
            f"{k}: {v}"
            for k, v in [
                ("rfe_id", "RHAIRFE-1234"),
                ("score", 8),
                ("pass", "true"),
                ("recommendation", "submit"),
                ("feasibility", "feasible"),
                ("auto_revised", "false"),
                ("needs_attention", "false"),
            ]
        )
        scores = "scores:\n  what: 2\n  why: 2\n  open_to_how: 2\n  not_a_task: 2\n  right_sized: 2"
        _write("review.md", f"---\n{fm}\n{scores}\n---\nBody.\n")
        data, body = read_frontmatter_validated("review.md", "rfe-review")
        assert data["rfe_id"] == "RHAIRFE-1234"
        assert "Body." in body

    def test_migrates_old_revised(self, tmp_dir):
        fm = "\n".join(
            f"{k}: {v}"
            for k, v in [
                ("rfe_id", "RHAIRFE-1234"),
                ("score", 8),
                ("pass", "true"),
                ("recommendation", "submit"),
                ("feasibility", "feasible"),
                ("revised", "true"),
                ("needs_attention", "false"),
            ]
        )
        scores = "scores:\n  what: 2\n  why: 2\n  open_to_how: 2\n  not_a_task: 2\n  right_sized: 2"
        _write("review.md", f"---\n{fm}\n{scores}\n---\nBody.\n")
        data, _ = read_frontmatter_validated("review.md", "rfe-review")
        assert data["auto_revised"] is True
        assert "revised" not in data

    def test_rejects_invalid_data(self, tmp_dir):
        _write("review.md", "---\nbogus: true\n---\nBody.\n")
        with pytest.raises(ValidationError):
            read_frontmatter_validated("review.md", "rfe-review")

    def test_no_frontmatter_raises(self, tmp_dir):
        _write("review.md", "No frontmatter here.\n")
        with pytest.raises(ValidationError, match="No frontmatter"):
            read_frontmatter_validated("review.md", "rfe-review")


# ── write_frontmatter ─────────────────────────────────────────────────────────


class TestWriteFrontmatter:
    def test_creates_file(self, tmp_dir):
        write_frontmatter("out.md", VALID_REVIEW_FM.copy(), "rfe-review")
        assert os.path.exists("out.md")
        data, _ = read_frontmatter("out.md")
        assert data["rfe_id"] == "RHAIRFE-1234"

    def test_preserves_body(self, tmp_dir):
        _write("out.md", "---\nold: data\n---\nKeep this body.\n")
        write_frontmatter("out.md", VALID_REVIEW_FM.copy(), "rfe-review")
        data, body = read_frontmatter("out.md")
        assert data["rfe_id"] == "RHAIRFE-1234"
        assert "Keep this body." in body

    def test_migrates_on_write(self, tmp_dir):
        data = {**VALID_REVIEW_FM}
        data["revised"] = data.pop("auto_revised")
        write_frontmatter("out.md", data, "rfe-review")
        written, _ = read_frontmatter("out.md")
        assert "auto_revised" in written
        assert "revised" not in written

    def test_rejects_invalid_data(self, tmp_dir):
        data = {**VALID_REVIEW_FM, "recommendation": "invalid"}
        with pytest.raises(ValidationError):
            write_frontmatter("out.md", data, "rfe-review")

    def test_creates_parent_dirs(self, tmp_dir):
        write_frontmatter("a/b/c/out.md", VALID_REVIEW_FM.copy(), "rfe-review")
        assert os.path.exists("a/b/c/out.md")

    def test_overwrites_unparseable_block_keeping_body(self, tmp_dir):
        _write("review.md", CORRUPT_REVIEW)
        write_frontmatter("review.md", VALID_REVIEW_FM.copy(), "rfe-review")
        data, body = read_frontmatter("review.md")
        assert data["rfe_id"] == "RHAIRFE-1234"
        assert "Keep this body." in body


# ── update_frontmatter ────────────────────────────────────────────────────────


class TestUpdateFrontmatter:
    def test_merges_updates(self, tmp_dir):
        write_frontmatter("review.md", VALID_REVIEW_FM.copy(), "rfe-review")
        update_frontmatter("review.md", {"auto_revised": True}, "rfe-review")
        data, _ = read_frontmatter("review.md")
        assert data["auto_revised"] is True
        assert data["rfe_id"] == "RHAIRFE-1234"  # unchanged

    def test_migrates_old_field_in_existing_file(self, tmp_dir):
        # Simulate an old-format file on disk
        _write(
            "review.md",
            "---\nrfe_id: RHAIRFE-1234\nscore: 8\npass: true\n"
            "recommendation: submit\nfeasibility: feasible\n"
            "revised: false\nneeds_attention: false\n"
            "scores:\n  what: 2\n  why: 2\n  open_to_how: 2\n"
            "  not_a_task: 2\n  right_sized: 2\n---\nBody.\n",
        )
        # Setting a new field should not fail due to old 'revised' key
        update_frontmatter("review.md", {"needs_attention": True}, "rfe-review")
        data, _ = read_frontmatter("review.md")
        assert data["needs_attention"] is True
        assert data["auto_revised"] is False
        assert "revised" not in data

    def test_rejects_invalid_update(self, tmp_dir):
        write_frontmatter("review.md", VALID_REVIEW_FM.copy(), "rfe-review")
        with pytest.raises(ValidationError):
            update_frontmatter("review.md", {"recommendation": "invalid"}, "rfe-review")


class TestUpdateFrontmatterRepair:
    """An unparseable block must be repairable through frontmatter.py.

    update_frontmatter is the only writer, so if it refuses on corrupt input
    the caller's only recourse is hand-editing YAML — the thing that corrupted
    the file to begin with.
    """

    # What review-agent.md Step 4 actually passes: no auto_revised, which the
    # schema defaults to False.
    STEP_4_FIELDS = {
        "rfe_id": "RFE-015",
        "score": 8,
        "pass": True,
        "recommendation": "submit",
        "feasibility": "feasible",
        "needs_attention": True,
        "needs_attention_reason": "Blocked: needs architecture input",
        "scores": {"what": 2, "why": 2, "open_to_how": 2, "not_a_task": 1, "right_sized": 1},
    }

    def test_repairs_block_and_keeps_body(self, tmp_dir):
        _write("review.md", CORRUPT_REVIEW)
        update_frontmatter("review.md", dict(self.STEP_4_FIELDS), "rfe-review")
        data, body = read_frontmatter("review.md")
        assert data["rfe_id"] == "RFE-015"
        assert data["auto_revised"] is False  # supplied by schema default
        assert "Keep this body." in body

    def test_repaired_value_with_colon_round_trips(self, tmp_dir):
        """The colon that broke the file is fine once yaml.dump does the quoting."""
        _write("review.md", CORRUPT_REVIEW)
        update_frontmatter("review.md", dict(self.STEP_4_FIELDS), "rfe-review")
        data, _ = read_frontmatter("review.md")
        assert data["needs_attention_reason"] == "Blocked: needs architecture input"

    def test_warns_that_the_block_was_replaced(self, tmp_dir, capsys):
        _write("review.md", CORRUPT_REVIEW)
        update_frontmatter("review.md", dict(self.STEP_4_FIELDS), "rfe-review")
        assert "replacing unparseable frontmatter" in capsys.readouterr().err

    def test_refuses_incomplete_updates(self, tmp_dir):
        """Validation is the guard — a partial update must not silently drop fields.

        This is the second Step 4 call, which normally relies on merging.
        """
        _write("review.md", CORRUPT_REVIEW)
        with pytest.raises(ValidationError):
            update_frontmatter("review.md", {"before_score": 8}, "rfe-review")

    def test_leaves_file_untouched_when_it_refuses(self, tmp_dir):
        _write("review.md", CORRUPT_REVIEW)
        with pytest.raises(ValidationError):
            update_frontmatter("review.md", {"before_score": 8}, "rfe-review")
        with open("review.md") as f:
            assert f.read() == CORRUPT_REVIEW

    def test_stamping_over_an_empty_block_keeps_the_whole_body(self, tmp_dir):
        """The recovery path slices on the same regex, so it truncated too.

        With the old pattern the mis-detected delimiter sat in the middle of the
        body, and everything above it — heading, score table — was dropped
        silently instead of crashing.
        """
        _write("review.md", EMPTY_BLOCK_REVIEW)
        update_frontmatter("review.md", dict(self.STEP_4_FIELDS), "rfe-review")
        data, body = read_frontmatter("review.md")
        assert data["rfe_id"] == "RFE-015"
        assert body == EMPTY_BLOCK_REVIEW.split("---\n---\n", 1)[1]

    def test_stamping_over_an_empty_block_leaves_one_block(self, tmp_dir):
        _write("review.md", EMPTY_BLOCK_REVIEW)
        update_frontmatter("review.md", dict(self.STEP_4_FIELDS), "rfe-review")
        with open("review.md") as f:
            content = f.read()
        # The body's own horizontal rule is the only `---` line left below the
        # block, so a duplicated empty block would show up as an extra one.
        assert content.count("\n---\n") == 2


class TestBodyRuleNotMistakenForDelimiter:
    """A `---` horizontal rule in the body must never be taken for the closing
    delimiter, or the lines above it are silently dropped — the exact
    crash->silent-truncation failure the repair path was meant to avoid.
    """

    FIELDS = {
        "rfe_id": "RFE-015",
        "score": 8,
        "pass": True,
        "recommendation": "submit",
        "feasibility": "feasible",
        "needs_attention": False,
        "scores": {"what": 2, "why": 2, "open_to_how": 2, "not_a_task": 1, "right_sized": 1},
    }

    # Body-only file (per Step 3) whose body opens with a thematic-break rule and
    # a heading. The heading is a YAML comment, so the block parses to null — but
    # it is NOT an empty block, and the second `---` is a body rule.
    LEADING_RULE_BODY = "---\n## Assessor Feedback\n---\n\nScores look good. Submit.\n"

    def test_read_preserves_leading_rule_section(self, tmp_dir):
        _write("review.md", self.LEADING_RULE_BODY)
        data, body = read_frontmatter("review.md")
        assert data == {}
        assert body == self.LEADING_RULE_BODY  # nothing consumed

    def test_update_keeps_heading_above_body_rule(self, tmp_dir):
        _write("review.md", self.LEADING_RULE_BODY)
        update_frontmatter("review.md", dict(self.FIELDS), "rfe-review")
        data, body = read_frontmatter("review.md")
        assert data["rfe_id"] == "RFE-015"
        assert "## Assessor Feedback" in body
        assert "Scores look good. Submit." in body

    def test_unparseable_block_without_clean_close_keeps_body(self, tmp_dir):
        """No valid closing `---`; a body rule is the first one the regex sees.

        On the pre-fix repair path this truncated the body to everything below
        the body rule, silently dropping the paragraph above it.
        """
        content = (
            "---\n"
            "rfe_id: RHAIRFE-1595\n"
            "score: 7\n"
            "# Review Summary\n"
            "\n"
            "Important analysis a stakeholder must not lose.\n"
            "\n"
            "---\n"
            "\n"
            "## Detailed scores\n"
        )
        _write("review.md", content)
        update_frontmatter("review.md", dict(self.FIELDS), "rfe-review")
        with open("review.md") as f:
            out = f.read()
        assert "Important analysis a stakeholder must not lose." in out
        # And the file is now valid frontmatter that round-trips.
        assert read_frontmatter("review.md")[0]["rfe_id"] == "RFE-015"

    def test_write_and_update_agree_on_scalar_block(self, tmp_dir):
        """The two writers must not disagree on a non-mapping block; both keep
        the content (lossless) rather than one dropping it.
        """
        original = "---\njust a string\n---\nReal body.\n"
        _write("w.md", original)
        _write("u.md", original)
        write_frontmatter("w.md", dict(self.FIELDS), "rfe-review")
        update_frontmatter("u.md", dict(self.FIELDS), "rfe-review")
        with open("w.md") as f:
            w = f.read()
        with open("u.md") as f:
            u = f.read()
        assert w == u
        assert "Real body." in w
        assert read_frontmatter("w.md")[0]["rfe_id"] == "RFE-015"

    def test_update_keeps_mapping_like_markdown_body(self, tmp_dir):
        """A body that opens with `---`, a `key: value` line, and a `- ` list
        item, then another `---`, is invalid YAML but must not be stripped.

        The mapping key alone looks frontmatter-ish, but the top-level sequence
        item makes it invalid YAML and never real frontmatter — so the region is
        body and must survive the repair.
        """
        _write("review.md", "---\nSummary: needs review\n- investigate\n---\n\nReal body.\n")
        update_frontmatter("review.md", dict(self.FIELDS), "rfe-review")
        with open("review.md") as f:
            out = f.read()
        assert "Summary: needs review" in out
        assert "- investigate" in out
        assert "Real body." in out
        assert read_frontmatter("review.md")[0]["rfe_id"] == "RFE-015"


class TestLooksLikeFrontmatterBlock:
    def test_key_value_lines_are_a_block(self):
        assert _looks_like_frontmatter_block("rfe_id: RFE-1\nscore: 8\n")

    def test_value_with_colon_is_a_block(self):
        assert _looks_like_frontmatter_block("reason: Blocked: needs input\n")

    def test_nested_mapping_is_a_block(self):
        assert _looks_like_frontmatter_block("scores:\n  what: 2\n  why: 2\n")

    def test_valid_list_value_is_a_block(self):
        # yaml.dump writes list values as "- item" at column 0; the parse-to-dict
        # fast path must still recognise this as real frontmatter.
        assert _looks_like_frontmatter_block("labels:\n- a\n- b\n")

    def test_mapping_then_top_level_sequence_is_not_a_block(self):
        # A key line followed by a top-level "- " item is invalid YAML and never
        # real frontmatter — it is a markdown body that must be preserved.
        assert not _looks_like_frontmatter_block("Summary: needs review\n- investigate\n")

    def test_blank_line_means_not_a_block(self):
        assert not _looks_like_frontmatter_block("rfe_id: RFE-1\n\nprose\n")

    def test_markdown_heading_is_not_a_block(self):
        assert not _looks_like_frontmatter_block("## Assessor Feedback\n")

    def test_prose_is_not_a_block(self):
        assert not _looks_like_frontmatter_block("Some analysis without a colon\n")

    def test_empty_is_not_a_block(self):
        assert not _looks_like_frontmatter_block("")

    def test_body_without_frontmatter_preserves_non_block(self, tmp_dir):
        _write("f.md", "---\n## Heading\n---\nbody\n")
        assert _body_without_frontmatter("f.md") == "---\n## Heading\n---\nbody\n"

    def test_body_without_frontmatter_strips_real_block(self, tmp_dir):
        _write("f.md", "---\nrfe_id: RFE-1\n---\nbody\n")
        assert _body_without_frontmatter("f.md") == "body\n"


# ── Initiative schemas ───────────────────────────────────────────────────────

VALID_INITIATIVE_REVIEW_FM = {
    "initiative_id": "INIT-001",
    "score": 7,
    "pass": True,
    "recommendation": "submit",
    "feasibility": "feasible",
    "auto_revised": False,
    "needs_attention": False,
    "scores": {
        "what": 2,
        "why": 1,
        "scope": 2,
        "open_to_how": 1,
        "right_sized": 1,
    },
}

VALID_INITIATIVE_TASK_FM = {
    "initiative_id": "INIT-001",
    "title": "Test Initiative",
    "priority": "Major",
    "status": "Ready",
}


class TestInitiativeSchemas:
    def test_initiative_review_schema_exists(self):
        assert "initiative-review" in SCHEMAS

    def test_initiative_task_schema_exists(self):
        assert "initiative-task" in SCHEMAS

    def test_initiative_review_has_alignment(self):
        assert "alignment" in SCHEMAS["initiative-review"]

    def test_alignment_defaults_to_not_assessed(self):
        """Omitting alignment must read the same as the alignment file's own value."""
        data = {}
        apply_defaults(data, "initiative-review")
        assert data["alignment"] == "not_assessed"

    def test_initiative_review_score_fields(self):
        fields = SCHEMAS["initiative-review"]["scores"]["fields"]
        assert "what" in fields
        assert "why" in fields
        assert "scope" in fields
        assert "open_to_how" in fields
        assert "right_sized" in fields

    def test_initiative_task_has_initiative_id(self):
        assert "initiative_id" in SCHEMAS["initiative-task"]

    def test_initiative_task_no_size_field(self):
        assert "size" not in SCHEMAS["initiative-task"]


class TestInitiativeValidate:
    def test_valid_initiative_review(self):
        errors = validate(VALID_INITIATIVE_REVIEW_FM, "initiative-review")
        assert errors == []

    def test_valid_initiative_task(self):
        errors = validate(VALID_INITIATIVE_TASK_FM, "initiative-task")
        assert errors == []

    def test_initiative_review_rejects_rfe_id(self):
        data = {**VALID_INITIATIVE_REVIEW_FM, "initiative_id": "RHAIRFE-1234"}
        errors = validate(data, "initiative-review")
        assert any("initiative_id" in e for e in errors)

    def test_initiative_task_accepts_rhoaieng_id(self):
        data = {**VALID_INITIATIVE_TASK_FM, "initiative_id": "RHOAIENG-5000"}
        errors = validate(data, "initiative-task")
        assert errors == []

    def test_initiative_task_parent_key(self):
        data = {**VALID_INITIATIVE_TASK_FM, "parent_key": "RHAISTRAT-100"}
        errors = validate(data, "initiative-task")
        assert errors == []


class TestInitiativeFrontmatter:
    def test_write_and_read_initiative_review(self, tmp_dir):
        write_frontmatter("init-review.md", VALID_INITIATIVE_REVIEW_FM.copy(), "initiative-review")
        assert os.path.exists("init-review.md")
        data, _ = read_frontmatter("init-review.md")
        assert data["initiative_id"] == "INIT-001"
        assert data["scores"]["what"] == 2

    def test_write_and_read_initiative_task(self, tmp_dir):
        write_frontmatter("init-task.md", VALID_INITIATIVE_TASK_FM.copy(), "initiative-task")
        data, _ = read_frontmatter("init-task.md")
        assert data["initiative_id"] == "INIT-001"
        assert data["title"] == "Test Initiative"

    def test_update_initiative_review(self, tmp_dir):
        write_frontmatter("init-review.md", VALID_INITIATIVE_REVIEW_FM.copy(), "initiative-review")
        update_frontmatter("init-review.md", {"auto_revised": True}, "initiative-review")
        data, _ = read_frontmatter("init-review.md")
        assert data["auto_revised"] is True
        assert data["initiative_id"] == "INIT-001"


class TestRenamePersistsLocalId:
    """Renaming to the Jira key must not destroy the pre-submission identity.

    rfe_id/initiative_id is overwritten with the key, so before this the
    RFE-001 -> RHAIRFE-3082 mapping survived only as a git rename in the
    results repo's history — unreadable from any single commit. The run
    report's provenance field reads local_id back from these files.
    """

    def _setup_rfe(self):
        os.makedirs("artifacts/rfe-tasks")
        os.makedirs("artifacts/rfe-reviews")
        write_frontmatter(
            "artifacts/rfe-tasks/RFE-001.md",
            {"rfe_id": "RFE-001", "title": "T", "priority": "Major", "status": "Ready"},
            "rfe-task",
        )
        write_frontmatter(
            "artifacts/rfe-reviews/RFE-001-review.md",
            {**VALID_REVIEW_FM, "rfe_id": "RFE-001"},
            "rfe-review",
        )

    def test_rfe_task_and_review_carry_local_id(self, tmp_dir):
        self._setup_rfe()

        rename_to_jira_key("artifacts", "RFE-001", "RHAIRFE-3082")

        task, _ = read_frontmatter("artifacts/rfe-tasks/RHAIRFE-3082.md")
        review, _ = read_frontmatter("artifacts/rfe-reviews/RHAIRFE-3082-review.md")
        assert task["rfe_id"] == "RHAIRFE-3082"
        assert task["local_id"] == "RFE-001"
        assert review["local_id"] == "RFE-001"

    def test_renamed_files_still_validate(self, tmp_dir):
        """local_id must be legal under the schemas or every later
        update_frontmatter on a renamed file would raise."""
        self._setup_rfe()

        rename_to_jira_key("artifacts", "RFE-001", "RHAIRFE-3082")

        data, _ = read_frontmatter_validated("artifacts/rfe-tasks/RHAIRFE-3082.md", "rfe-task")
        assert data["local_id"] == "RFE-001"
        # and a subsequent update keeps working
        update_frontmatter(
            "artifacts/rfe-tasks/RHAIRFE-3082.md", {"status": "Submitted"}, "rfe-task"
        )

    def test_initiative_rename_carries_local_id(self, tmp_dir):
        os.makedirs("artifacts/initiatives")
        os.makedirs("artifacts/initiative-reviews")
        write_frontmatter(
            "artifacts/initiatives/INIT-001.md",
            VALID_INITIATIVE_TASK_FM.copy(),
            "initiative-task",
        )

        rename_initiative_to_jira_key("artifacts", "INIT-001", "RHOAIENG-1234")

        data, _ = read_frontmatter("artifacts/initiatives/RHOAIENG-1234.md")
        assert data["local_id"] == "INIT-001"

    def test_local_id_pattern_rejects_jira_keys(self):
        """The field means "pre-submission id" — a Jira key in it is a bug."""
        data = {
            "rfe_id": "RHAIRFE-3082",
            "title": "T",
            "priority": "Major",
            "status": "Submitted",
            "local_id": "RHAIRFE-3082",
        }
        errors = validate(data, "rfe-task")
        assert any("local_id" in e for e in errors)


class TestRenameRejectsMalformedIds:
    """Both ids become path components; jira_key arrives from a Jira API
    response, so shapes outside the documented grammar are rejected before
    any file operation (path-traversal guard)."""

    def test_traversal_in_jira_key_rejected(self, tmp_dir):
        os.makedirs("artifacts/rfe-tasks")
        with pytest.raises(ValueError, match="invalid Jira key"):
            rename_to_jira_key("artifacts", "RFE-001", "../../etc/passwd")

    def test_traversal_in_local_id_rejected(self, tmp_dir):
        os.makedirs("artifacts/rfe-tasks")
        with pytest.raises(ValueError, match="invalid local id"):
            rename_to_jira_key("artifacts", "../RFE-001", "RHAIRFE-1600")

    def test_initiative_guards(self, tmp_dir):
        os.makedirs("artifacts/initiatives")
        with pytest.raises(ValueError, match="invalid Jira key"):
            rename_initiative_to_jira_key("artifacts", "INIT-001", "RHOAIENG-1/../2")
        with pytest.raises(ValueError, match="invalid local id"):
            rename_initiative_to_jira_key("artifacts", "INIT-001x", "RHOAIENG-1234")


class TestRenameInitiativeToJiraKey:
    """Companion files must be renamed as companions, not as the task file.

    Every unmatched name falls through to `{jira_key}.md` — the main task
    file's new name — so a missed companion silently overwrites it.
    """

    def _setup(self, tmp_dir, extra_files=()):
        os.makedirs("artifacts/initiatives")
        os.makedirs("artifacts/initiative-reviews")
        write_frontmatter(
            "artifacts/initiatives/INIT-001.md",
            VALID_INITIATIVE_TASK_FM.copy(),
            "initiative-task",
        )
        for name in extra_files:
            with open(f"artifacts/initiatives/{name}", "w") as f:
                f.write(f"contents of {name}\n")

    def test_comments_companion_keeps_its_suffix(self, tmp_dir):
        self._setup(tmp_dir, ["INIT-001-comments.md"])

        rename_initiative_to_jira_key("artifacts", "INIT-001", "RHOAIENG-1234")

        assert os.path.exists("artifacts/initiatives/RHOAIENG-1234-comments.md")
        with open("artifacts/initiatives/RHOAIENG-1234-comments.md") as f:
            assert f.read() == "contents of INIT-001-comments.md\n"

    def test_task_file_survives_companion_rename(self, tmp_dir):
        self._setup(tmp_dir, ["INIT-001-comments.md"])

        rename_initiative_to_jira_key("artifacts", "INIT-001", "RHOAIENG-1234")

        data, _ = read_frontmatter("artifacts/initiatives/RHOAIENG-1234.md")
        assert data["initiative_id"] == "RHOAIENG-1234"
        assert data["status"] == "Submitted"

    def test_removed_context_companions_still_handled(self, tmp_dir):
        self._setup(
            tmp_dir,
            ["INIT-001-removed-context.yaml", "INIT-001-removed-context.md"],
        )

        rename_initiative_to_jira_key("artifacts", "INIT-001", "RHOAIENG-1234")

        assert os.path.exists("artifacts/initiatives/RHOAIENG-1234-removed-context.yaml")
        assert os.path.exists("artifacts/initiatives/RHOAIENG-1234-removed-context.md")
        assert os.path.exists("artifacts/initiatives/RHOAIENG-1234.md")
