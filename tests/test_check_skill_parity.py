#!/usr/bin/env python3
"""Tests for scripts/check_skill_parity.py — the rfe.* / initiative-* drift lint."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import check_skill_parity as csp  # noqa: E402

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _make_pair(tmp_path, review_rfe="", review_init="", speedrun_rfe="", speedrun_init=""):
    """Create a minimal skills dir with the pairs the tests exercise.

    Dir names match the on-disk convention the rules target: RFE dotted,
    Initiative dashed.
    """
    skills = tmp_path / "skills"
    for name, text in [
        ("rfe.review", review_rfe),
        ("initiative-review", review_init),
        ("rfe.speedrun", speedrun_rfe),
        ("initiative-speedrun", speedrun_init),
    ]:
        d = skills / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(text)
    return str(skills)


def test_real_tree_passes_with_empty_baseline():
    # The shipped tree has no known drifts, and the baseline is empty, so the
    # check is fully enforcing and must be green — proving the PR #143 drifts
    # (poll barrier, reassess cycle, --priority) still hold in both forks.
    exit_code, report = csp.run(
        skills_dir=os.path.join(REPO_ROOT, ".claude", "skills"),
        baseline_path=os.path.join(REPO_ROOT, "scripts", "skill_parity_baseline.json"),
    )
    assert exit_code == 0
    assert not report["new"], report["new"]
    assert not report["known"], report["known"]
    assert not report["warnings"], report["warnings"]


def test_shipped_baseline_is_empty():
    baseline = csp.load_baseline(os.path.join(REPO_ROOT, "scripts", "skill_parity_baseline.json"))
    assert baseline == set()


def test_new_require_both_drift_is_caught(tmp_path):
    skills = _make_pair(
        tmp_path,
        review_rfe="run check_review_progress.py and reassess_cycle and filter_for_revision",
        review_init="just wait for all to complete",  # dropped all three
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"known_drifts": []}))
    exit_code, report = csp.run(skills_dir=skills, baseline_path=str(baseline))
    assert exit_code == 1
    new_ids = {rid for rid, _ in report["new"]}
    assert "review:poll-barrier" in new_ids
    assert "review:reassess-cycle" in new_ids
    assert "review:regression-guard" in new_ids


def test_new_arg_parity_drift_is_caught(tmp_path):
    skills = _make_pair(
        tmp_path,
        speedrun_rfe="create --priority <priority> --labels <labels> <prompt>",
        speedrun_init="create <prompt>",  # dropped --priority and --labels
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"known_drifts": []}))
    exit_code, report = csp.run(skills_dir=skills, baseline_path=str(baseline))
    assert exit_code == 1
    new_ids = {rid for rid, _ in report["new"]}
    assert "speedrun:priority" in new_ids
    assert "speedrun:labels" in new_ids


def test_baseline_suppresses_known_drift(tmp_path):
    skills = _make_pair(
        tmp_path,
        speedrun_rfe="create --priority <priority> <prompt>",
        speedrun_init="create <prompt>",
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"known_drifts": ["speedrun:priority"]}))
    exit_code, report = csp.run(skills_dir=skills, baseline_path=str(baseline))
    assert exit_code == 0
    assert {rid for rid, _ in report["known"]} == {"speedrun:priority"}


def test_arg_parity_not_violated_when_rfe_lacks_flag(tmp_path):
    # If the rfe fork never passes --priority, the initiative fork isn't required to.
    skills = _make_pair(
        tmp_path,
        speedrun_rfe="create <prompt>",
        speedrun_init="create <prompt>",
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"known_drifts": []}))
    exit_code, report = csp.run(skills_dir=skills, baseline_path=str(baseline))
    assert exit_code == 0
    assert not report["new"]


def test_resolved_baseline_entry_reported_and_strict_fails(tmp_path):
    # Baseline claims a drift, but both forks now have the invariant -> resolved.
    skills = _make_pair(
        tmp_path,
        speedrun_rfe="create --priority <priority> <prompt>",
        speedrun_init="create --priority <priority> <prompt>",
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"known_drifts": ["speedrun:priority"]}))

    exit_code, report = csp.run(skills_dir=skills, baseline_path=str(baseline))
    assert exit_code == 0
    assert any(rid == "speedrun:priority" for rid, _ in report["resolved"])

    exit_code_strict, _ = csp.run(skills_dir=skills, baseline_path=str(baseline), strict=True)
    assert exit_code_strict == 1
