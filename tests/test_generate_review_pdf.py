#!/usr/bin/env python3
"""Tests for generate_diff helpers: path safety, frontmatter stripping, size cap."""

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import generate_review_pdf
import type_registry
from generate_review_pdf import (
    MAX_DIFF_FILE_SIZE,
    PASS_THRESHOLD,
    REPORT_CONFIG,
    _ensure_trailing_newline,
    _open_nofollow,
    _safe_artifact_path,
    _split_outcome,
    _strip_frontmatter,
    generate_diff,
)


class TestSafeArtifactPath:
    def test_normal_file(self, tmp_path):
        f = tmp_path / "file.md"
        f.write_text("hello")
        assert _safe_artifact_path(str(tmp_path), "file.md") == str(f.resolve())

    def test_missing_file(self, tmp_path):
        result = _safe_artifact_path(str(tmp_path), "missing.md")
        assert result is None or not os.path.exists(result)

    def test_symlink_rejected(self, tmp_path):
        target = tmp_path / "secret.txt"
        target.write_text("secret")
        link = tmp_path / "link.md"
        link.symlink_to(target)
        assert _safe_artifact_path(str(tmp_path), "link.md") is None

    def test_traversal_rejected(self, tmp_path):
        outside = tmp_path / "outside.txt"
        outside.write_text("outside")
        subdir = tmp_path / "sub"
        subdir.mkdir()
        assert _safe_artifact_path(str(subdir), "../outside.txt") is None

    def test_symlink_to_outside_rejected(self, tmp_path):
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        subdir = tmp_path / "artifacts"
        subdir.mkdir()
        link = subdir / "bad.md"
        link.symlink_to(outside)
        assert _safe_artifact_path(str(subdir), "bad.md") is None


class TestStripFrontmatter:
    def test_no_frontmatter(self):
        text = "Just some content\nwith lines\n"
        assert _strip_frontmatter(text) == text

    def test_standard_frontmatter(self):
        text = "---\ntitle: Test\nstatus: Ready\n---\nBody content\n"
        assert _strip_frontmatter(text) == "Body content\n"

    def test_triple_hyphens_in_body_preserved(self):
        text = "---\ntitle: Test\n---\nSome text\n--- separator ---\nMore text\n"
        result = _strip_frontmatter(text)
        assert "--- separator ---" in result
        assert "More text" in result

    def test_no_closing_delimiter(self):
        text = "---\ntitle: Test\nno closing\n"
        assert _strip_frontmatter(text) == text

    def test_empty_string(self):
        assert _strip_frontmatter("") == ""

    def test_hyphens_inside_content_not_split(self):
        text = "---\nkey: value\n---\nLine with --- in middle\n"
        result = _strip_frontmatter(text)
        assert result == "Line with --- in middle\n"

    def test_old_split_would_fail(self):
        """Old split('---', 2) would incorrectly split on '---' inside values."""
        text = "---\nkey: a---b\n---\nBody\n"
        result = _strip_frontmatter(text)
        assert result == "Body\n"


class TestGenerateDiffSizeCap:
    def test_oversized_original_returns_none(self, tmp_path):
        originals = tmp_path / "originals"
        tasks = tmp_path / "tasks"
        originals.mkdir()
        tasks.mkdir()
        (originals / "BIG.md").write_text("x" * (MAX_DIFF_FILE_SIZE + 1))
        (tasks / "BIG.md").write_text("small content")
        assert generate_diff("BIG", str(tasks), str(originals)) is None

    def test_oversized_revised_returns_none(self, tmp_path):
        originals = tmp_path / "originals"
        tasks = tmp_path / "tasks"
        originals.mkdir()
        tasks.mkdir()
        (originals / "BIG.md").write_text("small content")
        (tasks / "BIG.md").write_text("x" * (MAX_DIFF_FILE_SIZE + 1))
        assert generate_diff("BIG", str(tasks), str(originals)) is None

    def test_normal_size_produces_diff(self, tmp_path):
        originals = tmp_path / "originals"
        tasks = tmp_path / "tasks"
        originals.mkdir()
        tasks.mkdir()
        (originals / "RFE-001.md").write_text("original line\n")
        (tasks / "RFE-001.md").write_text("revised line\n")
        result = generate_diff("RFE-001", str(tasks), str(originals))
        assert result is not None
        assert "-original line" in result
        assert "+revised line" in result


