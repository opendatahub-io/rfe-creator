#!/usr/bin/env python3
"""Generate a structured YAML run report from review frontmatter."""

import argparse
import os
import re
import sys
from datetime import datetime, timezone

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from artifact_utils import (
    find_review_file,
    read_frontmatter,
    resolve_ids,
    scan_initiative_task_files,
    scan_task_files,
)

DEFAULT_ARTIFACTS_DIR = os.path.join(os.getcwd(), "artifacts")

TYPE_CONFIG = {
    "rfe": {
        "score_fields": ["what", "why", "open_to_how", "not_a_task", "right_sized"],
        "reviews_dir": "rfe-reviews",
        "item_key": "per_rfe",
        "output_prefix": "",
        "extra_entry_fields": [],
        "scan_tasks": scan_task_files,
        "id_field": "rfe_id",
        # parent_key values that mean "split from"; see split_children_map.
        "child_parent_prefixes": ("RFE-", "RHAIRFE-"),
    },
    "initiative": {
        "score_fields": [
            "what",
            "why",
            "scope",
            "open_to_how",
            "right_sized",
        ],
        "reviews_dir": "initiative-reviews",
        "item_key": "per_initiative",
        "output_prefix": "initiative-run-",
        "extra_entry_fields": ["alignment", "feasibility", "needs_attention"],
        "scan_tasks": scan_initiative_task_files,
        "id_field": "initiative_id",
        "child_parent_prefixes": ("INIT-", "RHOAIENG-"),
    },
}

SCORE_FIELDS = TYPE_CONFIG["rfe"]["score_fields"]


def split_children_map(artifacts_dir, config):
    """Map parent ID -> child IDs for split children found in the task dir.

    A child declares its parent with `parent_key`. On the initiative side that
    same field also carries the RHAISTRAT Outcome link, which is a strategy
    rollup, not a split — so only same-family parents count.

    `config` is a TYPE_CONFIG entry.
    """
    children_map = {}
    for _, task_data in config["scan_tasks"](artifacts_dir):
        parent = task_data.get("parent_key")
        if parent and parent.startswith(config["child_parent_prefixes"]):
            children_map.setdefault(parent, []).append(task_data[config["id_field"]])
    return children_map


def _parse_run_id(start_time):
    """Derive run_id from a timestamp. Accepts YYYYMMDD-HHMMSS or ISO format."""
    if re.match(r"^\d{8}-\d{6}$", start_time):
        return start_time
    return datetime.fromisoformat(start_time.replace("Z", "+00:00")).strftime("%Y%m%d-%H%M%S")


