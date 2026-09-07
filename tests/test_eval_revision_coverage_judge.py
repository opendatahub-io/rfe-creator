#!/usr/bin/env python3
"""Tests for the ``revision_coverage`` inline-check judge.

The judge body lives inline in both ``eval.yaml`` (rfe.speedrun) and
``eval-initiative.yaml`` (initiative-speedrun) and the two copies must remain
byte-identical; ``test_both_configs_identical`` fails the build if they drift.
The behavioural tests exec the body exactly the way the harness does
(``def _check(outputs, arguments): <indented body>``, see
``score._make_inline_check``) against crafted ``outputs`` records. The record
mirrors what ``score.load_case_record`` builds: ``files`` keyed by path relative
to the case directory and ``annotations`` loaded from the dataset case's
``annotations.yaml``.
"""

import os
import textwrap

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
EVAL_YAML = os.path.join(REPO_ROOT, "eval.yaml")
EVAL_INITIATIVE_YAML = os.path.join(REPO_ROOT, "eval-initiative.yaml")
JUDGE = "revision_coverage"

RFE_REVIEWS = "artifacts/rfe-reviews"
RFE_TASKS = "artifacts/rfe-tasks"
RFE_ORIGINALS = "artifacts/rfe-originals"
INIT_REVIEWS = "artifacts/initiative-reviews"
REPORT = "artifacts/auto-fix-runs/20260101-000000.yaml"

TAGGED = {"tags": ["weak-draft", "revision-expected", "missing-why"], "difficulty": "hard"}
UNTAGGED = {"tags": [], "difficulty": None}


def _config(path):
    return yaml.safe_load(open(path))


def _check_body(config_path):
    judge = next(j for j in _config(config_path)["judges"] if j["name"] == JUDGE)
    return judge["check"]


def _load_check(config_path=EVAL_YAML):
    """Compile the inline check body into a callable, mirroring score.py."""
    body = _check_body(config_path)
    wrapped = "def _check(outputs, arguments):\n" + textwrap.indent(body, "    ")
    ns = {"__builtins__": __builtins__}
    exec(compile(wrapped, f"<check:{JUDGE}>", "exec"), ns)  # noqa: S102 - trusted repo config
    return ns["_check"]


def _review(item_id, id_field="rfe_id", history="none", **frontmatter):
    frontmatter.setdefault(id_field, item_id)
    fm = yaml.safe_dump(frontmatter, sort_keys=True)
    return f"---\n{fm}---\n\n## Verdict\nok\n\n## Revision History\n{history}\n"


def _task(item_id, body="## Summary\n\nOriginal body.\n", status="Draft"):
    return f"---\nrfe_id: {item_id}\nstatus: {status}\n---\n\n{body}"


def _report(*entries, key="per_rfe"):
    return yaml.safe_dump({key: [dict(e) for e in entries]})


def _entry(item_id, before=8, after=8, cycles=0, auto_revised=False):
    return {
        "id": item_id,
        "before_score": before,
        "after_score": after,
        "revision_cycles": cycles,
        "auto_revised": auto_revised,
    }


def _run(files, annotations=None):
    record = {"files": files}
    if annotations is not None:
        record["annotations"] = annotations
    return _load_check()(record, {})


# A run in which some OTHER item was revised, so the run-level gate is open and
# only the per-case/tag logic is under test.
def _other_revised():
    return {REPORT: _report(_entry("RFE-9", before=6, after=8, cycles=1, auto_revised=True))}


# --- Drift guard: the two configs must carry the identical judge body ---------


def test_both_configs_identical():
    assert _check_body(EVAL_YAML) == _check_body(EVAL_INITIATIVE_YAML), (
        "revision_coverage has drifted between eval.yaml and eval-initiative.yaml; "
        "keep the two copies byte-identical."
    )


def test_threshold_arithmetic():
    # Same value in both configs, different denominators. RFE: 25 cases, 5 tagged
    # revision-expected, 3 of 5 revised must pass and 2 of 5 must fail.
    # Initiative: 16 cases, none tagged, one failing case allowed. Guard the float
    # boundary as well as the configured value.
    for path in (EVAL_YAML, EVAL_INITIATIVE_YAML):
        assert _config(path)["thresholds"][JUDGE]["min_pass_rate"] == 0.92
    assert (25 - 2) / 25 >= 0.92  # 3 revised -> 2 failures -> pass
    assert (25 - 3) / 25 < 0.92  # 2 revised -> 3 failures -> fail
    assert (16 - 1) / 16 >= 0.92  # one failing initiative case -> pass
    assert (16 - 2) / 16 < 0.92  # two failing initiative cases -> fail


# --- Run-level gate: zero revisions anywhere fails every case ----------------


