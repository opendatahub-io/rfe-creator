#!/usr/bin/env python3
"""Tests for markdown_to_adf — focused on the heading/paragraph infinite loop fix.

Bug: Lines starting with # that don't match the heading regex
(e.g. '### ' with no text, '##' with no space) caused an infinite loop
because they were excluded from both the heading handler AND the paragraph
accumulator, so `i` never advanced.
"""
import os
import signal
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from jira_utils import adf_to_markdown, markdown_to_adf


# ── Timeout helper ──────────────────────────────────────────────────────────

class _Timeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _Timeout("markdown_to_adf did not complete within timeout")


@pytest.fixture(autouse=True)
def enforce_timeout():
    """Kill any test that takes longer than 5 seconds — catches infinite loops."""
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(5)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old_handler)


# ── Well-formed markdown (behavior must not change) ────────────────────────

class TestWellFormedHeadings:
    def test_h1(self):
        result = markdown_to_adf("# Hello")
        assert result["content"][0]["type"] == "heading"
        assert result["content"][0]["attrs"]["level"] == 1
        assert result["content"][0]["content"][0]["text"] == "Hello"

    def test_h3(self):
        result = markdown_to_adf("### Sub-heading")
        assert result["content"][0]["type"] == "heading"
        assert result["content"][0]["attrs"]["level"] == 3
        assert result["content"][0]["content"][0]["text"] == "Sub-heading"

    def test_h6(self):
        result = markdown_to_adf("###### Deep")
        assert result["content"][0]["type"] == "heading"
        assert result["content"][0]["attrs"]["level"] == 6

    def test_heading_with_inline_formatting(self):
        result = markdown_to_adf("## **Bold heading**")
        heading = result["content"][0]
        assert heading["type"] == "heading"
        assert heading["attrs"]["level"] == 2

    def test_paragraph_text(self):
        result = markdown_to_adf("Just some text")
        assert result["content"][0]["type"] == "paragraph"
        assert result["content"][0]["content"][0]["text"] == "Just some text"

    def test_mixed_content(self):
        md = "## Title\n\nSome text\n\n### Section\n\nMore text"
        result = markdown_to_adf(md)
        types = [node["type"] for node in result["content"]]
        assert types == ["heading", "paragraph", "heading", "paragraph"]


# ── Malformed headings that caused infinite loops ──────────────────────────

class TestMalformedHeadings:
    """Lines starting with # that don't match the heading regex."""

    def test_hash_space_no_text(self):
        """'### ' with trailing space but no text — the CI trigger."""
        result = markdown_to_adf("### ")
        assert result["content"][0]["type"] == "heading"
        assert result["content"][0]["attrs"]["level"] == 3

    def test_hash_space_no_text_h1(self):
        result = markdown_to_adf("# ")
        assert result["content"][0]["type"] == "heading"
        assert result["content"][0]["attrs"]["level"] == 1

    def test_hash_space_no_text_h6(self):
        result = markdown_to_adf("###### ")
        assert result["content"][0]["type"] == "heading"
        assert result["content"][0]["attrs"]["level"] == 6

    def test_hashes_no_space(self):
        """'##' with no space — not a valid heading, should be paragraph."""
        result = markdown_to_adf("##")
        assert result["content"][0]["type"] == "paragraph"
        assert result["content"][0]["content"][0]["text"] == "##"

    def test_hash_text_no_space(self):
        """'#text' — no space after hash, should be paragraph."""
        result = markdown_to_adf("#text")
        assert result["content"][0]["type"] == "paragraph"
        assert result["content"][0]["content"][0]["text"] == "#text"

    def test_seven_hashes(self):
        """'#######' — too many hashes, should be paragraph."""
        result = markdown_to_adf("#######")
        assert result["content"][0]["type"] == "paragraph"
        assert result["content"][0]["content"][0]["text"] == "#######"

    def test_seven_hashes_with_space_and_text(self):
        """'####### text' — too many hashes, should be paragraph."""
        result = markdown_to_adf("####### text")
        assert result["content"][0]["type"] == "paragraph"
        assert result["content"][0]["content"][0]["text"] == "####### text"

    def test_hash_only(self):
        """Just '#' — no space, should be paragraph."""
        result = markdown_to_adf("#")
        assert result["content"][0]["type"] == "paragraph"
        assert result["content"][0]["content"][0]["text"] == "#"


# ── Real CI failure patterns ──────────────────────────────────────────────

