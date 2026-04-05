#!/usr/bin/env python3
"""Generate a structured YAML run report from review frontmatter."""

import argparse
import os
import re
import sys
from datetime import datetime, timezone

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from artifact_utils import find_review_file, read_frontmatter, scan_task_files

DEFAULT_ARTIFACTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "artifacts")
SCORE_FIELDS = ["what", "why", "open_to_how", "not_a_task", "right_sized"]


def _parse_run_id(start_time):
    """Derive run_id from a timestamp. Accepts YYYYMMDD-HHMMSS or ISO format."""
    if re.match(r'^\d{8}-\d{6}$', start_time):
        return start_time
    return (datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            .strftime("%Y%m%d-%H%M%S"))


def build_report(rfe_ids, start_time, batch_size, retried_ids, retry_success_ids,
                 artifacts_dir=None):
    if artifacts_dir is None:
        artifacts_dir = DEFAULT_ARTIFACTS_DIR
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Build parent->children map from task files
    children_map = {}
    for _, task_data in scan_task_files(artifacts_dir):
        parent = task_data.get("parent_key")
        if parent:
            children_map.setdefault(parent, []).append(task_data["rfe_id"])

    # Expand ID list to include split children discovered from task files
    all_children = [c for kids in children_map.values() for c in kids]
    expanded_ids = list(rfe_ids) + [c for c in all_children if c not in rfe_ids]

    per_rfe = []
    before_totals = {f: [] for f in SCORE_FIELDS}
    after_totals = {f: [] for f in SCORE_FIELDS}
    before_score_list, after_score_list = [], []
    counts = {"passed": 0, "failed": 0, "split": 0, "errors": 0}

    for rfe_id in expanded_ids:
        review_path = find_review_file(artifacts_dir, rfe_id)
        if not review_path:
            per_rfe.append({"id": rfe_id, "error": "review file not found"})
            counts["errors"] += 1
            continue
        try:
            data, _ = read_frontmatter(review_path)
        except Exception as e:
            per_rfe.append({"id": rfe_id, "error": str(e)})
            counts["errors"] += 1
            continue

        entry = {"id": rfe_id}
        rec = data.get("recommendation", "revise")
        entry["recommendation"] = rec
        entry["auto_revised"] = data.get("auto_revised", False)

        score = data.get("score", 0)
        entry["after_score"] = score
        after_score_list.append(score)

        before = data.get("before_score")
        if before is not None:
            entry["before_score"] = before
            before_score_list.append(before)

        # Revision cycles approximation
        if data.get("auto_revised") and before is not None and before != score:
            entry["revision_cycles"] = 1
        else:
            entry["revision_cycles"] = 0

        # Aggregate score components
        scores = data.get("scores") or {}
        for f in SCORE_FIELDS:
            if f in scores:
                after_totals[f].append(scores[f])
        before_scores = data.get("before_scores") or {}
        for f in SCORE_FIELDS:
            if f in before_scores:
                before_totals[f].append(before_scores[f])

        # Children (for splits)
        kids = children_map.get(rfe_id)
        if kids:
            entry["children"] = kids

        # Count results
        if rec == "split":
            counts["split"] += 1
        elif data.get("pass", False):
            counts["passed"] += 1
        else:
            counts["failed"] += 1

        per_rfe.append(entry)

    def avg(lst):
        return round(sum(lst) / len(lst), 1) if lst else 0.0

    report = {
        "run_id": _parse_run_id(start_time),
        "started": start_time,
        "completed": now,
        "batch_size": batch_size,
        "input_count": len(rfe_ids),
        "results": {
            **counts,
            "retried": len(retried_ids),
            "retry_successes": len(retry_success_ids),
        },
        "before_scores_avg": {
            "total": avg(before_score_list),
            **{f: avg(before_totals[f]) for f in SCORE_FIELDS},
        },
        "after_scores_avg": {
            "total": avg(after_score_list),
            **{f: avg(after_totals[f]) for f in SCORE_FIELDS},
        },
        "per_rfe": per_rfe,
        "errors": [e for e in per_rfe if "error" in e],
    }
    return report


def main():
    parser = argparse.ArgumentParser(description="Generate auto-fix run report")
    parser.add_argument("--start-time", required=True,
                        help="Timestamp (YYYYMMDD-HHMMSS or ISO format)")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--retried", default="", help="Comma-separated retried IDs")
    parser.add_argument("--retry-successes", default="",
                        help="Comma-separated retry success IDs")
    parser.add_argument("--artifacts-dir", default=None,
                        help="Artifacts directory (default: ../artifacts relative to script)")
    parser.add_argument("rfe_ids", nargs="*", help="RFE IDs (default: scan review files)")
    args = parser.parse_args()

    artifacts_dir = args.artifacts_dir or DEFAULT_ARTIFACTS_DIR

    # If no IDs provided, scan review files
    if not args.rfe_ids:
        reviews_dir = os.path.join(artifacts_dir, "rfe-reviews")
        args.rfe_ids = [f.replace("-review.md", "")
                        for f in sorted(os.listdir(reviews_dir))
                        if f.endswith("-review.md")]

    retried = [x for x in args.retried.split(",") if x]
    retry_ok = [x for x in args.retry_successes.split(",") if x]

    report = build_report(args.rfe_ids, args.start_time, args.batch_size,
                          retried, retry_ok, artifacts_dir=artifacts_dir)

    out_dir = os.path.join(artifacts_dir, "auto-fix-runs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{report['run_id']}.yaml")

    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(report, f, default_flow_style=False, sort_keys=False,
                  allow_unicode=True)

    print(out_path)


if __name__ == "__main__":
    main()