def test_zero_revision_run_fails_untagged_case():
    passed, msg = _run(
        {
            f"{RFE_REVIEWS}/RFE-1-review.md": _review(
                "RFE-1", auto_revised=False, score=9, before_score=9
            ),
            REPORT: _report(_entry("RFE-1", 9, 9), _entry("RFE-2", 10, 10)),
        },
        UNTAGGED,
    )
    assert passed is False
    assert "never exercised" in msg and "0/2" in msg


def test_zero_revision_run_without_report_fails():
    passed, msg = _run(
        {
            f"{RFE_REVIEWS}/RFE-1-review.md": _review(
                "RFE-1", auto_revised=False, score=9, before_score=9
            )
        },
        UNTAGGED,
    )
    assert passed is False
    assert "never exercised" in msg


def test_untagged_case_passes_when_another_item_was_revised():
    passed, msg = _run(
        {
            f"{RFE_REVIEWS}/RFE-1-review.md": _review(
                "RFE-1", auto_revised=False, score=9, before_score=9
            ),
            **_other_revised(),
        },
        UNTAGGED,
    )
    assert passed is True
    assert "not revised (untagged)" in msg and "1/1" in msg


def test_missing_annotations_treated_as_untagged():
    passed, _ = _run(
        {
            f"{RFE_REVIEWS}/RFE-1-review.md": _review(
                "RFE-1", auto_revised=False, score=9, before_score=9
            ),
            **_other_revised(),
        }
    )
    assert passed is True


def test_untagged_but_revised_counts_towards_coverage():
    # The initiative dataset has no revision-expected tags; a revised item must
    # still open the run-level gate for itself and (via the report) for others.
    passed, msg = _run(
        {
            f"{INIT_REVIEWS}/INIT-4-review.md": _review(
                "INIT-4", id_field="initiative_id", auto_revised=False, score=8, before_score=6
            ),
        },
        {"tags": ["new-feature"]},
    )
    assert passed is True
    assert "before_score 6 != score 8" in msg


# --- Tagged cases need revision evidence -------------------------------------


def test_tagged_without_evidence_fails():
    passed, msg = _run(
        {
            f"{RFE_REVIEWS}/RFE-21-review.md": _review(
                "RFE-21", auto_revised=False, score=9, before_score=9
            ),
            f"{RFE_TASKS}/RFE-21.md": _task("RFE-21"),
            **_other_revised(),
        },
        TAGGED,
    )
    assert passed is False
    assert "tagged revision-expected" in msg and "RFE-21" in msg


def test_tagged_auto_revised_flag_passes():
    passed, msg = _run(
        {
            f"{RFE_REVIEWS}/RFE-21-review.md": _review(
                "RFE-21", auto_revised=True, score=8, before_score=8
            )
        },
        TAGGED,
    )
    assert passed is True
    assert "auto_revised=true" in msg


def test_tagged_score_moved_passes():
    passed, msg = _run(
        {
            f"{RFE_REVIEWS}/RFE-21-review.md": _review(
                "RFE-21", auto_revised=False, score=8, before_score=5
            )
        },
        TAGGED,
    )
    assert passed is True
    assert "before_score 5 != score 8" in msg


def test_tagged_originals_body_differs_passes():
    passed, msg = _run(
        {
            f"{RFE_REVIEWS}/RFE-21-review.md": _review(
                "RFE-21", auto_revised=False, score=8, before_score=8
            ),
            f"{RFE_ORIGINALS}/RFE-21.md": _task("RFE-21", body="## Summary\n\nWeak body.\n"),
            f"{RFE_TASKS}/RFE-21.md": _task("RFE-21", body="## Summary\n\nStrengthened body.\n"),
            **_other_revised(),
        },
        TAGGED,
    )
    assert passed is True
    assert "originals body differs" in msg


def test_originals_identical_body_is_not_evidence():
    # Same body, different frontmatter/whitespace: a saved original alone is not
    # a revision (it is also the fetch baseline for existing issues).
    passed, msg = _run(
        {
            f"{RFE_REVIEWS}/RFE-21-review.md": _review(
                "RFE-21", auto_revised=False, score=8, before_score=8
            ),
            f"{RFE_ORIGINALS}/RFE-21.md": "## Summary\n\nSame   body.\n",
            f"{RFE_TASKS}/RFE-21.md": _task(
                "RFE-21", body="## Summary\n\nSame body.\n\n", status="Ready"
            ),
            **_other_revised(),
        },
        TAGGED,
    )
    assert passed is False
    assert "no revision evidence" in msg