class TestOpenNofollow:
    def test_regular_file(self, tmp_path):
        f = tmp_path / "file.md"
        f.write_text("hello")
        result = _open_nofollow(str(f), 1024)
        assert result is not None
        with result:
            assert result.read() == "hello"

    def test_symlink_rejected(self, tmp_path):
        target = tmp_path / "target.md"
        target.write_text("secret")
        link = tmp_path / "link.md"
        link.symlink_to(target)
        assert _open_nofollow(str(link), 1024) is None

    def test_oversized_rejected(self, tmp_path):
        f = tmp_path / "big.md"
        f.write_text("x" * 100)
        assert _open_nofollow(str(f), 50) is None

    def test_missing_file(self, tmp_path):
        assert _open_nofollow(str(tmp_path / "missing.md"), 1024) is None


class TestGenerateDiffSymlinkRejection:
    def test_symlinked_original_rejected(self, tmp_path):
        originals = tmp_path / "originals"
        tasks = tmp_path / "tasks"
        secret = tmp_path / "secret.txt"
        originals.mkdir()
        tasks.mkdir()
        secret.write_text("secret data\n")
        (originals / "RFE-001.md").symlink_to(secret)
        (tasks / "RFE-001.md").write_text("revised\n")
        assert generate_diff("RFE-001", str(tasks), str(originals)) is None

    def test_symlinked_revised_rejected(self, tmp_path):
        originals = tmp_path / "originals"
        tasks = tmp_path / "tasks"
        secret = tmp_path / "secret.txt"
        originals.mkdir()
        tasks.mkdir()
        secret.write_text("secret data\n")
        (originals / "RFE-001.md").write_text("original\n")
        (tasks / "RFE-001.md").symlink_to(secret)
        assert generate_diff("RFE-001", str(tasks), str(originals)) is None


class TestEnsureTrailingNewline:
    def test_adds_newline_when_missing(self):
        lines = ["hello"]
        result = _ensure_trailing_newline(lines)
        assert result == ["hello\n"]

    def test_preserves_existing_newline(self):
        lines = ["hello\n"]
        result = _ensure_trailing_newline(lines)
        assert result == ["hello\n"]

    def test_empty_list(self):
        assert _ensure_trailing_newline([]) == []

    def test_multiple_lines_only_fixes_last(self):
        lines = ["first\n", "second"]
        result = _ensure_trailing_newline(lines)
        assert result == ["first\n", "second\n"]


class TestGenerateDiffTokenization:
    """Verify both sides use identical tokenization (readlines, not splitlines)."""

    def test_u2028_no_spurious_diff(self, tmp_path):
        """Identical files with U+2028 should produce no diff."""
        originals = tmp_path / "originals"
        tasks = tmp_path / "tasks"
        originals.mkdir()
        tasks.mkdir()
        content = "line one line two\n"
        (originals / "RFE-001.md").write_text(content, encoding="utf-8")
        (tasks / "RFE-001.md").write_text(content, encoding="utf-8")
        result = generate_diff("RFE-001", str(tasks), str(originals))
        # No diff means empty string or None-like
        assert not result or result.strip() == ""

    def test_no_glued_lines_without_trailing_newline(self, tmp_path):
        """Changed last line without trailing newline should not glue diff lines."""
        originals = tmp_path / "originals"
        tasks = tmp_path / "tasks"
        originals.mkdir()
        tasks.mkdir()
        (originals / "RFE-001.md").write_text("old content")  # no trailing newline
        (tasks / "RFE-001.md").write_text("new content")  # no trailing newline
        result = generate_diff("RFE-001", str(tasks), str(originals))
        assert result is not None
        # Each diff line should be on its own line
        lines = result.split("\n")
        minus_lines = [ln for ln in lines if ln.startswith("-") and not ln.startswith("---")]
        plus_lines = [ln for ln in lines if ln.startswith("+") and not ln.startswith("+++")]
        assert len(minus_lines) >= 1
        assert len(plus_lines) >= 1
        # No minus line should contain a '+' prefix (glued)
        for line in minus_lines:
            assert "+" not in line, "Diff lines are glued together"


class TestGenerateDiffStderrWarnings:
    """Verify that policy-suppressed diffs emit stderr warnings."""

    def test_symlink_warns_stderr(self, tmp_path, capsys):
        originals = tmp_path / "originals"
        tasks = tmp_path / "tasks"
        secret = tmp_path / "secret.txt"
        originals.mkdir()
        tasks.mkdir()
        secret.write_text("secret data\n")
        (originals / "RFE-001.md").symlink_to(secret)
        (tasks / "RFE-001.md").write_text("revised\n")
        result = generate_diff("RFE-001", str(tasks), str(originals))
        assert result is None
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "RFE-001" in captured.err

    def test_oversized_warns_stderr(self, tmp_path, capsys):
        originals = tmp_path / "originals"
        tasks = tmp_path / "tasks"
        originals.mkdir()
        tasks.mkdir()
        (originals / "BIG.md").write_text("x" * (MAX_DIFF_FILE_SIZE + 1))
        (tasks / "BIG.md").write_text("small content")
        result = generate_diff("BIG", str(tasks), str(originals))
        assert result is None
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "BIG" in captured.err


