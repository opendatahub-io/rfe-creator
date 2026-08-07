#!/usr/bin/env python3
"""Lint: enforce parity of invariant procedural regions across skill-pair forks.

The RFE (``rfe.*``) and Initiative (``initiative-*``) skills were created by
fork-and-modify: each ``initiative-X`` began as a copy of ``rfe.X`` with
type-specific edits. That is a deliberate, defensible choice for the divergent
*judgment* prose (rubric, strategic-alignment gating, split calibration). But a
set of procedural disciplines is meant to stay identical in BOTH forks, and
copying has repeatedly dropped them by accident (PR #143 shipped, and later
fixed, an initiative-review that had lost the bounded review poll barrier and the
reassess cycle, and an initiative-speedrun that had dropped the --priority
passthrough).

This check flags, for each pair, when one fork contains an invariant its sibling
lacks. It checks *parity between the pair*, not absolute presence: a pattern
absent from both forks is consistent (reported only as a warning).

All known drifts are currently fixed, so the baseline (skill_parity_baseline.json)
is empty and the check is fully enforcing. If a legitimate, intentional
divergence is ever introduced, add its rule id to the baseline to record it as
tracked debt; a NEW divergence fails CI. Run with ``--strict`` to also fail when
a baselined drift has since been fixed (so the baseline cannot accumulate stale
entries).
"""

import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
DEFAULT_SKILLS_DIR = os.path.join(_REPO_ROOT, ".claude", "skills")
DEFAULT_BASELINE = os.path.join(_HERE, "skill_parity_baseline.json")

# Rule kinds:
#   require_both -> `pattern` must appear (>=1x) in BOTH forks, or neither.
#                   Violated when exactly one fork has it (an invariant was
#                   dropped from the copy).
#   arg_parity   -> if `pattern` (a CLI flag) appears in the rfe fork it must
#                   also appear in the initiative fork. Directional: catches a
#                   passthrough the copy forgot to forward.
#
# Pairs use the on-disk skill dir names: RFE skills are dotted (rfe.review),
# Initiative skills are dashed (initiative-review).
RULES = [
    {
        "id": "review:poll-barrier",
        "pair": ("rfe.review", "initiative-review"),
        "kind": "require_both",
        "pattern": r"check_review_progress",
        "desc": "bounded review poll barrier (check_review_progress.py)",
    },
    {
        "id": "review:reassess-cycle",
        "pair": ("rfe.review", "initiative-review"),
        "kind": "require_both",
        "pattern": r"reassess_cycle",
        "desc": "bounded reassess cycle counter",
    },
    {
        "id": "review:regression-guard",
        "pair": ("rfe.review", "initiative-review"),
        "kind": "require_both",
        "pattern": r"filter_for_revision",
        "desc": "score-regression guard (filter_for_revision.py)",
    },
    {
        "id": "speedrun:priority",
        "pair": ("rfe.speedrun", "initiative-speedrun"),
        "kind": "arg_parity",
        "pattern": r"--priority",
        "desc": "per-entry --priority passthrough to the create agent",
    },
    {
        "id": "speedrun:labels",
        "pair": ("rfe.speedrun", "initiative-speedrun"),
        "kind": "arg_parity",
        "pattern": r"--labels",
        "desc": "per-entry --labels passthrough to the create agent",
    },
    {
        "id": "autofix:wave-barrier",
        "pair": ("rfe.auto-fix", "initiative-auto-fix"),
        "kind": "require_both",
        "pattern": r"wait-for-wave",
        "desc": "pipeline wave synchronization barrier (wait-for-wave)",
    },
]


