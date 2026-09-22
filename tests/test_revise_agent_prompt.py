"""The revise-agent prompts: the frontmatter set is the agent's last action.

The wave barrier releases on the revise agent's review write that carries ``auto_revised:
true`` (check_review_progress.check_id, revise slot) and FIXUP's ``check_revised.py`` runs
right after it, so any write the agent makes afterwards can undo FIXUP's verdict — the
2026-09-21 stage dry run showed it on RHAIRFE-3444 (docs/state-machine/
pipeline-correctness-reference.md §1.16, A7). Both prompts therefore order the steps
Content Preservation → Revision History → Update Frontmatter (last), keep the flag as the
completion marker (a no-change revision still sets ``true``; ``false`` would leave the
slot pending under the legacy rule), and say that nothing may follow the set.
"""

import os
import re

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
PROMPTS = {
    "rfe": ".claude/skills/rfe.review/prompts/revise-agent.md",
    "initiative": ".claude/skills/initiative-review/prompts/revise-agent.md",
}


def _text(kind):
    with open(os.path.join(ROOT, PROMPTS[kind])) as f:
        return f.read()


@pytest.mark.parametrize("kind", sorted(PROMPTS))
class TestFrontmatterSetIsLast:
    def test_step_order(self, kind):
        text = _text(kind)
        headings = re.findall(r"^## Step (\d+): (.*)$", text, re.M)
        names = [h[1] for h in headings]
        assert [int(h[0]) for h in headings] == list(range(1, len(headings) + 1))
        assert names.index("Content Preservation") < names.index("Update Revision History")
        assert names[-1].startswith("Update Frontmatter")

    def test_set_command_is_the_last_command_and_keeps_the_marker(self, kind):
        text = _text(kind)
        commands = re.findall(r"^python3 scripts/\S+.*$", text, re.M)
        assert commands, "no commands found"
        last = commands[-1]
        assert last.startswith("python3 scripts/frontmatter.py set ")
        assert "-review.md" in last
        assert "auto_revised=true" in last
        assert "needs_attention=<true/false>" in last
        assert "needs_attention_reason=" in last
        # Only this one command sets the flag: FIXUP owns the correction.
        assert sum("auto_revised=" in c for c in commands) == 1

    def test_no_change_case_and_nothing_after_the_set(self, kind):
        text = _text(kind)
        assert "set it even if you changed nothing" in text
        assert "scripts/check_revised.py" in text
        assert "nothing may follow it" in text
        assert text.rstrip().endswith(
            "Your work is complete when the frontmatter set above has run and it was your"
            " last write."
        )

    def test_step_two_points_at_the_final_step(self, kind):
        """The Step 2 rules refer forward to the frontmatter step by its new number."""
        text = _text(kind)
        head = text.split("## Step 3:", 1)[0]
        assert "Step 3" not in head
        assert "in Step 5" in head