class TestGenerateDiffMissingArtifact:
    """Missing artifacts are expected (e.g. new RFEs) and should not warn."""

    def test_missing_original_no_warning(self, tmp_path, capsys):
        originals = tmp_path / "originals"
        tasks = tmp_path / "tasks"
        originals.mkdir()
        tasks.mkdir()
        (tasks / "RFE-001.md").write_text("new content\n")
        result = generate_diff("RFE-001", str(tasks), str(originals))
        assert result is None
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_missing_revised_no_warning(self, tmp_path, capsys):
        originals = tmp_path / "originals"
        tasks = tmp_path / "tasks"
        originals.mkdir()
        tasks.mkdir()
        (originals / "RFE-001.md").write_text("original content\n")
        result = generate_diff("RFE-001", str(tasks), str(originals))
        assert result is None
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_dangling_symlink_still_warns(self, tmp_path, capsys):
        originals = tmp_path / "originals"
        tasks = tmp_path / "tasks"
        originals.mkdir()
        tasks.mkdir()
        (originals / "RFE-001.md").symlink_to(tmp_path / "nonexistent")
        (tasks / "RFE-001.md").write_text("content\n")
        result = generate_diff("RFE-001", str(tasks), str(originals))
        assert result is None
        captured = capsys.readouterr()
        assert "WARNING" in captured.err


class TestOpenNofollowEncoding:
    """Verify os.fdopen uses UTF-8 encoding with error replacement."""

    def test_utf8_content_read_correctly(self, tmp_path):
        f = tmp_path / "utf8.md"
        f.write_text("café naïve", encoding="utf-8")
        result = _open_nofollow(str(f), 1024)
        assert result is not None
        with result:
            content = result.read()
        assert "café" in content
        assert "naïve" in content

    def test_invalid_utf8_replaced(self, tmp_path):
        f = tmp_path / "bad.md"
        # Write raw bytes that are not valid UTF-8
        f.write_bytes(b"hello \xff\xfe world")
        result = _open_nofollow(str(f), 1024)
        assert result is not None
        with result:
            content = result.read()
        # Should not raise, replacement char should appear
        assert "hello" in content
        assert "world" in content
        assert "�" in content


class TestSplitOutcome:
    """Three-way disposition: only 'failed' may be partially applied in
    Jira — lumping not-attempted parents into failed misstated the Jira
    state in every renderer (CodeRabbit on #170)."""

    def test_refusals(self):
        assert _split_outcome("split_refused: too many leaf children") == "refused"
        assert _split_outcome("split_failed: agent did not write split-status file") == "refused"

    def test_failed(self):
        assert _split_outcome("split_submit_failed: exit 5") == "failed"

    def test_not_attempted(self):
        assert _split_outcome("split_not_attempted: split phase aborted") == "not_attempted"

    def test_clean(self):
        assert _split_outcome(None) is None
        assert _split_outcome("") is None


# A descriptor that ships outside types/ (never enumerated by default): the third type the
# registry-derived config must project without a code change.
FIXTURE_TYPES = os.path.join(os.path.dirname(__file__), "fixtures", "types")


