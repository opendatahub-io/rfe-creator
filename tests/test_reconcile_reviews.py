"""Tests for scripts/reconcile_reviews.py (AISDLC-33).

The COLLECT reconcile re-applies saved review state a late agent write clobbered and flags
every item still failing once no further revision can happen in the batch.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import preserve_review_state as prs  # noqa: E402
import reconcile_reviews as rr  # noqa: E402
from artifact_utils import read_frontmatter  # noqa: E402

RFE = ("rfe", "RHAIRFE-1", "rfe_id", "artifacts/rfe-reviews")
INIT = ("initiative", "INIT-1", "initiative_id", "artifacts/initiative-reviews")
RFE_ZERO_WHY = {"what": 2, "why": 0, "open_to_how": 2, "not_a_task": 2, "right_sized": 2}
INIT_ZERO_SCOPE = {"what": 2, "why": 2, "scope": 0, "open_to_how": 2, "right_sized": 2}


def _review(id_field, item_id, scores, **over):
    fm = {
        id_field: item_id,
        "score": sum(scores.values()),
        "pass": False,
        "recommendation": "revise",
        "feasibility": "feasible",
        "needs_attention": False,
        "scores": dict(scores),
        "auto_revised": True,
    }
    fm.update(over)
    import yaml

    return f"---\n{yaml.safe_dump(fm, sort_keys=False)}---\n# Review\n\n## Revision History\nnone\n"


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for _, _, _, reviews_dir in (RFE, INIT):
        os.makedirs(reviews_dir)
    return tmp_path


def _write(path, content):
    with open(path, "w") as f:
        f.write(content)


def _fm(item_id):
    return read_frontmatter(prs.review_path(item_id))[0]


@pytest.mark.parametrize("type_name,item_id,id_field,reviews_dir", [RFE, INIT])
def test_restores_state_a_late_write_clobbered(workdir, type_name, item_id, id_field, reviews_dir):
    scores = RFE_ZERO_WHY if type_name == "rfe" else INIT_ZERO_SCOPE
    with open(prs.state_path(item_id), "w") as f:
        json.dump(
            {
                "before_score": 6,
                "before_scores": scores,
                "auto_revised": True,
                "revision_history": "- cycle 1",
            },
            f,
        )
    # The late write: a fresh non-first-pass review, passing, flag at its default.
    passing = {k: 2 for k in scores}
    _write(
        prs.review_path(item_id),
        _review(
            id_field,
            item_id,
            passing,
            **{"pass": True, "recommendation": "submit", "auto_revised": False},
        ),
    )
    restored, flagged = rr.reconcile([item_id], type_name, cycles=2)
    assert restored == [item_id] and flagged == []
    data = _fm(item_id)
    assert data["auto_revised"] is True and data["before_score"] == 6
    assert data["score"] == 10 and data["pass"] is True  # the new pass is kept
    assert not os.path.exists(prs.state_path(item_id))
    assert "- cycle 1" in open(prs.review_path(item_id)).read()


def test_flags_an_item_still_failing_after_the_cap(workdir):
    type_name, item_id, id_field, _ = RFE
    _write(prs.review_path(item_id), _review(id_field, item_id, RFE_ZERO_WHY))
    restored, flagged = rr.reconcile([item_id], type_name, cycles=2)
    assert flagged == [item_id] and restored == []
    data = _fm(item_id)
    assert data["needs_attention"] is True
    assert data["needs_attention_reason"] == (
        "Still failing after auto-revision (2 reassess cycles): WHY scored 0/2."
    )
    assert data["recommendation"] == "revise" and data["pass"] is False


def test_initiative_labels_and_not_revised_wording(workdir):
    type_name, item_id, id_field, _ = INIT
    _write(
        prs.review_path(item_id), _review(id_field, item_id, INIT_ZERO_SCOPE, auto_revised=False)
    )
    rr.reconcile([item_id], type_name, cycles=0)
    assert (
        _fm(item_id)["needs_attention_reason"] == "Failing and not auto-revised: Scope scored 0/2."
    )


def test_below_threshold_without_a_zero(workdir):
    type_name, item_id, id_field, _ = RFE
    ones = {"what": 1, "why": 1, "open_to_how": 1, "not_a_task": 1, "right_sized": 2}
    _write(prs.review_path(item_id), _review(id_field, item_id, ones))
    rr.reconcile([item_id], type_name, cycles=1)
    assert _fm(item_id)["needs_attention_reason"] == (
        "Still failing after auto-revision (1 reassess cycle): "
        "score 6/10 below the pass threshold of 7."
    )


def test_keeps_an_existing_reason_and_is_idempotent(workdir):
    type_name, item_id, id_field, _ = RFE
    _write(
        prs.review_path(item_id),
        _review(
            id_field,
            item_id,
            RFE_ZERO_WHY,
            needs_attention=True,
            needs_attention_reason="agent said so",
        ),
    )
    assert rr.reconcile([item_id], type_name, cycles=2) == ([], [])
    assert _fm(item_id)["needs_attention_reason"] == "agent said so"
    # Flag set without a reason: the reason is filled in, the flag left alone.
    _write(
        prs.review_path("RHAIRFE-2"),
        _review(id_field, "RHAIRFE-2", RFE_ZERO_WHY, needs_attention=True),
    )
    assert rr.reconcile(["RHAIRFE-2"], type_name, cycles=2) == ([], ["RHAIRFE-2"])
    assert _fm("RHAIRFE-2")["needs_attention_reason"].startswith("Still failing")
    assert rr.reconcile(["RHAIRFE-2"], type_name, cycles=2) == ([], [])


@pytest.mark.parametrize(
    "over",
    [
        {"pass": True, "recommendation": "submit"},
        {"recommendation": "split"},
        {"recommendation": "reject"},
        {"recommendation": "autorevise_reject"},
        {"error": "review_failed"},
        {"pass": True, "recommendation": "revise"},  # marginal pass: the agent's call, not ours
    ],
)
def test_other_outcomes_are_left_alone(workdir, over):
    type_name, item_id, id_field, _ = RFE
    _write(prs.review_path(item_id), _review(id_field, item_id, RFE_ZERO_WHY, **over))
    before = open(prs.review_path(item_id)).read()
    assert rr.reconcile([item_id], type_name, cycles=2) == ([], [])
    assert open(prs.review_path(item_id)).read() == before


def test_missing_or_corrupt_review_does_not_abort_the_batch(workdir, capsys):
    type_name, item_id, id_field, reviews_dir = RFE
    _write(os.path.join(reviews_dir, "RHAIRFE-9-review.md"), "---\nscore: [\n---\n")
    _write(prs.review_path(item_id), _review(id_field, item_id, RFE_ZERO_WHY))
    restored, flagged = rr.reconcile(["RHAIRFE-8", "RHAIRFE-9", item_id], type_name, cycles=2)
    assert flagged == [item_id]
    out = capsys.readouterr().out
    assert "RESTORED=\n" in out and f"FLAGGED={item_id}" in out


def test_exactly_one_restored_line_for_pipeline_state(workdir, capsys):
    # prs.restore prints RESTORED=<id> per item; pipeline_state._parse_line_ids takes the first
    # RESTORED= line, so only the summary may reach stdout.
    type_name, _, id_field, _ = RFE
    for rid in ("RHAIRFE-1", "RHAIRFE-2"):
        with open(prs.state_path(rid), "w") as f:
            json.dump({"before_score": 6, "before_scores": RFE_ZERO_WHY, "auto_revised": True}, f)
        _write(
            prs.review_path(rid),
            _review(
                id_field,
                rid,
                {k: 2 for k in RFE_ZERO_WHY},
                **{"pass": True, "recommendation": "submit", "auto_revised": False},
            ),
        )
    rr.reconcile(["RHAIRFE-1", "RHAIRFE-2"], type_name, cycles=1)
    out = capsys.readouterr().out
    assert out.count("RESTORED=") == 1 and "RESTORED=RHAIRFE-1,RHAIRFE-2\n" in out


def test_per_item_failures_are_reported_on_one_errors_line(workdir, capsys, monkeypatch):
    type_name, item_id, id_field, _ = RFE
    _write(prs.review_path(item_id), _review(id_field, item_id, RFE_ZERO_WHY))
    _write(prs.review_path("RHAIRFE-2"), _review(id_field, "RHAIRFE-2", RFE_ZERO_WHY))
    real = rr.update_frontmatter

    def failing(path, updates, schema):
        if "RHAIRFE-2" in path:
            raise OSError("disk says no")
        return real(path, updates, schema)

    monkeypatch.setattr(rr, "update_frontmatter", failing)
    restored, flagged = rr.reconcile([item_id, "RHAIRFE-2", "../evil"], type_name, cycles=2)
    assert flagged == [item_id]
    out = capsys.readouterr().out
    assert "RECONCILE_ERROR RHAIRFE-2: disk says no" in out
    assert "RECONCILE_ERROR ../evil: invalid item id" in out
    assert out.count("RECONCILE_ERRORS=") == 1 and "RECONCILE_ERRORS=RHAIRFE-2,../evil\n" in out


def test_keep_state_leaves_the_state_file_for_the_final_reconcile(workdir):
    type_name, item_id, id_field, _ = RFE
    passing = {k: 2 for k in RFE_ZERO_WHY}
    late = {"pass": True, "recommendation": "submit", "auto_revised": False}
    with open(prs.state_path(item_id), "w") as f:
        json.dump({"before_score": 6, "before_scores": RFE_ZERO_WHY, "auto_revised": True}, f)
    _write(prs.review_path(item_id), _review(id_field, item_id, passing, **late))
    assert rr.reconcile([item_id], type_name, cycles=1, keep_state=True) == ([item_id], [])
    assert os.path.exists(prs.state_path(item_id))
    assert _fm(item_id)["auto_revised"] is True
    # The late write lands again; the final reconcile (no flag) repairs and removes.
    _write(prs.review_path(item_id), _review(id_field, item_id, passing, **late))
    assert rr.reconcile([item_id], type_name, cycles=1) == ([item_id], [])
    assert _fm(item_id)["auto_revised"] is True and _fm(item_id)["before_score"] == 6
    assert not os.path.exists(prs.state_path(item_id))
    assert rr.main(["--type", "rfe", "--keep-state", item_id]) == 0


def test_cli(workdir, capsys):
    type_name, item_id, id_field, _ = RFE
    _write(prs.review_path(item_id), _review(id_field, item_id, RFE_ZERO_WHY))
    assert rr.main(["--type", "rfe", "--cycles", "2", item_id]) == 0
    out = capsys.readouterr().out
    assert f"FLAGGED={item_id}" in out
    with pytest.raises(SystemExit) as exc:
        rr.main(["--type", "nope", item_id])
    assert exc.value.code == 2
