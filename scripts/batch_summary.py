#!/usr/bin/env python3
"""Aggregate review results for batch/final summaries."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from artifact_utils import read_frontmatter


def main():
    parser = argparse.ArgumentParser(
        description="Aggregate RFE review results for batch summaries.")
    parser.add_argument("ids", nargs="+", help="RFE IDs (e.g. RHAIRFE-100)")
    args = parser.parse_args()

    artifacts_dir = os.path.join(os.getcwd(), "artifacts")
    reviews_dir = os.path.join(artifacts_dir, "rfe-reviews")

    passed = 0
    failed = 0
    split = 0
    errors = 0
    lines = []

    for rfe_id in args.ids:
        review_path = os.path.join(reviews_dir, f"{rfe_id}-review.md")

        if not os.path.exists(review_path):
            errors += 1
            lines.append(f"{rfe_id}: ERROR (review file missing)")
            continue

        try:
            data, _ = read_frontmatter(review_path)
        except Exception as e:
            errors += 1
            lines.append(f"{rfe_id}: ERROR ({e})")
            continue

        if data.get("error"):
            errors += 1
            lines.append(f"{rfe_id}: ERROR ({data['error']})")
            continue

        rec = data.get("recommendation", "unknown")
        score = data.get("score")
        score_str = f"{score}/10" if score is not None else "?/10"

        if rec == "split":
            split += 1

        if data.get("pass"):
            passed += 1
        else:
            failed += 1

        # Build detail suffix
        details = []
        scores = data.get("scores", {})
        rs = scores.get("right_sized")
        if rs is not None and rs <= 1:
            details.append(f"right_sized={rs}")

        detail_str = f", {', '.join(details)}" if details else ""
        lines.append(f"{rfe_id}: {rec} ({score_str}{detail_str})")

    total = len(args.ids)
    print(f"TOTAL={total} PASSED={passed} FAILED={failed} "
          f"SPLIT={split} ERRORS={errors}")
    for line in lines:
        print(line)


if __name__ == "__main__":
    main()