def test_tagged_removed_context_companion_passes():
    for companion in ("RFE-21-removed-context.yaml", "RFE-21-removed-context.md"):
        passed, msg = _run(
            {
                f"{RFE_REVIEWS}/RFE-21-review.md": _review(
                    "RFE-21", auto_revised=False, score=8, before_score=8
                ),
                f"{RFE_TASKS}/{companion}": "blocks: []\n",
            },
            TAGGED,
        )
        assert passed is True
        assert "removed-context companion" in msg


def test_tagged_revision_history_passes():
    history = "none\n- **Auto-revision (2026-01-01):** Addressed WHY.\nnone"
    passed, msg = _run(
        {
            f"{RFE_REVIEWS}/RFE-21-review.md": _review(
                "RFE-21", auto_revised=False, score=8, before_score=8, history=history
            )
        },
        TAGGED,
    )
    assert passed is True
    assert "non-empty Revision History" in msg


def test_revision_history_subheading_entries_count():
    # Some revisions record history under a ### sub-heading directly below the
    # section heading; the section must run to the next ## heading, not stop at ###.
    history = "### Auto-revision 1 (2026-01-01)\n\n**WHY (0/2 -> 1/2)**: added evidence."
    content = _review("RFE-21", auto_revised=False, score=8, before_score=8, history=history)
    content += "\n## Trailing Section\nnone\n"
    passed, msg = _run({f"{RFE_REVIEWS}/RFE-21-review.md": content}, TAGGED)
    assert passed is True
    assert "non-empty Revision History" in msg


def test_revision_history_placeholders_are_empty():
    for history in ("none", "None", "_None_", "N/A", "", "- none"):
        passed, msg = _run(
            {
                f"{RFE_REVIEWS}/RFE-21-review.md": _review(
                    "RFE-21", auto_revised=False, score=8, before_score=8, history=history
                ),
                **_other_revised(),
            },
            TAGGED,
        )
        assert passed is False, history
        assert "no revision evidence" in msg


def test_tagged_run_report_evidence_passes():
    passed, msg = _run(
        {
            f"{RFE_REVIEWS}/RFE-21-review.md": _review(
                "RFE-21", auto_revised=False, score=8, before_score=8
            ),
            REPORT: _report(_entry("RFE-21", 6, 8)),
        },
        TAGGED,
    )
    assert passed is True
    assert "run report records a revision" in msg


def test_tagged_leftover_review_state_passes():
    passed, msg = _run(
        {
            f"{RFE_REVIEWS}/RFE-21-review.md": _review(
                "RFE-21", auto_revised=False, score=8, before_score=8
            ),
            f"{RFE_REVIEWS}/RFE-21-review-state.json": '{"revision_history": "- cycle 1"}',
        },
        TAGGED,
    )
    assert passed is True
    assert "leftover review-state file" in msg


def test_any_review_in_case_counts():
    # Any review present in the case directory counts for the case. Split
    # children are NOT routed to a case in batch mode (collect.py:_collect_batch
    # matches RFE-{n:03d} prefixes for n <= case count only, so RFE-026+ land in
    # no case dir); they reach a case only in single-item runs, where the whole
    # workspace is the case. In a batch a child's revision is visible solely via
    # the run report - see test_tagged_split_parent_without_case_evidence_fails.
    passed, _ = _run(
        {
            f"{RFE_REVIEWS}/RFE-21-review.md": _review(
                "RFE-21", auto_revised=False, score=9, before_score=9
            ),
            f"{RFE_REVIEWS}/RFE-26-review.md": _review(
                "RFE-26", auto_revised=True, score=8, before_score=6
            ),
        },
        TAGGED,
    )
    assert passed is True


def test_tagged_split_parent_without_case_evidence_fails():
    # A tagged weak draft that the first review split: the parent never enters
    # the revise path and its children live in no case directory. The child's
    # revision opens the run-level gate via the report but is not evidence for
    # the parent's case, so the case fails and consumes one unit of the
    # threshold's 2-failure slack (documented limitation, pinned here).
    passed, msg = _run(
        {
            f"{RFE_REVIEWS}/RFE-21-review.md": _review(
                "RFE-21", auto_revised=False, score=9, before_score=9
            ),
            REPORT: _report(
                {
                    **_entry("RFE-21", 9, 9),
                    "role": "intermediary",
                    "recommendation": "split",
                    "children": ["RFE-26", "RFE-27"],
                },
                _entry("RFE-26", 6, 8, cycles=1, auto_revised=True),
                _entry("RFE-27", 9, 9),
            ),
        },
        TAGGED,
    )
    assert passed is False
    assert "tagged revision-expected" in msg and "1/3" in msg


# --- Single-item runs: Harbor tasks and execution.mode: case ------------------
# The shared run report then describes exactly one input item, so "the run
# revised nothing" carries no coverage signal and the tag alone decides.


