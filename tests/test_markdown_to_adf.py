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

from jira_utils import markdown_to_adf


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
        """Blockquote prefix stripping can produce malformed headings."""
        md = "> ### \n> **Some text**"
        result = markdown_to_adf(md)
        bq = result["content"][0]
        assert bq["type"] == "blockquote"
        assert len(bq["content"]) >= 1

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
