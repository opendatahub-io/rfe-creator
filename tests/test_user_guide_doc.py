"""The user guide for work item types stays true to the registry (AISDLC-222).

docs/working-with-work-item-types.md is written for the person filing an RFE or an Initiative.
Two plain checks keep it honest as types come and go: the page names every registered type
together with the Jira project and issue type it maps to and the ``--type`` spelling the user
types, and it reads as a user page (no pull-request numbers, no design-section marks, no
script paths or module names). The links that lead a user to the page are checked too, so a
rename of the page cannot leave the README or a skill body pointing at nothing.
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import type_registry  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_REL = "docs/working-with-work-item-types.md"
DOC = REPO_ROOT / DOC_REL
GENERIC_SKILLS = ("create", "review", "split", "submit", "speedrun", "auto-fix")

# Strings that belong to the project's internals, never to a page a PM reads.
NOT_FOR_USERS = ("PR #", "§", "scripts/", ".py")


def _text():
    return DOC.read_text(encoding="utf-8")


def test_names_every_registered_type_and_its_jira_binding():
    """Each registered type appears by its display name in a table row, with the Jira project
    and issue type it maps to and the ``--type <name>`` flag the user passes (matched as a
    whole word, so ``--type rfe`` is not satisfied by ``--type rfe-something``)."""
    registry = type_registry.load(extra_roots=[], env={})
    text = _text()
    assert registry.names(), "no registered types"
    for name in registry.names():
        desc = registry.get(name)
        entity = desc.get("display.entity")
        assert f"| {entity} |" in text, (name, entity)
        for fact in (desc.get("identity.jira.project"), desc.get("identity.jira.issue_type")):
            assert fact in text, (name, fact)
        assert re.search(rf"--type {re.escape(name)}\b", text), (name, f"--type {name}")


def test_reads_as_a_user_page():
    """No pull-request references, design-section marks or script internals on the page."""
    text = _text()
    for token in NOT_FOR_USERS:
        assert token not in text, token


def test_is_linked_from_the_readme_and_the_generic_skills():
    assert DOC_REL in (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for stage in GENERIC_SKILLS:
        skill = REPO_ROOT / ".claude" / "skills" / f"rfe-{stage}" / "SKILL.md"
        assert DOC_REL in skill.read_text(encoding="utf-8"), stage
