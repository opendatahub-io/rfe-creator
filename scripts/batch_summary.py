#!/usr/bin/env python3
"""Aggregate review results for batch/final summaries."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import type_registry
from artifact_utils import read_frontmatter, resolve_ids
from generate_run_report import TYPE_CONFIG, split_children_map

_TYPES = type_registry.load()

# The dirs view of the registry-derived TYPE_CONFIG this script already imports — one
# source for the per-type directories, no second copy.
_TYPE_CONFIG = {
    name: {"reviews_dir": cfg["reviews_dir"], "tasks_dir": cfg["tasks_dir"]}
    for name, cfg in TYPE_CONFIG.items()
}


def main():
    parser = argparse.ArgumentParser(description="Aggregate review results for batch summaries.")
    parser.add_argument("ids", nargs="*", help="IDs (e.g. RHAIRFE-100)")
    parser.add_argument("--type", choices=_TYPES.choices(), default="rfe")
    parser.add_argument(
        "--ids-file", help="Read IDs from a file (one per line) instead of positional args"
    )
    parser.add_argument(
        "--counts-only", action="store_true", help="Print only the counts line, no per-ID details"
    )
    args = parser.parse_args()

    ids = resolve_ids(args.ids, args.ids_file)
    if not ids:
        parser.error("no IDs provided (pass positionally or via --ids-file)")

    tc = _TYPE_CONFIG[args.type]
    artifacts_dir = os.path.join(os.getcwd(), "artifacts")
    reviews_dir = os.path.join(artifacts_dir, tc["reviews_dir"])

    # Expand to include split children. Children are discovered from their own
    # `parent_key` — the same rule the run report uses — because nothing writes
    # a `children` list onto the parent. The parent-side read stays as a
    # fallback for hand-maintained artifacts.
    all_ids = list(ids)
    id_set = set(all_ids)
    children_map = split_children_map(artifacts_dir, TYPE_CONFIG[args.type])
    for rfe_id in ids:
        declared = []
        task_path = os.path.join(artifacts_dir, tc["tasks_dir"], f"{rfe_id}.md")
        try:
            data, _ = read_frontmatter(task_path)
            declared = data.get("children") or []
        except Exception:
            pass
        for child_id in list(declared) + children_map.get(rfe_id, []):
            if child_id not in id_set:
                all_ids.append(child_id)
                id_set.add(child_id)

    passed = 0
    failed = 0
    split = 0
    errors = 0
    lines = []

    for rfe_id in all_ids:
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
        feasibility = data.get("feasibility", "unknown")
        if feasibility != "feasible":
            details.append(f"feasibility={feasibility}")
        alignment = data.get("alignment")
        if alignment and alignment != "not_assessed":
            details.append(f"alignment={alignment}")

        detail_str = f", {', '.join(details)}" if details else ""
        lines.append(f"{rfe_id}: {rec} ({score_str}{detail_str})")

    total = len(all_ids)
    print(f"TOTAL={total} PASSED={passed} FAILED={failed} SPLIT={split} ERRORS={errors}")
    if not args.counts_only:
        for line in lines:
            print(line)


if __name__ == "__main__":
    main()