def test_single_item_run_untagged_passes():
    passed, msg = _run(
        {
            f"{RFE_REVIEWS}/RFE-1-review.md": _review(
                "RFE-1", auto_revised=False, score=9, before_score=9
            ),
            REPORT: _report(_entry("RFE-1", 9, 9)),
        },
        UNTAGGED,
    )
    assert passed is True
    assert "not revised (untagged)" in msg and "0/1" in msg and "single-item run" in msg


def test_single_item_run_tagged_still_fails():
    passed, msg = _run(
        {
            f"{RFE_REVIEWS}/RFE-21-review.md": _review(
                "RFE-21", auto_revised=False, score=9, before_score=9
            ),
            REPORT: _report(_entry("RFE-21", 9, 9)),
        },
        TAGGED,
    )
    assert passed is False
    assert "tagged revision-expected" in msg and "0/1" in msg


def test_single_item_run_split_without_revision_passes():
    # The one input was split: the report lists parent + children (3 entries)
    # but still describes a single input item, so the untagged case passes.
    # Two independent inputs (test_zero_revision_run_fails_untagged_case) are a
    # batch and keep failing.
    passed, msg = _run(
        {
            f"{RFE_REVIEWS}/RFE-1-review.md": _review(
                "RFE-1", auto_revised=False, score=9, before_score=9
            ),
            f"{RFE_REVIEWS}/RFE-2-review.md": _review(
                "RFE-2", auto_revised=False, score=9, before_score=9
            ),
            REPORT: _report(
                {
                    **_entry("RFE-1", 9, 9),
                    "role": "intermediary",
                    "recommendation": "split",
                    "children": ["RFE-2", "RFE-3"],
                },
                _entry("RFE-2", 9, 9),
                _entry("RFE-3", 9, 9),
            ),
        },
        UNTAGGED,
    )
    assert passed is True
    assert "0/3" in msg and "single-item run" in msg


# --- Robustness: malformed / legacy inputs must never raise --------------------
# A raising check is recorded as value=None and dropped from the pass-rate
# denominator, so a crash on a tagged case would silently relax the gate.


def test_no_review_files():
    passed, msg = _run({f"{RFE_TASKS}/RFE-1.md": "body"}, TAGGED)
    assert passed is False
    assert "No review files" in msg


def test_malformed_inputs_do_not_raise():
    passed, _ = _run(
        {
            f"{RFE_REVIEWS}/RFE-bad-review.md": "---\nrfe_id:\n  - RFE-BAD\n---\n",
            f"{RFE_REVIEWS}/RFE-21-review.md": _review(
                "RFE-21", auto_revised=False, score=8, before_score=6
            ),
            f"{RFE_REVIEWS}/RFE-21-review-state.json": "[1, 2]",
            f"{RFE_ORIGINALS}/RFE-21.md": {"_binary": True, "path": "/x", "name": "RFE-21.md"},
            "artifacts/auto-fix-runs/list.yaml": "- a\n- b\n",
            "artifacts/auto-fix-runs/bad.yaml": (
                "per_rfe: [{id: [1], revision_cycles: '1'}, 7, "
                "{id: RFE-8, children: 'x'}, {id: RFE-9, children: [1, null]}]\n"
            ),
            # load_case_record stores an undecodable file as a marker dict.
            "artifacts/auto-fix-runs/bin.yaml": {"_binary": True, "path": "/x", "name": "bin.yaml"},
            "artifacts/auto-fix-runs/issue-snapshot-1.yaml": "issues: []\n",
        },
        {"tags": "revision-expected", "difficulty": "hard"},  # tags not a list
    )
    assert passed is True


def test_legacy_revised_field_honored():
    passed, msg = _run(
        {
            f"{RFE_REVIEWS}/RFE-21-review.md": _review(
                "RFE-21", revised=True, score=8, before_score=8
            )
        },
        TAGGED,
    )
    assert passed is True
    assert "auto_revised=true" in msg


def test_modified_mirror_does_not_shadow_task():
    # collect.py mirrors in-place edits under _modified/; the mirror must not
    # overwrite the artifacts/ copy in the basename index.
    passed, msg = _run(
        {
            f"{RFE_REVIEWS}/RFE-21-review.md": _review(
                "RFE-21", auto_revised=False, score=8, before_score=8
            ),
            f"{RFE_ORIGINALS}/RFE-21.md": "## Summary\n\nWeak.\n",
            f"{RFE_TASKS}/RFE-21.md": _task("RFE-21", body="## Summary\n\nStrong.\n"),
            f"_modified/{RFE_TASKS}/RFE-21.md": _task("RFE-21", body="## Summary\n\nWeak.\n"),
        },
        TAGGED,
    )
    assert passed is True
    assert "originals body differs" in msg
