#!/usr/bin/env python3
"""Tests for scripts/check_skill_parity.py — the rfe.* / initiative-* drift lint."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import check_skill_parity as csp  # noqa: E402

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _all_skills():
    """Every skill dir referenced by any rule (both forks)."""
    skills = set()
    for rule in csp.RULES:
        skills.update(rule["pair"])
    return sorted(skills)


def _make_skills(tmp_path, content=None, omit=()):
    """Build a skills dir containing every configured skill pair.

    content: {skill_dir: SKILL.md text} overrides (default empty body).
    omit:    skill dirs to NOT create at all (to exercise the missing-file path).
    Creating every pair by default keeps unrelated rules from tripping the
    missing-file failure during a targeted test.
    """
    content = content or {}
    skills = tmp_path / "skills"
    for skill in _all_skills():
        if skill in omit:
            continue
        d = skills / skill
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(content.get(skill, ""))
    return str(skills)


def _run(skills, baseline_entries=None, strict=False, tmp_path=None):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"known_drifts": baseline_entries or []}))
    return csp.run(skills_dir=skills, baseline_path=str(baseline), strict=strict)


# ── Shipped tree ────────────────────────────────────────────────────────────


def test_real_tree_passes_with_empty_baseline():
    # The shipped tree has no known drifts and the baseline is empty, so the
    # check is fully enforcing and must be green — proving the PR #143 drifts
    # (poll barrier, reassess cycle, --priority) still hold in both forks and
    # that every monitored skill exists.
    exit_code, report = csp.run(
        skills_dir=os.path.join(REPO_ROOT, ".claude", "skills"),
        baseline_path=os.path.join(REPO_ROOT, "scripts", "skill_parity_baseline.json"),
    )
    assert exit_code == 0
    assert not report["new"], report["new"]
    assert not report["known"], report["known"]
    assert not report["warnings"], report["warnings"]
    assert not report["missing"], report["missing"]


def test_shipped_baseline_is_empty():
    baseline = csp.load_baseline(os.path.join(REPO_ROOT, "scripts", "skill_parity_baseline.json"))
    assert baseline == set()


# ── Drift detection ─────────────────────────────────────────────────────────


def test_new_require_both_drift_is_caught(tmp_path):
    skills = _make_skills(
        tmp_path,
        content={
            "rfe.review": "check_review_progress and reassess_cycle and filter_for_revision",
            "initiative-review": "just wait for all to complete",  # dropped all three
        },
    )
    exit_code, report = _run(skills, tmp_path=tmp_path)
    assert exit_code == 1
    new_ids = {rid for rid, _ in report["new"]}
    assert {"review:poll-barrier", "review:reassess-cycle", "review:regression-guard"} <= new_ids


def test_new_arg_parity_drift_is_caught(tmp_path):
    skills = _make_skills(
        tmp_path,
        content={
            "rfe.speedrun": "create --priority <priority> --labels <labels> <prompt>",
            "initiative-speedrun": "create <prompt>",  # dropped --priority and --labels
        },
    )
    exit_code, report = _run(skills, tmp_path=tmp_path)
    assert exit_code == 1
    new_ids = {rid for rid, _ in report["new"]}
    assert "speedrun:priority" in new_ids
    assert "speedrun:labels" in new_ids


def test_baseline_suppresses_known_drift(tmp_path):
    skills = _make_skills(
        tmp_path,
        content={
            "rfe.speedrun": "create --priority <priority> <prompt>",
            "initiative-speedrun": "",
        },
    )
    exit_code, report = _run(skills, baseline_entries=["speedrun:priority"], tmp_path=tmp_path)
    assert exit_code == 0
    assert {rid for rid, _ in report["known"]} == {"speedrun:priority"}


def test_arg_parity_not_violated_when_rfe_lacks_flag(tmp_path):
    # If the rfe fork never passes --priority, the initiative fork isn't required to.
    skills = _make_skills(tmp_path)  # all present, all empty
    exit_code, report = _run(skills, tmp_path=tmp_path)
    assert exit_code == 0
    assert not report["new"]
    assert not report["missing"]


def test_resolved_baseline_entry_reported_and_strict_fails(tmp_path):
    # Baseline claims a drift, but both forks now have the invariant -> resolved.
    skills = _make_skills(
        tmp_path,
        content={
            "rfe.speedrun": "create --priority <priority> <prompt>",
            "initiative-speedrun": "create --priority <priority> <prompt>",
        },
    )
    exit_code, report = _run(skills, baseline_entries=["speedrun:priority"], tmp_path=tmp_path)
    assert exit_code == 0
    assert any(rid == "speedrun:priority" for rid, _ in report["resolved"])

    exit_code_strict, _ = _run(
        skills, baseline_entries=["speedrun:priority"], strict=True, tmp_path=tmp_path
    )
    assert exit_code_strict == 1


# ── Missing skill files (a monitored skill vanished / rule went stale) ───────


def test_missing_skill_file_fails(tmp_path):
    # A renamed/deleted skill must fail the check, not silently pass.
    skills = _make_skills(tmp_path, omit={"initiative-speedrun"})
    exit_code, report = _run(skills, tmp_path=tmp_path)
    assert exit_code == 1
    missing_ids = {rid for rid, _ in report["missing"]}
    assert {"speedrun:priority", "speedrun:labels"} <= missing_ids
    # It's reported as missing, not as an arg-parity drift.
    assert not report["new"]


def test_missing_arg_parity_source_is_not_silently_ok(tmp_path):
    # Previously a missing rfe (source) skill made arg_parity return "ok" (exit 0).
    skills = _make_skills(tmp_path, omit={"rfe.speedrun"})
    exit_code, report = _run(skills, tmp_path=tmp_path)
    assert exit_code == 1
    assert {rid for rid, _ in report["missing"]} >= {"speedrun:priority", "speedrun:labels"}


def test_missing_is_not_suppressed_by_baseline(tmp_path):
    # A vanished skill fails even if its rule id is baselined — baseline is for
    # intentional divergences, not for a structurally broken/renamed skill.
    skills = _make_skills(tmp_path, omit={"initiative-review"})
    exit_code, report = _run(
        skills,
        baseline_entries=[
            "review:poll-barrier",
            "review:reassess-cycle",
            "review:regression-guard",
        ],
        tmp_path=tmp_path,
    )
    assert exit_code == 1
    assert report["missing"]
