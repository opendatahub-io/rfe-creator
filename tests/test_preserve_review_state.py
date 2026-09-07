"""Tests for scripts/preserve_review_state.py.

The re-review recreates each review file from scratch, so everything cumulative
(before_scores, the auto_revised flag, revision history) survives only through
save/restore. An item revised in cycle 1 that passes in cycle 2 is not revised
again, so nothing else would ever set auto_revised back; the flag drives the
auto-revised Jira label at submit.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import preserve_review_state as prs  # noqa: E402
from artifact_utils import read_frontmatter  # noqa: E402

RFE_SCORES = "what: 2\n  why: 0\n  open_to_how: 1\n  not_a_task: 1\n  right_sized: 2"
INIT_SCORES = "what: 2\n  why: 0\n  scope: 1\n  open_to_how: 1\n  right_sized: 2"


def _revised_review(id_field, item_id, scores, auto_revised):
    """Review as it looks right before REASSESS_SAVE: revised, still failing."""
    return f"""---
{id_field}: {item_id}
score: 6
pass: false
recommendation: revise
feasibility: feasible
needs_attention: false
scores:
  {scores}
auto_revised: {"true" if auto_revised else "false"}
before_score: 6
before_scores:
  {scores}
---
# Review

## Revision History
- 2026-09-04 (auto-revise): reframed the objective as a business outcome
"""


def _fresh_review(id_field, item_id, scores):
    """Review as REASSESS_REVIEW writes it: new pass, schema-default flag."""
    return f"""---
{id_field}: {item_id}
score: 9
pass: true
recommendation: submit
feasibility: feasible
needs_attention: false
scores:
  {scores}
auto_revised: false
---
# Review

## Revision History
"""


CASES = [
    ("RHAIRFE-1", "rfe_id", "artifacts/rfe-reviews", RFE_SCORES),
    ("INIT-1", "initiative_id", "artifacts/initiative-reviews", INIT_SCORES),
]


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for _, _, reviews_dir, _ in CASES:
        os.makedirs(reviews_dir)
    return tmp_path


def _write(path, content):
    with open(path, "w") as f:
        f.write(content)


@pytest.mark.parametrize("item_id,id_field,reviews_dir,scores", CASES)
def test_save_records_the_verified_flag(workdir, item_id, id_field, reviews_dir, scores):
    _write(prs.review_path(item_id), _revised_review(id_field, item_id, scores, True))
    prs.save(item_id)
    state = json.load(open(prs.state_path(item_id)))
    assert state["auto_revised"] is True
    assert state["before_score"] == 6
    assert "reframed the objective" in state["revision_history"]


@pytest.mark.parametrize("item_id,id_field,reviews_dir,scores", CASES)
def test_restore_carries_the_flag_into_the_fresh_review(
    workdir, item_id, id_field, reviews_dir, scores
):
    _write(prs.review_path(item_id), _revised_review(id_field, item_id, scores, True))
    prs.save(item_id)
    # REASSESS_SAVE deletes the review; REASSESS_REVIEW writes a new one.
    _write(prs.review_path(item_id), _fresh_review(id_field, item_id, scores))

    prs.restore(item_id)

    data, body = read_frontmatter(prs.review_path(item_id))
    assert data["auto_revised"] is True
    assert data["score"] == 9  # the new pass is kept
    assert data["before_score"] == 6
    assert data["before_scores"]["why"] == 0
    assert "reframed the objective" in body
    assert not os.path.exists(prs.state_path(item_id))


def test_restore_never_raises_a_flag_that_was_unset(workdir):
    item_id = "RHAIRFE-2"
    _write(prs.review_path(item_id), _revised_review("rfe_id", item_id, RFE_SCORES, False))
    prs.save(item_id)
    _write(prs.review_path(item_id), _fresh_review("rfe_id", item_id, RFE_SCORES))

    prs.restore(item_id)

    data, _ = read_frontmatter(prs.review_path(item_id))
    assert data["auto_revised"] is False
    assert data["before_score"] == 6


def test_restore_tolerates_a_state_file_without_the_flag(workdir):
    """State files written before the flag was saved must still restore cleanly."""
    item_id = "RHAIRFE-3"
    _write(prs.review_path(item_id), _fresh_review("rfe_id", item_id, RFE_SCORES))
    with open(prs.state_path(item_id), "w") as f:
        json.dump({"before_score": 5, "before_scores": None, "revision_history": ""}, f)

    prs.restore(item_id)

    data, _ = read_frontmatter(prs.review_path(item_id))
    assert data["auto_revised"] is False
    assert data["before_score"] == 5
    assert not os.path.exists(prs.state_path(item_id))