def build_report(
    ids,
    start_time,
    batch_size=0,
    retried_ids=None,
    retry_success_ids=None,
    artifacts_dir=None,
    entry_type="rfe",
):
    if artifacts_dir is None:
        artifacts_dir = DEFAULT_ARTIFACTS_DIR
    if retried_ids is None:
        retried_ids = []
    if retry_success_ids is None:
        retry_success_ids = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    config = TYPE_CONFIG[entry_type]
    score_fields = config["score_fields"]
    reviews_dir = os.path.join(artifacts_dir, config["reviews_dir"])

    # Expand ID list to include split children discovered from task files
    children_map = split_children_map(artifacts_dir, config)
    all_children = [c for kids in children_map.values() for c in kids]
    expanded_ids = list(ids) + [c for c in all_children if c not in ids]

    per_item = []
    before_totals = {f: [] for f in score_fields}
    after_totals = {f: [] for f in score_fields}
    before_score_list, after_score_list = [], []
    counts = {"passed": 0, "failed": 0, "split": 0, "errors": 0}

    for item_id in expanded_ids:
        if entry_type == "rfe":
            review_path = find_review_file(artifacts_dir, item_id)
        else:
            review_path = os.path.join(reviews_dir, f"{item_id}-review.md")
            if not os.path.exists(review_path):
                review_path = None
        if not review_path:
            per_item.append({"id": item_id, "error": "review file not found"})
            counts["errors"] += 1
            continue
        try:
            data, _ = read_frontmatter(review_path)
        except Exception as e:
            per_item.append({"id": item_id, "error": str(e)})
            counts["errors"] += 1
            continue

        entry = {"id": item_id}
        rec = data.get("recommendation", "revise")
        entry["recommendation"] = rec
        entry["auto_revised"] = data.get("auto_revised", False)

        for field in config["extra_entry_fields"]:
            val = data.get(field)
            if val is not None:
                entry[field] = val

        score = data.get("score", 0)
        entry["after_score"] = score
        after_score_list.append(score)

        before = data.get("before_score")
        if before is not None:
            entry["before_score"] = before
        # An item that was never revised has no before_score, but its "before"
        # is its "after". Averaging only the revised items would compare two
        # different populations and report a phantom regression.
        before_score_list.append(before if before is not None else score)

        if data.get("auto_revised") and before is not None and before != score:
            entry["revision_cycles"] = 1
        else:
            entry["revision_cycles"] = 0

        scores = data.get("scores") or {}
        before_scores = data.get("before_scores") or {}
        for f in score_fields:
            if f in scores:
                after_totals[f].append(scores[f])
            if f in before_scores:
                before_totals[f].append(before_scores[f])
            elif f in scores:
                before_totals[f].append(scores[f])

        kids = children_map.get(item_id)
        if kids:
            entry["children"] = kids

        if rec == "split":
            counts["split"] += 1
        elif data.get("pass", False):
            counts["passed"] += 1
        else:
            counts["failed"] += 1

        per_item.append(entry)

    def avg(lst):
        return round(sum(lst) / len(lst), 1) if lst else 0.0

    results = {**counts}
    if entry_type == "rfe":
        results["retried"] = len(retried_ids)
        results["retry_successes"] = len(retry_success_ids)

    report = {
        "run_id": _parse_run_id(start_time),
        "started": start_time,
        "completed": now,
        "input_count": len(ids),
        "results": results,
        "before_scores_avg": {
            "total": avg(before_score_list),
            **{f: avg(before_totals[f]) for f in score_fields},
        },
        "after_scores_avg": {
            "total": avg(after_score_list),
            **{f: avg(after_totals[f]) for f in score_fields},
        },
        config["item_key"]: per_item,
        "errors": [e for e in per_item if "error" in e],
    }
    if entry_type == "rfe":
        report["batch_size"] = batch_size
    return report


def main():
    parser = argparse.ArgumentParser(description="Generate auto-fix run report")
    parser.add_argument(
        "--start-time", required=True, help="Timestamp (YYYYMMDD-HHMMSS or ISO format)"
    )
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--retried", default="", help="Comma-separated retried IDs")
    parser.add_argument("--retry-successes", default="", help="Comma-separated retry success IDs")
    parser.add_argument(
        "--artifacts-dir",
        default=None,
        help="Artifacts directory (default: ./artifacts)",
    )
    parser.add_argument("--ids-file", help="Read IDs from a file (one per line)")
    parser.add_argument(
        "--type",
        choices=["rfe", "initiative"],
        default="rfe",
        help="Entry type (default: rfe)",
    )
    parser.add_argument("ids", nargs="*", help="IDs (default: scan review files)")
    args = parser.parse_args()

    artifacts_dir = args.artifacts_dir or DEFAULT_ARTIFACTS_DIR
    config = TYPE_CONFIG[args.type]

    ids = resolve_ids(args.ids, args.ids_file)
    if not ids:
        # A run that reviewed nothing still gets a zero-count report. Only a
        # missing reviews directory is an error.
        reviews_dir = os.path.join(artifacts_dir, config["reviews_dir"])
        if not os.path.isdir(reviews_dir):
            parser.error(f"no IDs provided and no reviews directory at {reviews_dir}")
        ids = [
            f.replace("-review.md", "")
            for f in sorted(os.listdir(reviews_dir))
            if f.endswith("-review.md")
        ]

    retried = [x for x in args.retried.split(",") if x]
    retry_ok = [x for x in args.retry_successes.split(",") if x]

    report = build_report(
        ids,
        args.start_time,
        batch_size=args.batch_size,
        retried_ids=retried,
        retry_success_ids=retry_ok,
        artifacts_dir=artifacts_dir,
        entry_type=args.type,
    )

    out_dir = os.path.join(artifacts_dir, "auto-fix-runs")
    os.makedirs(out_dir, exist_ok=True)
    prefix = config["output_prefix"]
    out_path = os.path.join(out_dir, f"{prefix}{report['run_id']}.yaml")

    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(report, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    print(out_path)


if __name__ == "__main__":
    main()
