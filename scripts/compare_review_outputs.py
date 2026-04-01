#!/usr/bin/env python3
"""Compare golden reference review outputs against new outputs for regression testing."""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from artifact_utils import read_frontmatter

import yaml


EXACT_FIELDS = ["pass", "recommendation", "auto_revised", "feasibility", "needs_attention"]
TOLERANCE_FIELDS = ["score"]
SCORE_SUB_FIELDS = ["what", "why", "open_to_how", "not_a_task", "right_sized"]


def compare_review(rfe_id, golden_dir, new_dir, golden_review_path):
    """Compare a single review. Returns (fails, warns)."""
    fails, warns = 0, 0
    rel = os.path.relpath(golden_review_path, golden_dir)
    new_review_path = os.path.join(new_dir, rel)

    print(f"Comparing {rfe_id}...")

    if not os.path.exists(new_review_path):
        print(f"  FAIL: review file missing: {rel}")
        return 1, 0

    golden_data, _ = read_frontmatter(golden_review_path)
    new_data, _ = read_frontmatter(new_review_path)

    # Exact match fields
    for field in EXACT_FIELDS:
        gv, nv = golden_data.get(field), new_data.get(field)
        if gv == nv:
            print(f"  OK: {field} matches ({gv})")
        else:
            print(f"  FAIL: {field} mismatch ({gv} vs {nv})")
            fails += 1

    # Score within tolerance
    for field in TOLERANCE_FIELDS:
        gv, nv = golden_data.get(field), new_data.get(field)
        if gv is not None and nv is not None and abs(gv - nv) <= 1:
            if gv == nv:
                print(f"  OK: {field} within tolerance ({gv} vs {nv})")
            else:
                print(f"  WARN: {field} differs ({gv} vs {nv}, within tolerance)")
                warns += 1
        elif gv == nv:
            print(f"  OK: {field} matches ({gv})")
        else:
            print(f"  FAIL: {field} out of tolerance ({gv} vs {nv})")
            fails += 1

    # Sub-scores
    g_scores = golden_data.get("scores", {}) or {}
    n_scores = new_data.get("scores", {}) or {}
    for sub in SCORE_SUB_FIELDS:
        gv, nv = g_scores.get(sub), n_scores.get(sub)
        if gv is not None and nv is not None and abs(gv - nv) <= 1:
            if gv != nv:
                print(f"  WARN: scores.{sub} differs ({gv} vs {nv}, within tolerance)")
                warns += 1
        elif gv != nv:
            print(f"  FAIL: scores.{sub} out of tolerance ({gv} vs {nv})")
            fails += 1

    # Check companion files exist
    prefix = rfe_id
    companion_suffixes = ["-review.md"]
    task_suffixes = [".md", "-comments.md"]
    original_name = f"{prefix}.md"

    missing = []
    # Check rfe-originals
    if os.path.exists(os.path.join(golden_dir, "rfe-originals", original_name)):
        if not os.path.exists(os.path.join(new_dir, "rfe-originals", original_name)):
            missing.append(f"rfe-originals/{original_name}")
    # Check task companions
    for suffix in task_suffixes:
        gpath = os.path.join(golden_dir, "rfe-tasks", f"{prefix}{suffix}")
        if os.path.exists(gpath):
            npath = os.path.join(new_dir, "rfe-tasks", f"{prefix}{suffix}")
            if not os.path.exists(npath):
                missing.append(f"rfe-tasks/{prefix}{suffix}")

    if missing:
        print(f"  FAIL: missing files: {', '.join(missing)}")
        fails += 1
    else:
        print(f"  OK: all files present")

    # Compare removed-context YAML headings
    g_rc = os.path.join(golden_dir, "rfe-tasks", f"{prefix}-removed-context.yaml")
    n_rc = os.path.join(new_dir, "rfe-tasks", f"{prefix}-removed-context.yaml")
    if os.path.exists(g_rc):
        if not os.path.exists(n_rc):
            print(f"  FAIL: removed-context YAML missing")
            fails += 1
        else:
            with open(g_rc) as f:
                g_blocks = yaml.safe_load(f) or []
            with open(n_rc) as f:
                n_blocks = yaml.safe_load(f) or []
            g_headings = {b.get("heading") for b in g_blocks if isinstance(b, dict)}
            n_headings = {b.get("heading") for b in n_blocks if isinstance(b, dict)}
            if g_headings == n_headings:
                print(f"  OK: removed-context headings match")
            else:
                only_golden = g_headings - n_headings
                only_new = n_headings - g_headings
                parts = []
                if only_golden:
                    parts.append(f"missing: {only_golden}")
                if only_new:
                    parts.append(f"extra: {only_new}")
                print(f"  FAIL: removed-context headings differ ({'; '.join(parts)})")
                fails += 1

    print()
    return fails, warns


def main():
    parser = argparse.ArgumentParser(
        description="Compare golden reference review outputs against new outputs.")
    parser.add_argument("golden_dir", help="Golden reference artifacts directory")
    parser.add_argument("new_dir", help="New output artifacts directory")
    args = parser.parse_args()

    golden_reviews = sorted(glob.glob(
        os.path.join(args.golden_dir, "rfe-reviews", "*-review.md")))

    if not golden_reviews:
        print("No review files found in golden directory.", file=sys.stderr)
        sys.exit(1)

    total_fails, total_warns, total = 0, 0, 0
    for gpath in golden_reviews:
        fname = os.path.basename(gpath)
        rfe_id = fname.replace("-review.md", "")
        f, w = compare_review(rfe_id, args.golden_dir, args.new_dir, gpath)
        total_fails += f
        total_warns += w
        total += 1

    label_parts = [f"{total} compared"]
    if total_fails:
        label_parts.append(f"{total_fails} FAIL")
    if total_warns:
        label_parts.append(f"{total_warns} WARN")
    print(f"Summary: {', '.join(label_parts)}")

    sys.exit(1 if total_fails else 0)


if __name__ == "__main__":
    main()