def _read_skill(skill, skills_dir):
    """Return the SKILL.md text for a skill dir, or '' if it does not exist."""
    path = os.path.join(skills_dir, skill, "SKILL.md")
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def evaluate_rule(rule, skills_dir):
    """Evaluate one rule.

    Returns (status, message) where status is one of:
      "ok"        -> parity holds
      "violation" -> an invariant is present in one fork but missing in the other
      "warning"   -> pattern absent from both forks (rule may be stale, or a
                     shared regression); not a failure
    """
    rfe_skill, init_skill = rule["pair"]
    rfe_text = _read_skill(rfe_skill, skills_dir)
    init_text = _read_skill(init_skill, skills_dir)
    rfe_n = len(re.findall(rule["pattern"], rfe_text))
    init_n = len(re.findall(rule["pattern"], init_text))

    if rule["kind"] == "arg_parity":
        if rfe_n > 0 and init_n == 0:
            return "violation", (
                f"{init_skill} drops '{rule['pattern']}' ({rule['desc']}) "
                f"that {rfe_skill} passes ({rfe_n}x)"
            )
        return "ok", ""

    # require_both
    if rfe_n > 0 and init_n == 0:
        return "violation", (
            f"{init_skill} is missing '{rule['pattern']}' ({rule['desc']}) "
            f"present in {rfe_skill} ({rfe_n}x)"
        )
    if init_n > 0 and rfe_n == 0:
        return "violation", (
            f"{rfe_skill} is missing '{rule['pattern']}' ({rule['desc']}) "
            f"present in {init_skill} ({init_n}x)"
        )
    if rfe_n == 0 and init_n == 0:
        return "warning", (
            f"'{rule['pattern']}' ({rule['desc']}) absent from BOTH "
            f"{rfe_skill} and {init_skill} — rule may be stale or a shared regression"
        )
    return "ok", ""


def load_baseline(path):
    """Return the set of baselined (known-drift) rule ids."""
    if not path or not os.path.isfile(path):
        return set()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return set(data.get("known_drifts", []))


def run(skills_dir=DEFAULT_SKILLS_DIR, baseline_path=DEFAULT_BASELINE, strict=False):
    """Run all rules. Return (exit_code, report) where report is a dict."""
    baseline = load_baseline(baseline_path)
    report = {"new": [], "known": [], "warnings": [], "resolved": []}

    for rule in RULES:
        status, msg = evaluate_rule(rule, skills_dir)
        if status == "violation":
            if rule["id"] in baseline:
                report["known"].append((rule["id"], msg))
            else:
                report["new"].append((rule["id"], msg))
        elif status == "warning":
            report["warnings"].append((rule["id"], msg))

    # Baselined ids that no longer violate -> the drift was fixed; suggest cleanup.
    rules_by_id = {r["id"]: r for r in RULES}
    for rid in sorted(baseline):
        rule = rules_by_id.get(rid)
        if rule is None:
            report["resolved"].append((rid, "baseline id has no matching rule"))
            continue
        status, _ = evaluate_rule(rule, skills_dir)
        if status != "violation":
            report["resolved"].append((rid, "drift fixed — remove from baseline to lock it in"))

    exit_code = 1 if report["new"] else 0
    if strict and report["resolved"]:
        exit_code = 1
    return exit_code, report


def _print_report(report, strict):
    for rid, msg in report["warnings"]:
        print(f"  ⚠ WARN  [{rid}] {msg}")
    for rid, msg in report["known"]:
        print(f"  ○ KNOWN [{rid}] {msg} (baselined)")
    for rid, msg in report["resolved"]:
        tag = "FIXED" if not strict else "FIXED (strict → failing)"
        print(f"  ✓ {tag} [{rid}] {msg}")
    for rid, msg in report["new"]:
        print(f"  ✗ DRIFT [{rid}] {msg}")

    if report["new"]:
        print(
            f"\nSkill-parity check FAILED: {len(report['new'])} new divergence(s). "
            "A procedural invariant present in one fork is missing from its sibling. "
            "Fix the copy, or (if intended) add the rule id to skill_parity_baseline.json."
        )
    else:
        summary = "Skill-parity check passed"
        if report["known"]:
            summary += f" ({len(report['known'])} known drift(s) baselined)"
        print(f"\n{summary}.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-dir", default=DEFAULT_SKILLS_DIR)
    parser.add_argument("--baseline", default=DEFAULT_BASELINE)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also fail if a baselined drift has been fixed (forces baseline cleanup)",
    )
    args = parser.parse_args(argv)
    exit_code, report = run(args.skills_dir, args.baseline, args.strict)
    _print_report(report, args.strict)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