class TestReportConfigIsRegistryDerived:
    """REPORT_CONFIG is a projection of types/<name>/type.yaml, not a hand-kept dict."""

    def test_keys_are_the_registry_types_in_choices_order(self):
        registry = type_registry.load(extra_roots=[], env={})
        assert list(REPORT_CONFIG) == registry.choices()

    def test_pass_threshold_is_one_module_constant_for_every_type(self):
        assert PASS_THRESHOLD == 7
        assert {cfg["pass_threshold"] for cfg in REPORT_CONFIG.values()} == {PASS_THRESHOLD}

    def test_shipped_entries_are_the_descriptor_projection(self):
        registry = type_registry.load(extra_roots=[], env={})
        for name, cfg in REPORT_CONFIG.items():
            desc = registry.get(name)
            dirs = desc.dirs("bare")
            entity = desc.get("display.entity")
            assert (cfg["reviews_dir"], cfg["tasks_dir"], cfg["originals_dir"]) == (
                dirs["reviews"],
                dirs["tasks"],
                dirs["originals"],
            )
            assert cfg["id_field"] == desc.id_field
            assert cfg["jira_prefix"] == desc.write_prefix
            assert cfg["local_prefix"] == desc.local_prefix
            assert cfg["entity_name"] == entity
            assert cfg["entity_name_plural"] == desc.get("display.entity_plural")
            # Interpolated raw into the <h1>: the entity, not a bare ampersand.
            assert cfg["report_title"] == f"{entity} Review &amp; Remediation Report"
            assert cfg["default_output"] == f"{desc.get('pipeline.poll_prefix')}review-report.html"
            assert cfg["criterion_keys"] == desc.score_fields
            assert cfg["criterion_labels"] == desc.get("reporting.criterion_labels")
            assert list(cfg["criterion_labels"]) == list(desc.get("reporting.criterion_labels"))
            assert cfg["criterion_short_labels"] == desc.get("reporting.criterion_short_labels")
            assert cfg["before_score_name_map"] == desc.get("reporting.before_score_name_map")
            assert list(cfg["before_score_name_map"]) == list(
                desc.get("reporting.before_score_name_map")
            )
            assert cfg["extra_fields"] == desc.get("reporting.pdf.extra_fields")

    def test_a_drop_in_descriptor_projects_without_a_code_change(self):
        registry = type_registry.load(extra_roots=[FIXTURE_TYPES], env={})
        cfg = generate_review_pdf._report_config(registry.get("epic"))
        assert set(cfg) == set(REPORT_CONFIG["rfe"])  # same shape, so main() is untouched
        assert cfg["entity_name"] == "Epic"
        assert cfg["entity_name_plural"] == "Epics"
        assert cfg["report_title"] == "Epic Review &amp; Remediation Report"
        assert cfg["default_output"] == "review-report.html"  # poll_prefix ""
        assert cfg["pass_threshold"] == PASS_THRESHOLD
        assert cfg["criterion_keys"] == ["score"]
        assert cfg["criterion_labels"] == {"score": "Score"}
        # No criterion_short_labels in the descriptor: the long labels apply.
        assert cfg["criterion_short_labels"] == {"score": "Score"}
        assert cfg["before_score_name_map"] == {}
        assert cfg["extra_fields"] == []  # no reporting.pdf block
        assert (cfg["jira_prefix"], cfg["local_prefix"]) == ("RHAI-", "RHAISTRAT-")

    def test_short_labels_override_only_the_keys_they_name(self):
        registry = type_registry.load(extra_roots=[], env={})
        data = copy.deepcopy(registry.get("rfe").data)
        data["reporting"]["criterion_short_labels"] = {"right_sized": "Scope"}
        cfg = generate_review_pdf._report_config(type_registry.Descriptor("rfe", data))
        assert cfg["criterion_short_labels"] == {
            "what": "WHAT",
            "why": "WHY",
            "open_to_how": "HOW",
            "not_a_task": "Not-a-task",
            "right_sized": "Scope",
        }
        # Display order stays the long-label order.
        assert list(cfg["criterion_short_labels"]) == list(cfg["criterion_labels"])

    def test_projection_does_not_alias_descriptor_data(self):
        registry = type_registry.load(extra_roots=[], env={})
        desc = registry.get("initiative")
        cfg = generate_review_pdf._report_config(desc)
        cfg["criterion_labels"]["mutated"] = "X"
        cfg["before_score_name_map"]["mutated"] = "x"
        cfg["extra_fields"].append("mutated")
        assert "mutated" not in desc.get("reporting.criterion_labels")
        assert "mutated" not in desc.get("reporting.before_score_name_map")
        assert "mutated" not in desc.get("reporting.pdf.extra_fields")


def test_report_config_escapes_descriptor_display_strings():
    """display.entity / entity_plural are interpolated into HTML; escape at derivation."""
    import generate_review_pdf as grp

    class _Desc:
        id_field = "x_id"
        write_prefix = "X-"
        local_prefix = "LX-"

        def dirs(self, form="artifacts"):
            return {"reviews": "x-reviews", "tasks": "x-tasks", "originals": "x-originals"}

        def get(self, key, default=None):
            return {
                "display.entity": "<Widget> & Co",
                "display.entity_plural": "Widgets<script>",
                "reporting.criterion_labels": {"what": "WHAT"},
                "reporting.criterion_short_labels": {},
                "reporting.before_score_name_map": {},
                "reporting.pdf.extra_fields": [],
                "pipeline.poll_prefix": "x-",
                "schema.review.score_fields": ["what"],
            }.get(key, default)

        @property
        def score_fields(self):
            return ["what"]

    cfg = grp._report_config(_Desc())
    assert cfg["entity_name"] == "&lt;Widget&gt; &amp; Co"
    assert cfg["entity_name_plural"] == "Widgets&lt;script&gt;"
    assert cfg["report_title"].startswith("&lt;Widget&gt; &amp; Co Review &amp; Remediation Report")
    # Shipped values are unchanged by escaping.
    for name in ("rfe", "initiative"):
        assert grp.REPORT_CONFIG[name]["entity_name"] in ("RFE", "Initiative")