class TestCIFailurePatterns:
    """Reproduce the exact patterns from the 2026-04-04 CI run."""

    def test_heading_marker_then_bold_text(self):
        """The exact pattern from RHAIRFE-1461 et al."""
        md = "### \n**Proposed Solution/Rationale**"
        result = markdown_to_adf(md)
        types = [node["type"] for node in result["content"]]
        assert "heading" in types
        assert "paragraph" in types

    def test_multiple_malformed_in_sequence(self):
        """Multiple malformed headings in a row."""
        md = "### \n## \n# \nSome text"
        result = markdown_to_adf(md)
        assert len(result["content"]) == 4  # 3 empty headings + 1 paragraph

    def test_malformed_heading_in_blockquote(self):
        """Blockquote with headings (even malformed) becomes a panel."""
        md = "> ### \n> **Some text**"
        result = markdown_to_adf(md)
        node = result["content"][0]
        assert node["type"] == "panel"
        assert len(node["content"]) >= 1

    def test_heading_in_blockquote_becomes_panel(self):
        """Blockquote with headings becomes an ADF panel (not blockquote).

        ADF blockquotes cannot contain headings (Jira returns HTTP 400).
        Quoted headings originate from Jira panels converted to markdown
        blockquotes on fetch, so they round-trip back as panels.
        Regression: RHAIRFE-584 in 20260405-012640 run.
        """
        md = "> ### **Problem**\n> \n> Description here.\n\nText after."
        result = markdown_to_adf(md)
        panel = result["content"][0]
        assert panel["type"] == "panel"
        assert panel["attrs"]["panelType"] == "info"
        # Heading preserved as a real heading inside the panel
        assert panel["content"][0]["type"] == "heading"
        assert panel["content"][0]["attrs"]["level"] == 3

    def test_plain_blockquote_stays_blockquote(self):
        """Blockquote without headings stays as a blockquote."""
        md = "> Some quoted text.\n> More text."
        result = markdown_to_adf(md)
        bq = result["content"][0]
        assert bq["type"] == "blockquote"

    def test_full_document_with_malformed_headings(self):
        """Simulates a full RFE with the problematic pattern throughout."""
        md = (
            "## Summary\n\n"
            "Some summary text.\n\n"
            "### \n"
            "**Problem Statement**\n\n"
            "The problem is described here.\n\n"
            "### \n"
            "**Acceptance Criteria**\n\n"
            "- Criterion one\n"
            "- Criterion two\n\n"
            "### \n"
            "**Scope**\n\n"
            "In scope items.\n"
        )
        result = markdown_to_adf(md)
        assert len(result["content"]) > 0
        # Verify we got headings, paragraphs, and a bullet list
        types = [node["type"] for node in result["content"]]
        assert "heading" in types
        assert "paragraph" in types
        assert "bulletList" in types


# ── Panel round-trip tests ────────────────────────────────────────────────

class TestPanelRoundTrip:
    """Verify ADF panel → markdown → ADF survives the fetch/edit/submit cycle."""

    def test_multiline_panel_all_lines_quoted(self):
        """adf_to_markdown must prefix every line with '> ', not just the first."""
        panel_adf = {
            "type": "doc", "version": 1,
            "content": [{
                "type": "panel",
                "attrs": {"panelType": "info"},
                "content": [
                    {"type": "heading", "attrs": {"level": 3},
                     "content": [{"type": "text", "text": "Problem",
                                  "marks": [{"type": "strong"}]}]},
                    {"type": "paragraph",
                     "content": [{"type": "text",
                                  "text": "Description here."}]},
                ],
            }],
        }
        md = adf_to_markdown(panel_adf)
        # Every non-empty line should be quoted
        for line in md.strip().split("\n"):
            if line.strip():
                assert line.startswith("> "), \
                    f"Line not quoted: {line!r}"

    def test_panel_round_trip_preserves_headings(self):
        """Panel with headings round-trips: panel → markdown → panel."""
        panel_adf = {
            "type": "doc", "version": 1,
            "content": [{
                "type": "panel",
                "attrs": {"panelType": "info"},
                "content": [
                    {"type": "heading", "attrs": {"level": 3},
                     "content": [{"type": "text", "text": "Problem",
                                  "marks": [{"type": "strong"}]}]},
                    {"type": "paragraph",
                     "content": [{"type": "text",
                                  "text": "Description here."}]},
                ],
            }],
        }
        md = adf_to_markdown(panel_adf)
        result = markdown_to_adf(md)
        node = result["content"][0]
        assert node["type"] == "panel"
        assert node["content"][0]["type"] == "heading"
        assert node["content"][1]["type"] == "paragraph"


# ── Safety net tests ──────────────────────────────────────────────────────

class TestSafetyNet:
    """Verify the defensive fallback handles any unrecognized line."""

    def test_deeply_nested_hashes(self):
        """10 hashes — way beyond valid heading range."""
        result = markdown_to_adf("##########")
        assert result["content"][0]["type"] == "paragraph"

    def test_hash_with_only_whitespace(self):
        """'#   ' — hash followed by spaces only."""
        result = markdown_to_adf("#   ")
        assert result["content"][0]["type"] == "heading"
        assert result["content"][0]["attrs"]["level"] == 1
